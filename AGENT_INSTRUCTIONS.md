# Agent Instructions

## Latest updates
- 2025-11-21: Config loader now supports both attribute and dictionary access, raising `KeyError` when required fields (`discord.token`, `discord.guild_id`) are missing. Keep this behavior intact when modifying configuration handling.
- 2025-11-21: Discord channel IDs for AI learning, code fixes, orchestrator, alerts, stats, and approvals now fall back to `channels.*` values when the `auto_remediation.notifications.*` entries are absent. Preserve these fallbacks to keep Discord logging active.

## Working notes
- Keep inline code comments and docstrings in English. User-facing responses (e.g., Discord alerts or CLI output) remain in German.
- After completing major changes, update README.md (and this file when relevant) so future agents can track progress.
