"""
PlannerMixin — KI-gestützte Planung und Prompt-Erstellung
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Dict, Optional, TYPE_CHECKING

from .models import RemediationPlan

# Knowledge Base for AI Learning
from ..knowledge_base import get_knowledge_base

if TYPE_CHECKING:
    from .models import SecurityEventBatch

logger = logging.getLogger('shadowops')


class PlannerMixin:
    """Mixin für KI-koordinierte Planung und Prompt-Erstellung"""

    async def _create_coordinated_plan(self, batch: SecurityEventBatch) -> Optional[RemediationPlan]:
        """
        KI erstellt koordinierten Gesamt-Plan für ALLE Events zusammen

        Wichtig: Die KI analysiert alle Events zusammen und erkennt:
        - Abhängigkeiten zwischen Fixes
        - Optimale Reihenfolge
        - Gemeinsame Schritte (z.B. ein Backup für alle)
        """

        # Sende initiale Discord-Message: KI-Analyse startet
        status_text = "🧠 **KI-Analyse startet**\n\nLlama3.1 analysiert alle Events und erstellt koordinierten Plan...\n\n⏳ Dies kann 2-3 Minuten dauern"
        await self._send_batch_status(batch, status_text, 0xF39C12)  # Orange

        # Build comprehensive context with ALL events
        context = {
            'batch_id': batch.batch_id,
            'events': [e.to_dict() for e in batch.events],
            'event_count': len(batch.events),
            'sources': list(batch.sources),
            'highest_severity': max([e.severity for e in batch.events], key=lambda s: {'CRITICAL': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(s, 0)),
            'is_coordinated_planning': True  # Flag for JSON parser
        }

        # Create streaming state for live Discord updates
        streaming_state = {
            'token_count': 0,
            'last_snippet': '',
            'batch': batch,
            'start_time': time.time()
        }
        context['streaming_state'] = streaming_state

        # Special prompt for coordinated planning
        prompt = self._build_coordinated_planning_prompt(context)

        # Use AI to create coordinated plan
        logger.info("🧠 Rufe KI für koordinierte Planung auf...")

        # Start background task for live Discord updates während Streaming
        update_task = asyncio.create_task(self._stream_ai_progress_to_discord(streaming_state))

        try:
            # Use generate_coordinated_plan with coordinated planning context
            result = await self.ai_service.generate_coordinated_plan(prompt, context)

            # Stop streaming updates
            streaming_state['done'] = True
            await update_task  # Wait for final update

            if not result:
                logger.error("❌ KI konnte keinen koordinierten Plan erstellen")
                status_text = "❌ **KI-Analyse fehlgeschlagen**\n\nKonnte keinen koordinierten Plan erstellen"
                await self._send_batch_status(batch, status_text, 0xE74C3C)  # Red
                return None

            # Parse AI response into RemediationPlan
            plan = RemediationPlan(
                batch_id=batch.batch_id,
                description=result.get('description', 'Koordinierte Remediation'),
                phases=result.get('phases', []),
                confidence=result.get('confidence', 0.0),
                estimated_duration_minutes=result.get('estimated_duration_minutes', 30),
                requires_restart=result.get('requires_restart', False),
                rollback_plan=result.get('rollback_plan', 'Automatisches Rollback via Backups'),
                ai_model=result.get('ai_model', 'unknown')
            )

            # Validierung: Leerer Plan mit 0% Confidence ist ein Fehler
            if plan.confidence == 0 and len(plan.phases) == 0:
                logger.error(
                    f"KI konnte keinen verwertbaren Plan erstellen "
                    f"(Confidence: 0%, keine Phasen). "
                    f"Prompt-Länge: {len(prompt)} Zeichen. "
                    f"AI-Rohantwort: {json.dumps(result, default=str, ensure_ascii=False)[:500]}"
                )
                status_text = (
                    "KI konnte keinen verwertbaren Plan erstellen "
                    "(Confidence: 0%, keine Phasen). "
                    "Der Prompt war möglicherweise zu lang."
                )
                await self._send_batch_status(batch, status_text, 0xE74C3C)  # Rot
                return None

            # Sende finale Discord-Message: Plan erstellt
            phase_names = "\n".join([f"• **Phase {i+1}**: {p['name']}" for i, p in enumerate(plan.phases)])
            status_text = f"**Plan erstellt**\n\n{phase_names}\n\n Geschätzte Dauer: **{plan.estimated_duration_minutes}min**\n Confidence: **{plan.confidence:.0%}**"
            await self._send_batch_status(batch, status_text, 0x2ECC71)  # Green

            logger.info(f"Koordinierter Plan erstellt: {len(plan.phases)} Phasen, {plan.confidence:.0%} Confidence")
            return plan

        except Exception as e:
            # Stop streaming updates on error
            streaming_state['done'] = True
            try:
                await update_task
            except:
                pass

            logger.error(f"❌ Fehler bei koordinierter Planung: {e}", exc_info=True)
            status_text = f"❌ **KI-Analyse fehlgeschlagen**\n\nFehler: {str(e)}"
            await self._send_batch_status(batch, status_text, 0xE74C3C)  # Red
            return None

    async def _stream_ai_progress_to_discord(self, streaming_state: Dict):
        """
        Monitored streaming_state und sendet Live-Updates während KI-Analyse
        """
        batch = streaming_state['batch']
        update_interval = 5  # Update Discord alle 5 Sekunden
        expected_tokens = 400  # Llama3.1 generiert ~400 tokens für einen Plan

        last_token_count = 0

        while not streaming_state.get('done', False):
            await asyncio.sleep(update_interval)

            token_count = streaming_state.get('token_count', 0)
            last_snippet = streaming_state.get('last_snippet', '')
            elapsed = int(time.time() - streaming_state['start_time'])

            # Nur updaten wenn neue Tokens generiert wurden
            if token_count > last_token_count:
                last_token_count = token_count

                # Progress bar basierend auf Token-Count
                progress = min(100, int((token_count / expected_tokens) * 100))
                bar_length = 20
                filled = int((progress / 100) * bar_length)
                bar = "█" * filled + "░" * (bar_length - filled)

                # Format snippet für Discord (max 100 chars)
                snippet_preview = last_snippet[:100] + "..." if len(last_snippet) > 100 else last_snippet

                # Geschätzte Restzeit (basierend auf bisheriger Speed)
                if token_count > 0 and elapsed > 0:
                    tokens_per_sec = token_count / elapsed
                    remaining_tokens = max(0, expected_tokens - token_count)
                    eta_seconds = int(remaining_tokens / tokens_per_sec) if tokens_per_sec > 0 else 0
                    eta_text = f"⏱️ ETA: ~{eta_seconds}s"
                else:
                    eta_text = "⏱️ ETA: Berechne..."

                # Phase detection aus snippet
                phase_info = ""
                if "Phase 1" in last_snippet or "Backup" in last_snippet:
                    phase_info = "🔍 Analysiere: **Phase 1 (Backup)**"
                elif "Phase 2" in last_snippet or "Docker" in last_snippet or "Update" in last_snippet:
                    phase_info = "🔍 Analysiere: **Phase 2 (Updates)**"
                elif "Phase 3" in last_snippet or "trivy" in last_snippet.lower() or "Remediation" in last_snippet:
                    phase_info = "🔍 Analysiere: **Phase 3 (Remediation)**"
                elif token_count > 50:
                    phase_info = "🔍 Analysiere: **Sicherheitsplan**"

                status_text = f"🧠 **KI-Analyse läuft**\n\n{phase_info}\n\n📊 Tokens: **{token_count}** / ~{expected_tokens}\n⚡ Zeit: **{elapsed}s** | {eta_text}\n\n{bar} {progress}%"

                # Füge snippet hinzu falls vorhanden
                if snippet_preview:
                    status_text += f"\n\n💬 *\"{snippet_preview}\"*"

                await self._send_batch_status(batch, status_text, 0xF39C12)  # Orange

        # Finale Message falls noch nicht von _create_coordinated_plan() gesendet
        # (kann passieren wenn done=True gesetzt wird bevor letzte Update)

    def _summarize_event_details(self, event_details, max_chars: int = 2000) -> str:
        """
        Kürzt Event-Details intelligent auf max_chars.

        Erkennt Event-Typen und erstellt kompakte Zusammenfassungen:
        - Trivy: Image-Name, Critical/High Counts, empfohlene Aktion
        - Fail2ban: IP, Jail, Zeitraum
        - CrowdSec: Decision-Type, IP, Szenario
        - Allgemein: JSON mit indent=2, dann auf max_chars kürzen
        """
        # Falls String, versuche JSON zu parsen
        if isinstance(event_details, str):
            try:
                event_details = json.loads(event_details)
            except (json.JSONDecodeError, TypeError):
                # Einfacher String — direkt kürzen
                if len(event_details) <= max_chars:
                    return event_details
                return event_details[:max_chars - 20] + "\n... [gekürzt]"

        if not isinstance(event_details, dict):
            text = str(event_details)
            if len(text) <= max_chars:
                return text
            return text[:max_chars - 20] + "\n... [gekürzt]"

        # Trivy-Events erkennen (CVE-Scans)
        if 'AffectedImages' in event_details or 'ImageDetails' in event_details or 'vulnerabilities' in event_details:
            summary_parts = []
            summary_parts.append("=== Trivy CVE-Scan Zusammenfassung ===")

            # Vulnerability-Counts
            vulns = event_details.get('vulnerabilities', {})
            if vulns:
                total = sum(v for v in vulns.values() if isinstance(v, (int, float)))
                summary_parts.append(f"Gesamt: {total} Vulnerabilities")
                for sev in ['critical', 'high', 'medium', 'low']:
                    count = vulns.get(sev, 0)
                    if count > 0:
                        summary_parts.append(f"  {sev.upper()}: {count}")

            # Totals auf Top-Level
            for key in ['total_critical', 'total_high', 'total_medium', 'total_low']:
                val = event_details.get(key)
                if val and val > 0:
                    severity_name = key.replace('total_', '').upper()
                    summary_parts.append(f"  {severity_name}: {val}")

            # Betroffene Images
            affected = event_details.get('AffectedImages', [])
            if affected:
                summary_parts.append(f"Betroffene Images ({len(affected)}):")
                for img in affected[:5]:
                    img_details = event_details.get('ImageDetails', {}).get(img, {})
                    critical = img_details.get('critical', 0)
                    high = img_details.get('high', 0)
                    project = img_details.get('project', 'unbekannt')
                    summary_parts.append(f"  - {img}: CRITICAL={critical}, HIGH={high} (Projekt: {project})")
                if len(affected) > 5:
                    summary_parts.append(f"  ... und {len(affected) - 5} weitere Images")

            # Empfohlene Aktion
            action = event_details.get('recommended_action', event_details.get('action'))
            if action:
                summary_parts.append(f"Empfohlene Aktion: {action}")

            result = "\n".join(summary_parts)
            if len(result) <= max_chars:
                return result
            return result[:max_chars - 20] + "\n... [gekürzt]"

        # Fail2ban-Events erkennen
        if any(k in event_details for k in ['jail', 'banned_ip', 'ban_time']):
            summary_parts = []
            summary_parts.append("=== Fail2ban Event ===")
            if event_details.get('banned_ip'):
                summary_parts.append(f"IP: {event_details['banned_ip']}")
            if event_details.get('jail'):
                summary_parts.append(f"Jail: {event_details['jail']}")
            if event_details.get('ban_time'):
                summary_parts.append(f"Ban-Dauer: {event_details['ban_time']}")
            if event_details.get('failures'):
                summary_parts.append(f"Fehlversuche: {event_details['failures']}")
            if event_details.get('action'):
                summary_parts.append(f"Aktion: {event_details['action']}")
            return "\n".join(summary_parts)

        # CrowdSec-Events erkennen
        if any(k in event_details for k in ['decision_type', 'scenario', 'crowdsec']):
            summary_parts = []
            summary_parts.append("=== CrowdSec Event ===")
            if event_details.get('decision_type'):
                summary_parts.append(f"Decision: {event_details['decision_type']}")
            if event_details.get('source_ip'):
                summary_parts.append(f"IP: {event_details['source_ip']}")
            if event_details.get('scenario'):
                summary_parts.append(f"Szenario: {event_details['scenario']}")
            if event_details.get('duration'):
                summary_parts.append(f"Dauer: {event_details['duration']}")
            if event_details.get('action'):
                summary_parts.append(f"Aktion: {event_details['action']}")
            return "\n".join(summary_parts)

        # Allgemein: JSON mit indent=2, dann kürzen
        try:
            text = json.dumps(event_details, indent=2, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(event_details)

        if len(text) <= max_chars:
            return text
        return text[:max_chars - 20] + "\n... [gekürzt]"

    def _build_coordinated_planning_prompt(self, context: Dict) -> str:
        """Baut Prompt für koordinierte Planung mit Infrastructure Context"""

        prompt_parts = []

        # ADD: Context Manager Integration for Infrastructure Knowledge
        max_infra_context_chars = 3000
        if self.ai_service and hasattr(self.ai_service, 'context_manager') and self.ai_service.context_manager:
            prompt_parts.append("# INFRASTRUCTURE & PROJECT KNOWLEDGE BASE")
            prompt_parts.append("Du hast Zugriff auf detaillierte Informationen über die Server-Infrastruktur und laufende Projekte.")
            prompt_parts.append("Nutze diesen Kontext für informierte, sichere Entscheidungen.\n")

            # Get relevant context for all events in batch
            for event in context['events']:
                relevant_context = self.ai_service.context_manager.get_relevant_context(
                    event['source'],
                    event.get('event_type', 'unknown')
                )
                if relevant_context:
                    # Kürze Infrastructure-Kontext auf max_infra_context_chars
                    if len(relevant_context) > max_infra_context_chars:
                        logger.info(f"Infrastructure-Kontext gekürzt: {len(relevant_context)} -> {max_infra_context_chars} Zeichen")
                        relevant_context = relevant_context[:max_infra_context_chars - 30] + "\n... [Kontext gekürzt]"
                    prompt_parts.append(relevant_context)
                    break  # Only add context once (same for all events in batch)

            prompt_parts.append("\n" + "="*80 + "\n")

        # Main coordination prompt
        prompt_parts.append(f"""# Koordinierte Security Remediation

