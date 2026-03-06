"""
Pre-Push Verification Pipeline

Prüft Fix-Strategien vor dem Push anhand mehrerer Checks:
1. Confidence-Schwellwert
2. Projekt-Tests (falls konfiguriert)
3. Claude-Verifikation bei kritischen/hohen Severities
4. Knowledge-Base-Erfolgsrate historischer Fixes

Gibt ein Dict zurück mit approved (bool), reason (str) und checks (list).
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger('shadowops.verification')


class VerificationPipeline:
    """
    Pre-Push Verification Pipeline für Fix-Strategien.

    Führt eine Reihe von Checks durch bevor ein Fix gepusht wird:
    - Confidence muss über dem Schwellwert liegen
    - Projekt-Tests müssen bestehen (falls konfiguriert)
    - Claude verifiziert kritische/hohe Fixes (falls aktiviert)
    - Knowledge Base prüft historische Erfolgsrate
    """

    def __init__(
        self,
        ai_engine,
        knowledge_base=None,
        config=None
    ):
        """
        Initialisiert die Verification Pipeline.

        Args:
            ai_engine: AIEngine-Instanz mit verify_fix() Methode
            knowledge_base: Optional, hat get_similar_fixes(source, event_type) Methode
            config: Bot-Config Objekt, config.ai.get('verification', {}) für Einstellungen
        """
        self.ai_engine = ai_engine
        self.knowledge_base = knowledge_base

        # Verification-Config aus der Bot-Konfiguration laden
        verification_config = {}
        if config is not None:
            verification_config = config.ai.get('verification', {})

        self.test_before_push: bool = verification_config.get('test_before_push', True)
        self.verify_critical: bool = verification_config.get('verify_critical_with_claude', True)
        self.min_confidence: float = verification_config.get('min_confidence', 0.85)

        logger.info(
            f"Verification Pipeline initialisiert — "
            f"min_confidence={self.min_confidence}, "
            f"test_before_push={self.test_before_push}, "
            f"verify_critical={self.verify_critical}"
        )

    async def verify(
        self,
        fix_strategy: Dict[str, Any],
        event: Dict[str, Any],
        project_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Verifiziert eine Fix-Strategie anhand mehrerer Checks.

        Args:
            fix_strategy: Dict mit mindestens 'confidence' und 'description'
            event: Event-Dict mit 'source', 'event_type', 'severity' etc.
            project_config: Optionales Projekt-Config-Dict mit deploy.test_command und path

        Returns:
            Dict mit:
                - approved (bool): Ob der Fix gepusht werden darf
                - reason (str): Begründung
                - checks (list): Liste aller durchgeführten Checks
        """
        checks: List[Dict[str, Any]] = []
        severity = event.get('severity', 'UNKNOWN').upper()

        # --- Check 1: Confidence-Schwellwert ---
        confidence = fix_strategy.get('confidence', 0.0)
        confidence_passed = confidence >= self.min_confidence

        check_confidence = {
            'name': 'Confidence-Check',
            'passed': confidence_passed,
            'detail': (
                f"Confidence {confidence:.2f} >= {self.min_confidence:.2f}"
                if confidence_passed
                else f"Confidence {confidence:.2f} < {self.min_confidence:.2f} — zu niedrig"
            )
        }
        checks.append(check_confidence)

        if not confidence_passed:
            reason = (
                f"Confidence {confidence:.2f} liegt unter dem Schwellwert "
                f"von {self.min_confidence:.2f}"
            )
            logger.warning(f"Verification fehlgeschlagen: {reason}")
            return {'approved': False, 'reason': reason, 'checks': checks}

        # --- Check 2: Projekt-Tests ---
        if self.test_before_push and project_config is not None:
            deploy_config = project_config.get('deploy', {})
            test_command = deploy_config.get('test_command')
            project_path = project_config.get('path')

            if test_command and project_path:
                logger.info(f"Führe Tests aus: {test_command} in {project_path}")
                test_result = await self._run_tests(test_command, project_path)

                check_tests = {
                    'name': 'Test-Check',
                    'passed': test_result['passed'],
                    'detail': test_result['detail']
                }
                checks.append(check_tests)

                if not test_result['passed']:
                    reason = f"Tests fehlgeschlagen: {test_result['detail']}"
                    logger.warning(f"Verification fehlgeschlagen: {reason}")
                    return {'approved': False, 'reason': reason, 'checks': checks}

        # --- Check 3: Claude-Verifikation bei CRITICAL/HIGH ---
        if self.verify_critical and severity in ('CRITICAL', 'HIGH'):
            logger.info(f"Claude-Verifikation für {severity}-Event angefordert")

            try:
                claude_result = await self.ai_engine.verify_fix(
                    fix_description=fix_strategy.get('description', ''),
                    fix_commands=[
                        step.get('command', '')
                        for step in fix_strategy.get('steps', [])
                        if step.get('command')
                    ],
                    event=event
                )

                if claude_result is not None:
                    claude_approved = claude_result.get('approved', False)
                    concerns = claude_result.get('concerns', [])
                    recommendation = claude_result.get('recommendation', '')

                    detail_parts = []
                    if recommendation:
                        detail_parts.append(f"Empfehlung: {recommendation}")
                    if concerns:
                        detail_parts.append(f"Bedenken: {', '.join(concerns)}")

                    check_claude = {
                        'name': 'Claude-Verifikation',
                        'passed': claude_approved,
                        'detail': ' | '.join(detail_parts) if detail_parts else (
                            'Genehmigt' if claude_approved else 'Abgelehnt'
                        )
                    }
                    checks.append(check_claude)

                    if not claude_approved:
                        reason = (
                            f"Claude-Verifikation abgelehnt: "
                            f"{recommendation or 'Keine Empfehlung'}"
                        )
                        logger.warning(f"Verification fehlgeschlagen: {reason}")
                        return {'approved': False, 'reason': reason, 'checks': checks}
                else:
                    # AI nicht erreichbar — Check überspringen
                    check_claude = {
                        'name': 'Claude-Verifikation',
                        'passed': True,
                        'detail': 'Übersprungen — AI nicht erreichbar'
                    }
                    checks.append(check_claude)
                    logger.info("Claude-Verifikation übersprungen — AI nicht erreichbar")

            except Exception as exc:
                # Bei Fehler: Check als übersprungen markieren, nicht blockieren
                check_claude = {
                    'name': 'Claude-Verifikation',
                    'passed': True,
                    'detail': f'Übersprungen wegen Fehler: {exc}'
                }
                checks.append(check_claude)
                logger.warning(f"Claude-Verifikation fehlgeschlagen, übersprungen: {exc}")

        # --- Check 4: Knowledge Base ---
        if self.knowledge_base is not None:
            kb_result = await self._check_knowledge_base(fix_strategy, event)

            check_kb = {
                'name': 'Knowledge-Base-Check',
                'passed': kb_result['passed'],
                'detail': kb_result['detail']
            }
            checks.append(check_kb)

            if not kb_result['passed']:
                reason = f"Knowledge Base warnt: {kb_result['detail']}"
                logger.warning(f"Verification fehlgeschlagen: {reason}")
                return {'approved': False, 'reason': reason, 'checks': checks}

        # --- Alle Checks bestanden ---
        logger.info(
            f"Verification bestanden — {len(checks)} Check(s) durchgeführt, "
            f"Severity={severity}, Confidence={confidence:.2f}"
        )
        return {
            'approved': True,
            'reason': 'Alle Checks bestanden',
            'checks': checks
        }

    async def _run_tests(self, test_command: str, project_path: str) -> Dict[str, Any]:
        """
        Führt Projekt-Tests als Subprocess aus.

        Args:
            test_command: Shell-Befehl für die Tests
            project_path: Arbeitsverzeichnis für den Test-Lauf

        Returns:
            Dict mit passed (bool) und detail (str)
        """
        try:
            process = await asyncio.create_subprocess_shell(
                test_command,
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=300
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                logger.error(f"Test-Timeout nach 300s: {test_command}")
                return {
                    'passed': False,
                    'detail': f'Test-Timeout nach 300 Sekunden: {test_command}'
                }

            returncode = process.returncode
            stdout_text = stdout.decode('utf-8', errors='replace').strip()
            stderr_text = stderr.decode('utf-8', errors='replace').strip()

            if returncode == 0:
                # Letzte Zeilen als Zusammenfassung
                output_lines = stdout_text.split('\n')
                summary = output_lines[-1] if output_lines else 'Tests bestanden'
                logger.info(f"Tests bestanden: {summary}")
                return {
                    'passed': True,
                    'detail': f'Tests bestanden (exit 0): {summary}'
                }
            else:
                # Fehler-Output zusammenfassen
                error_output = stderr_text or stdout_text
                error_lines = error_output.split('\n')
                # Maximal die letzten 5 Zeilen als Detail
                error_summary = '\n'.join(error_lines[-5:]) if len(error_lines) > 5 else error_output
                logger.warning(f"Tests fehlgeschlagen (exit {returncode}): {error_summary}")
                return {
                    'passed': False,
                    'detail': f'Tests fehlgeschlagen (exit {returncode}): {error_summary}'
                }

        except Exception as exc:
            logger.error(f"Fehler beim Ausführen der Tests: {exc}")
            return {
                'passed': False,
                'detail': f'Test-Ausführungsfehler: {exc}'
            }

    async def _check_knowledge_base(
        self,
        fix_strategy: Dict[str, Any],
        event: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Prüft die historische Erfolgsrate ähnlicher Fixes in der Knowledge Base.

        Args:
            fix_strategy: Aktuelle Fix-Strategie
            event: Auslösendes Event

        Returns:
            Dict mit passed (bool) und detail (str)
        """
        try:
            source = event.get('source', 'unknown')
            event_type = event.get('event_type', 'unknown')

            similar_fixes = self.knowledge_base.get_similar_fixes(source, event_type)

            if not similar_fixes:
                return {
                    'passed': True,
                    'detail': 'Keine ähnlichen Fixes in der Knowledge Base gefunden'
                }

            total = len(similar_fixes)
            successful = sum(
                1 for fix in similar_fixes
                if fix.get('result') == 'success'
            )
            success_rate = successful / total if total > 0 else 0.0

            detail = (
                f"Erfolgsrate: {successful}/{total} ({success_rate:.0%}) "
                f"für {source}/{event_type}"
            )

            # Bei weniger als 3 Einträgen ist die Datenbasis zu dünn für eine Ablehnung
            if total >= 3 and success_rate < 0.5:
                logger.warning(
                    f"Knowledge Base warnt: Erfolgsrate nur {success_rate:.0%} "
                    f"bei {total} ähnlichen Fixes für {source}/{event_type}"
                )
                return {
                    'passed': False,
                    'detail': f'{detail} — Erfolgsrate unter 50% bei ausreichender Datenbasis'
                }

            return {
                'passed': True,
                'detail': detail
            }

        except Exception as exc:
            # Bei Fehler: graceful durchlassen, nicht blockieren
            logger.warning(f"Knowledge-Base-Check fehlgeschlagen, übersprungen: {exc}")
            return {
                'passed': True,
                'detail': f'Knowledge-Base-Check übersprungen wegen Fehler: {exc}'
            }
