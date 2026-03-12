"""
Unit Tests für Content Sanitizer — Filtert sensible Informationen aus Patch Notes.
Stellt sicher, dass IP-Adressen, Pfade, Ports und Config-Referenzen entfernt werden,
bevor Inhalte an Discord oder die Webseite gesendet werden.
"""

import pytest

from src.integrations.content_sanitizer import ContentSanitizer


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sanitizer():
    """Standard-Sanitizer mit Default-Patterns."""
    return ContentSanitizer()


# ============================================================================
# ABSOLUTE PFADE
# ============================================================================

class TestAbsolutePaths:
    def test_removes_home_paths(self, sanitizer):
        text = "Gefixt in /home/cmdshadow/GuildScout/src/auth.py"
        result = sanitizer.sanitize(text)
        assert '/home/' not in result
        assert 'cmdshadow' not in result

    def test_removes_var_paths(self, sanitizer):
        text = "Log unter /var/log/shadowops/bot.log"
        result = sanitizer.sanitize(text)
        assert '/var/' not in result

    def test_removes_etc_paths(self, sanitizer):
        text = "Config in /etc/systemd/system/shadowops.service"
        result = sanitizer.sanitize(text)
        assert '/etc/' not in result

    def test_removes_tmp_paths(self, sanitizer):
        text = "Backup in /tmp/shadowops_backups/2026-03-10"
        result = sanitizer.sanitize(text)
        assert '/tmp/' not in result


# ============================================================================
# TILDE-PFADE
# ============================================================================

class TestTildePaths:
    def test_removes_tilde_paths(self, sanitizer):
        text = "Datei unter ~/shadowops-bot/config/secrets.json"
        result = sanitizer.sanitize(text)
        assert '~/shadowops-bot' not in result

    def test_removes_tilde_guildscout_path(self, sanitizer):
        text = "Geändert in ~/GuildScout/api/main.go"
        result = sanitizer.sanitize(text)
        assert '~/GuildScout' not in result


# ============================================================================
# RELATIVE SOURCE-PFADE
# ============================================================================

class TestRelativePaths:
    def test_removes_src_integrations_path(self, sanitizer):
        text = "Fehler in src/integrations/ai_learning/agent.py behoben"
        result = sanitizer.sanitize(text)
        assert 'src/integrations' not in result

    def test_removes_tests_unit_path(self, sanitizer):
        text = "Neuer Test in tests/unit/test_ai_engine.py"
        result = sanitizer.sanitize(text)
        assert 'tests/unit' not in result

    def test_removes_config_dir_path(self, sanitizer):
        text = "Angepasst in config/safe_upgrades.yaml"
        result = sanitizer.sanitize(text)
        assert 'config/' not in result


# ============================================================================
# IP-ADRESSEN
# ============================================================================

class TestIPAddresses:
    def test_removes_vpn_ip(self, sanitizer):
        text = "Server 10.8.0.1 und 172.23.0.5 neu gestartet"
        result = sanitizer.sanitize(text)
        assert '10.8.0.1' not in result
        assert '172.23.0.5' not in result

    def test_removes_ip_with_port(self, sanitizer):
        text = "Verbindung zu 127.0.0.1:6379 hergestellt"
        result = sanitizer.sanitize(text)
        assert '127.0.0.1' not in result
        assert '6379' not in result

    def test_removes_localhost(self, sanitizer):
        text = "Verbindung zu 127.0.0.1:6379 und localhost:5433"
        result = sanitizer.sanitize(text)
        assert '127.0.0.1' not in result
        assert 'localhost' not in result

    def test_removes_localhost_without_port(self, sanitizer):
        text = "Läuft auf localhost"
        result = sanitizer.sanitize(text)
        assert 'localhost' not in result


# ============================================================================
# PORT-REFERENZEN
# ============================================================================

class TestPorts:
    def test_removes_port_references(self, sanitizer):
        text = "API läuft auf Port 5433 und Port 8766"
        result = sanitizer.sanitize(text)
        assert '5433' not in result
        assert '8766' not in result

    def test_removes_port_case_insensitive(self, sanitizer):
        text = "Gestartet auf port 3000"
        result = sanitizer.sanitize(text)
        assert '3000' not in result