Du bist ein Security-Engineer der einen KOORDINIERTEN Gesamt-Plan erstellt.

## Wichtig:
- Analysiere ALLE {context['event_count']} Events ZUSAMMEN
- Nutze den INFRASTRUCTURE & PROJECT KNOWLEDGE BASE Kontext oben
- Erkenne Abhängigkeiten zwischen Projekten und Services
- Erstelle EINE sequentielle Ausführungs-Pipeline
- Vermeide Race Conditions und Breaking Changes
- Berücksichtige laufende Services (docker-compose.yml, Versionen)

## Events im Batch:
""")

        for i, event in enumerate(context['events'], 1):
            prompt_parts.append(f"\n### Event {i}: {event['source']} ({event['severity']})\n")
            # Event-Details intelligent kürzen statt volles JSON
            raw_details = event.get('details', 'N/A')
            summarized_details = self._summarize_event_details(raw_details, max_chars=2000)
            prompt_parts.append(f"```\n{summarized_details}\n```\n")

        prompt_parts.append("""

## Aufgabe:
Erstelle einen koordinierten Plan mit Phasen die NACHEINANDER ausgeführt werden.

**WICHTIG: Alle Texte MÜSSEN auf DEUTSCH sein!**

Ausgabe als JSON:
{
  "description": "Kurze Beschreibung des Gesamt-Plans (DEUTSCH)",
  "confidence": 0.XX,
  "estimated_duration_minutes": XX,
  "requires_restart": true/false,
  "phases": [
    {
      "name": "Phase 1: Backup",
      "description": "System-Backup erstellen",
      "steps": ["Schritt 1", "Schritt 2"],
      "estimated_minutes": 5
    },
    {
      "name": "Phase 2: Docker Updates",
      "description": "CVEs in Docker Images beheben",
      "steps": ["Update packages", "Rebuild images", "Test"],
      "estimated_minutes": 15
    }
  ],
  "rollback_plan": "Beschreibung wie Rollback funktioniert (DEUTSCH)"
}
""")

        final_prompt = "\n".join(prompt_parts)
        logger.info(f"Koordinierter Prompt erstellt: {len(final_prompt)} Zeichen, {len(context['events'])} Events")
        return final_prompt
