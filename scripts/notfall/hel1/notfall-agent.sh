#!/usr/bin/env bash
#
# notfall-agent.sh — Stufe 2 (ZERODOX #5627): headless Claude-Lauf auf HEL1, der den
# Hauptserver über den SSH-Wrapper diagnostiziert und nur Umkehrbares tut.
#
# Gestartet von notfall-waechter.sh, höchstens 1× je Vorfall. Zeitlimit 15 min.
#
# Drei unabhängige Schranken:
#   1. --tools Bash + --allowedTools "Bash(ssh notfall-haupt:*)" + --permission-mode dontAsk
#      → der Agent hat kein Read/Write/Edit/WebFetch/MCP; jeder andere Bash-Befehl wird verweigert
#   2. Er läuft als unprivilegierter Benutzer `notfall` auf HEL1 (kein sudo, kein docker)
#   3. Auf dem Hauptserver erzwingt authorized_keys `command=notfall-wrapper.sh` — die
#      eigentliche Grenze, unabhängig von 1 und 2
#
# Ausgabe: Bericht auf stdout (der Wächter schickt ihn nach ntfy + Discord).
set -uo pipefail

BASIS="${NOTFALL_BASIS:-/opt/notfall}"
ARBEIT="${NOTFALL_ARBEIT:-/var/lib/notfall/agent}"
mkdir -p "$ARBEIT"
cd "$ARBEIT" || exit 2

# Verschachtelte Sitzung verhindern (libs.md) und nur den Notfall-Token nutzen.
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT ANTHROPIC_API_KEY
# shellcheck disable=SC1091
[ -r /etc/notfall/claude.env ] && . /etc/notfall/claude.env
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "ERGEBNIS: UNKLAR"
  echo "FÜR DEN BETREIBER: Kein Claude-Token auf HEL1 (/etc/notfall/claude.env) — Agent konnte nicht starten."
  exit 3
fi
export CLAUDE_CODE_OAUTH_TOKEN

CLAUDE="${NOTFALL_CLAUDE:-$BASIS/bin/claude}"
timeout --kill-after=30 "${NOTFALL_AGENT_TIMEOUT_S:-900}" "$CLAUDE" -p "$(cat "$BASIS/notfall-prompt.md")" \
  --tools Bash \
  --allowedTools "Bash(ssh notfall-haupt:*)" \
  --permission-mode dontAsk \
  --strict-mcp-config \
  --setting-sources "" \
  --no-session-persistence \
  --output-format text 2>&1
rc=$?
[ $rc -eq 124 ] && echo "ERGEBNIS: UNKLAR — Zeitlimit 15 min erreicht"
exit $rc
