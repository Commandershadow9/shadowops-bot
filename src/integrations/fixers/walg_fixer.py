"""
WAL-G Fixer - Update WAL-G to a secure version
"""
import logging
import re
import shlex
import asyncio
import os
import hashlib
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

        # Hardcoded checksums for v3.0.8 PostgreSQL assets
        self.checksums = {
            "wal-g-pg-20.04-amd64": "9a09c2b1afad6a4e7d87444b34726dd098b60ec816032af921d2f887e6e285c5",
            "wal-g-pg-22.04-amd64": "f30544c5ce93cf83b87578e3c4a2e9c0e0ffc3d160ef89ecddaf75f397d98deb",
            "wal-g-pg-20.04-aarch64": "c3dc13b90fce8fe498742143f9ae07db19a603f7f26de00f161419e163e6175f",
            "wal-g-pg-22.04-aarch64": "794d1a81f0c27825a1603bd39c0f2cf5dd8bed7cc36b598ca05d8d963c3d5fcf"
        }

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
        raw_arch = arch_res.stdout.strip()
        arch = "amd64" if "x86_64" in raw_arch else "aarch64"

        # We assume PostgreSQL use case as per finding #195
        # Try 20.04 then 22.04
        success_dl = False
        tmp_path = f"/tmp/wal-g-new"
        asset_used = ""

        for ubuntu_ver in ["20.04", "22.04"]:
            asset_name = f"wal-g-pg-{ubuntu_ver}-{arch}"
            download_url = f"https://github.com/wal-g/wal-g/releases/download/{self.target_version}/{asset_name}"

            logger.info(f"   Attempting download of {asset_name}...")
            # Using --fail to exit on HTTP errors and timeouts to prevent hanging
            dl_res = await self.executor.execute(
                f"curl -L --fail --max-time 300 --connect-timeout 15 -o {tmp_path} {download_url}"
            )

            if dl_res.success:
                # Verify checksum
                if asset_name in self.checksums:
                    expected_sha = self.checksums[asset_name]
                    actual_sha = await self._calculate_sha256(tmp_path)

                    if actual_sha == expected_sha:
                        logger.info(f"   ✅ Checksum verified for {asset_name}")
                        success_dl = True
                        asset_used = asset_name
                        break
                    else:
                        logger.warning(f"   ❌ Checksum mismatch for {asset_name}! Expected: {expected_sha}, Got: {actual_sha}")
                else:
                    logger.warning(f"   ⚠️ No checksum found for {asset_name}, skipping verification (NOT RECOMMENDED)")
                    # In a real scenario we might fail here if security is paramount
                    success_dl = True
                    asset_used = asset_name
                    break

        if not success_dl:
            return {
                'status': 'failed',
                'error': f'Download or verification failed for WAL-G {self.target_version} assets'
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
                'message': f'WAL-G successfully updated from {current_version} to {self.target_version} (Asset: {asset_used})',
                'details': {
                    'path': current_path,
                    'old_version': current_version,
                    'new_version': self.target_version,
                    'asset': asset_used
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

    async def _calculate_sha256(self, filepath: str) -> str:
        """Calculates SHA256 checksum of a file"""
        sha256_hash = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                # Read in chunks
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating checksum: {e}")
            return ""
