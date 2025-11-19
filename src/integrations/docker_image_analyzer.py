"""
Docker Image Analyzer
Intelligent distinction between external and own Docker images with update detection.
"""

import logging
import re
import os
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger('shadowops.docker_analyzer')


@dataclass
class ImageInfo:
    """Information about a Docker image"""
    name: str
    tag: str
    full_name: str
    is_external: bool
    has_dockerfile: bool
    dockerfile_path: Optional[str] = None
    latest_version: Optional[str] = None
    update_available: bool = False
    registry: str = "docker.io"


class DockerImageAnalyzer:
    """
    Analyzes Docker images to determine:
    - External (Docker Hub) vs Own (with Dockerfile)
    - Available updates for external images
    - Smart remediation recommendations
    """

    def __init__(self, project_paths: Optional[List[str]] = None):
        """
        Initialize analyzer

        Args:
            project_paths: List of project paths to search for Dockerfiles
        """
        self.project_paths = project_paths or [
            '/home/cmdshadow/shadowops-bot',
            '/home/cmdshadow/GuildScout',
            '/home/cmdshadow/project'
        ]

        logger.info("🔍 Docker Image Analyzer initialized")

    def analyze_image(self, image_name: str) -> ImageInfo:
        """
        Analyze a Docker image to determine type and update availability

        Args:
            image_name: Docker image name (e.g., 'postgres:15' or 'shadowops-bot:latest')

        Returns:
            ImageInfo with analysis results
        """
        logger.info(f"🔍 Analyzing image: {image_name}")

        # Parse image name and tag
        name, tag = self._parse_image_name(image_name)

        # Check if this is an own image (has Dockerfile)
        dockerfile_path = self._find_dockerfile(name)
        is_external = dockerfile_path is None

        # For external images, check for updates
        latest_version = None
        update_available = False

        if is_external:
            logger.info(f"   📦 External image detected: {name}")
            latest_version = self._check_dockerhub_latest(name, tag)

            if latest_version and latest_version != tag:
                update_available = True
                logger.info(f"   ⬆️  Update available: {tag} → {latest_version}")
            else:
                logger.info(f"   ✅ Already on latest version: {tag}")
        else:
            logger.info(f"   🏠 Own image with Dockerfile: {dockerfile_path}")

        return ImageInfo(
            name=name,
            tag=tag,
            full_name=image_name,
            is_external=is_external,
            has_dockerfile=dockerfile_path is not None,
            dockerfile_path=dockerfile_path,
            latest_version=latest_version,
            update_available=update_available
        )

    def get_running_images(self) -> List[str]:
        """
        Get list of all running Docker images

        Returns:
            List of image names
        """
        try:
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Image}}'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                images = [img.strip() for img in result.stdout.strip().split('\n') if img.strip()]
                logger.info(f"📋 Found {len(images)} running images")
                return images
            else:
                logger.warning("⚠️  Could not get running images")
                return []

        except Exception as e:
            logger.error(f"❌ Error getting running images: {e}")
            return []

    def get_remediation_strategy(self, image_info: ImageInfo, vulnerability_count: int) -> Dict:
        """
        Generate smart remediation strategy based on image type

        Args:
            image_info: Image information
            vulnerability_count: Number of vulnerabilities

        Returns:
            Dict with recommended strategy
        """
        if image_info.is_external:
            if image_info.update_available:
                return {
                    'action': 'upgrade',
                    'description': f"Upgrade external image from {image_info.tag} to {image_info.latest_version}",
                    'steps': [
                        f"Pull latest image: docker pull {image_info.name}:{image_info.latest_version}",
                        f"Update docker-compose.yml or deployment config",
                        f"Restart containers with new version"
                    ],
                    'confidence': 'high',
                    'reason': f"Update available: {image_info.tag} → {image_info.latest_version}"
                }
            else:
                # NEW: Check for major version upgrades
                upgrade_info = self.check_major_version_upgrade(image_info.name, image_info.tag)

                if upgrade_info and vulnerability_count > 50:  # Only if CRITICAL situation
                    return {
                        'action': 'major_upgrade',
                        'description': f"Consider major version upgrade: {upgrade_info['current_version']} → {upgrade_info['recommended_version']}",
                        'steps': [
                            f"⚠️  MANUAL REVIEW REQUIRED",
                            f"Current: {image_info.name}:{upgrade_info['current_version']}",
                            f"Recommended: {image_info.name}:{upgrade_info['recommended_version']}",
                            f"Notes: {upgrade_info['notes']}",
                            f"Risk Level: {upgrade_info['risk_level']}",
                            f"Migration Guide: {upgrade_info['migration_url']}",
                            f"Vulnerabilities: {vulnerability_count} (justifies upgrade consideration)",
                            "Review breaking changes in upstream documentation",
                            "Test in staging environment first",
                            "Perform backup before upgrading"
                        ],
                        'confidence': 'medium',
                        'reason': f"No updates on current version, but major upgrade available. {vulnerability_count} vulnerabilities present.",
                        'requires_approval': True
                    }

                # Fallback to monitoring
                return {
                    'action': 'monitor',
                    'description': f"Monitor external image {image_info.full_name} for future updates",
                    'steps': [
                        f"No update available for {image_info.name}:{image_info.tag}",
                        f"Vulnerabilities ({vulnerability_count}) require upstream fixes",
                        f"Will check again in next scan cycle"
                    ],
                    'confidence': 'low',
                    'reason': 'Already on latest version - waiting for upstream fixes'
                }
        else:
            return {
                'action': 'rebuild',
                'description': f"Rebuild own Docker image with updated dependencies",
                'steps': [
                    f"Update dependencies in Dockerfile",
                    f"Rebuild image: docker build -t {image_info.name}:{image_info.tag} {os.path.dirname(image_info.dockerfile_path)}",
                    f"Run tests and verify",
                    f"Deploy updated image"
                ],
                'confidence': 'high',
                'reason': 'Own image with Dockerfile - can rebuild with fixes'
            }

    def _parse_image_name(self, image_name: str) -> Tuple[str, str]:
        """
        Parse Docker image name into name and tag

        Args:
            image_name: Full image name (e.g., 'postgres:15' or 'redis:7-alpine')

        Returns:
            Tuple of (name, tag)
        """
        if ':' in image_name:
            name, tag = image_name.rsplit(':', 1)
        else:
            name = image_name
            tag = 'latest'

        return name, tag

    def check_major_version_upgrade(self, image_name: str, current_tag: str) -> Optional[Dict]:
        """
        Check if major version upgrade is available and safe

        Args:
            image_name: Image name (e.g., 'postgres', 'redis')
            current_tag: Current tag (e.g., '15', '7-alpine')

        Returns:
            Dict with upgrade info or None if not recommended
        """
        # Known safe upgrade paths with migration notes
        safe_upgrades = {
            'postgres': {
                '15': {
                    'next': '16',
                    'notes': 'Requires pg_upgrade or dump/restore. Breaking changes: logical replication changes, new pg_hba.conf defaults',
                    'risk': 'medium',
                    'migration_url': 'https://www.postgresql.org/docs/16/release-16.html'
                },
                '14': {
                    'next': '15',
                    'notes': 'Minor breaking changes in config. Check SECURITY INVOKER views',
                    'risk': 'low',
                    'migration_url': 'https://www.postgresql.org/docs/15/release-15.html'
                },
                '13': {
                    'next': '14',
                    'notes': 'Mostly compatible. Check server encoding changes',
                    'risk': 'low',
                    'migration_url': 'https://www.postgresql.org/docs/14/release-14.html'
                },
            },
            'redis': {
                '7': {
                    'next': '8',
                    'notes': 'NOT YET RELEASED - Monitor redis.io for Redis 8.0',
                    'risk': 'unknown',
                    'migration_url': 'https://redis.io/'
                },
                '6': {
                    'next': '7',
                    'notes': 'Check for deprecated commands. Review ACL changes. Functions vs EVAL changes',
                    'risk': 'medium',
                    'migration_url': 'https://redis.io/docs/about/releases/'
                },
            },
            'mysql': {
                '8.0': {
                    'next': '8.4',
                    'notes': 'LTS upgrade. Check authentication plugin changes',
                    'risk': 'low',
                    'migration_url': 'https://dev.mysql.com/doc/relnotes/mysql/8.4/en/'
                },
            },
            'nginx': {
                '1.24': {
                    'next': '1.26',
                    'notes': 'Stable to stable upgrade. Review new directives',
                    'risk': 'low',
                    'migration_url': 'https://nginx.org/en/CHANGES'
                },
            },
        }

        # Extract major version from tag
        version_match = re.match(r'^(\d+)(?:\.(\d+))?', current_tag)
        if not version_match:
            return None

        current_major = version_match.group(1)
        if version_match.group(2):
            current_major += f".{version_match.group(2)}"

        # Check if we have upgrade info for this image
        if image_name not in safe_upgrades:
            return None

        if current_major not in safe_upgrades[image_name]:
            return None

        upgrade_info = safe_upgrades[image_name][current_major]

        # Preserve tag variant (e.g., -alpine, -slim)
        tag_variant = ''
        variant_match = re.search(r'-(.+)$', current_tag)
        if variant_match:
            tag_variant = f"-{variant_match.group(1)}"

        return {
            'current_version': current_tag,
            'recommended_version': f"{upgrade_info['next']}{tag_variant}",
            'upgrade_type': 'major',
            'notes': upgrade_info['notes'],
            'risk_level': upgrade_info['risk'],
            'migration_url': upgrade_info['migration_url'],
            'requires_manual_migration': True
        }

    def _find_dockerfile(self, image_name: str) -> Optional[str]:
        """
        Find Dockerfile for an image in known project paths

        Args:
            image_name: Image name (without tag)

        Returns:
            Path to Dockerfile or None if not found
        """
        # Check if image name matches a project
        for project_path in self.project_paths:
            project_name = os.path.basename(project_path).lower()

            # Check for exact match or similar name
            if project_name in image_name.lower() or image_name.lower() in project_name:
                dockerfile = os.path.join(project_path, 'Dockerfile')

                if os.path.exists(dockerfile):
                    logger.debug(f"   Found Dockerfile: {dockerfile}")
                    return dockerfile

        # Also check for Dockerfile in standard locations
        for project_path in self.project_paths:
            dockerfile = os.path.join(project_path, 'Dockerfile')
            if os.path.exists(dockerfile):
                # Read Dockerfile to check if it builds this image
                try:
                    with open(dockerfile, 'r') as f:
                        content = f.read()
                        # Check for image name in comments or labels
                        if image_name.lower() in content.lower():
                            logger.debug(f"   Found Dockerfile with reference: {dockerfile}")
                            return dockerfile
                except Exception:
                    pass

        logger.debug(f"   No Dockerfile found for: {image_name}")
        return None

    def _check_dockerhub_latest(self, image_name: str, current_tag: str) -> Optional[str]:
        """
        Check Docker Hub for latest version of an image

        Args:
            image_name: Image name (without tag)
            current_tag: Current tag

        Returns:
            Latest tag or None if check failed
        """
        try:
            # Strategy 1: Use docker pull to check if newer version exists
            # For official images like postgres, redis, check common version patterns

            # Official images often have:
            # - Major version tags (15, 7, etc.)
            # - Major.minor tags (15.3, 7.0, etc.)
            # - Variant tags (15-alpine, 7.0-alpine, etc.)

            # Extract base version from current tag
            version_match = re.match(r'(\d+)(?:\.(\d+))?(?:-(.+))?', current_tag)

            if version_match:
                major = version_match.group(1)
                minor = version_match.group(2)
                variant = version_match.group(3)

                # Try to check for newer versions using docker manifest
                # This is a heuristic approach - in production you'd use Docker Hub API

                # For now, we'll check if there's a newer major.minor version
                try:
                    # Try current version
                    result = subprocess.run(
                        ['docker', 'manifest', 'inspect', f"{image_name}:{current_tag}"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )

                    if result.returncode == 0:
                        # Image exists, try to check for newer versions
                        # This is a simplified check - for production use Docker Hub API

                        # Try checking next major version
                        next_major = int(major) + 1
                        test_tag = f"{next_major}" + (f"-{variant}" if variant else "")

                        test_result = subprocess.run(
                            ['docker', 'manifest', 'inspect', f"{image_name}:{test_tag}"],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )

                        if test_result.returncode == 0:
                            logger.info(f"   Found newer version: {test_tag}")
                            return test_tag

                        # Try checking 'latest' tag
                        latest_result = subprocess.run(
                            ['docker', 'manifest', 'inspect', f"{image_name}:latest"],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )

                        if latest_result.returncode == 0 and current_tag != 'latest':
                            # Parse manifest to compare versions
                            # For now, suggest 'latest' if different from current
                            logger.info(f"   'latest' tag available (currently on {current_tag})")
                            # Don't suggest 'latest' for version-pinned images
                            # return 'latest'

                except subprocess.TimeoutExpired:
                    logger.warning("   Docker manifest check timed out")

            logger.debug(f"   No newer version found for {image_name}:{current_tag}")
            return None

        except Exception as e:
            logger.debug(f"   Could not check Docker Hub updates: {e}")
            return None

    def analyze_trivy_scan(self, scan_file: str) -> List[Dict]:
        """
        Parse Trivy JSON scan file and extract image vulnerability details

        Args:
            scan_file: Path to Trivy JSON scan file

        Returns:
            List of dictionaries with image vulnerability info
        """
        try:
            if not os.path.exists(scan_file):
                logger.warning(f"⚠️  Scan file not found: {scan_file}")
                return []

            with open(scan_file, 'r') as f:
                data = json.load(f)

            # Trivy JSON format has Results array
            results = data.get('Results', [])

            image_vulns = []

            for result in results:
                target = result.get('Target', '')
                vulnerabilities = result.get('Vulnerabilities', [])

                if vulnerabilities:
                    critical = sum(1 for v in vulnerabilities if v.get('Severity') == 'CRITICAL')
                    high = sum(1 for v in vulnerabilities if v.get('Severity') == 'HIGH')
                    medium = sum(1 for v in vulnerabilities if v.get('Severity') == 'MEDIUM')
                    low = sum(1 for v in vulnerabilities if v.get('Severity') == 'LOW')

                    # Extract image name from target (e.g., "postgres:15 (debian 11.7)")
                    image_match = re.match(r'^([^\s(]+)', target)
                    image_name = image_match.group(1) if image_match else target

                    image_vulns.append({
                        'image': image_name,
                        'target': target,
                        'critical': critical,
                        'high': high,
                        'medium': medium,
                        'low': low,
                        'total': len(vulnerabilities)
                    })

            logger.info(f"📊 Analyzed {len(image_vulns)} images from scan file")
            return image_vulns

        except Exception as e:
            logger.error(f"❌ Error parsing Trivy scan: {e}")
            return []
