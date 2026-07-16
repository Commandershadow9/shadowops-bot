#!/usr/bin/env bash
# Validiert, dass GuildScout und ZERODOX separate S3-Credentials verwenden.
# Ausführen nach IAM-Credential-Rotation auf Hetzner Object Storage.
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

GUILDSCOUT_ENV="${GUILDSCOUT_ENV:-/home/cmdshadow/GuildScout/.env}"
ZERODOX_ENV="${ZERODOX_ENV:-/home/cmdshadow/ZERODOX/.env}"

ok=0
warn=0
fail=0

check() {
    local label="$1" result="$2"
    if [ "$result" = "PASS" ]; then
        echo -e "  ${GREEN}✓${NC} $label"
        ((ok++))
    elif [ "$result" = "WARN" ]; then
        echo -e "  ${YELLOW}⚠${NC} $label"
        ((warn++))
    else
        echo -e "  ${RED}✗${NC} $label"
        ((fail++))
    fi
}

echo "╔══════════════════════════════════════════════════╗"
echo "║  S3 Credential-Isolation — Validierung          ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# 1. Prüfe ob beide .env existieren
for f in "$GUILDSCOUT_ENV" "$ZERODOX_ENV"; do
    if [ -f "$f" ]; then
        check ".env vorhanden: $f" "PASS"
    else
        check ".env nicht gefunden: $f" "FAIL"
    fi
done

# 2. Extrahiere Credentials
GS_KEY=$(grep "^AWS_ACCESS_KEY_ID=" "$GUILDSCOUT_ENV" 2>/dev/null | cut -d= -f2-)
ZD_KEY=$(grep "^AWS_ACCESS_KEY_ID=" "$ZERODOX_ENV" 2>/dev/null | cut -d= -f2-)
GS_SECRET=$(grep "^AWS_SECRET_ACCESS_KEY=" "$GUILDSCOUT_ENV" 2>/dev/null | cut -d= -f2-)
ZD_SECRET=$(grep "^AWS_SECRET_ACCESS_KEY=" "$ZERODOX_ENV" 2>/dev/null | cut -d= -f2-)
GS_BUCKET=$(grep "^BACKUP_S3_BUCKET=" "$GUILDSCOUT_ENV" 2>/dev/null | cut -d= -f2-)
ZD_BUCKET=$(grep "^BACKUP_S3_BUCKET=" "$ZERODOX_ENV" 2>/dev/null | cut -d= -f2-)

# 3. Prüfe Isolation
if [ -n "$GS_KEY" ] && [ -n "$ZD_KEY" ]; then
    if [ "$GS_KEY" = "$ZD_KEY" ]; then
        check "AWS_ACCESS_KEY_ID ist identisch — Least-Privilege verletzt!" "FAIL"
    else
        check "AWS_ACCESS_KEY_ID ist getrennt" "PASS"
    fi
else
    check "AWS_ACCESS_KEY_ID fehlt in mindestens einer .env" "FAIL"
fi

if [ -n "$GS_SECRET" ] && [ -n "$ZD_SECRET" ]; then
    if [ "$GS_SECRET" = "$ZD_SECRET" ]; then
        check "AWS_SECRET_ACCESS_KEY ist identisch — Least-Privilege verletzt!" "FAIL"
    else
        check "AWS_SECRET_ACCESS_KEY ist getrennt" "PASS"
    fi
else
    check "AWS_SECRET_ACCESS_KEY fehlt in mindestens einer .env" "FAIL"
fi

if [ -n "$GS_BUCKET" ] && [ -n "$ZD_BUCKET" ]; then
    if [ "$GS_BUCKET" = "$ZD_BUCKET" ]; then
        check "BACKUP_S3_BUCKET ist identisch — getrennte Buckets empfohlen" "WARN"
    else
        check "BACKUP_S3_BUCKET ist getrennt" "PASS"
    fi
fi

# 4. Funktionstest: S3-Zugriff mit jeweiligen Credentials
echo ""
echo "Funktionstest (S3-Zugriff):"
for project in "GuildScout" "ZERODOX"; do
    if [ "$project" = "GuildScout" ]; then
        env_file="$GUILDSCOUT_ENV"
    else
        env_file="$ZERODOX_ENV"
    fi

    key=$(grep "^AWS_ACCESS_KEY_ID=" "$env_file" 2>/dev/null | cut -d= -f2-)
    secret=$(grep "^AWS_SECRET_ACCESS_KEY=" "$env_file" 2>/dev/null | cut -d= -f2-)
    bucket=$(grep "^BACKUP_S3_BUCKET=" "$env_file" 2>/dev/null | cut -d= -f2-)
    endpoint=$(grep "^BACKUP_S3_ENDPOINT=" "$env_file" 2>/dev/null | cut -d= -f2-)

    if [ -n "$key" ] && [ -n "$secret" ] && [ -n "$bucket" ]; then
        endpoint_arg=""
        [ -n "$endpoint" ] && endpoint_arg="--endpoint-url https://$endpoint"
        if AWS_ACCESS_KEY_ID="$key" AWS_SECRET_ACCESS_KEY="$secret" \
           aws s3 ls "s3://$bucket/" $endpoint_arg --max-items 1 >/dev/null 2>&1; then
            check "$project: S3-Zugriff funktioniert" "PASS"
        else
            check "$project: S3-Zugriff fehlgeschlagen" "FAIL"
        fi
    else
        check "$project: Credentials unvollständig" "FAIL"
    fi
done

echo ""
echo "════════════════════════════════════════════════════"
echo -e "Ergebnis: ${GREEN}$ok OK${NC}, ${YELLOW}$warn Warnungen${NC}, ${RED}$fail Fehler${NC}"

if [ "$fail" -gt 0 ]; then
    echo ""
    echo "HANDLUNGSBEDARF:"
    echo "  1. Hetzner Object Storage Console öffnen"
    echo "  2. Separaten IAM-Sub-User für GuildScout erstellen"
    echo "  3. Bucket-Policy: GuildScout-User nur auf Prefix 'guildscout/' zugreifen lassen"
    echo "  4. Neue Credentials in GuildScout/.env eintragen"
    echo "  5. Dieses Skript erneut ausführen"
    echo "  6. Alte geteilte Credentials rotieren/deaktivieren"
    exit 1
fi
