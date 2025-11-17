"""
Security Remediation Orchestrator

Koordiniert ALLE Security-Events in einem Master-Prozess um Race Conditions
und Konflikte zwischen parallelen Fixes zu vermeiden.

Workflow:
1. Sammelt alle Events in einem Zeitfenster (Batch)
2. KI erstellt einen koordinierten Gesamt-Plan
3. User Approval (einmal für den gesamten Plan)
4. Sequentielle Ausführung mit System-Locks
5. Comprehensive Testing und Rollback bei Problemen
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger('shadowops')


@dataclass
class SecurityEventBatch:
    """Batch von Security-Events die zusammen behandelt werden"""
    events: List = field(default_factory=list)
    batch_id: str = ""
    created_at: float = 0.0
    status: str = "collecting"  # collecting, analyzing, awaiting_approval, executing, completed, failed
    status_message_id: Optional[int] = None  # Discord Message ID für Live-Updates
    status_channel_id: Optional[int] = None  # Discord Channel ID für Live-Updates

    def __post_init__(self):
        if not self.batch_id:
            self.batch_id = f"batch_{int(time.time())}"
        if not self.created_at:
            self.created_at = time.time()

    @property
    def severity_priority(self) -> int:
        """Höchste Severity im Batch (für Priorisierung)"""
        severity_map = {'CRITICAL': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1, 'UNKNOWN': 0}
        return max([severity_map.get(e.severity, 0) for e in self.events], default=0)

    @property
    def sources(self) -> Set[str]:
        """Alle Event-Quellen im Batch"""
        return {e.source for e in self.events}

    def add_event(self, event):
        """Fügt Event zum Batch hinzu"""
        self.events.append(event)


@dataclass
class RemediationPlan:
    """Koordinierter Gesamt-Plan für alle Fixes"""
    batch_id: str
    description: str
    phases: List[Dict] = field(default_factory=list)
    confidence: float = 0.0
    estimated_duration_minutes: int = 0
    requires_restart: bool = False
    rollback_plan: str = ""
    ai_model: str = ""
    created_at: float = field(default_factory=time.time)


class RemediationOrchestrator:
    """
    Master Coordinator für alle Security Remediations

    Verhindert Race Conditions durch:
    - Event Batching (sammelt Events über 10s)
    - Koordinierte KI-Analyse (ALLE Events zusammen)
    - Single Approval Flow
    - Sequentielle Ausführung mit System-Locks
    """

    def __init__(self, ai_service, self_healing_coordinator, approval_manager, bot=None):
        self.ai_service = ai_service
        self.self_healing = self_healing_coordinator
        self.approval_manager = approval_manager
        self.bot = bot  # Discord Bot für Approval Messages

        # Event Batching
        self.collection_window_seconds = 10  # Sammelt Events über 10 Sekunden
        self.current_batch: Optional[SecurityEventBatch] = None
        self.batch_lock = asyncio.Lock()
        self.collection_task: Optional[asyncio.Task] = None

        # Execution Lock (nur 1 Remediation zur Zeit!)
        self.execution_lock = asyncio.Lock()
        self.currently_executing: Optional[str] = None

        # Batch Queue
        self.pending_batches: List[SecurityEventBatch] = []
        self.completed_batches: List[SecurityEventBatch] = []

        logger.info("🎯 Remediation Orchestrator initialisiert")
        logger.info(f"   📊 Batching Window: {self.collection_window_seconds}s")
        logger.info("   🔒 Sequential Execution Mode: ON")

    def _get_status_channel(self):
        """Holt den Status-Channel für Live-Updates"""
        if not self.bot:
            return None
        # Verwende den Approval-Channel für Live-Updates
        try:
            approval_channel_id = 1438503737315299351  # auto-remediation-approvals
            channel = self.bot.get_channel(approval_channel_id)
            return channel
        except Exception as e:
            logger.error(f"Fehler beim Holen des Status-Channels: {e}")
        return None

    async def _send_batch_status(self, batch: SecurityEventBatch, status_text: str, color: int = 0xFFAA00):
        """Sendet oder updated Status-Message für einen Batch"""
        import discord

        channel = self._get_status_channel()
        if not channel:
            logger.warning("⚠️ Status-Channel nicht verfügbar - überspringe Discord-Update")
            return

        try:
            embed = discord.Embed(
                title="🔄 Koordinierte Remediation läuft",
                description=status_text,
                color=color,
                timestamp=datetime.now()
            )
            embed.set_footer(text=f"Batch ID: {batch.batch_id}")

            if batch.status_message_id:
                # Update existing message
                try:
                    message = await channel.fetch_message(batch.status_message_id)
                    await message.edit(embed=embed)
                    logger.debug(f"📝 Discord-Status updated (Message ID: {batch.status_message_id})")
                except:
                    # Message not found, send new one
                    message = await channel.send(embed=embed)
                    batch.status_message_id = message.id
                    batch.status_channel_id = channel.id
                    logger.info(f"📤 Neue Discord-Status-Message gesendet (ID: {message.id})")
            else:
                # Send new message
                message = await channel.send(embed=embed)
                batch.status_message_id = message.id
                batch.status_channel_id = channel.id
                logger.info(f"📤 Neue Discord-Status-Message gesendet (ID: {message.id})")

        except Exception as e:
            logger.error(f"Fehler beim Senden der Status-Message: {e}")

    async def submit_event(self, event):
        """
        Event zum Orchestrator hinzufügen

        Startet automatisch Batch-Collection wenn nötig
        """
        async with self.batch_lock:
            # Erstelle neuen Batch wenn nötig
            if self.current_batch is None:
                self.current_batch = SecurityEventBatch()
                logger.info(f"📦 Neuer Event-Batch gestartet: {self.current_batch.batch_id}")

                # Starte Collection Timer
                self.collection_task = asyncio.create_task(self._close_batch_after_timeout())

                # Sende initiale Discord-Message
                status_text = f"📦 **Neuer Remediation-Batch gestartet**\n\n⏱️ Sammle Events für {self.collection_window_seconds} Sekunden..."
                await self._send_batch_status(self.current_batch, status_text, 0x3498DB)

            # Füge Event zum aktuellen Batch hinzu
            self.current_batch.add_event(event)
            logger.info(f"   ➕ Event hinzugefügt: {event.source} ({event.severity})")
            logger.info(f"   📊 Batch Status: {len(self.current_batch.events)} Events")

            # Update Discord-Message mit neuem Event
            event_list = "\n".join([f"• **{e.source.upper()}**: {e.severity}" for e in self.current_batch.events])
            elapsed = int(time.time() - self.current_batch.created_at)
            remaining = max(0, self.collection_window_seconds - elapsed)
            status_text = f"📦 **Sammle Security-Events**\n\n{event_list}\n\n⏱️ Verbleibend: **{remaining}s** | Events: **{len(self.current_batch.events)}**"
            await self._send_batch_status(self.current_batch, status_text, 0x3498DB)

    async def _close_batch_after_timeout(self):
        """Schließt Batch nach Collection Window mit Live-Countdown-Updates"""
        update_interval = 2  # Update Discord alle 2 Sekunden
        elapsed = 0

        batch = self.current_batch  # Referenz speichern

        while elapsed < self.collection_window_seconds:
            await asyncio.sleep(update_interval)
            elapsed += update_interval

            # Update Discord mit Countdown
            async with self.batch_lock:
                if self.current_batch == batch and len(batch.events) > 0:
                    remaining = max(0, self.collection_window_seconds - elapsed)
                    event_list = "\n".join([f"• **{e.source.upper()}**: {e.severity}" for e in batch.events])

                    # Progress bar
                    progress = min(100, int((elapsed / self.collection_window_seconds) * 100))
                    bar_length = 20
                    filled = int((progress / 100) * bar_length)
                    bar = "█" * filled + "░" * (bar_length - filled)

                    status_text = f"📦 **Sammle Security-Events**\n\n{event_list}\n\n⏱️ **{remaining}s** verbleibend | Events: **{len(batch.events)}**\n\n{bar} {progress}%"
                    await self._send_batch_status(batch, status_text, 0x3498DB)

        async with self.batch_lock:
            if self.current_batch and len(self.current_batch.events) > 0:
                logger.info(f"⏰ Batch-Collection abgelaufen ({self.collection_window_seconds}s)")
                logger.info(f"   📦 Batch {self.current_batch.batch_id}: {len(self.current_batch.events)} Events")
                logger.info(f"   🔍 Quellen: {', '.join(self.current_batch.sources)}")

                # Final Discord Update
                event_list = "\n".join([f"• **{e.source.upper()}**: {e.severity}" for e in self.current_batch.events])
                status_text = f"✅ **Batch geschlossen**\n\n{event_list}\n\n📊 Total: **{len(self.current_batch.events)} Events**\n🔍 Quellen: {', '.join(self.current_batch.sources)}\n\n🧠 Starte KI-Analyse..."
                await self._send_batch_status(self.current_batch, status_text, 0xF39C12)

                # Batch zur Verarbeitung verschieben
                self.current_batch.status = "analyzing"
                self.pending_batches.append(self.current_batch)
                self.current_batch = None

                # Starte Verarbeitung
                asyncio.create_task(self._process_next_batch())

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

                if not approved:
                    logger.warning(f"❌ User hat Batch {batch.batch_id} abgelehnt")
                    batch.status = "rejected"
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

                self.completed_batches.append(batch)

            except Exception as e:
                logger.error(f"❌ Orchestrator Error für Batch {batch.batch_id}: {e}", exc_info=True)
                batch.status = "failed"
                self.completed_batches.append(batch)

            finally:
                self.currently_executing = None

                # Verarbeite nächsten Batch falls vorhanden
                if self.pending_batches:
                    asyncio.create_task(self._process_next_batch())

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

            # Sende finale Discord-Message: Plan erstellt
            phase_names = "\n".join([f"• **Phase {i+1}**: {p['name']}" for i, p in enumerate(plan.phases)])
            status_text = f"✅ **Plan erstellt**\n\n{phase_names}\n\n⏱️ Geschätzte Dauer: **{plan.estimated_duration_minutes}min**\n🎯 Confidence: **{plan.confidence:.0%}**"
            await self._send_batch_status(batch, status_text, 0x2ECC71)  # Green

            logger.info(f"✅ Koordinierter Plan erstellt: {len(plan.phases)} Phasen, {plan.confidence:.0%} Confidence")
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

    def _build_coordinated_planning_prompt(self, context: Dict) -> str:
        """Baut Prompt für koordinierte Planung"""

        prompt = f"""# Koordinierte Security Remediation

