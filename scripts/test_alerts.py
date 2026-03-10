#!/usr/bin/env python3
"""
Test-Script für ShadowOps Discord Alerts
Sendet Test-Nachrichten in alle Channels
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from utils.config import get_config
from utils.embeds import EmbedBuilder

def print_test_plan():
    """Gibt Test-Plan aus"""
    config = get_config()

    print("=" * 70)
    print("🧪 ShadowOps Alert Test Plan")
    print("=" * 70)
    print()
    print(f"Guild ID: {config.guild_id}")
    print()
    print("Channels:")
    print(f"  🔴 Critical:          {config.critical_channel}")
    print(f"  🛡️ Sicherheitsdienst: {config.sicherheitsdienst_channel}")
    print(f"  ⚡ NEXUS:             {config.nexus_channel}")
    print(f"  🚫 Fail2ban:          {config.fail2ban_channel}")
    print(f"  🐳 Docker:            {config.docker_channel}")
    print(f"  💾 Backups:           {config.backups_channel}")
    print()
    print("Test-Embeds:")
    print("  1. Fail2ban IP-Ban")
    print("  2. CrowdSec Threat")
    print("  3. Docker Scan (CRITICAL)")
    print("  4. Docker Scan (SUCCESS)")
    print("  5. Backup Success")
    print("  6. Backup Failed")
    print("  7. AIDE Check")
    print("  8. Status Overview")
    print()

if __name__ == "__main__":
    print_test_plan()
