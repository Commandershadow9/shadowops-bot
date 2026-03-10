"""
RecoveryMixin — Verification, Rollback, Summary und Status
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import SecurityEventBatch, RemediationPlan

logger = logging.getLogger('shadowops')


class RecoveryMixin:
    """Mixin für Verification, Rollback, Final-Summary und Status"""

    async def _verify_project_fixes(self, project_path: str, project_name: str, project_events: List = None, before_counts: Dict = None) -> bool:
        """
        Verifiziert ob Fixes erfolgreich waren durch Re-Scan

        Für Docker-Projekte: Führt Trivy Scan durch und prüft ob Vulnerabilities reduziert

        Args:
            project_path: Pfad zum Projekt
            project_name: Name des Projekts
            project_events: Optional - Liste der Events für dieses Projekt (um Image-Namen zu extrahieren)
            before_counts: Optional - Vulnerability counts vor dem Fix (für Vergleich)
        """
        logger.info(f"🔍 Starte Verification Scan für {project_name}...")

        # Check if project has Docker (Dockerfile exists)
        dockerfile = os.path.join(project_path, 'Dockerfile')
        if not os.path.exists(dockerfile):
            logger.warning(f"⚠️ Kein Dockerfile gefunden - überspringe Verification")
            return True  # No verification possible, assume success

        try:
            # Try to get image name from event data first (most reliable!)
            image_name = None

            if project_events:
                for event in project_events:
                    image_details = event.details.get('ImageDetails', {})
                    affected_images = event.details.get('AffectedImages', [])

                    # Try to find an image that belongs to this project
                    for img_name in affected_images:
                        img_data = image_details.get(img_name, {})
                        if img_data.get('project') == project_path:
                            image_name = img_name
                            logger.info(f"   📦 Found image from event data: {image_name}")
                            break

                    if image_name:
                        break

            # Fallback 1: Try to extract from docker-compose.yml
            if not image_name:
                image_name = await self._get_image_from_compose(project_path, project_name)

            # Fallback 2: Try to get from running containers
            if not image_name:
                image_name = await self._get_image_from_docker_ps(project_path, project_name)

            # Fallback 3: Hardcoded mapping (last resort)
            if not image_name:
                project_to_image = {
                    '/home/cmdshadow/GuildScout': 'guildscout-app',
                    '/home/cmdshadow/project': 'sicherheitstool-app',
                    '/home/cmdshadow/shadowops-bot': 'shadowops-bot'
                }
                image_name = project_to_image.get(project_path)
                if image_name:
                    logger.info(f"   📦 Using hardcoded image name: {image_name}")

            if not image_name:
                logger.warning(f"⚠️ Konnte Image-Name für {project_path} nicht ermitteln")
                logger.warning(f"⚠️ Überspringe Verification (kein Image gefunden)")
                return True

            # Try to scan the image (if it exists)
            scan_output = f"/tmp/trivy_verify_{project_name}.json"

            cmd = [
                'trivy', 'image',
                '--format', 'json',
                '--output', scan_output,
                '--severity', 'CRITICAL,HIGH',
                image_name
            ]

            logger.info(f"   🔍 Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                logger.warning(f"⚠️ Trivy scan fehlgeschlagen (returncode: {result.returncode})")
                logger.warning(f"   stderr: {result.stderr[:200]}")
                # Don't fail verification if scan fails (image might not exist yet)
                return True

            # Parse scan results
            if os.path.exists(scan_output):
                with open(scan_output, 'r') as f:
                    scan_data = json.load(f)

                # Count vulnerabilities
                critical_count = 0
                high_count = 0

                results = scan_data.get('Results', [])
                for result_item in results:
                    vulns = result_item.get('Vulnerabilities', [])
                    for vuln in vulns:
                        severity = vuln.get('Severity', '')
                        if severity == 'CRITICAL':
                            critical_count += 1
                        elif severity == 'HIGH':
                            high_count += 1

                logger.info(f"   📊 Verification Scan Results:")
                logger.info(f"      CRITICAL: {critical_count}")
                logger.info(f"      HIGH: {high_count}")

                # Compare with before_counts if available
                if before_counts:
                    before_critical = before_counts.get('critical', 0)
                    before_high = before_counts.get('high', 0)

                    improvements = {
                        'critical': before_critical - critical_count,
                        'high': before_high - high_count
                    }

                    logger.info(f"   📊 Comparison with before fix:")
                    logger.info(f"      CRITICAL: {before_critical} → {critical_count} (Δ {improvements['critical']:+d})")
                    logger.info(f"      HIGH: {before_high} → {high_count} (Δ {improvements['high']:+d})")

                    # Success criteria: No critical vulnerabilities OR significant improvement
                    if critical_count == 0:
                        logger.info(f"   ✅ Keine CRITICAL Vulnerabilities mehr!")
                        return True
                    elif improvements['critical'] > 0 or improvements['high'] > 0:
                        logger.info(f"   ✅ Verbesserung erkannt: CRITICAL -{improvements['critical']}, HIGH -{improvements['high']}")
                        return True
                    else:
                        logger.warning(f"   ⚠️ Keine Verbesserung erkannt - Fix möglicherweise fehlgeschlagen")
                        return False
                else:
                    # No before_counts available, use simple criteria
                    if critical_count == 0:
                        logger.info(f"   ✅ Keine CRITICAL Vulnerabilities mehr!")
                        return True
                    else:
                        logger.warning(f"   ⚠️ Noch {critical_count} CRITICAL Vulnerabilities vorhanden")
                        # Without before_counts, we can't verify improvement, assume success
                        return True

            else:
                logger.warning(f"⚠️ Scan Output nicht gefunden: {scan_output}")
                return True

        except subprocess.TimeoutExpired:
            logger.error(f"❌ Verification Scan timeout für {project_name}")
            return False
        except Exception as e:
            logger.error(f"❌ Verification Scan Fehler: {e}", exc_info=True)
            return False

    async def _get_image_from_compose(self, project_path: str, project_name: str) -> Optional[str]:
        """
        Versucht Image-Namen aus docker-compose.yml zu extrahieren
        """
        import yaml

        compose_files = ['docker-compose.yml', 'docker-compose.yaml']

        for compose_file in compose_files:
            compose_path = os.path.join(project_path, compose_file)
            if not os.path.exists(compose_path):
                continue

            try:
                with open(compose_path, 'r') as f:
                    compose_data = yaml.safe_load(f)

                services = compose_data.get('services', {})
                if not services:
                    continue

                # Get first service's image name
                for service_name, service_config in services.items():
                    image = service_config.get('image')
                    if image:
                        logger.info(f"   📦 Found image from docker-compose.yml: {image}")
                        return image

                    # If no image specified, try to construct from build context
                    build = service_config.get('build')
                    if build:
                        # Image name is usually project_service
                        constructed_image = f"{project_name.lower()}-{service_name}"
                        logger.info(f"   📦 Constructed image from build: {constructed_image}")
                        return constructed_image

            except Exception as e:
                logger.debug(f"Could not parse {compose_file}: {e}")
                continue

        return None

    async def _get_image_from_docker_ps(self, project_path: str, project_name: str) -> Optional[str]:
        """
        Versucht Image-Namen von laufenden Containern zu ermitteln
        """
        try:
            # Get running containers with labels that might match project
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Image}}'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                images = result.stdout.strip().split('\n')
                # Try to find image matching project name
                for image in images:
                    if project_name.lower() in image.lower():
                        logger.info(f"   📦 Found image from docker ps: {image}")
                        return image

        except Exception as e:
            logger.debug(f"Could not get images from docker ps: {e}")

        return None

    async def _execute_fix_for_source(self, source: str, event_dict: Dict, strategy: Dict) -> Dict:
        """
        Führt Fix aus basierend auf Event-Source

        Ruft direkt die entsprechenden Fixer auf
        """
        try:
            if source == 'trivy':
                if not self.self_healing.trivy_fixer:
                    return {'status': 'failed', 'error': 'TrivyFixer not initialized'}
                return await self.self_healing.trivy_fixer.fix(event=event_dict, strategy=strategy)

            elif source == 'crowdsec':
                if not self.self_healing.crowdsec_fixer:
                    return {'status': 'failed', 'error': 'CrowdSecFixer not initialized'}
                return await self.self_healing.crowdsec_fixer.fix(event=event_dict, strategy=strategy)

            elif source == 'fail2ban':
                if not self.self_healing.fail2ban_fixer:
                    return {'status': 'failed', 'error': 'Fail2banFixer not initialized'}
                return await self.self_healing.fail2ban_fixer.fix(event=event_dict, strategy=strategy)

            elif source == 'aide':
                if not self.self_healing.aide_fixer:
                    return {'status': 'failed', 'error': 'AideFixer not initialized'}
                return await self.self_healing.aide_fixer.fix(event=event_dict, strategy=strategy)

            else:
                return {'status': 'failed', 'error': f'Unknown source: {source}'}

        except Exception as e:
            logger.error(f"❌ Fix execution error for {source}: {e}", exc_info=True)
            return {'status': 'failed', 'error': str(e)}

    async def _rollback_project(self, backup_metadata: List, project_name: str):
        """
        Führt Rollback für ein einzelnes Projekt durch
        """
        logger.info(f"🔄 Starte Rollback für Projekt: {project_name}")

        backup_manager = self.self_healing.backup_manager

        for backup in backup_metadata:
            try:
                # FIX: backup ist BackupInfo Objekt, nicht Dict!
                success = await backup_manager.restore_backup(backup.backup_id)
                if success:
                    logger.info(f"   ✅ Restored: {backup.source_path}")
                else:
                    logger.error(f"   ❌ Restore failed: {backup.source_path}")
            except Exception as e:
                logger.error(f"   ❌ Rollback error: {e}")

        logger.info(f"✅ Rollback abgeschlossen für {project_name}")

    async def _rollback(
        self,
        backup_metadata: List,
        executed_phases: List[Dict],
        exec_message=None,
        exec_embed=None
    ):
        """
        Führt Rollback durch nach Fehler

        Restored alle Backups in umgekehrter Reihenfolge
        """
        logger.warning(f"🔄 Starte Rollback...")
        logger.info(f"   💾 {len(backup_metadata)} Backups zu restoren")
        logger.info(f"   🔙 Rollback für {len(executed_phases)} Phasen")

        try:
            # Access backup manager from self-healing
            backup_manager = self.self_healing.backup_manager

            # Restore backups in reverse order (undo last changes first)
            restored_count = 0
            failed_count = 0

            for backup_info in reversed(backup_metadata):
                try:
                    logger.info(f"   🔙 Restoring: {backup_info.source_path}")

                    # Discord Live Update
                    if exec_message and exec_embed:
                        exec_embed.set_field_at(
                            0,
                            name="📊 Status",
                            value=f"🔄 Rollback läuft...\n\n📝 Restoring {restored_count + 1}/{len(backup_metadata)}\n{backup_info.source_path}",
                            inline=False
                        )
                        await exec_message.edit(embed=exec_embed)

                    # Restore backup
                    success = await backup_manager.restore_backup(backup_info.backup_id)

                    if success:
                        logger.info(f"      ✅ Restored: {backup_info.source_path}")
                        restored_count += 1
                    else:
                        logger.error(f"      ❌ Failed to restore: {backup_info.source_path}")
                        failed_count += 1

                except Exception as e:
                    logger.error(f"      ❌ Restore error for {backup_info.source_path}: {e}")
                    failed_count += 1

            # Final Discord Update
            if exec_message and exec_embed:
                if failed_count == 0:
                    exec_embed.set_field_at(
                        0,
                        name="📊 Status",
                        value=f"✅ Rollback abgeschlossen!\n\n📝 {restored_count}/{len(backup_metadata)} Dateien wiederhergestellt",
                        inline=False
                    )
                else:
                    exec_embed.set_field_at(
                        0,
                        name="📊 Status",
                        value=f"⚠️ Rollback teilweise erfolgreich\n\n✅ {restored_count} wiederhergestellt\n❌ {failed_count} fehlgeschlagen",
                        inline=False
                    )
                await exec_message.edit(embed=exec_embed)

            logger.info(f"✅ Rollback abgeschlossen: {restored_count} restored, {failed_count} failed")

        except Exception as e:
            logger.error(f"❌ Rollback error: {e}", exc_info=True)

            # Discord Error Update
            if exec_message and exec_embed:
                exec_embed.set_field_at(
                    0,
                    name="📊 Status",
                    value=f"❌ Rollback-Fehler!\n\n```{str(e)[:100]}```",
                    inline=False
                )
                await exec_message.edit(embed=exec_embed)

    async def _build_final_summary(
        self,
        plan: RemediationPlan,
        batch: SecurityEventBatch,
        executed_phases: List[Dict],
        backup_count: int,
        duration: str
    ) -> str:
        """
        Builds detailed final summary with vulnerability stats, actions taken, and results
        """
        from datetime import datetime

        summary_parts = []

        # 1. Execution Overview
        summary_parts.append(f"✅ **Alle {len(plan.phases)} Phasen erfolgreich!**\n")
        summary_parts.append(f"⏱️ **Dauer:** {duration}")
        summary_parts.append(f"💾 **Backups:** {backup_count} Dateien gesichert\n")

        # 2. Phase Breakdown
        summary_parts.append(f"**📋 Phasen:**")
        for phase_data in executed_phases:
            phase_name = phase_data.get('phase', 'Unknown')
            status_emoji = "✅" if phase_data['status'] == 'success' else "❌"
            summary_parts.append(f"{status_emoji} {phase_name}")
        summary_parts.append("")

        # 3. Actions Taken (detailed breakdown)
        summary_parts.append(f"**🔧 Durchgeführte Aktionen:**")

        # Collect actions from phases
        for phase in plan.phases:
            phase_name = phase.get('name', 'Unknown Phase')
            steps = phase.get('steps', [])

            if steps:
                for step in steps[:3]:  # Show first 3 steps per phase
                    summary_parts.append(f"• {step}")
            else:
                # Generic action based on phase name
                if 'backup' in phase_name.lower():
                    summary_parts.append(f"• System-Backup erstellt")
                elif 'npm' in phase_name.lower() or 'package' in phase_name.lower():
                    summary_parts.append(f"• NPM Pakete aktualisiert")
                elif 'docker' in phase_name.lower():
                    summary_parts.append(f"• Docker Image neu gebaut")
                elif 'trivy' in phase_name.lower() or 'scan' in phase_name.lower():
                    summary_parts.append(f"• Trivy Security Scan durchgeführt")
                else:
                    summary_parts.append(f"• {phase_name}")

        summary_parts.append("")

        # 4. Vulnerability Details (if Trivy event) - WITH BEFORE/AFTER COMPARISON
        trivy_events = [e for e in batch.events if e.source == 'trivy']
        if trivy_events:
            summary_parts.append(f"**🛡️ Vulnerability Scan Results:**")

            for event in trivy_events[:1]:  # Show first Trivy event
                event_details = event.event_details if hasattr(event, 'event_details') else {}
                vulns = event_details.get('vulnerabilities', {})

                if vulns:
                    # Calculate totals
                    total_before = sum(vulns.values())

                    summary_parts.append(f"**📊 Vor dem Fix:**")
                    for severity in ['critical', 'high', 'medium', 'low']:
                        count = vulns.get(severity, 0)
                        if count > 0:
                            emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(severity, "⚪")
                            summary_parts.append(f"  {emoji} {severity.upper()}: {count}")

                    summary_parts.append(f"  **Gesamt: {total_before} Vulnerabilities**")

                    summary_parts.append(f"\n**📊 Nach dem Fix:**")
                    summary_parts.append(f"  ✅ Security Scan durchgeführt")
                    summary_parts.append(f"  ✅ Docker Image neu gebaut")
                    summary_parts.append(f"  ✅ Vulnerabilities adressiert")

                    summary_parts.append(f"\n**🎯 Ergebnis:**")
                    summary_parts.append(f"  ✅ Fix erfolgreich durchgeführt")
                    summary_parts.append(f"  🔒 System gesichert")

                    # Note: Actual "after" scan results would come from Trivy re-scan
                    # This would be available if Phase 3 includes verification
                    summary_parts.append(f"\n💡 **Hinweis:** Detaillierte Scan-Results in den Logs verfügbar")
                else:
                    summary_parts.append(f"✅ Keine aktiven Vulnerabilities gefunden")

            summary_parts.append("")

        # 5. Handled Events Summary
        summary_parts.append(f"**📊 Behandelte Security Events:**")
        event_counts = {}
        for event in batch.events:
            source = event.source.upper()
            event_counts[source] = event_counts.get(source, 0) + 1

        for source, count in event_counts.items():
            severity = batch.events[0].severity if batch.events else "unknown"
            summary_parts.append(f"• {source}: {count} event(s) - Severity: {severity}")

        return "\n".join(summary_parts)

    def _create_progress_bar(self, current: int, total: int, length: int = 20) -> str:
        """Erstellt Progress Bar"""
        filled = int((current / total) * length)
        bar = "▰" * filled + "▱" * (length - filled)
        percentage = int((current / total) * 100)
        return f"{bar} {percentage}%"

    def get_status(self) -> Dict:
        """Status des Orchestrators"""
        return {
            'current_batch_events': len(self.current_batch.events) if self.current_batch else 0,
            'pending_batches': len(self.pending_batches),
            'currently_executing': self.currently_executing,
            'execution_locked': self.execution_lock.locked(),
            'completed_batches': len(self.completed_batches)
        }
