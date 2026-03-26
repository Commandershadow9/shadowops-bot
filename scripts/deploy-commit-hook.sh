#!/usr/bin/env bash
# ============================================================================
# Deploy Conventional Commit Hook auf Projekte
# ============================================================================
#
# Nutzung:
#   ./scripts/deploy-commit-hook.sh               # Nur ShadowOps Bot
#   ./scripts/deploy-commit-hook.sh ~/ZERODOX      # Bestimmtes Projekt
#   ./scripts/deploy-commit-hook.sh --all          # Alle Projekte
#
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_SOURCE="$SCRIPT_DIR/commit-msg-hook.sh"

# Alle bekannten Projekte
ALL_PROJECTS=(
    "$HOME/shadowops-bot"
    "$HOME/ZERODOX"
    "$HOME/GuildScout"
    "$HOME/agents"
    "$HOME/libs/shared-ui"
)

deploy_hook() {
    local project_path="$1"
    local hook_dest="$project_path/.git/hooks/commit-msg"

    if [ ! -d "$project_path/.git" ]; then
        echo "⚠️  Kein Git-Repo: $project_path — uebersprungen"
        return
    fi

    # Hooks-Verzeichnis erstellen falls noetig
    mkdir -p "$project_path/.git/hooks"

    # Hook kopieren (nicht symlinken — funktioniert besser mit Git)
    cp "$HOOK_SOURCE" "$hook_dest"
    chmod +x "$hook_dest"

    echo "✅ Hook installiert: $project_path"
}

if [ "${1:-}" = "--all" ]; then
    echo "🚀 Deploye Conventional Commit Hook auf alle Projekte..."
    echo ""
    for project in "${ALL_PROJECTS[@]}"; do
        deploy_hook "$project"
    done
    echo ""
    echo "Fertig! Hook auf ${#ALL_PROJECTS[@]} Projekte deployed."
elif [ -n "${1:-}" ]; then
    deploy_hook "$1"
else
    deploy_hook "$SCRIPT_DIR/.."
fi