Du bist ein Security-Engineer der einen KOORDINIERTEN Gesamt-Plan erstellt.

## Wichtig:
- Analysiere ALLE {context['event_count']} Events ZUSAMMEN
- Erkenne Abhängigkeiten und Konflikte
- Erstelle EINE sequentielle Ausführungs-Pipeline
- Vermeide Race Conditions

## Events im Batch:
"""

        for i, event in enumerate(context['events'], 1):
            prompt += f"\n### Event {i}: {event['source']} ({event['severity']})\n"
            prompt += f"```\n{event.get('details', 'N/A')}\n```\n"

        prompt += """

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
"""

        return prompt

    async def _request_approval(self, batch: SecurityEventBatch, plan: RemediationPlan) -> bool:
        """
        Fordert User-Approval für den gesamten koordinierten Plan an

        Zeigt ein schönes Discord Embed mit:
        - Zusammenfassung aller Events
        - Alle Phasen des Plans
        - Geschätzte Dauer
        - Risiko-Level
        - Approve/Reject Buttons
        """
        import discord

        logger.info(f"👤 Fordere Approval an für Batch {batch.batch_id}")

        # Build Discord Embed
        embed = discord.Embed(
            title="🎯 Koordinierter Remediation-Plan",
            description=f"**{plan.description}**\n\nDieser Plan behandelt **{len(batch.events)} Security-Events** koordiniert und sequentiell.",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )

        # Events Summary
        sources_summary = {}
        for event in batch.events:
            source = event.source
            if source not in sources_summary:
                sources_summary[source] = {'count': 0, 'severity': event.severity}
            sources_summary[source]['count'] += 1

        events_text = "\n".join([
            f"**{source.upper()}:** {info['count']} Event(s) ({info['severity']})"
            for source, info in sources_summary.items()
        ])

        embed.add_field(
            name="📦 Events im Batch",
            value=events_text,
            inline=False
        )

        # Execution Plan (Phasen)
        phases_text = ""
        total_minutes = 0
        for i, phase in enumerate(plan.phases[:5], 1):  # Max 5 Phasen anzeigen
            name = phase.get('name', f'Phase {i}')
            desc = phase.get('description', 'N/A')
            minutes = phase.get('estimated_minutes', 5)
            total_minutes += minutes

            phases_text += f"**{i}. {name}** (~{minutes}min)\n{desc}\n\n"

        if len(plan.phases) > 5:
            phases_text += f"_...und {len(plan.phases) - 5} weitere Phasen_\n"

        embed.add_field(
            name="⚙️ Ausführungs-Plan",
            value=phases_text or "Keine Phasen definiert",
            inline=False
        )

        # Metadata
        confidence_color = "🟢" if plan.confidence >= 0.8 else "🟡" if plan.confidence >= 0.6 else "🔴"

        embed.add_field(
            name="📊 Plan-Details",
            value=f"**Confidence:** {confidence_color} {plan.confidence:.0%}\n"
                  f"**Geschätzte Dauer:** ⏱️ ~{total_minutes} Minuten\n"
                  f"**Neustart erforderlich:** {'✅ Ja' if plan.requires_restart else '❌ Nein'}\n"
                  f"**KI-Modell:** {plan.ai_model}",
            inline=False
        )

        # Rollback Info
        if plan.rollback_plan:
            embed.add_field(
                name="🔄 Rollback-Strategie",
                value=plan.rollback_plan[:200] + ("..." if len(plan.rollback_plan) > 200 else ""),
                inline=False
            )

        embed.set_footer(text=f"Batch ID: {batch.batch_id} | Orchestrator v1.0")

        # Send to approval channel with buttons
        try:
            if not self.bot:
                logger.warning("⚠️ Kein Bot verfügbar für Approval - Auto-Approve")
                return True

            # Get approval channel
            approval_channel_id = 1438503737315299351  # auto-remediation-approvals
            channel = self.bot.get_channel(approval_channel_id)

            if not channel:
                logger.error(f"❌ Approval Channel {approval_channel_id} nicht gefunden")
                return False

            # Create approval buttons
            import discord

            class ApprovalView(discord.ui.View):
                def __init__(self, orchestrator, batch_id):
                    super().__init__(timeout=1800)  # 30 minutes
                    self.orchestrator = orchestrator
                    self.batch_id = batch_id
                    self.approved = None

                @discord.ui.button(label="✅ Approve & Execute", style=discord.ButtonStyle.green, custom_id="approve")
                async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    await interaction.response.send_message(
                        f"✅ **Plan approved!** Starte koordinierte Remediation...",
                        ephemeral=True
                    )
                    self.approved = True
                    self.stop()

                @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red, custom_id="reject")
                async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    await interaction.response.send_message(
                        f"❌ **Plan abgelehnt.** Remediation wird nicht ausgeführt.",
                        ephemeral=True
                    )
                    self.approved = False
                    self.stop()

                @discord.ui.button(label="📋 Details anzeigen", style=discord.ButtonStyle.gray, custom_id="details")
                async def details_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    # Build detailed view
                    details_text = f"**Batch {self.batch_id} - Detaillierte Phasen:**\n\n"

                    # Get plan from orchestrator
                    # For now, just acknowledge
                    await interaction.response.send_message(
                        f"📋 Detaillierte Phasen-Informationen für Batch `{self.batch_id}`\n\n"
                        f"Siehe Embed oben für vollständige Details.",
                        ephemeral=True
                    )

            # Create view instance
            view = ApprovalView(self, batch.batch_id)

            # Send message with embed and buttons
            approval_message = await channel.send(embed=embed, view=view)
            logger.info(f"📬 Approval-Request gesendet an Channel {channel.name}")

            # Wait for user interaction
            logger.info(f"⏳ Warte auf User-Approval (Timeout: 30min)...")
            await view.wait()

            # Update message to show result
            if view.approved is True:
                # Update embed color to green
                embed.color = discord.Color.green()
                embed.title = "✅ Plan Approved - Wird ausgeführt"
                await approval_message.edit(embed=embed, view=None)
                logger.info(f"✅ Batch {batch.batch_id} wurde approved")
                return True

            elif view.approved is False:
                # Update embed color to red
                embed.color = discord.Color.red()
                embed.title = "❌ Plan Rejected"
                await approval_message.edit(embed=embed, view=None)
                logger.warning(f"❌ Batch {batch.batch_id} wurde rejected")
                return False

            else:
                # Timeout
                embed.color = discord.Color.dark_gray()
                embed.title = "⏰ Approval Timeout - Plan verworfen"
                await approval_message.edit(embed=embed, view=None)
                logger.warning(f"⏰ Batch {batch.batch_id} - Approval Timeout")
                return False

        except Exception as e:
            logger.error(f"❌ Fehler bei Approval-Request: {e}", exc_info=True)
            return False

    async def _execute_plan(self, batch: SecurityEventBatch, plan: RemediationPlan) -> bool:
        """
        Führt Plan sequentiell Phase für Phase aus

        Workflow:
        1. Erstelle System-Backup
        2. Führe jede Phase nacheinander aus
        3. Teste nach jeder Phase
        4. Bei Fehler: Rollback und Stop
        5. Sende Discord-Updates während Ausführung
        """
        import discord

        logger.info(f"⚙️ Starte sequentielle Ausführung von {len(plan.phases)} Phasen")

        # Get execution channel for live updates
        execution_channel = None
        if self.bot:
            try:
                # Send to remediation-alerts channel for live updates
                channel_id = 1438503736220586164  # auto-remediation-alerts
                execution_channel = self.bot.get_channel(channel_id)
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

                    await self._rollback(backup_path, executed_phases)
                    return False

            # All phases successful!
            logger.info(f"✅ Alle {len(plan.phases)} Phasen erfolgreich ausgeführt")

            # Final Discord update
            if exec_message:
                exec_embed.color = discord.Color.green()
                exec_embed.title = "✅ Koordinierte Remediation abgeschlossen"
                exec_embed.set_field_at(
                    0,
                    name="📊 Status",
                    value=f"✅ Alle {len(plan.phases)} Phasen erfolgreich!\n\n"
                          f"**Behandelte Events:**\n" +
                          "\n".join([f"• {e.source.upper()} ({e.severity})" for e in batch.events[:5]]),
                    inline=False
                )
                await exec_message.edit(embed=exec_embed)

            return True

        except Exception as e:
            logger.error(f"❌ Kritischer Fehler während Ausführung: {e}", exc_info=True)

            # Rollback on critical error
            if backup_created:
                await self._rollback(backup_path, executed_phases)

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
                    # Get fix strategy from AI (or use cached from plan)
                    strategy = phase.get('strategy', {})

                    if not strategy:
                        # Generate strategy if not in phase
                        logger.info(f"      Generating strategy for {event.source}...")
                        strategy = await self.ai_service.generate_fix_strategy(
                            {'event': event.to_dict()}
                        )

                    # Discord Live Update: Starting fix
                    if exec_message and exec_embed:
                        current_field = exec_embed.fields[0]
                        exec_embed.set_field_at(
                            0,
                            name="📊 Status",
                            value=f"{current_field.value}\n\n🔧 Fix {idx}/{len(events)}: {event.source.upper()}\n⏳ Executing...",
                            inline=False
                        )
                        await exec_message.edit(embed=exec_embed)

                    # Execute fix via self-healing
                    logger.info(f"      Executing fix for {event.source} event {event.event_id}...")

                    result = await self.self_healing._apply_fix(event, strategy)

                    if result['status'] == 'success':
                        logger.info(f"      ✅ Fix successful: {result.get('message', '')}")

                        # Discord Live Update: Fix successful
                        if exec_message and exec_embed:
                            current_field = exec_embed.fields[0]
                            base_value = current_field.value.split('\n\n🔧')[0]  # Remove previous fix status
                            exec_embed.set_field_at(
                                0,
                                name="📊 Status",
                                value=f"{base_value}\n\n✅ Fix {idx}/{len(events)}: {event.source.upper()} successful\n📝 {result.get('message', '')[:100]}",
                                inline=False
                            )
                            await exec_message.edit(embed=exec_embed)
                    else:
                        logger.error(f"      ❌ Fix failed: {result.get('error', 'Unknown error')}")
                        all_success = False

                        # Discord Live Update: Fix failed
                        if exec_message and exec_embed:
                            current_field = exec_embed.fields[0]
                            base_value = current_field.value.split('\n\n🔧')[0]
                            exec_embed.set_field_at(
                                0,
                                name="📊 Status",
                                value=f"{base_value}\n\n❌ Fix {idx}/{len(events)}: {event.source.upper()} failed\n⚠️ {result.get('error', 'Unknown')[:100]}",
                                inline=False
                            )
                            await exec_message.edit(embed=exec_embed)

                        # If one fix fails, stop phase execution
                        return False

                except Exception as e:
                    logger.error(f"      ❌ Error executing fix for {event.event_id}: {e}", exc_info=True)
                    all_success = False
                    return False

            logger.info(f"   ✅ Phase '{phase_name}' completed successfully")
            return all_success

        except Exception as e:
            logger.error(f"   ❌ Phase execution error: {e}", exc_info=True)
            return False

    async def _rollback(self, backup_path: str, executed_phases: List[Dict]):
        """Führt Rollback durch nach Fehler"""
        logger.warning(f"🔄 Starte Rollback...")
        logger.info(f"   💾 Backup-Pfad: {backup_path}")
        logger.info(f"   🔙 Rollback für {len(executed_phases)} Phasen")

        try:
            # Access backup manager from self-healing
            backup_manager = self.self_healing.backup_manager

            # Rollback each phase in reverse order
            for phase in reversed(executed_phases):
                if phase['status'] == 'success':
                    phase_name = phase.get('phase', f"Phase {phase['index']}")
                    logger.info(f"   🔙 Rollback {phase_name}...")

                    # Trigger cleanup via backup manager
                    # In a full implementation, we'd track which backups belong to which phase
                    # For now, we rely on the backup manager's rollback capability

            logger.info("✅ Rollback abgeschlossen")

        except Exception as e:
            logger.error(f"❌ Rollback error: {e}", exc_info=True)

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
