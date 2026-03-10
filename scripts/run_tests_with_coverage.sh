#!/bin/bash
# ╔═══════════════════════════════════════════════════════════════╗
# ║ Test Runner mit Coverage — Ergebnisse für Patch Notes Stats  ║
# ║                                                               ║
# ║ Schreibt Ergebnisse nach data/test_results.json              ║
# ║ ACHTUNG: Tests einzeln ausführen (8 GB VPS, OOM-Gefahr!)     ║
# ╚═══════════════════════════════════════════════════════════════╝

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_FILE="$PROJECT_DIR/data/test_results.json"
VENV_DIR="$PROJECT_DIR/.venv"

# Virtuelle Umgebung aktivieren
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
fi

cd "$PROJECT_DIR"

echo "🧪 Starte Tests mit Coverage..."
echo "   Projekt: $PROJECT_DIR"
echo "   Output:  $RESULTS_FILE"
echo ""

# Variablen
PASSED=0
FAILED=0
ERRORS=0
TOTAL=0
COVERAGE=0
TEST_FILES=()

# Test-Dateien finden
while IFS= read -r -d '' file; do
    TEST_FILES+=("$file")
done < <(find tests/ -name "test_*.py" -type f -print0 2>/dev/null | sort -z)

TOTAL_FILES=${#TEST_FILES[@]}

if [ "$TOTAL_FILES" -eq 0 ]; then
    echo "⚠️  Keine Test-Dateien gefunden."
    cat > "$RESULTS_FILE" << 'ENDJSON'
{
  "tests_passed": 0,
  "tests_failed": 0,
  "tests_errors": 0,
  "tests_total": 0,
  "coverage_percent": null,
  "timestamp": null,
  "status": "no_tests"
}
ENDJSON
    exit 0
fi

echo "📋 ${TOTAL_FILES} Test-Dateien gefunden"
echo "───────────────────────────────────"

# Tests einzeln ausführen (OOM-Schutz)
for test_file in "${TEST_FILES[@]}"; do
    echo -n "  ▸ $(basename "$test_file")... "

    # pytest mit JUnit XML output (leise, einzeln)
    if python -m pytest "$test_file" -x -q --tb=no 2>/dev/null; then
        echo "✅"
        PASSED=$((PASSED + 1))
    else
        echo "❌"
        FAILED=$((FAILED + 1))
    fi

    TOTAL=$((TOTAL + 1))
done

echo "───────────────────────────────────"
echo "📊 Ergebnis: $PASSED/$TOTAL bestanden, $FAILED fehlgeschlagen"

# Coverage (optional, nur wenn pytest-cov installiert)
if python -c "import pytest_cov" 2>/dev/null; then
    echo ""
    echo "📈 Berechne Coverage..."

    # Coverage über alle Tests (leise)
    COV_OUTPUT=$(python -m pytest tests/ -x -q --tb=no \
        --cov=src --cov-report=term-missing --cov-report=json:"$PROJECT_DIR/data/coverage.json" \
        2>&1 || true)

    # Coverage-Prozent aus JSON extrahieren
    if [ -f "$PROJECT_DIR/data/coverage.json" ]; then
        COVERAGE=$(python3 -c "
import json
with open('$PROJECT_DIR/data/coverage.json') as f:
    data = json.load(f)
print(f\"{data.get('totals', {}).get('percent_covered', 0):.1f}\")
" 2>/dev/null || echo "0")
        echo "   Coverage: ${COVERAGE}%"
    fi
else
    echo ""
    echo "ℹ️  pytest-cov nicht installiert — Coverage übersprungen"
    echo "   Install: pip install pytest-cov"
    COVERAGE="null"
fi

# Ergebnisse als JSON speichern
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$RESULTS_FILE" << ENDJSON
{
  "tests_passed": $PASSED,
  "tests_failed": $FAILED,
  "tests_errors": $ERRORS,
  "tests_total": $TOTAL,
  "coverage_percent": $COVERAGE,
  "timestamp": "$TIMESTAMP",
  "status": "$([ "$FAILED" -eq 0 ] && echo 'passed' || echo 'failed')"
}
ENDJSON

echo ""
echo "✅ Ergebnisse gespeichert: $RESULTS_FILE"
