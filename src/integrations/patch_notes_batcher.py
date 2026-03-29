"""
Patch Notes Batcher — Sammelt ALLE Commits und gibt sie kontrolliert frei.

Jeder Commit wird gesammelt, unabhängig von Version-Bumps oder Hotfixes.
Release nur via: Cron (Sonntag), Notbremse (≥20 Commits), oder manuell.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger('shadowops')


class PatchNotesBatcher:
    """
    Sammelt ALLE Commits und gibt sie kontrolliert frei.

    Regeln:
    - ALLE Commits werden gesammelt (keine Ausnahmen)
    - Release via Cron (wöchentlich), Notbremse (≥ emergency_threshold), oder manuell
    - Manuelles Freigeben jederzeit via /release-notes
    """

    def __init__(self, data_dir: Path, batch_threshold: int = 8,
                 emergency_threshold: int = 20,
                 cron_day: str = 'sunday', cron_hour: int = 20,
                 cron_min_commits: int = 3):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.batch_file = self.data_dir / 'pending_batch.json'
        self.batch_threshold = batch_threshold  # Legacy, nicht mehr für auto-release
        self.emergency_threshold = emergency_threshold
        self.cron_day = cron_day.lower()
        self.cron_hour = cron_hour
        self.cron_min_commits = cron_min_commits

        self.pending: Dict[str, Dict] = self._load_pending()

        logger.info(
            f"✅ PatchNotesBatcher initialisiert "
            f"(Notbremse: {emergency_threshold}, Cron: {cron_day} {cron_hour}:00, "
            f"min: {cron_min_commits} Commits)"
        )

    def _load_pending(self) -> Dict[str, Dict]:
        """Lade ausstehende Batches von Disk."""
        if not self.batch_file.exists():
            return {}
        try:
            with open(self.batch_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Fehler beim Laden der Batch-Datei: {e}")
            return {}

    def _save_pending(self) -> None:
        """Speichere ausstehende Batches auf Disk."""
        try:
            with open(self.batch_file, 'w', encoding='utf-8') as f:
                json.dump(self.pending, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Fehler beim Speichern der Batch-Datei: {e}")

    def should_batch(self, commits: list, project: str) -> bool:
        """
        ALLE Commits werden gesammelt — keine Ausnahmen.

        Release nur via Cron (Sonntag), Notbremse (≥20), oder manuell (/release-notes).

        Returns:
            True (immer)
        """
        return True

    def add_commits(self, project: str, commits: list) -> Dict:
        """
        Füge Commits zum Batch hinzu.

        Returns:
            Dict mit {'batched': True/False, 'total_pending': int, 'ready': bool}
            ready=True nur bei Notbremse (≥ emergency_threshold Commits)
        """
        if project not in self.pending:
            self.pending[project] = {
                'commits': [],
                'first_added': datetime.now(timezone.utc).isoformat(),
                'last_added': datetime.now(timezone.utc).isoformat(),
            }

        batch = self.pending[project]

        for commit in commits:
            # Duplikate vermeiden
            existing_ids = {c.get('id', c.get('sha', '')) for c in batch['commits']}
            commit_id = commit.get('id', commit.get('sha', ''))
            if commit_id and commit_id not in existing_ids:
                batch['commits'].append(commit)

        batch['last_added'] = datetime.now(timezone.utc).isoformat()

        total = len(batch['commits'])
        # Notbremse: nur bei sehr vielen Commits auto-releasen
        ready = total >= self.emergency_threshold

        self._save_pending()

        logger.info(
            f"📦 Batch für {project}: {total} Commits gesammelt"
        )

        return {
            'batched': True,
            'total_pending': total,
            'ready': ready,
        }

    def release_batch(self, project: str) -> Optional[List[Dict]]:
        """
        Gib gesammelte Commits für ein Projekt frei.

        Returns:
            Liste der gesammelten Commits oder None wenn kein Batch vorhanden
        """
        if project not in self.pending:
            return None

        batch = self.pending.pop(project)
        self._save_pending()

        commits = batch.get('commits', [])
        logger.info(f"🚀 Batch für {project} freigegeben: {len(commits)} Commits")

        return commits

    def get_pending_summary(self) -> Dict[str, Dict]:
        """Zusammenfassung aller ausstehenden Batches."""
        summary = {}
        for project, batch in self.pending.items():
            commits = batch.get('commits', [])
            summary[project] = {
                'count': len(commits),
                'first_added': batch.get('first_added'),
                'last_added': batch.get('last_added'),
            }
        return summary

    def get_cron_releasable_projects(self) -> List[str]:
        """Projekte die beim wöchentlichen Cron released werden sollen (≥ min Commits)."""
        releasable = []
        for project, batch in self.pending.items():
            count = len(batch.get('commits', []))
            if count >= self.cron_min_commits:
                releasable.append(project)
        return releasable

    def get_daily_releasable_projects(self, daily_min_commits: int = 3) -> List[str]:
        """Projekte die beim täglichen Release freigegeben werden sollen."""
        releasable = []
        for project, batch in self.pending.items():
            count = len(batch.get('commits', []))
            if count >= daily_min_commits:
                releasable.append(project)
        return releasable

    def has_pending(self, project: str) -> bool:
        """Prüfe ob ein Projekt ausstehende Commits hat."""
        return project in self.pending and len(self.pending[project].get('commits', [])) > 0

    def should_release_by_time(self, project: str) -> bool:
        """
        Prüfe ob der Batch zeitbasiert freigegeben werden soll.

        Gibt True zurück wenn:
        - Mindestens 2 Commits gesammelt wurden
        - Der älteste Commit älter als max_wait_minutes ist
        """
        if project not in self.pending:
            return False

        batch = self.pending[project]
        commits = batch.get('commits', [])
        if len(commits) < 2:
            return False

        first_added = batch.get('first_added')
        if not first_added:
            return False

        try:
            first_dt = datetime.fromisoformat(first_added)
            elapsed_minutes = (datetime.now(timezone.utc) - first_dt).total_seconds() / 60
            if elapsed_minutes >= self.max_wait_minutes:
                logger.info(
                    f"⏰ Zeitbasierter Release für {project}: "
                    f"{len(commits)} Commits, {elapsed_minutes:.0f}min gewartet"
                )
                return True
        except Exception as e:
            logger.debug(f"Zeitbasierter Release Check fehlgeschlagen: {e}")

        return False


def get_patch_notes_batcher(data_dir: Path = None, batch_threshold: int = 8) -> PatchNotesBatcher:
    """Factory für PatchNotesBatcher."""
    if data_dir is None:
        data_dir = Path.home() / '.shadowops' / 'patch_notes_training'
    return PatchNotesBatcher(data_dir, batch_threshold)
