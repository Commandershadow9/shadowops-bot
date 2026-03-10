"""
Git state tracking methods for GitHubIntegration.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger('shadowops')


class StateMixin:

    def _get_guild_id(self) -> int:
        """Resolve guild id from config in a safe way."""
        try:
            return int(getattr(self.config, 'guild_id'))
        except Exception:
            pass

        if isinstance(self.config, dict):
            return int(self.config.get('discord', {}).get('guild_id', 0))

        discord_cfg = getattr(self.config, 'discord', {}) or {}
        return int(discord_cfg.get('guild_id', 0))

    def _get_git_state(self) -> Dict[str, str]:
        guild_id = self._get_guild_id()
        state = self.state_manager.get_value(guild_id, 'git_push_state', {})
        return state if isinstance(state, dict) else {}

    def _set_git_state(self, state: Dict[str, str]) -> None:
        guild_id = self._get_guild_id()
        self.state_manager.set_value(guild_id, 'git_push_state', state)

    def _git_state_key(self, repo_name: str, branch: str) -> str:
        normalized = self._normalize_repo_name(repo_name)
        return f"{normalized}:{branch}"

    def _get_last_processed_commit(self, repo_name: str, branch: str) -> Optional[str]:
        state = self._get_git_state()
        primary_key = self._git_state_key(repo_name, branch)
        if primary_key in state:
            return state.get(primary_key)

        # Backward-compat keys (case variants before normalization)
        legacy_keys = {
            f"{repo_name}:{branch}",
            f"{repo_name.lower()}:{branch}",
            f"{repo_name.upper()}:{branch}",
        }
        for key in legacy_keys:
            if key in state:
                return state.get(key)
        return None

    def _set_last_processed_commit(self, repo_name: str, branch: str, commit_sha: str) -> None:
        state = self._get_git_state()
        state[self._git_state_key(repo_name, branch)] = commit_sha
        self._set_git_state(state)

    def _is_duplicate_push(self, repo_name: str, branch: str, commit_sha: str) -> bool:
        return self._get_last_processed_commit(repo_name, branch) == commit_sha

    def _commit_key(self, repo_name: str, branch: str, commit_sha: str) -> str:
        normalized = self._normalize_repo_name(repo_name)
        return f"{normalized}:{branch}:{commit_sha}"

    def _cleanup_inflight(self) -> None:
        if not self._inflight_commits:
            return
        now = datetime.now(timezone.utc).timestamp()
        expired = [
            key for key, ts in self._inflight_commits.items()
            if now - ts > self.dedupe_ttl_seconds
        ]
        for key in expired:
            self._inflight_commits.pop(key, None)

    def _is_commit_inflight(self, repo_name: str, branch: str, commit_sha: str) -> bool:
        self._cleanup_inflight()
        key = self._commit_key(repo_name, branch, commit_sha)
        return key in self._inflight_commits

    def _mark_commit_inflight(self, repo_name: str, branch: str, commit_sha: str) -> None:
        self._cleanup_inflight()
        key = self._commit_key(repo_name, branch, commit_sha)
        self._inflight_commits[key] = datetime.now(timezone.utc).timestamp()

    def _unmark_commit_inflight(self, repo_name: str, branch: str, commit_sha: str) -> None:
        key = self._commit_key(repo_name, branch, commit_sha)
        self._inflight_commits.pop(key, None)

    def _reserve_commit_processing(self, repo_name: str, branch: str, commit_sha: str) -> bool:
        normalized = self._normalize_repo_name(repo_name)
        if self._is_duplicate_push(normalized, branch, commit_sha):
            return False
        if self._is_commit_inflight(normalized, branch, commit_sha):
            return False
        self._mark_commit_inflight(normalized, branch, commit_sha)
        return True