# ============================================================================
# CONFIG-DATEI-REFERENZEN
# ============================================================================

class TestConfigReferences:
    def test_removes_config_yaml(self, sanitizer):
        text = "Token aus config.yaml geladen, .env aktualisiert"
        result = sanitizer.sanitize(text)
        assert 'config.yaml' not in result
        assert '.env' not in result

    def test_removes_env_production(self, sanitizer):
        text = "Werte in .env.production gesetzt"
        result = sanitizer.sanitize(text)
        assert '.env.production' not in result

    def test_removes_credentials_json(self, sanitizer):
        text = "Neue Keys in credentials.json hinterlegt"
        result = sanitizer.sanitize(text)
        assert 'credentials.json' not in result

    def test_removes_secrets_json(self, sanitizer):
        text = "Rotiert in secrets.json"
        result = sanitizer.sanitize(text)
        assert 'secrets.json' not in result


# ============================================================================
# API-ENDPUNKTE
# ============================================================================

class TestAPIEndpoints:
    def test_removes_api_endpoint(self, sanitizer):
        text = "SQL-Injection in /api/users/login gefixt"
        result = sanitizer.sanitize(text)
        assert '/api/users/login' not in result

    def test_removes_api_changelogs(self, sanitizer):
        text = "Neuer Endpunkt /api/changelogs hinzugefügt"
        result = sanitizer.sanitize(text)
        assert '/api/changelogs' not in result


# ============================================================================
# NORMALER CONTENT BLEIBT ERHALTEN
# ============================================================================

class TestPreservesContent:
    def test_preserves_normal_text(self, sanitizer):
        text = "Neues OAuth2-Feature implementiert. API-Performance um 40% verbessert."
        result = sanitizer.sanitize(text)
        assert result == text

    def test_preserves_version_numbers(self, sanitizer):
        text = "Update auf v4.0.1 abgeschlossen"
        result = sanitizer.sanitize(text)
        assert 'v4.0.1' in result

    def test_preserves_empty_string(self, sanitizer):
        result = sanitizer.sanitize("")
        assert result == ""

    def test_preserves_urls(self, sanitizer):
        text = "Docs unter https://example.com/docs"
        result = sanitizer.sanitize(text)
        assert 'https://example.com/docs' in result


# ============================================================================
# NACHBEREITUNG (CLEANUP)
# ============================================================================

class TestCleanup:
    def test_cleans_empty_bullets(self, sanitizer):
        text = "Feature A\n• \n- \nFeature B"
        result = sanitizer.sanitize(text)
        lines = result.split('\n')
        # Leere Bullet-Zeilen sollen entfernt sein
        for line in lines:
            stripped = line.strip()
            assert stripped not in ('•', '-', '╰')

    def test_collapses_multiple_spaces(self, sanitizer):
        text = "Wort    zwischen    Leerzeichen"
        result = sanitizer.sanitize(text)
        assert '    ' not in result
        # Nur einzelne Leerzeichen
        assert 'Wort zwischen Leerzeichen' == result

    def test_reduces_multiple_blank_lines(self, sanitizer):
        text = "Zeile 1\n\n\n\n\nZeile 2"
        result = sanitizer.sanitize(text)
        assert '\n\n\n' not in result
        assert 'Zeile 1' in result
        assert 'Zeile 2' in result

    def test_strips_trailing_whitespace(self, sanitizer):
        text = "  Text mit Leerzeichen   "
        result = sanitizer.sanitize(text)
        assert result == result.strip()


# ============================================================================
# CUSTOM PATTERNS
# ============================================================================

class TestCustomPatterns:
    def test_custom_pattern_applied(self):
        sanitizer = ContentSanitizer(custom_patterns=[r'GEHEIM-\d+'])
        text = "Token GEHEIM-12345 wurde rotiert"
        result = sanitizer.sanitize(text)
        assert 'GEHEIM-12345' not in result

    def test_multiple_custom_patterns(self):
        sanitizer = ContentSanitizer(custom_patterns=[
            r'SECRET_\w+',
            r'TOKEN_[A-Z0-9]+',
        ])
        text = "Nutzt SECRET_KEY und TOKEN_ABC123"
        result = sanitizer.sanitize(text)
        assert 'SECRET_KEY' not in result
        assert 'TOKEN_ABC123' not in result

    def test_custom_patterns_combined_with_defaults(self):
        sanitizer = ContentSanitizer(custom_patterns=[r'CUSTOM-\d+'])
        text = "Fix in /home/user/app und CUSTOM-999"
        result = sanitizer.sanitize(text)
        assert '/home/' not in result
        assert 'CUSTOM-999' not in result


