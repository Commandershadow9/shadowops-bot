"""
ExecutorMixin — Plan-Ausführung, Multi-Projekt-Logik und Phase-Execution
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, TYPE_CHECKING

# Knowledge Base for AI Learning
from ..knowledge_base import get_knowledge_base

if TYPE_CHECKING:
    from .models import SecurityEventBatch, RemediationPlan

logger = logging.getLogger('shadowops')


class ExecutorMixin:
    """Mixin für Plan-Ausführung, Multi-Projekt und Phase-Execution"""

    async def _process_next_batch(self):
        """Verarbeitet nächsten Batch (mit Execution Lock!)"""

        # Warte auf Execution Lock (nur 1 Remediation gleichzeitig!)
        if self.execution_lock.locked():
            logger.info("⏳ Execution Lock aktiv - warte auf Abschluss der laufenden Remediation...")
            return

        async with self.execution_lock:
            if not self.pending_batches:
                return

            # Hole Batch mit höchster Priorität
            batch = max(self.pending_batches, key=lambda b: b.severity_priority)
            self.pending_batches.remove(batch)
            self.currently_executing = batch.batch_id

            logger.info(f"🚀 Starte koordinierte Remediation für Batch {batch.batch_id}")
            logger.info(f"   📊 {len(batch.events)} Events aus {len(batch.sources)} Quellen")

            try:
                # Phase 1: KI erstellt koordinierten Gesamt-Plan
                logger.info("🧠 Phase 1: KI-Analyse aller Events...")
                plan = await self._create_coordinated_plan(batch)

                if not plan:
                    logger.error(f"❌ KI konnte keinen Plan erstellen für Batch {batch.batch_id}")
                    batch.status = "failed"
                    self.completed_batches.append(batch)
                    return

                logger.info(f"✅ Koordinierter Plan erstellt:")
                logger.info(f"   📝 {len(plan.phases)} Phasen")
                logger.info(f"   ⏱️  Geschätzte Dauer: {plan.estimated_duration_minutes} Minuten")
                logger.info(f"   🎯 Confidence: {plan.confidence:.0%}")

                # Phase 2: User Approval (einmal für ALLES)
                logger.info("👤 Phase 2: Warte auf User-Approval...")
                approved = await self._request_approval(batch, plan)

                if approved is None:
                    # Timeout — eskaliere zu GitHub Issue
                    logger.warning(f"⏰ Batch {batch.batch_id} — Timeout, eskaliere zu GitHub Issue")
                    batch.status = "escalated"
                    await self._escalate_to_github(batch, plan)
                    await self._mark_events_escalated(batch)
                    self.completed_batches.append(batch)
                    return

                if approved is False:
                    # Explizit abgelehnt — nicht neu triggern
                    logger.warning(f"❌ User hat Batch {batch.batch_id} abgelehnt")
                    batch.status = "rejected"
                    await self._mark_events_escalated(batch)
                    self.completed_batches.append(batch)
                    return

                # Phase 3: Sequentielle Ausführung
                logger.info("⚙️ Phase 3: Sequentielle Ausführung...")
                batch.status = "executing"
                success = await self._execute_plan(batch, plan)

                if success:
                    logger.info(f"✅ Batch {batch.batch_id} erfolgreich abgeschlossen!")
                    batch.status = "completed"
                else:
                    logger.error(f"❌ Batch {batch.batch_id} fehlgeschlagen")
                    batch.status = "failed"
                    # Cache clearen damit Events beim naechsten Scan neu erkannt werden
                    await self._clear_event_cache_for_batch(batch)

                self.completed_batches.append(batch)

            except Exception as e:
                logger.error(f"❌ Orchestrator Error für Batch {batch.batch_id}: {e}", exc_info=True)
                batch.status = "failed"
                await self._clear_event_cache_for_batch(batch)
                self.completed_batches.append(batch)

            finally:
                self.currently_executing = None

                # Verarbeite nächsten Batch falls vorhanden
                if self.pending_batches:
                    asyncio.create_task(self._process_next_batch())

    async def _escalate_to_github(self, batch, plan):
        """Erstellt ein GitHub Issue fuer einen Batch der nicht approved wurde."""
        import asyncio as _asyncio

        try:
            # Baue Issue-Body aus Plan-Daten
            events_summary = []
            for event in batch.events:
                events_summary.append(f"- **{event.source.upper()}** ({event.severity}): {event.event_type}")

            phases_summary = []
            for i, phase in enumerate(plan.phases, 1):
                steps = phase.get('steps', [])
                steps_text = "\n".join(f"  - {s}" for s in steps[:5])
                phases_summary.append(f"### Phase {i}: {phase.get('name', 'N/A')}\n{phase.get('description', '')}\n{steps_text}")

            body = (
                f"## Automatisch eskaliert\n"
                f"Dieser Fix-Plan wurde vom ShadowOps Bot erstellt aber nicht innerhalb von 30 Minuten approved.\n\n"
                f"**Batch ID:** `{batch.batch_id}`\n"
                f"**Confidence:** {plan.confidence:.0%}\n"
                f"**Geschaetzte Dauer:** {plan.estimated_duration_minutes} Minuten\n\n"
                f"## Events\n" + "\n".join(events_summary) + "\n\n"
                f"## Geplante Phasen\n" + "\n\n".join(phases_summary) + "\n\n"
                f"## Rollback-Plan\n{plan.rollback_plan}\n\n"
                f"---\n"
                f"*Erstellt von ShadowOps Bot — Approval-Timeout Eskalation*"
            )

            title = f"[ShadowOps] {plan.description[:80]}"

            # gh issue create — keine Shell-Injection da alle Werte intern erzeugt
            proc = await _asyncio.create_subprocess_exec(
                'gh', 'issue', 'create',
                '--repo', 'Commandershadow9/shadowops-bot',
                '--title', title,
                '--body', body,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode == 0:
                issue_url = stdout.decode().strip()
                logger.info(f"📋 GitHub Issue erstellt: {issue_url}")

                # Discord-Benachrichtigung
                if self.discord_logger:
                    self.discord_logger.log_orchestrator(
                        f"📋 **Approval-Timeout — GitHub Issue erstellt**\n"
                        f"🆔 Batch: `{batch.batch_id}`\n"
                        f"🔗 {issue_url}\n"
                        f"ℹ️ Events werden nicht erneut getriggert bis Issue geschlossen wird",
                        severity="warning"
                    )
            else:
                error = stderr.decode().strip()
                logger.error(f"❌ GitHub Issue Erstellung fehlgeschlagen: {error[:200]}")

        except Exception as e:
            logger.error(f"❌ Eskalation zu GitHub fehlgeschlagen: {e}", exc_info=True)

    async def _mark_events_escalated(self, batch):
        """Markiert Batch-Events als eskaliert, damit sie nicht erneut getriggert werden."""
        try:
            watcher = getattr(self.bot, 'event_watcher', None) if self.bot else None
            if watcher:
                await watcher.escalate_events(batch.events)
                logger.info(f"🚫 {len(batch.events)} Events als eskaliert markiert — werden nicht erneut getriggert")
            else:
                logger.debug("Event-Watcher nicht verfuegbar — Eskalations-Markierung uebersprungen")
        except Exception as e:
            logger.warning(f"⚠️ Eskalations-Markierung fehlgeschlagen: {e}")

    async def _clear_event_cache_for_batch(self, batch):
        """Entfernt fehlgeschlagene Batch-Events aus dem Event-Cache des Watchers."""
        try:
            watcher = getattr(self.bot, 'event_watcher', None) if self.bot else None
            if watcher:
                cleared = await watcher.clear_failed_events(batch.events)
                if cleared:
                    logger.info(f"🗑️ {cleared} Events aus Cache entfernt — werden beim naechsten Scan neu erkannt")
            else:
                logger.debug("Event-Watcher nicht verfuegbar — Cache nicht bereinigt")
        except Exception as e:
            logger.warning(f"⚠️ Event-Cache Bereinigung fehlgeschlagen: {e}")

    async def _execute_plan(self, batch: SecurityEventBatch, plan: RemediationPlan) -> bool:
        """
        Führt Plan sequentiell Phase für Phase aus

        Workflow:
        1. Erstelle System-Backup
        2. Führe jede Phase nacheinander aus
        3. Teste nach jeder Phase
        4. Bei Fehler: Rollback und Stop
        5. Sende Discord-Updates während Ausführung

        MULTI-PROJECT MODE:
        - Erkennt wenn mehrere Projekte betroffen sind
        - Führt Projekte sequentiell aus (eins nach dem anderen)
        - Für jedes Projekt: Backup → Fix → Verify Scan → Check Success
        - Nur wenn Projekt erfolgreich: Fahre mit nächstem fort
        - Bei Fehler: Rollback und Retry mit AI Learning
        """
        import discord
        from datetime import datetime

        logger.info(f"⚙️ Starte sequentielle Ausführung von {len(plan.phases)} Phasen")

        # Check for multi-project batch
        projects_map = self._group_events_by_project(batch.events)
        multi_project_mode = len(projects_map) > 1

        if multi_project_mode:
            logger.info(f"🐳 MULTI-PROJECT MODE erkannt: {len(projects_map)} Projekte betroffen")
            for project_path, project_events in projects_map.items():
                project_name = project_path.split('/')[-1]
                logger.info(f"   📂 {project_name}: {len(project_events)} Events")

        # Discord Channel Logger: Execution Start
        if self.discord_logger:
            if multi_project_mode:
                project_list = "\n".join([f"   📂 {p.split('/')[-1]}" for p in projects_map.keys()])
                self.discord_logger.log_orchestrator(
                    f"⚙️ **MULTI-PROJECT Execution gestartet**\n"
                    f"🆔 Batch: `{batch.batch_id}`\n"
                    f"🐳 Projekte: **{len(projects_map)}**\n{project_list}\n"
                    f"📋 Phasen: **{len(plan.phases)}**\n"
                    f"⚠️ Sequentielle Verarbeitung: Eins nach dem anderen",
                    severity="info"
                )
            else:
                self.discord_logger.log_orchestrator(
                    f"⚙️ **Execution gestartet**\n"
                    f"🆔 Batch: `{batch.batch_id}`\n"
                    f"📋 Phasen: **{len(plan.phases)}**\n"
                    f"⏱️ Est. Duration: {plan.estimated_duration_minutes}min",
                    severity="info"
                )

        # MULTI-PROJECT MODE: Process projects sequentially
        if multi_project_mode:
            return await self._execute_multi_project_plan(batch, plan, projects_map)

        # SINGLE PROJECT MODE: Original execution flow

        # Track execution start time and plan confidence for phase strategy
        self._execution_start_time = datetime.now()
        self._current_plan_confidence = plan.confidence

        # Get execution channel for live updates
        execution_channel = None
        if self.bot:
            try:
                # Send to remediation-alerts channel for live updates (from config, includes fallbacks)
                if not self.config:
                    raise ValueError("Config nicht gesetzt")
                execution_channel = self.bot.get_channel(self.config.alerts_channel)
            except Exception as e:
                logger.warning(f"⚠️ Konnte Execution-Channel nicht laden: {e}")

        # Create execution status embed
        exec_embed = None
        exec_message = None

        if execution_channel:
            exec_embed = discord.Embed(
                title="⚙️ Koordinierte Remediation läuft",
                description=f"**Batch {batch.batch_id}**\n{plan.description}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            exec_embed.add_field(
                name="📊 Status",
                value="🔄 Starte Ausführung...",
                inline=False
            )
            exec_message = await execution_channel.send(embed=exec_embed)

        # Track execution results
        executed_phases = []
        backup_created = False
        backup_path = None

        try:
            # Phase 0: Create system backup
            logger.info("💾 Phase 0: Erstelle System-Backup...")
            if exec_message:
                exec_embed.set_field_at(
                    0,
                    name="📊 Status",
                    value="💾 Phase 0/0: System-Backup wird erstellt...",
                    inline=False
                )
                await exec_message.edit(embed=exec_embed)

            # Create backup using BackupManager from self_healing
            backup_manager = self.self_healing.backup_manager
            backup_metadata = []

            # Collect files to backup based on events
            files_to_backup = set()
            for event in batch.events:
                if event.source == 'trivy':
                    # Backup Docker-related files
                    files_to_backup.add('/home/cmdshadow/shadowops-bot/package.json')
                    files_to_backup.add('/home/cmdshadow/shadowops-bot/Dockerfile')
                elif event.source in ['fail2ban', 'crowdsec']:
                    # Backup firewall configs
                    files_to_backup.add('/etc/fail2ban/jail.local')
                    files_to_backup.add('/etc/ufw/user.rules')
                elif event.source == 'aide':
                    # Backup will be handled by AIDE fixer
                    pass

            # Create backups
            for file_path in files_to_backup:
                if os.path.exists(file_path):
                    try:
                        backup = await backup_manager.create_backup(
                            file_path,
                            metadata={'batch_id': batch.batch_id}
                        )
                        backup_metadata.append(backup)
                        logger.info(f"   💾 Backed up: {file_path}")
                    except Exception as e:
                        logger.warning(f"   ⚠️ Could not backup {file_path}: {e}")

            backup_created = len(backup_metadata) > 0
            backup_path = f"Batch {batch.batch_id} - {len(backup_metadata)} backups created"
            logger.info(f"✅ Backup Phase abgeschlossen: {len(backup_metadata)} Dateien gesichert")

            # Execute each phase sequentially
            for phase_idx, phase in enumerate(plan.phases, 1):
                phase_name = phase.get('name', f'Phase {phase_idx}')
                phase_desc = phase.get('description', '')
                phase_steps = phase.get('steps', [])

                logger.info(f"🔧 Phase {phase_idx}/{len(plan.phases)}: {phase_name}")
                logger.info(f"   📝 {phase_desc}")
                logger.info(f"   📋 {len(phase_steps)} Schritte")

                # Update Discord
                if exec_message:
                    progress_bar = self._create_progress_bar(phase_idx, len(plan.phases))
                    exec_embed.set_field_at(
                        0,
                        name="📊 Status",
                        value=f"🔧 Phase {phase_idx}/{len(plan.phases)}: {phase_name}\n{progress_bar}\n\n{phase_desc}",
                        inline=False
                    )
                    await exec_message.edit(embed=exec_embed)

                # Execute phase steps (pass Discord message for live updates)
                phase_success = await self._execute_phase(
                    phase,
                    batch.events,
                    exec_message=exec_message,
                    exec_embed=exec_embed
                )

                if phase_success:
                    logger.info(f"✅ Phase {phase_idx} erfolgreich")
                    executed_phases.append({
                        'phase': phase_name,
                        'status': 'success',
                        'index': phase_idx
                    })
                else:
                    logger.error(f"❌ Phase {phase_idx} fehlgeschlagen!")
                    executed_phases.append({
                        'phase': phase_name,
                        'status': 'failed',
                        'index': phase_idx
                    })

                    # Rollback on failure
                    if exec_message:
                        exec_embed.color = discord.Color.red()
                        exec_embed.set_field_at(
                            0,
                            name="📊 Status",
                            value=f"❌ Phase {phase_idx} fehlgeschlagen!\n🔄 Starte Rollback...",
                            inline=False
                        )
                        await exec_message.edit(embed=exec_embed)

                    await self._rollback(backup_metadata, executed_phases, exec_message, exec_embed)
                    return False

            # All phases successful!
            logger.info(f"✅ Alle {len(plan.phases)} Phasen erfolgreich ausgeführt")

            # Calculate execution duration
            if hasattr(self, '_execution_start_time'):
                duration = (datetime.now() - self._execution_start_time).total_seconds()
                duration_str = f"{int(duration // 60)}m {int(duration % 60)}s"
            else:
                duration_str = "Unknown"

            # Build detailed final summary
            final_summary = await self._build_final_summary(
                plan=plan,
                batch=batch,
                executed_phases=executed_phases,
                backup_count=len(backup_metadata),
                duration=duration_str
            )

            # Final Discord update with detailed summary
            if exec_message:
                exec_embed.color = discord.Color.green()
                exec_embed.title = "✅ Koordinierte Remediation abgeschlossen"
                exec_embed.set_field_at(
                    0,
                    name="📊 Execution Summary",
                    value=final_summary,
                    inline=False
                )
                await exec_message.edit(embed=exec_embed)

            return True

        except Exception as e:
            logger.error(f"❌ Kritischer Fehler während Ausführung: {e}", exc_info=True)

            # Rollback on critical error
            if backup_created:
                await self._rollback(backup_metadata, executed_phases, exec_message, exec_embed)

            # Update Discord
            if exec_message:
                exec_embed.color = discord.Color.red()
                exec_embed.title = "❌ Remediation fehlgeschlagen"
                exec_embed.set_field_at(
                    0,
                    name="📊 Status",
                    value=f"❌ Kritischer Fehler!\n```{str(e)[:100]}```\n\n🔄 Rollback durchgeführt",
                    inline=False
                )
                await exec_message.edit(embed=exec_embed)

            return False

    def _group_events_by_project(self, events: List) -> Dict[str, List]:
        """
        Gruppiert Events nach betroffenen Projekten

        Returns:
            Dict[project_path, List[events]]
        """
        projects_map = {}

        for event in events:
            affected_projects = event.details.get('AffectedProjects', [])

            # If no AffectedProjects specified, use default
            if not affected_projects:
                affected_projects = ['/home/cmdshadow/shadowops-bot']

            # Add event to each affected project
            for project_path in affected_projects:
                if project_path not in projects_map:
                    projects_map[project_path] = []
                projects_map[project_path].append(event)

        return projects_map

    async def _execute_multi_project_plan(
        self,
        batch: SecurityEventBatch,
        plan: RemediationPlan,
        projects_map: Dict[str, List]
    ) -> bool:
        """
        Führt Multi-Project Remediation sequentiell aus

        Workflow für jedes Projekt:
        1. Backup erstellen (Dockerfile, docker-compose, etc.)
        2. Fixes ausführen für alle Events des Projekts
        3. Verification Scan durchführen (Trivy re-scan)
        4. Erfolg prüfen (Vulnerabilities reduziert?)
        5. Bei Fehler: Rollback und Retry mit neuem AI Learning
        6. Nur wenn erfolgreich: Fahre mit nächstem Projekt fort

        Returns:
            bool: True wenn ALLE Projekte erfolgreich gefixt wurden
        """
        import discord
        from datetime import datetime

        logger.info(f"🐳 Starte MULTI-PROJECT Sequential Execution: {len(projects_map)} Projekte")

        # Track execution start time
        self._execution_start_time = datetime.now()

        # Get execution channel for live updates
        execution_channel = None
        if self.bot:
            try:
                # Get alerts channel from config, fallback handled in config helper
                if not self.config:
                    raise ValueError("Config nicht gesetzt")
                execution_channel = self.bot.get_channel(self.config.alerts_channel)
            except Exception as e:
                logger.warning(f"⚠️ Konnte Execution-Channel nicht laden: {e}")

        # Create execution status embed
        exec_embed = None
        exec_message = None

        if execution_channel:
            project_list = "\n".join([f"• {p.split('/')[-1]}" for p in projects_map.keys()])
            exec_embed = discord.Embed(
                title="🐳 Multi-Project Remediation",
                description=f"**Batch {batch.batch_id}**\n\nSequentielle Verarbeitung von {len(projects_map)} Projekten:\n{project_list}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            exec_embed.add_field(
                name="📊 Status",
                value="🔄 Starte Multi-Project Execution...",
                inline=False
            )
            exec_message = await execution_channel.send(embed=exec_embed)

        # Track overall results
        all_projects_successful = True
        project_results = []

        # Process each project sequentially
        for project_idx, (project_path, project_events) in enumerate(projects_map.items(), 1):
            project_name = project_path.split('/')[-1]

            logger.info(f"")
            logger.info(f"{'='*60}")
            logger.info(f"🐳 PROJECT {project_idx}/{len(projects_map)}: {project_name}")
            logger.info(f"   Path: {project_path}")
            logger.info(f"   Events: {len(project_events)}")
            logger.info(f"{'='*60}")

            # Discord: Project Start
            if self.discord_logger:
                self.discord_logger.log_orchestrator(
                    f"🐳 **Projekt {project_idx}/{len(projects_map)} gestartet**\n"
                    f"📂 Name: **{project_name}**\n"
                    f"📍 Path: `{project_path}`\n"
                    f"📊 Events: {len(project_events)}",
                    severity="info"
                )

            # Update Discord Embed
            if exec_message:
                progress = f"Project {project_idx}/{len(projects_map)}"
                exec_embed.set_field_at(
                    0,
                    name="📊 Status",
                    value=f"🐳 {progress}: {project_name}\n\n🔄 Backup wird erstellt...",
                    inline=False
                )
                await exec_message.edit(embed=exec_embed)

            # Execute this project's remediation
            project_success = await self._execute_single_project(
                project_path=project_path,
                project_events=project_events,
                batch=batch,
                plan=plan,
                exec_message=exec_message,
                exec_embed=exec_embed,
                project_idx=project_idx,
                total_projects=len(projects_map)
            )

            # Track result
            project_results.append({
                'project': project_name,
                'path': project_path,
                'success': project_success,
                'events_count': len(project_events)
            })

            if project_success:
                logger.info(f"✅ Projekt {project_name} erfolgreich gefixt!")

                # Discord: Project Success
                if self.discord_logger:
                    self.discord_logger.log_orchestrator(
                        f"✅ **Projekt {project_idx}/{len(projects_map)} erfolgreich**\n"
                        f"📂 {project_name}: Alle Fixes angewendet und verifiziert",
                        severity="success"
                    )
            else:
                logger.error(f"❌ Projekt {project_name} fehlgeschlagen!")
                all_projects_successful = False

                # Discord: Project Failed
                if self.discord_logger:
                    self.discord_logger.log_orchestrator(
                        f"❌ **Projekt {project_idx}/{len(projects_map)} fehlgeschlagen**\n"
                        f"📂 {project_name}: Fix konnte nicht angewendet werden\n"
                        f"⚠️ Rollback durchgeführt, fahre mit nächstem Projekt fort",
                        severity="error"
                    )

                # Continue with next project (don't stop the whole batch)
                logger.warning(f"⚠️ Fahre mit nächstem Projekt fort trotz Fehler in {project_name}")

        # Calculate final duration
        if hasattr(self, '_execution_start_time'):
            duration = (datetime.now() - self._execution_start_time).total_seconds()
            duration_str = f"{int(duration // 60)}m {int(duration % 60)}s"
        else:
            duration_str = "Unknown"

        # Build final summary
        successful_projects = [r for r in project_results if r['success']]
        failed_projects = [r for r in project_results if not r['success']]

        summary_parts = []
        summary_parts.append(f"**Multi-Project Remediation abgeschlossen**")
        summary_parts.append(f"")
        summary_parts.append(f"✅ Erfolgreich: **{len(successful_projects)}/{len(project_results)}** Projekte")
        if failed_projects:
            summary_parts.append(f"❌ Fehlgeschlagen: **{len(failed_projects)}** Projekte")
        summary_parts.append(f"⏱️ Dauer: {duration_str}")
        summary_parts.append(f"")

        if successful_projects:
            summary_parts.append(f"**Erfolgreiche Projekte:**")
            for r in successful_projects:
                summary_parts.append(f"   ✅ {r['project']} ({r['events_count']} events)")

        if failed_projects:
            summary_parts.append(f"")
            summary_parts.append(f"**Fehlgeschlagene Projekte:**")
            for r in failed_projects:
                summary_parts.append(f"   ❌ {r['project']} ({r['events_count']} events)")

        final_summary = "\n".join(summary_parts)

        # Final Discord update
        if exec_message:
            if all_projects_successful:
                exec_embed.color = discord.Color.green()
                exec_embed.title = "✅ Multi-Project Remediation erfolgreich"
            else:
                exec_embed.color = discord.Color.orange()
                exec_embed.title = "⚠️ Multi-Project Remediation teilweise erfolgreich"

            exec_embed.set_field_at(
                0,
                name="📊 Final Summary",
                value=final_summary,
                inline=False
            )
            await exec_message.edit(embed=exec_embed)

        # Discord Channel Logger: Final Summary
        if self.discord_logger:
            if all_projects_successful:
                self.discord_logger.log_orchestrator(
                    f"✅ **Multi-Project Remediation ERFOLGREICH**\n"
                    f"📊 {len(successful_projects)}/{len(project_results)} Projekte gefixt\n"
                    f"⏱️ Dauer: {duration_str}",
                    severity="success"
                )
            else:
                self.discord_logger.log_orchestrator(
                    f"⚠️ **Multi-Project Remediation TEILWEISE erfolgreich**\n"
                    f"✅ Erfolgreich: {len(successful_projects)}\n"
                    f"❌ Fehlgeschlagen: {len(failed_projects)}\n"
                    f"⏱️ Dauer: {duration_str}",
                    severity="warning"
                )

        logger.info(f"")
        logger.info(f"{'='*60}")
        logger.info(f"🐳 MULTI-PROJECT EXECUTION ABGESCHLOSSEN")
        logger.info(f"   ✅ Erfolgreich: {len(successful_projects)}/{len(project_results)}")
        logger.info(f"   ⏱️ Dauer: {duration_str}")
        logger.info(f"{'='*60}")

        return all_projects_successful

    async def _execute_single_project(
        self,
        project_path: str,
        project_events: List,
        batch: SecurityEventBatch,
        plan: RemediationPlan,
        exec_message,
        exec_embed,
        project_idx: int,
        total_projects: int
    ) -> bool:
        """
        Führt Remediation für ein einzelnes Projekt aus

        Workflow:
        1. Backup (Dockerfile, docker-compose.yml, etc.)
        2. Execute Fixes (alle Events für dieses Projekt)
        3. Verify Scan (Trivy re-scan)
        4. Check Success (Vulnerabilities reduziert?)
        5. Bei Fehler: Rollback und Retry (max 2 Versuche)

        Returns:
            bool: True wenn Projekt erfolgreich gefixt
        """
        import os

        project_name = project_path.split('/')[-1]
        logger.info(f"")
        logger.info(f"🔧 Starte Remediation für Projekt: {project_name}")

        # Get backup manager
        backup_manager = self.self_healing.backup_manager

        # Phase 1: Create Backups
        logger.info(f"📦 Phase 1/4: Erstelle Backups für {project_name}...")

        if exec_message:
            exec_embed.set_field_at(
                0,
                name="📊 Status",
                value=f"🐳 Project {project_idx}/{total_projects}: {project_name}\n\n📦 Phase 1/4: Backup wird erstellt...",
                inline=False
            )
            await exec_message.edit(embed=exec_embed)

        backup_metadata = []
        files_to_backup = []

        # Determine files to backup based on project type
        dockerfile = os.path.join(project_path, 'Dockerfile')
        docker_compose = os.path.join(project_path, 'docker-compose.yml')
        package_json = os.path.join(project_path, 'package.json')

        if os.path.exists(dockerfile):
            files_to_backup.append(dockerfile)
        if os.path.exists(docker_compose):
            files_to_backup.append(docker_compose)
        if os.path.exists(package_json):
            files_to_backup.append(package_json)

        # Create backups
        for file_path in files_to_backup:
            try:
                backup = await backup_manager.create_backup(
                    file_path,
                    metadata={
                        'batch_id': batch.batch_id,
                        'project': project_path,
                        'project_name': project_name
                    }
                )
                backup_metadata.append(backup)
                logger.info(f"   💾 Backed up: {os.path.basename(file_path)}")
            except Exception as e:
                logger.warning(f"   ⚠️ Could not backup {file_path}: {e}")

        if len(backup_metadata) == 0:
            logger.warning(f"⚠️ Keine Backup-Dateien gefunden für {project_name}")
            logger.warning(f"⚠️ Fahre trotzdem fort, aber RISIKO erhöht!")

        logger.info(f"✅ Backup Phase abgeschlossen: {len(backup_metadata)} Dateien gesichert")

        # Phase 2: Execute Fixes
        logger.info(f"🔧 Phase 2/4: Führe Fixes aus für {project_name}...")

        if exec_message:
            exec_embed.set_field_at(
                0,
                name="📊 Status",
                value=f"🐳 Project {project_idx}/{total_projects}: {project_name}\n\n🔧 Phase 2/4: Fixes werden ausgeführt...",
                inline=False
            )
            await exec_message.edit(embed=exec_embed)

        # OPTIMIZATION: Group events by source to generate strategies efficiently
        events_by_source = {}
        for event in project_events:
            if event.source not in events_by_source:
                events_by_source[event.source] = []
            events_by_source[event.source].append(event)

        logger.info(f"   📊 Events grouped by source: {', '.join([f'{src}({len(evs)})' for src, evs in events_by_source.items()])}")

        # Execute fixes grouped by source
        fixes_successful = True
        fix_results = []

        for source, source_events in events_by_source.items():
            # Safety check: Skip empty event lists
            if not source_events:
                logger.warning(f"   ⚠️ Skipping empty event list for source: {source}")
                continue

            logger.info(f"   🔧 Processing {len(source_events)} {source} event(s)...")

            try:
                # Generate ONE strategy for all events of this source
                # Use first event as representative (they're all same source/project)
                first_event = source_events[0]
                context = {
                    'event': first_event.to_dict(),
                    'previous_attempts': [],
                    'project_path': project_path,
                    'batch_mode': len(source_events) > 1,  # Indicate batch processing
                    'event_count': len(source_events)
                }

                logger.info(f"      🧠 Generating AI strategy for {len(source_events)} {source} event(s)...")
                strategy = await self.ai_service.generate_fix_strategy(context)

                if not strategy:
                    logger.error(f"   ❌ Konnte keine Strategy generieren für {source} events")
                    fixes_successful = False
                    continue

                logger.info(f"      ✅ Strategy generated: {strategy.get('description', 'N/A')[:80]}")

                # Apply the SAME strategy to all events of this source
                for event in source_events:
                    try:
                        event_dict = event.to_dict()
                        fix_result = await self._execute_fix_for_source(event.source, event_dict, strategy)

                        fix_results.append(fix_result)

                        # Fix-Ergebnis in Knowledge DB speichern
                        try:
                            from integrations.ai_learning.knowledge_db import get_knowledge_db
                            get_knowledge_db().add_fix_result(
                                project=project_name,
                                fix_type="orchestrator",
                                description=strategy.get('description', 'N/A')[:500],
                                commands=[s.get('command', '') for s in strategy.get('steps', [])],
                                success=fix_result.get('status') == 'success',
                                confidence=strategy.get('confidence'),
                                ai_model=strategy.get('ai_model', 'unknown')
                            )
                        except Exception:
                            pass

                        if fix_result.get('status') != 'success':
                            logger.error(f"      ❌ Fix fehlgeschlagen für Event {event.event_id[:8]}: {fix_result.get('error', 'Unknown')[:50]}")
                            fixes_successful = False
                        else:
                            logger.info(f"      ✅ Fix erfolgreich für Event {event.event_id[:8]}")

                    except Exception as e:
                        logger.error(f"      ❌ Exception während Fix: {e}", exc_info=True)
                        fixes_successful = False

            except Exception as e:
                logger.error(f"   ❌ Exception während {source} processing: {e}", exc_info=True)
                fixes_successful = False

        if not fixes_successful:
            logger.error(f"❌ Fixes fehlgeschlagen für {project_name}")

            # Rollback
            logger.info(f"🔄 Führe Rollback durch für {project_name}...")
            await self._rollback_project(backup_metadata, project_name)

            return False

        logger.info(f"✅ Fix Phase abgeschlossen für {project_name}")

        # Phase 3: Verification Scan
        logger.info(f"🔍 Phase 3/4: Führe Verification Scan durch...")

        if exec_message:
            exec_embed.set_field_at(
                0,
                name="📊 Status",
                value=f"🐳 Project {project_idx}/{total_projects}: {project_name}\n\n🔍 Phase 3/4: Verification Scan läuft...",
                inline=False
            )
            await exec_message.edit(embed=exec_embed)

        # Extract before_counts from events
        before_counts = {}
        for event in project_events:
            if event.source == 'trivy':
                # Extract vulnerability counts from event details
                details = event.details
                before_counts['critical'] = details.get('total_critical', 0)
                before_counts['high'] = details.get('total_high', 0)
                before_counts['medium'] = details.get('total_medium', 0)
                before_counts['low'] = details.get('total_low', 0)
                break  # Use first trivy event

        verification_success = await self._verify_project_fixes(project_path, project_name, project_events, before_counts)

        if not verification_success:
            logger.error(f"❌ Verification fehlgeschlagen für {project_name}")

            # Rollback
            logger.info(f"🔄 Führe Rollback durch für {project_name}...")
            await self._rollback_project(backup_metadata, project_name)

            return False

        logger.info(f"✅ Verification erfolgreich für {project_name}")

        # Phase 4: Success!
        logger.info(f"🎉 Phase 4/4: Projekt {project_name} erfolgreich gefixt!")

        if exec_message:
            exec_embed.set_field_at(
                0,
                name="📊 Status",
                value=f"🐳 Project {project_idx}/{total_projects}: {project_name}\n\n✅ Phase 4/4: Erfolgreich abgeschlossen!",
                inline=False
            )
            await exec_message.edit(embed=exec_embed)

        return True

    async def _execute_phase(
        self,
        phase: Dict,
        events: List,
        exec_message=None,
        exec_embed=None
    ) -> bool:
        """
        Führt eine einzelne Phase aus

        Delegiert an Self-Healing für tatsächliche Fix-Ausführung
        Sendet Live-Updates an Discord während der Ausführung
        """
        phase_steps = phase.get('steps', [])
        phase_name = phase.get('name', 'Unnamed Phase')

        logger.info(f"   ⚙️ Führe Phase '{phase_name}' mit {len(phase_steps)} Schritten aus...")

        try:
            # Execute fixes for each event in this phase
            all_success = True

            for idx, event in enumerate(events, 1):
                try:
                    # Build learning context once per event
                    event_signature = f"{event.source}_{event.event_type}"
                    previous_attempts = self.event_history.get(event_signature, [])[-3:]
                    if previous_attempts:
                        logger.info(f"      📚 Found {len(previous_attempts)} previous attempt(s) for {event_signature}")

                    # Build strategy from coordinated plan's phase data
                    # Der Plan hat bereits alle Infos — kein zweiter AI-Call noetig!
                    strategy = phase.get('strategy', {})

                    if not strategy:
                        # Construct strategy from phase data (name, description, steps)
                        strategy = {
                            'description': phase.get('description', phase.get('name', 'Fix')),
                            'confidence': getattr(self, '_current_plan_confidence', 0.8),
                            'steps': phase.get('steps', []),
                            'phase_name': phase.get('name', 'Unnamed'),
                            'estimated_minutes': phase.get('estimated_minutes', 5),
                        }
                        logger.info(f"      Using plan phase data as strategy: {strategy['description'][:80]}")

                        # Nur wenn Phase-Daten zu duenn sind (z.B. leere Steps), KI fragen
                        if not strategy['steps'] and not strategy['description']:
                            logger.info(f"      Phase hat keine Details — generiere Strategy via KI fuer {event.source}...")
                            strategy = await self.ai_service.generate_fix_strategy({
                                'event': event.to_dict(),
                                'previous_attempts': previous_attempts
                            })

                    # Show planned steps for this fix (for transparency)
                    steps_preview = ""
                    if phase_steps and len(phase_steps) > 0:
                        steps_preview = "\n**Geplante Schritte:**\n" + "\n".join([f"  {i+1}. {step[:60]}" for i, step in enumerate(phase_steps[:4])])

                    # Discord: Show what will be done
                    if exec_message and exec_embed and steps_preview:
                        current_field = exec_embed.fields[0]
                        exec_embed.set_field_at(
                            0,
                            name="📊 Status",
                            value=f"{current_field.value}\n\n📋 Fix {idx}/{len(events)}: {event.source.upper()}{steps_preview}",
                            inline=False
                        )
                        await exec_message.edit(embed=exec_embed)

                    # RETRY LOGIC: Try fix up to 3 times
                    max_retries = 3
                    fix_success = False
                    last_error = None
                    fix_start_time = time.time()

                    for attempt in range(1, max_retries + 1):
                        # Discord Live Update: Starting fix (with retry info)
                        if exec_message and exec_embed:
                            current_field = exec_embed.fields[0]
                            retry_info = f" (Attempt {attempt}/{max_retries})" if attempt > 1 else ""
                            exec_embed.set_field_at(
                                0,
                                name="📊 Status",
                                value=f"{current_field.value}\n\n🔧 Fix {idx}/{len(events)}: {event.source.upper()}{retry_info}\n⏳ Executing...",
                                inline=False
                            )
                            await exec_message.edit(embed=exec_embed)

                        # Execute fix via self-healing
                        logger.info(f"      Executing fix for {event.source} event {event.event_id} (Attempt {attempt}/{max_retries})...")

                        result = await self.self_healing._apply_fix(event, strategy)

                        if result['status'] == 'success':
                            logger.info(f"      ✅ Fix successful on attempt {attempt}/{max_retries}: {result.get('message', '')}")
                            fix_success = True

                            # NEW: Record successful fix in history for learning
                            if event_signature not in self.event_history:
                                self.event_history[event_signature] = []

                            self.event_history[event_signature].append({
                                'timestamp': datetime.now().isoformat(),
                                'strategy': strategy,
                                'result': 'success',
                                'message': result.get('message'),
                                'details': result.get('details'),
                                'attempt': attempt,
                                'phase': phase_name
                            })

                            # Keep only last 10 attempts per event type
                            self.event_history[event_signature] = self.event_history[event_signature][-10:]
                            self._save_event_history()

                            # NEW: Record in Knowledge Base for AI learning
                            try:
                                kb = get_knowledge_base()
                                duration = time.time() - fix_start_time
                                kb.record_fix(
                                    event=event.to_dict(),
                                    strategy=strategy,
                                    result='success',
                                    duration_seconds=duration,
                                    retry_count=attempt - 1
                                )
                            except Exception as kb_error:
                                logger.debug(f"KB tracking failed: {kb_error}")

                            # Discord Live Update: Fix successful
                            if exec_message and exec_embed:
                                current_field = exec_embed.fields[0]
                                base_value = current_field.value.split('\n\n🔧')[0]  # Remove previous fix status
                                success_msg = f" after {attempt} attempt(s)" if attempt > 1 else ""
                                exec_embed.set_field_at(
                                    0,
                                    name="📊 Status",
                                    value=f"{base_value}\n\n✅ Fix {idx}/{len(events)}: {event.source.upper()} successful{success_msg}\n📝 {result.get('message', '')[:100]}",
                                    inline=False
                                )
                                await exec_message.edit(embed=exec_embed)
                            break  # Success! No more retries needed
                        else:
                            last_error = result.get('error', 'Unknown error')
                            logger.warning(f"      ⚠️ Fix attempt {attempt}/{max_retries} failed: {last_error}")

                            # NEW: Record failed attempt in history for learning
                            if event_signature not in self.event_history:
                                self.event_history[event_signature] = []

                            self.event_history[event_signature].append({
                                'timestamp': datetime.now().isoformat(),
                                'strategy': strategy,
                                'result': 'failed',
                                'error': last_error,
                                'attempt': attempt,
                                'phase': phase_name
                            })

                            self.event_history[event_signature] = self.event_history[event_signature][-10:]
                            self._save_event_history()

                            # NEW: Record failure in Knowledge Base (only on last attempt)
                            if attempt == max_retries:
                                try:
                                    kb = get_knowledge_base()
                                    duration = time.time() - fix_start_time
                                    kb.record_fix(
                                        event=event.to_dict(),
                                        strategy=strategy,
                                        result='failure',
                                        error_message=last_error,
                                        duration_seconds=duration,
                                        retry_count=attempt - 1
                                    )
                                except Exception as kb_error:
                                    logger.debug(f"KB tracking failed: {kb_error}")

                            if attempt < max_retries:
                                # Not the last attempt - retry!
                                logger.info(f"      🔄 Retrying... ({attempt}/{max_retries})")

                                # Discord Live Update: Retry info
                                if exec_message and exec_embed:
                                    current_field = exec_embed.fields[0]
                                    base_value = current_field.value.split('\n\n🔧')[0]
                                    exec_embed.set_field_at(
                                        0,
                                        name="📊 Status",
                                        value=f"{base_value}\n\n⚠️ Attempt {attempt} failed - Retrying...\n🔄 {last_error[:100]}",
                                        inline=False
                                    )
                                    await exec_message.edit(embed=exec_embed)

                                # Adaptive delay before retry based on success rate
                                delay = self._calculate_adaptive_retry_delay(
                                    event_signature=event_signature,
                                    attempt=attempt,
                                    last_error=last_error
                                )
                                logger.debug(f"      ⏱️ Adaptive delay: {delay}s")
                                await asyncio.sleep(delay)

                    # Check if fix ultimately succeeded after all retries
                    if not fix_success:
                        logger.error(f"      ❌ Fix failed after {max_retries} attempts: {last_error}")
                        all_success = False

                        # Discord Live Update: All retries failed
                        if exec_message and exec_embed:
                            current_field = exec_embed.fields[0]
                            base_value = current_field.value.split('\n\n🔧')[0]
                            exec_embed.set_field_at(
                                0,
                                name="📊 Status",
                                value=f"{base_value}\n\n❌ Fix {idx}/{len(events)}: {event.source.upper()} failed\n⚠️ All {max_retries} attempts failed\n💔 {last_error[:80]}",
                                inline=False
                            )
                            await exec_message.edit(embed=exec_embed)

                        # If one fix fails after all retries, stop phase execution
                        return False

                except Exception as e:
                    logger.error(f"      ❌ Error executing fix for {event.event_id}: {e}", exc_info=True)

                    # Discord Update: Exception occurred
                    if exec_message and exec_embed:
                        current_field = exec_embed.fields[0]
                        base_value = current_field.value.split('\n\n🔧')[0]
                        exec_embed.set_field_at(
                            0,
                            name="📊 Status",
                            value=f"{base_value}\n\n💥 Exception: {event.source.upper()}\n⚠️ {str(e)[:150]}",
                            inline=False
                        )
                        await exec_message.edit(embed=exec_embed)

                    all_success = False
                    return False

            logger.info(f"   ✅ Phase '{phase_name}' completed successfully")
            return all_success

        except Exception as e:
            logger.error(f"   ❌ Phase execution error: {e}", exc_info=True)

            # Discord Update: Phase-level exception
            if exec_message and exec_embed:
                exec_embed.set_field_at(
                    0,
                    name="📊 Status",
                    value=f"💥 Phase Exception: {phase_name}\n\n⚠️ {str(e)[:200]}",
                    inline=False
                )
                await exec_message.edit(embed=exec_embed)

            return False
