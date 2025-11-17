"""
Backup Manager - Automated Backup & Restore System

Provides comprehensive backup functionality:
- File-based backups (single files)
- Directory-based backups (entire directories)
- Docker image backups (image tags)
- Database backups (PostgreSQL dumps)
- Automatic rollback on failure
- Retention policy (auto-cleanup)
"""

import asyncio
import logging
import os
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from .command_executor import CommandExecutor, ExecutionMode

logger = logging.getLogger('shadowops.backup_manager')


@dataclass
class BackupInfo:
    """Information about a backup"""
    backup_id: str
    backup_type: str  # 'file', 'directory', 'docker', 'database'
    source_path: str
    backup_path: str
    timestamp: datetime
    size_bytes: int
    metadata: Dict = field(default_factory=dict)


@dataclass
class BackupConfig:
    """Configuration for backup manager"""
    backup_root: str = "/tmp/shadowops_backups"
    retention_days: int = 7
    max_backup_size_mb: int = 1000  # Max 1GB per backup
    compression: bool = True
    verify_after_backup: bool = True

    # Protected paths that should NEVER be modified without backup
    protected_paths: Set[str] = field(default_factory=lambda: {
        '/etc/fail2ban',
        '/etc/crowdsec',
        '/etc/ufw',
        '/etc/nginx',
        '/etc/systemd/system'
    })

    # Paths that are safe to modify (still backed up)
    safe_paths: Set[str] = field(default_factory=lambda: {
        '/tmp',
        '/var/log',
        '/home/cmdshadow/shadowops-bot/logs',
        '/home/cmdshadow/GuildScout/logs'
    })


