"""
WAL-G Fixer - Update WAL-G to a secure version
"""
import logging
import re
import shlex
import asyncio
from typing import Dict, List, Optional
from ..command_executor import CommandExecutor

logger = logging.getLogger('shadowops.walg_fixer')

class WalGFixer:
    """
    Fixer for WAL-G vulnerabilities by updating to a secure version (v3.0.8+).
    """

    def __init__(self, executor: Optional[CommandExecutor] = None):
        self.executor = executor or CommandExecutor()
        self.target_version = "v3.0.8"
        self.default_binary_path = "/usr/local/bin/wal-g"

    async def fix(self, event: Dict, strategy: Dict) -> Dict:
        """
        Updates WAL-G binary to the target version.
        """
        logger.info("🔧 Starting WAL-G update process")

        # Get binary path from event details or use default
        current_path = self.default_binary_path
        if 'affected_files' in event and event['affected_files']:
            current_path = event['affected_files'][0]

        # Fallback if the above is still not a useful path
        if not current_path or not current_path.startswith('/'):
            current_path = self.default_binary_path

        # 1. Resolve path if it's just a command name
        which_res = await self.executor.execute(f"which {shlex.quote(current_path)} || echo {shlex.quote(current_path)}")
        current_path = which_res.stdout.strip()

        # Check if current version is already secure
        version_res = await self.executor.execute(f"{shlex.quote(current_path)} --version")
        current_version = "unknown"
        if version_res.success:
            # WAL-G version output usually looks like: wal-g version v2.0.1
            match = re.search(r'v(\d+\.\d+\.\d+)', version_res.stdout)
            if match:
                current_version = match.group(1)
            else:
                # Try without 'v'
                match = re.search(r'(\d+\.\d+\.\d+)', version_res.stdout)
                if match:
                    current_version = match.group(1)

        logger.info(f"   Current path: {current_path}, Version: {current_version}")

        # Check if update is needed
        target_ver_clean = self.target_version.lstrip('v')
        if current_version != "unknown" and self._is_version_at_least(current_version, target_ver_clean):
            logger.info(f"   WAL-G is already at version {current_version} (>= {target_ver_clean})")
            return {
                'status': 'success',
                'message': f'WAL-G is already at a secure version: {current_version}'
            }

        # 2. Prepare download
        arch_res = await self.executor.execute("uname -m")
        arch = "amd64" if "x86_64" in arch_res.stdout else "arm64"

        # We assume PostgreSQL use case as per finding #195
        asset_name = f"wal-g-pg-20.04-{arch}"
        download_url = f"https://github.com/wal-g/wal-g/releases/download/{self.target_version}/{asset_name}"
        tmp_path = f"/tmp/wal-g-new"

        logger.info(f"   Downloading secure version from {download_url}...")
        dl_res = await self.executor.execute(f"curl -L -o {tmp_path} {download_url}")

        if not dl_res.success:
            # Try 22.04 asset as fallback
            asset_name = f"wal-g-pg-22.04-{arch}"
            download_url = f"https://github.com/wal-g/wal-g/releases/download/{self.target_version}/{asset_name}"
            logger.info(f"   Retrying with {asset_name}...")
            dl_res = await self.executor.execute(f"curl -L -o {tmp_path} {download_url}")

            if not dl_res.success:
                return {
                    'status': 'failed',
                    'error': f'Download failed for both 20.04 and 22.04 assets: {dl_res.error_message}'
                }

        # 3. Replace binary safely
        backup_path = f"{current_path}.bak_security_update"
        logger.info(f"   Updating binary at {current_path} (Backup: {backup_path})")

        try:
            # Backup if it exists
            exists_res = await self.executor.execute(f"ls {shlex.quote(current_path)}")
            if exists_res.success:
                await self.executor.execute(f"sudo cp {shlex.quote(current_path)} {shlex.quote(backup_path)}")

            # Prepare new binary
            await self.executor.execute(f"chmod +x {tmp_path}")

            # Ensure target directory exists
            target_dir = os.path.dirname(current_path)
            await self.executor.execute(f"sudo mkdir -p {shlex.quote(target_dir)}")

            # Replace
            await self.executor.execute(f"sudo mv {tmp_path} {shlex.quote(current_path)}")
            # Ensure correct ownership
            await self.executor.execute(f"sudo chown root:root {shlex.quote(current_path)}")
        except Exception as e:
            logger.error(f"   Replacement error: {e}")
            return {'status': 'failed', 'error': f'Failed to replace binary: {str(e)}'}

        # 4. Verify update
        verify_res = await self.executor.execute(f"{shlex.quote(current_path)} --version")
        if verify_res.success and target_ver_clean in verify_res.stdout:
            logger.info(f"✅ WAL-G successfully updated to {self.target_version}")
            return {
                'status': 'success',
                'message': f'WAL-G successfully updated from {current_version} to {self.target_version}',
                'details': {
                    'path': current_path,
                    'old_version': current_version,
                    'new_version': self.target_version
                }
            }
        else:
            # Rollback if backup exists
            logger.warning(f"⚠️ Verification failed. Output: {verify_res.stdout}. Rolling back...")
            backup_exists = await self.executor.execute(f"ls {shlex.quote(backup_path)}")
            if backup_exists.success:
                await self.executor.execute(f"sudo mv {shlex.quote(backup_path)} {shlex.quote(current_path)}")
            return {
                'status': 'failed',
                'error': f'Post-update verification failed. Output: {verify_res.stdout}'
            }

    def _is_version_at_least(self, current: str, target: str) -> bool:
        """Simple semver comparison"""
        try:
            c_parts = [int(p) for p in current.split('.')]
            t_parts = [int(p) for p in target.split('.')]
            # Pad with zeros
            while len(c_parts) < 3: c_parts.append(0)
            while len(t_parts) < 3: t_parts.append(0)
            return c_parts >= t_parts
        except Exception:
            return False
