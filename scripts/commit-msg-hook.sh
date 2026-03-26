#!/usr/bin/env bash
# ============================================================================
# Conventional Commit Hook — Enterprise-Level Commit-Message-Validierung
# ============================================================================
#
# Installiert als .git/hooks/commit-msg in jedem Projekt.
# Erzwingt Conventional Commits und warnt bei fehlenden Details.
#
# Deployment:
#   ./scripts/deploy-commit-hook.sh [projekt-pfad]
#   ./scripts/deploy-commit-hook.sh --all
#
# Erlaubte Prefixe:
#   feat, fix, docs, chore, refactor, test, perf, ci, build, style, revert, improve
#
# Format:
#   <type>[(<scope>)][!]: <beschreibung>
#
#   feat(auth): Discord als Login-Methode
#   fix!: Breaking Change in API
#   docs: README aktualisiert
#
# ============================================================================

COMMIT_MSG_FILE="$1"
COMMIT_MSG=$(cat "$COMMIT_MSG_FILE")
FIRST_LINE=$(head -1 "$COMMIT_MSG_FILE")

# Farben
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

# --- Merge-Commits und Reverts immer erlauben ---
if echo "$FIRST_LINE" | grep -qE "^(Merge |Revert )"; then
    exit 0
fi

# --- Conventional Commit Prefix pruefen ---
VALID_TYPES="feat|fix|docs|doc|chore|refactor|test|perf|ci|build|style|revert|improve|update"
if ! echo "$FIRST_LINE" | grep -qE "^($VALID_TYPES)(\([a-zA-Z0-9._-]+\))?!?:"; then
    echo -e "${RED}❌ Commit-Message muss mit einem Conventional Commit Prefix beginnen:${NC}"
    echo ""
    echo "  Erlaubt: feat, fix, docs, chore, refactor, test, perf, ci, build, style, revert, improve"
    echo ""
    echo "  Beispiele:"
    echo "    feat(auth): Discord als Login-Methode"
    echo "    fix: Crash bei leerem Input behoben"
    echo "    docs: README aktualisiert"
    echo "    feat!: Breaking Change in API"
    echo ""
    echo "  Deine Message: $FIRST_LINE"
    exit 1
fi

# --- Beschreibung nach dem Prefix pruefen ---
DESCRIPTION=$(echo "$FIRST_LINE" | sed -E "s/^($VALID_TYPES)(\([^)]*\))?!?:\s*//")
if [ ${#DESCRIPTION} -lt 5 ]; then
    echo -e "${RED}❌ Commit-Beschreibung zu kurz (mindestens 5 Zeichen nach dem Prefix)${NC}"
    echo "  Deine Message: $FIRST_LINE"
    exit 1
fi

if [ ${#FIRST_LINE} -gt 120 ]; then
    echo -e "${YELLOW}⚠️  Erste Zeile ist laenger als 120 Zeichen (${#FIRST_LINE}). Bitte kuerzen.${NC}"
    # Nur Warnung, kein Abbruch
fi

# --- Bei feat: ohne Body warnen (kein Abbruch) ---
TYPE=$(echo "$FIRST_LINE" | grep -oE "^[a-z]+")
BODY_LINES=$(tail -n +3 "$COMMIT_MSG_FILE" | grep -v "^$" | grep -v "^Co-Authored-By:" | grep -v "^Signed-off-by:" | wc -l)

if [ "$TYPE" = "feat" ] && [ "$BODY_LINES" -lt 1 ]; then
    echo -e "${YELLOW}💡 Tipp: feat:-Commits profitieren von einem Body mit Details:${NC}"
    echo ""
    echo "  feat(auth): Discord als Login-Methode"
    echo ""
    echo "  Kunden koennen sich jetzt ueber Discord einloggen."
    echo "  OAuth2-Flow mit postMessage-Sicherheit."
    echo ""
    # Kein exit 1 — nur Hinweis
fi

echo -e "${GREEN}✅ Commit-Message OK: $FIRST_LINE${NC}"
exit 0
