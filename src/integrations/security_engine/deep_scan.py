"""
DeepScanMode — AI-gesteuerte Security-Sessions mit Learning Pipeline

Migriert SecurityAnalyst-Logik in die Security Engine.
Nutzt SecurityDB statt separater AnalystDB.

Adaptive Session-Modi:
- fix_only: >=20 offene Findings, bis 3 Sessions/Tag
- full_scan: 5-19 Findings, bis 2 Sessions/Tag
- quick_scan: 1-4 Findings, 1 Session/Tag
- maintenance: 0 Findings, nur wenn >3 Tage seit letztem Scan
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from .models import EngineMode

logger = logging.getLogger('shadowops.deep_scan')


# Session-Konfiguration pro Modus
SESSION_CONFIG = {
    'fix_only': {
        'max_sessions_per_day': 3,
        'timeout_minutes': 120,
        'max_turns': 200,
        'scan_enabled': False,
        'fix_enabled': True,
    },
    'full_scan': {
        'max_sessions_per_day': 2,
        'timeout_minutes': 45,
        'max_turns': 60,
        'scan_enabled': True,
        'fix_enabled': True,
    },
    'quick_scan': {
        'max_sessions_per_day': 1,
        'timeout_minutes': 20,
        'max_turns': 30,
        'scan_enabled': True,
        'fix_enabled': True,
    },
    'maintenance': {
        'max_sessions_per_day': 1,
        'timeout_minutes': 10,
        'max_turns': 15,
        'scan_enabled': True,
        'fix_enabled': False,
    },
}


class DeepScanMode:
    """AI-gesteuerte Security-Sessions mit Full Learning Pipeline"""

    def __init__(self, db, ai_engine=None, executor=None, context_manager=None):
        self.db = db
        self.ai_engine = ai_engine
        self.executor = executor
        self.context_manager = context_manager
        self.sessions_today: int = 0
        self.current_session: Optional[Dict] = None

    async def _determine_session_mode(self) -> str:
        """Bestimmt Session-Modus basierend auf offenen Findings"""
        open_count = await self.db.get_open_findings_count()

        if open_count >= 20:
            return 'fix_only'
        elif open_count >= 5:
            return 'full_scan'
        elif open_count >= 1:
            return 'quick_scan'
        else:
            return 'maintenance'

    def _get_session_config(self, mode: str) -> Dict[str, Any]:
        """Gibt Session-Konfiguration fuer den Modus zurueck"""
        return SESSION_CONFIG.get(mode, SESSION_CONFIG['maintenance'])

    async def can_start_session(self) -> bool:
        """Prueft ob eine weitere Session heute erlaubt ist"""
        mode = await self._determine_session_mode()
        config = self._get_session_config(mode)
        return self.sessions_today < config['max_sessions_per_day']

    async def run_session(self) -> Dict[str, Any]:
        """
        Fuehrt eine vollstaendige Deep-Scan-Session aus.

        Returns: Session-Summary mit findings, fixes, tokens, etc.
        """
        mode = await self._determine_session_mode()
        config = self._get_session_config(mode)

        if self.sessions_today >= config['max_sessions_per_day']:
            logger.info(f"Session-Limit erreicht ({self.sessions_today}/{config['max_sessions_per_day']})")
            return {'status': 'skipped', 'reason': 'session_limit', 'mode': mode}

        logger.info(f"Starte Deep-Scan Session im Modus '{mode}'")
        self.sessions_today += 1

        session_result = {
            'mode': mode,
            'status': 'running',
            'findings_count': 0,
            'fixes_count': 0,
            'config': config,
        }
        self.current_session = session_result

        try:
            # Phase 1: Pre-Session Maintenance
            await self._pre_session_maintenance()

            # Phase 2: Scan (wenn aktiviert)
            if config['scan_enabled']:
                findings = await self._run_scan_phase(mode, config)
                session_result['findings_count'] = len(findings)

            # Phase 3: Fix (wenn aktiviert)
            if config['fix_enabled']:
                fixes = await self._run_fix_phase(mode, config)
                session_result['fixes_count'] = fixes

            session_result['status'] = 'completed'
            logger.info(
                f"Session abgeschlossen: {session_result['findings_count']} Findings, "
                f"{session_result['fixes_count']} Fixes"
            )

        except Exception as e:
            session_result['status'] = 'failed'
            session_result['error'] = str(e)
            logger.error(f"Session fehlgeschlagen: {e}")

        self.current_session = None
        return session_result

    async def _pre_session_maintenance(self) -> None:
        """Pre-Session: Fix-Verifikation + Knowledge-Decay"""
        logger.info("Pre-Session Maintenance...")

        # Knowledge-Decay: Altes Wissen verliert Confidence
        try:
            if hasattr(self.db, 'decay_old_knowledge'):
                decayed = await self.db.decay_old_knowledge(days=14, decay_pct=5)
                if decayed:
                    logger.info(f"   {decayed} Knowledge-Eintraege decayed")
        except Exception as e:
            logger.debug(f"Knowledge-Decay fehlgeschlagen: {e}")

        # Fix-Verifikation: Pruefe ob letzte Fixes noch halten
        try:
            if hasattr(self.db, 'get_unverified_fixes'):
                unverified = await self.db.get_unverified_fixes(days=14)
                if unverified:
                    logger.info(f"   {len(unverified)} Fixes zur Verifikation")
        except Exception as e:
            logger.debug(f"Fix-Verifikation fehlgeschlagen: {e}")

    async def _run_scan_phase(self, mode: str, config: Dict) -> List[Dict]:
        """Scan-Phase: KI analysiert Server"""
        logger.info(f"Scan-Phase ({mode})...")

        if not self.ai_engine:
            logger.warning("Kein AI-Engine — Scan uebersprungen")
            return []

        # Kontext bauen
        knowledge_context = ''
        try:
            knowledge = await self.db.get_knowledge('security', min_confidence=0.3)
            if knowledge:
                knowledge_context = '\n'.join([
                    f"- {k['subject']}: {k['content']}" for k in knowledge[:20]
                ])
        except Exception:
            pass

        # Hier wuerde der eigentliche AI-Scan laufen
        # (In der vollstaendigen Integration wird das der Analyst-Prompt sein)
        return []

    async def _run_fix_phase(self, mode: str, config: Dict) -> int:
        """Fix-Phase: Offene Findings abarbeiten via Executor"""
        logger.info(f"Fix-Phase ({mode})...")

        if not self.executor:
            logger.warning("Kein Executor — Fix uebersprungen")
            return 0

        # Offene Findings zaehlen (in vollstaendiger Integration: Findings aus DB laden + fixen)
        open_count = await self.db.get_open_findings_count()
        logger.info(f"   {open_count} offene Findings")

        return 0  # In vollstaendiger Integration: Anzahl gefixte Findings

    def reset_daily(self) -> None:
        """Taeglicher Reset der Session-Zaehler"""
        self.sessions_today = 0