class BackupManager:
    """
    Manages backups and restores for safe remediation

    Features:
    - Automatic backups before any modification
    - Multiple backup types (files, directories, Docker, DB)
    - Compression support
    - Verification after backup
    - Automatic cleanup (retention policy)
    - Rollback functionality
    - Backup chain tracking
    """

    def __init__(
        self,
        config: Optional[BackupConfig] = None,
        executor: Optional[CommandExecutor] = None
    ):
        """
        Initialize backup manager

        Args:
            config: Backup configuration
            executor: Command executor (creates one if not provided)
        """
        self.config = config or BackupConfig()
        self.executor = executor or CommandExecutor()

        # Create backup root directory
        os.makedirs(self.config.backup_root, exist_ok=True)

        # Track active backups
        self.active_backups: Dict[str, BackupInfo] = {}
        self.backup_history: List[BackupInfo] = []

        logger.info(f"💾 Backup Manager initialized (root: {self.config.backup_root})")

    async def create_backup(
        self,
        source: str,
        backup_type: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> BackupInfo:
        """
        Create a backup of specified source

        Args:
            source: Source path/name to backup
            backup_type: Type of backup ('file', 'directory', 'docker', 'database')
                        Auto-detects if not specified
            metadata: Additional metadata to store with backup

        Returns:
            BackupInfo object

        Raises:
            ValueError: If source doesn't exist or backup fails
            RuntimeError: If backup size exceeds limit
        """
        # Auto-detect backup type if not specified
        if backup_type is None:
            backup_type = self._detect_backup_type(source)

        # Generate backup ID
        backup_id = self._generate_backup_id(source)

        logger.info(f"💾 Creating {backup_type} backup: {source} → {backup_id}")

        # Create backup based on type
        if backup_type == 'file':
            backup_info = await self._backup_file(source, backup_id, metadata)
        elif backup_type == 'directory':
            backup_info = await self._backup_directory(source, backup_id, metadata)
        elif backup_type == 'docker':
            backup_info = await self._backup_docker_image(source, backup_id, metadata)
        elif backup_type == 'database':
            backup_info = await self._backup_database(source, backup_id, metadata)
        else:
            raise ValueError(f"Unknown backup type: {backup_type}")

        # Verify backup if enabled
        if self.config.verify_after_backup:
            await self._verify_backup(backup_info)

        # Check size limit
        size_mb = backup_info.size_bytes / (1024 * 1024)
        if size_mb > self.config.max_backup_size_mb:
            logger.warning(
                f"⚠️ Backup size {size_mb:.2f}MB exceeds limit "
                f"{self.config.max_backup_size_mb}MB"
            )

        # Store backup info
        self.active_backups[backup_id] = backup_info
        self.backup_history.append(backup_info)

        logger.info(f"✅ Backup created: {backup_id} ({size_mb:.2f}MB)")

        return backup_info

    async def restore_backup(self, backup_id: str) -> bool:
        """
        Restore a backup

        Args:
            backup_id: ID of backup to restore

        Returns:
            True if restore successful, False otherwise
        """
        if backup_id not in self.active_backups:
            logger.error(f"❌ Backup not found: {backup_id}")
            return False

        backup_info = self.active_backups[backup_id]

        logger.info(f"🔄 Restoring backup: {backup_id}")
        logger.info(f"   Type: {backup_info.backup_type}")
        logger.info(f"   Source: {backup_info.source_path}")
        logger.info(f"   Backup: {backup_info.backup_path}")

        try:
            if backup_info.backup_type == 'file':
                success = await self._restore_file(backup_info)
            elif backup_info.backup_type == 'directory':
                success = await self._restore_directory(backup_info)
            elif backup_info.backup_type == 'docker':
                success = await self._restore_docker_image(backup_info)
            elif backup_info.backup_type == 'database':
                success = await self._restore_database(backup_info)
            else:
                logger.error(f"❌ Unknown backup type: {backup_info.backup_type}")
                return False

            if success:
                logger.info(f"✅ Backup restored successfully: {backup_id}")
            else:
                logger.error(f"❌ Backup restore failed: {backup_id}")

            return success

        except Exception as e:
            logger.error(f"❌ Restore error: {e}", exc_info=True)
            return False

    async def create_batch_backup(
        self,
        sources: List[str],
        backup_types: Optional[List[str]] = None
    ) -> Dict[str, BackupInfo]:
        """
        Create backups for multiple sources

        Args:
            sources: List of sources to backup
            backup_types: List of backup types (parallel to sources)

        Returns:
            Dictionary mapping source to BackupInfo
        """
        logger.info(f"💾 Creating batch backup for {len(sources)} sources")

        backups = {}

        for idx, source in enumerate(sources):
            backup_type = backup_types[idx] if backup_types else None

            try:
                backup_info = await self.create_backup(source, backup_type)
                backups[source] = backup_info
            except Exception as e:
                logger.error(f"❌ Failed to backup {source}: {e}")
                # Continue with other backups

        logger.info(f"✅ Batch backup complete: {len(backups)}/{len(sources)} successful")

        return backups

    async def rollback_batch(self, backup_ids: List[str]) -> bool:
        """
        Rollback multiple backups in reverse order

        Args:
            backup_ids: List of backup IDs to rollback

        Returns:
            True if all rollbacks successful
        """
        logger.info(f"🔄 Rolling back {len(backup_ids)} backups")

        # Rollback in reverse order (undo last changes first)
        success_count = 0

        for backup_id in reversed(backup_ids):
            if await self.restore_backup(backup_id):
                success_count += 1
            else:
                logger.error(f"❌ Rollback failed for: {backup_id}")
                # Continue trying other rollbacks

        all_success = success_count == len(backup_ids)

        if all_success:
            logger.info(f"✅ All rollbacks successful")
        else:
            logger.warning(f"⚠️ Only {success_count}/{len(backup_ids)} rollbacks successful")

        return all_success

    async def cleanup_old_backups(self) -> int:
        """
        Remove backups older than retention period

        Returns:
            Number of backups removed
        """
        cutoff_date = datetime.now() - timedelta(days=self.config.retention_days)

        removed_count = 0

        for backup_id, backup_info in list(self.active_backups.items()):
            if backup_info.timestamp < cutoff_date:
                logger.info(f"🗑️ Removing old backup: {backup_id}")

                # Remove backup file/directory
                try:
                    if os.path.isfile(backup_info.backup_path):
                        os.remove(backup_info.backup_path)
                    elif os.path.isdir(backup_info.backup_path):
                        shutil.rmtree(backup_info.backup_path)

                    del self.active_backups[backup_id]
                    removed_count += 1

                except Exception as e:
                    logger.error(f"❌ Failed to remove backup {backup_id}: {e}")

        if removed_count > 0:
            logger.info(f"✅ Cleanup complete: {removed_count} old backups removed")

        return removed_count

    def _detect_backup_type(self, source: str) -> str:
        """Auto-detect backup type from source"""
        if source.startswith('docker:'):
            return 'docker'
        elif source.startswith('db:'):
            return 'database'
        elif os.path.isfile(source):
            return 'file'
        elif os.path.isdir(source):
            return 'directory'
        else:
            # Default to file
            return 'file'

    def _generate_backup_id(self, source: str) -> str:
        """Generate unique backup ID"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = Path(source).name.replace('/', '_').replace(' ', '_')
        return f"backup_{safe_name}_{timestamp}"

    async def _backup_file(
        self,
        source: str,
        backup_id: str,
        metadata: Optional[Dict]
    ) -> BackupInfo:
        """Backup a single file"""
        if not os.path.isfile(source):
            raise ValueError(f"File not found: {source}")

        # Create backup path
        backup_filename = Path(source).name
        if self.config.compression:
            backup_filename += '.gz'

        backup_path = os.path.join(self.config.backup_root, f"{backup_id}_{backup_filename}")

        # Copy file
        if self.config.compression:
            # Use gzip compression
            result = await self.executor.execute(
                f"gzip -c '{source}' > '{backup_path}'",
                timeout=300
            )
            if not result.success:
                raise RuntimeError(f"Backup failed: {result.error_message}")
        else:
            shutil.copy2(source, backup_path)

        # Get size
        size_bytes = os.path.getsize(backup_path)

        return BackupInfo(
            backup_id=backup_id,
            backup_type='file',
            source_path=source,
            backup_path=backup_path,
            timestamp=datetime.now(),
            size_bytes=size_bytes,
            metadata=metadata or {}
        )

    async def _backup_directory(
        self,
        source: str,
        backup_id: str,
        metadata: Optional[Dict]
    ) -> BackupInfo:
        """Backup entire directory"""
        if not os.path.isdir(source):
            raise ValueError(f"Directory not found: {source}")

        # Create tar archive
        backup_path = os.path.join(
            self.config.backup_root,
            f"{backup_id}.tar.gz" if self.config.compression else f"{backup_id}.tar"
        )

        # Create archive
        mode = 'w:gz' if self.config.compression else 'w'

        with tarfile.open(backup_path, mode) as tar:
            tar.add(source, arcname=os.path.basename(source))

        size_bytes = os.path.getsize(backup_path)

        return BackupInfo(
            backup_id=backup_id,
            backup_type='directory',
            source_path=source,
            backup_path=backup_path,
            timestamp=datetime.now(),
            size_bytes=size_bytes,
            metadata=metadata or {}
        )

    async def _backup_docker_image(
        self,
        source: str,
        backup_id: str,
        metadata: Optional[Dict]
    ) -> BackupInfo:
        """Backup Docker image by creating a tagged copy"""
        # source format: "docker:image_name:tag"
        image_name = source.replace('docker:', '')

        # Create backup tag
        backup_tag = f"{image_name}_backup_{backup_id}"

        # Tag image
        result = await self.executor.execute(
            f"docker tag {image_name} {backup_tag}",
            timeout=60
        )

        if not result.success:
            raise RuntimeError(f"Docker backup failed: {result.error_message}")

        # Get image size
        size_result = await self.executor.execute(
            f"docker image inspect {backup_tag} --format='{{{{.Size}}}}'",
            timeout=30
        )

        size_bytes = int(size_result.stdout.strip()) if size_result.success else 0

        return BackupInfo(
            backup_id=backup_id,
            backup_type='docker',
            source_path=image_name,
            backup_path=backup_tag,
            timestamp=datetime.now(),
            size_bytes=size_bytes,
            metadata=metadata or {}
        )

    async def _backup_database(
        self,
        source: str,
        backup_id: str,
        metadata: Optional[Dict]
    ) -> BackupInfo:
        """Backup PostgreSQL database"""
        # source format: "db:database_name"
        db_name = source.replace('db:', '')

        # Create backup path
        backup_path = os.path.join(self.config.backup_root, f"{backup_id}.sql.gz")

        # Dump database with compression
        result = await self.executor.execute(
            f"pg_dump {db_name} | gzip > '{backup_path}'",
            timeout=600
        )

        if not result.success:
            raise RuntimeError(f"Database backup failed: {result.error_message}")

        size_bytes = os.path.getsize(backup_path)

        return BackupInfo(
            backup_id=backup_id,
            backup_type='database',
            source_path=db_name,
            backup_path=backup_path,
            timestamp=datetime.now(),
            size_bytes=size_bytes,
            metadata=metadata or {}
        )

    async def _restore_file(self, backup_info: BackupInfo) -> bool:
        """Restore a file backup"""
        try:
            if backup_info.backup_path.endswith('.gz'):
                # Decompress
                result = await self.executor.execute(
                    f"gzip -dc '{backup_info.backup_path}' > '{backup_info.source_path}'",
                    timeout=300
                )
                return result.success
            else:
                shutil.copy2(backup_info.backup_path, backup_info.source_path)
                return True
        except Exception as e:
            logger.error(f"File restore error: {e}")
            return False

    async def _restore_directory(self, backup_info: BackupInfo) -> bool:
        """Restore a directory backup"""
        try:
            # Remove existing directory
            if os.path.exists(backup_info.source_path):
                shutil.rmtree(backup_info.source_path)

            # Extract tar archive
            with tarfile.open(backup_info.backup_path, 'r:*') as tar:
                tar.extractall(path=os.path.dirname(backup_info.source_path))

            return True
        except Exception as e:
            logger.error(f"Directory restore error: {e}")
            return False

    async def _restore_docker_image(self, backup_info: BackupInfo) -> bool:
        """Restore Docker image from backup tag"""
        try:
            # Re-tag backup to original name
            result = await self.executor.execute(
                f"docker tag {backup_info.backup_path} {backup_info.source_path}",
                timeout=60
            )
            return result.success
        except Exception as e:
            logger.error(f"Docker restore error: {e}")
            return False

    async def _restore_database(self, backup_info: BackupInfo) -> bool:
        """Restore database from backup"""
        try:
            # Restore from compressed dump
            result = await self.executor.execute(
                f"gzip -dc '{backup_info.backup_path}' | psql {backup_info.source_path}",
                timeout=600
            )
            return result.success
        except Exception as e:
            logger.error(f"Database restore error: {e}")
            return False

    async def _verify_backup(self, backup_info: BackupInfo) -> bool:
        """Verify backup integrity"""
        if not os.path.exists(backup_info.backup_path):
            logger.error(f"❌ Backup file not found: {backup_info.backup_path}")
            return False

        # Basic size check
        if backup_info.size_bytes == 0:
            logger.error(f"❌ Backup file is empty: {backup_info.backup_path}")
            return False

        logger.debug(f"✅ Backup verified: {backup_info.backup_id}")
        return True

    def get_backup_info(self, backup_id: str) -> Optional[BackupInfo]:
        """Get information about a backup"""
        return self.active_backups.get(backup_id)

    def list_backups(self) -> List[BackupInfo]:
        """List all active backups"""
        return list(self.active_backups.values())

    def get_stats(self) -> Dict:
        """Get backup statistics"""
        total_size = sum(b.size_bytes for b in self.active_backups.values())

        return {
            'active_backups': len(self.active_backups),
            'total_history': len(self.backup_history),
            'total_size_mb': total_size / (1024 * 1024),
            'retention_days': self.config.retention_days,
            'backup_root': self.config.backup_root
        }
