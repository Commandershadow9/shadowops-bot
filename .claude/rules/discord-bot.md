---
paths:
  - "src/bot.py"
  - "src/cogs/**"
  - "src/utils/embeds.py"
  - "src/utils/discord_logger.py"
  - "src/utils/health_server.py"
---
# Discord Bot

## Architektur
- `bot.py` ist der Haupteinstieg — `ShadowOpsBot` erbt von `commands.Bot`
- Startup-Phasen: 1) Core → 2) Channels → 3) Auto-Remediation → 4) Services → 5) Multi-Project → 6) Server Assistant
- Signal-Handler in `setup_hook()`: SIGTERM (Shutdown), SIGUSR1 (Log-Rotation)
- `bot.run(token, log_handler=None)` — eigener Logger, nicht discord.py's

## Cogs
| Datei | Cog | Commands |
|-------|-----|----------|
| `monitoring.py` | MonitoringCog | `/status`, `/bans`, `/threats`, `/docker`, `/aide` |
| `admin.py` | AdminCog | `/scan`, `/stop-all-fixes`, `/remediation-stats`, `/set-approval-mode`, `/reload-context`, `/release-notes`, `/pending-notes`, `/mark-duplicate` |
| `inspector.py` | InspectorCog | `/get-ai-stats`, `/projekt-status`, `/alle-projekte`, `/agent-stats`, `/security-engine` |
| `customer_setup_commands.py` | CustomerSetupCommands | `/setup-customer-server` |

Alle AdminCog-Commands erfordern `administrator=True` (Discord-Berechtigung).

## Patterns
- Alle async: `await` fuer Discord API Calls
- Embeds via `EmbedBuilder` (`src/utils/embeds.py`)
- Channel-Logging via `DiscordChannelLogger` (`src/utils/discord_logger.py`)
- Health Check Server auf Port 8766 (`src/utils/health_server.py`)
- Persistent Views fuer Auto-Fix Proposals (Buttons in Discord)
- Channel IDs kommen aus `data/state.json`, NICHT hardcoded
- Guild-spezifische Slash Commands (nicht global)

## Wichtig
- Discord Rate Limits beachten — Dashboard-Updates max alle 5 Minuten
- Nachrichten > 2000 Zeichen werden automatisch gesplittet (`MessageHandler`)
- Bei Bot-Restart: Persistent Views neu registrieren in Phase 2