# ============================================================================
# SANITIZE_DICT
# ============================================================================

class TestSanitizeDict:
    def test_sanitizes_string_values(self, sanitizer):
        data = {
            'title': 'Update',
            'content': 'Gefixt in /home/user/app/main.py',
        }
        result = sanitizer.sanitize_dict(data, keys=['content'])
        assert '/home/' not in result['content']
        assert result['title'] == 'Update'

    def test_sanitizes_list_values(self, sanitizer):
        data = {
            'title': 'Update',
            'content': 'Gefixt in /home/user/app/main.py',
            'highlights': ['Server 10.0.0.1 optimiert', 'Normaler Text'],
        }
        result = sanitizer.sanitize_dict(data, keys=['content', 'highlights'])
        assert '/home/' not in result['content']
        assert '10.0.0.1' not in result['highlights'][0]
        assert result['highlights'][1] == 'Normaler Text'

    def test_uses_default_keys(self, sanitizer):
        data = {
            'content': 'Pfad /home/user/secret',
            'tldr': 'Fix auf Port 5433',
            'unrelated': '/home/should/stay',
        }
        result = sanitizer.sanitize_dict(data)
        assert '/home/' not in result['content']
        assert '5433' not in result['tldr']
        # 'unrelated' ist nicht in den Default-Keys
        assert '/home/should/stay' in result['unrelated']

    def test_leaves_non_matching_keys_untouched(self, sanitizer):
        data = {
            'content': 'Gefixt in /home/user/app',
            'version': '4.0.1',
            'count': 42,
        }
        result = sanitizer.sanitize_dict(data)
        assert result['version'] == '4.0.1'
        assert result['count'] == 42

    def test_returns_copy_not_original(self, sanitizer):
        data = {'content': 'Original /home/test/path'}
        result = sanitizer.sanitize_dict(data)
        assert result is not data
        # Original bleibt unverändert
        assert '/home/test/path' in data['content']


# ============================================================================
# ENABLED/DISABLED
# ============================================================================

class TestEnabledFlag:
    def test_disabled_sanitizer_passthrough(self):
        sanitizer = ContentSanitizer(enabled=False)
        text = "/home/secret/path"
        assert sanitizer.sanitize(text) == text

    def test_disabled_sanitize_dict_passthrough(self):
        sanitizer = ContentSanitizer(enabled=False)
        data = {'content': '/home/secret/path'}
        result = sanitizer.sanitize_dict(data)
        assert result['content'] == '/home/secret/path'

    def test_enabled_by_default(self):
        sanitizer = ContentSanitizer()
        text = "/home/secret/path"
        assert sanitizer.sanitize(text) != text


# ============================================================================
# EDGE CASES
# ============================================================================

class TestEdgeCases:
    def test_mixed_sensitive_content(self, sanitizer):
        """Mehrere sensible Infos in einem Text."""
        text = (
            "Fix in /home/cmdshadow/GuildScout/src/main.go: "
            "Verbindung zu 10.8.0.1:5433 (Port 5433) via localhost:6379. "
            "Config aus config.yaml geladen. "
            "Endpoint /api/users/login abgesichert."
        )
        result = sanitizer.sanitize(text)
        assert '/home/' not in result
        assert '10.8.0.1' not in result
        assert 'localhost' not in result
        assert 'config.yaml' not in result
        assert '/api/users/login' not in result
        assert '5433' not in result

    def test_sanitize_none_text(self, sanitizer):
        """Leerer String darf keinen Fehler werfen."""
        result = sanitizer.sanitize("")
        assert result == ""

    def test_text_only_sensitive(self, sanitizer):
        """Text der nur aus sensiblen Infos besteht."""
        text = "/home/cmdshadow/app"
        result = sanitizer.sanitize(text)
        assert '/home/' not in result
