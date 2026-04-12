"""
Patch Notes Batcher — Sammelt ALLE Commits und gibt sie kontrolliert frei.

Jeder Commit wird gesammelt, unabhängig von Version-Bumps oder Hotfixes.
Release via: Cron (wöchentlich/täglich), Notbremse (≥20, mit Cooldown), oder manuell.
Max 1 automatischer Release pro Projekt pro Tag (24h Cooldown).
"""

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger('shadowops')


class PatchNotesBatcher:
    """
    Sammelt ALLE Commits und gibt sie kontrolliert frei.

    Regeln:
    - ALLE Commits werden gesammelt (keine Ausnahmen)
    - Release via Cron (wöchentlich/täglich), oder manuell
    - Notbremse (≥ emergency_threshold) nur wenn kein Release in den letzten 24h
    - Manuelles Freigeben jederzeit via /release-notes
    - Max 1 automatischer Release pro Projekt pro Tag (Cooldown)
    """

    def __init__(self, data_dir: Path, batch_threshold: int = 8,
                 emergency_threshold: int = 20,
                 cron_day: str = 'sunday', cron_hour: int = 20,
                 cron_min_commits: int = 3,
                 release_cooldown_hours: int = 24):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.batch_file = self.data_dir / 'pending_batch.json'
        self.batch_threshold = batch_threshold  # Legacy, nicht mehr für auto-release
        self.emergency_threshold = emergency_threshold
        self.cron_day = cron_day.lower()
        self.cron_hour = cron_hour
        self.cron_min_commits = cron_min_commits
        self.release_cooldown_hours = release_cooldown_hours
        self.max_wait_minutes = 120  # Zeitbasierter Release nach 2h

        self.pending: Dict[str, Dict] = self._load_pending()
        # Letzte Release-Zeitpunkte pro Projekt (persistiert in pending_batch.json)
        self._last_releases: Dict[str, str] = self._load_last_releases()

        logger.info(
            f"✅ PatchNotesBatcher initialisiert "
            f"(Notbremse: {emergency_threshold}, Cooldown: {release_cooldown_hours}h, "
            f"Cron: {cron_day} {cron_hour}:00, min: {cron_min_commits} Commits)"
        )

    def _load_pending(self) -> Dict[str, Dict]:
        """Lade ausstehende Batches von Disk (mit Backup-Fallback + Validierung)."""
        if not self.batch_file.exists():
            return {}
        try:
            with open(self.batch_file, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            return self._validate_batch_structure(raw)
        except Exception as e:
            logger.error(f"Batch-Datei korrupt: {e} — versuche Backup")
            backup = self.batch_file.with_suffix('.backup')
            if backup.exists():
                try:
                    with open(backup, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                    result = self._validate_batch_structure(raw)
                    logger.warning(f"🔄 Batch aus Backup wiederhergestellt: {backup}")
                    return result
                except Exception:
                    pass
            logger.error("Batch und Backup nicht ladbar — starte leer")
            return {}

    def _validate_batch_structure(self, data) -> Dict[str, Dict]:
        """Validiere und bereinige geladene Batch-Daten."""
        if not isinstance(data, dict):
            logger.warning("Batch-Datei ist kein dict, resette")
            return {}
        cleaned = {}
        for project, batch in data.items():
            if not isinstance(project, str) or not isinstance(batch, dict):
                logger.warning("Ueberspringe ungueltige Batch-Entry: %s", project)
                continue
            commits = batch.get('commits', [])
            if not isinstance(commits, list):
                logger.warning("Ungueltige Commits fuer %s, resette zu []", project)
                commits = []
            cleaned[project] = {
                'commits': commits,
                'first_added': batch.get('first_added', datetime.now(timezone.utc).isoformat()),
                'last_added': batch.get('last_added', datetime.now(timezone.utc).isoformat()),
            }
        return cleaned

    def _load_last_releases(self) -> Dict[str, str]:
        """Lade letzte Release-Zeitpunkte aus der Batch-Datei."""
        release_file = self.data_dir / 'last_releases.json'
        if not release_file.exists():
            return {}
        try:
            with open(release_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_last_releases(self) -> None:
        """Speichere letzte Release-Zeitpunkte (atomic via temp-file + rename)."""
        release_file = self.data_dir / 'last_releases.json'
        try:
            # Backup vor Ueberschreiben
            try:
                if release_file.exists():
                    shutil.copy2(str(release_file),
                                 str(release_file.with_suffix('.backup')))
            except Exception as e:
                logger.warning("Release-Backup fehlgeschlagen: %s", e)
            fd, tmp_path = tempfile.mkstemp(
                dir=self.data_dir, suffix='.tmp', prefix='.releases_'
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self._last_releases, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, release_file)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception as e:
            logger.error(f"Fehler beim Speichern der Release-Zeitpunkte: {e}")

    def _record_release(self, project: str) -> None:
        """Speichere den Zeitpunkt eines Releases."""
        self._last_releases[project] = datetime.now(timezone.utc).isoformat()
        self._save_last_releases()

    def _is_in_cooldown(self, project: str) -> bool:
        """Prüfe ob ein Projekt im Release-Cooldown ist (letzter Release < N Stunden)."""
        last_release = self._last_releases.get(project)
        if not last_release:
            return False
        try:
            last_dt = datetime.fromisoformat(last_release)
            elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            return elapsed_hours < self.release_cooldown_hours
        except Exception:
            return False

    def _save_pending(self) -> None:
        """Speichere ausstehende Batches auf Disk (atomic via temp-file + rename)."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            # Backup vor Ueberschreiben
            try:
                if self.batch_file.exists():
                    shutil.copy2(str(self.batch_file),
                                 str(self.batch_file.with_suffix('.backup')))
            except Exception as e:
                logger.warning("Batch-Backup fehlgeschlagen: %s", e)
            fd, tmp_path = tempfile.mkstemp(
                dir=self.data_dir, suffix='.tmp', prefix='.batch_'
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self.pending, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self.batch_file)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
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

        # Notbremse: nur wenn genug Commits UND kein Cooldown aktiv
        if total >= self.emergency_threshold and not self._is_in_cooldown(project):
            ready = True
        else:
            ready = False
            if total >= self.emergency_threshold:
                logger.info(
                    f"⏳ {project}: {total} Commits (≥{self.emergency_threshold}), "
                    f"aber Cooldown aktiv — Release wird beim nächsten Cron gemacht"
                )

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

        # Cooldown setzen — verhindert mehrere Releases am selben Tag
        self._record_release(project)

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
