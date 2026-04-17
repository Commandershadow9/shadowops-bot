"""
Git operations (subprocess) methods for GitHubIntegration.
"""

import logging
import subprocess
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger('shadowops')


class GitOpsMixin:

    def _normalize_repo_name(self, repo_name: str) -> str:
        if not repo_name:
            return repo_name
        projects = self.config.projects if isinstance(self.config.projects, dict) else {}
        for key in projects.keys():
            if key.lower() == repo_name.lower():
                return key
        return repo_name.lower()

    def _run_git(self, repo_path: Path, args: list, timeout: int = 15) -> Optional[str]:
        try:
            result = subprocess.run(
                ['git'] + args,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            if result.returncode != 0:
                self.logger.debug(f"Git command failed: {' '.join(args)}: {result.stderr.strip()}")
                return None
            return result.stdout.strip()
        except Exception as e:
            self.logger.debug(f"Git command error: {' '.join(args)}: {e}")
            return None

    def _safe_git_fetch(self, repo_path: Path) -> bool:
        """Git fetch ausfuehren. Gibt True bei Erfolg zurueck, False bei Fehler."""
        try:
            proc = subprocess.run(
                ['git', 'fetch', '--all', '--prune'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            if proc.returncode != 0:
                self.logger.warning(
                    f"⚠️ Git fetch fehlgeschlagen fuer {repo_path}: {proc.stderr.strip()}"
                )
                return False
            return True
        except Exception as e:
            self.logger.warning(f"⚠️ Git fetch Fehler fuer {repo_path}: {e}")
            return False

    def _get_repo_branch(self, repo_path: Path, project_config: Dict) -> str:
        """Deploy-Branch aus Config nehmen, NICHT den aktuell ausgecheckten Branch.

        Vorher: git rev-parse HEAD → gab den ausgecheckten Branch zurück.
        Problem: Wenn ein Feature-Branch ausgecheckt ist (z.B. feat/referral-system),
        wurden unveröffentlichte Commits als "neue Updates" erkannt und Patchnotes
        für nicht-live Features generiert.

        Jetzt: Config-Branch (deploy.branch) hat Vorrang. Nur wenn nicht konfiguriert,
        wird der aktuelle Branch als Fallback verwendet.
        """
        deploy_branch = project_config.get('deploy', {}).get('branch')
        if deploy_branch:
            return deploy_branch
        # Fallback: aktuell ausgecheckter Branch
        branch = self._run_git(repo_path, ['rev-parse', '--abbrev-ref', 'HEAD'])
        if branch and branch != 'HEAD':
            return branch
        return 'main'

    def _get_upstream_ref(self, repo_path: Path) -> Optional[str]:
        return self._run_git(repo_path, ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])

    def _get_commit_sha(self, repo_path: Path, ref: str) -> Optional[str]:
        return self._run_git(repo_path, ['rev-parse', ref])

    def _normalize_repo_url(self, raw_url: Optional[str]) -> Optional[str]:
        if not raw_url:
            return None
        url = raw_url.strip()

        if url.startswith('git@'):
            # git@github.com:owner/repo.git
            remainder = url.split('@', 1)[1]
            if ':' in remainder:
                host, path = remainder.split(':', 1)
                url = f"https://{host}/{path}"
        elif url.startswith('ssh://git@'):
            url = url.replace('ssh://git@', 'https://')
        elif url.startswith('git://'):
            url = url.replace('git://', 'https://')

        if url.endswith('.git'):
            url = url[:-4]
        return url

    def _get_repo_url(self, repo_path: Path) -> Optional[str]:
        raw_url = self._run_git(repo_path, ['config', '--get', 'remote.origin.url'])
        return self._normalize_repo_url(raw_url)

    def _git_log_commits(self, repo_path: Path, rev_spec: str,
                         repo_url: Optional[str], max_commits: Optional[int]) -> list:
        if not rev_spec:
            return []

        format_str = "%H%x1f%an%x1f%B%x1e"
        cmd = ['log', '--no-color', f'--pretty=format:{format_str}']
        if max_commits:
            cmd.insert(1, f'-n{max_commits}')
        cmd.append(rev_spec)

        output = self._run_git(repo_path, cmd, timeout=30)
        if not output:
            return []

        commits = []
        entries = output.strip("\n\x1e").split("\x1e")
        for entry in entries:
            if not entry.strip():
                continue
            parts = entry.split("\x1f")
            if len(parts) < 3:
                continue
            commit_sha = parts[0].strip()
            author = parts[1].strip()
            message = parts[2].strip()
            commit_url = f"{repo_url}/commit/{commit_sha}" if repo_url else ""
            commits.append({
                'id': commit_sha,
                'author': {'name': author},
                'message': message or '(no message)',
                'url': commit_url
            })

        commits.reverse()
        return commits

    def _get_commits_between(self, repo_path: Path, start_sha: Optional[str],
                             end_ref: str, repo_url: Optional[str]) -> list:
        commits = []
        if start_sha:
            commits = self._git_log_commits(
                repo_path,
                f"{start_sha}..{end_ref}",
                repo_url,
                self.local_polling_max_commits
            )
        if commits:
            return commits

        fallback_limit = min(self.local_polling_max_commits, 5)
        return self._git_log_commits(
            repo_path,
            end_ref,
            repo_url,
            fallback_limit
        )
