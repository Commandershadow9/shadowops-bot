"""
Patch Notes Batcher — Sammelt kleine Patches und gibt sie gebündelt frei.

Kleine Commits ohne Version-Bump werden gesammelt, bis ein Schwellenwert
erreicht ist oder manuell freigegeben wird.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger('shadowops')


class PatchNotesBatcher:
    """
    Sammelt kleine Patches und gibt sie als gebündelte Patch Notes frei.

    Regeln:
    - Commits ohne Version-Bump (kein vX.Y.Z in Message) werden gesammelt
    - Hotfixes (Commit-Message enthält 'hotfix' oder 'critical') werden sofort veröffentlicht
    - Bei ≥ batch_threshold gesammelten Commits → automatisch freigeben
    - Manuelles Freigeben jederzeit möglich
    """

    def __init__(self, data_dir: Path, batch_threshold: int = 8):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.batch_file = self.data_dir / 'pending_batch.json'
        self.batch_threshold = batch_threshold

        self.pending: Dict[str, Dict] = self._load_pending()

        logger.info(f"✅ PatchNotesBatcher initialisiert (threshold: {batch_threshold})")

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
        Prüfe ob diese Commits gesammelt werden sollen statt sofort veröffentlicht.

        Returns:
            True wenn Commits gesammelt werden sollen
        """
        import re

        # Hotfixes werden nie gesammelt
        for commit in commits:
            msg = (commit.get('message', '') or '').lower()
            if 'hotfix' in msg or 'critical' in msg or 'security' in msg:
                return False

        # Commits mit Version-Bump werden nie gesammelt
        for commit in commits:
            msg = commit.get('message', '')
            # Negative Lookahead: Kein 4. Oktett (→ IP-Adressen ausschließen)
            if re.search(r'v?(?:ersion|elease)?\s*[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4}(?!\.[0-9])', msg, re.IGNORECASE):
                return False

        # Wenige Commits ohne Version → sammeln
        if len(commits) <= 4:
            return True

        return False

    def add_commits(self, project: str, commits: list) -> Dict:
        """
        Füge Commits zum Batch hinzu.

        Returns:
            Dict mit {'batched': True/False, 'total_pending': int, 'ready': bool}
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
        ready = total >= self.batch_threshold

        self._save_pending()

        logger.info(
            f"📦 Batch für {project}: {total} Commits gesammelt "
            f"(Threshold: {self.batch_threshold}, Ready: {ready})"
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
                'ready': len(commits) >= self.batch_threshold,
            }
        return summary

    def has_pending(self, project: str) -> bool:
        """Prüfe ob ein Projekt ausstehende Commits hat."""
        return project in self.pending and len(self.pending[project].get('commits', [])) > 0


def get_patch_notes_batcher(data_dir: Path = None, batch_threshold: int = 8) -> PatchNotesBatcher:
    """Factory für PatchNotesBatcher."""
    if data_dir is None:
        data_dir = Path.home() / '.shadowops' / 'patch_notes_training'
    return PatchNotesBatcher(data_dir, batch_threshold)
