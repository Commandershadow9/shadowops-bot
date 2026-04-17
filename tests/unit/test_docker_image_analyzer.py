import unittest
from unittest.mock import patch, MagicMock
import os
import yaml
from src.integrations.docker_image_analyzer import DockerImageAnalyzer

class TestDockerImageAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = DockerImageAnalyzer()

    def test_safe_upgrades_traefik_is_implemented(self):
        # Check if 'traefik' is correctly loaded from safe_upgrades.yaml
        upgrade_info = self.analyzer.check_major_version_upgrade('traefik', '3.0')
        self.assertIsNotNone(upgrade_info)
        self.assertEqual(upgrade_info['recommended_version'], '3.3')
        self.assertEqual(upgrade_info['risk_level'], 'critical')

    def test_safe_upgrades_traefik_v3_exact_tag(self):
        # Check if 'v3' tag for traefik is correctly loaded
        upgrade_info = self.analyzer.check_major_version_upgrade('traefik', 'v3')
        self.assertIsNotNone(upgrade_info)
        self.assertEqual(upgrade_info['recommended_version'], 'v3')

    def test_safe_upgrades_postgres_exists(self):
        # Confirming current hardcoded behavior
        upgrade_info = self.analyzer.check_major_version_upgrade('postgres', '15')
        self.assertIsNotNone(upgrade_info)
        self.assertEqual(upgrade_info['recommended_version'], '16')

if __name__ == '__main__':
    unittest.main()
