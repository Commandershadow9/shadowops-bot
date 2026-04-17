#!/bin/bash
# weekly_review_check.sh — Wrapper um weekly_review_check.py.
#
# Wöchentlicher Gesundheits-Check der Multi-Agent Review Pipeline.
# Empfehlung: Freitags nach Feierabend oder Montag morgens — ~5min.
#
# Zeigt: Pipeline-Throughput, Reviews pro Agent, Revert-Rate, Queue-Health,
# Jules-Limits, pending manual merges. Farbige Ampel-Warnings.
#
# Exit-Code 0 wenn alles im grünen Bereich, 1 wenn Warnings.
#
# Usage:
#   cd /home/cmdshadow/shadowops-bot
#   scripts/weekly_review_check.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

[ -f ".venv/bin/python" ] || { echo "✗ .venv fehlt"; exit 1; }

PYTHONPATH=src .venv/bin/python scripts/weekly_review_check.py "$@"
