---
name: test
description: Tests ausfuehren (einzeln wegen OOM-Gefahr auf 8 GB VPS)
---

# Tests ausfuehren

## WICHTIG
- NIEMALS alle Tests gleichzeitig (`pytest tests/`)
- IMMER mit `-x` Flag (stoppt bei erstem Fehler)
- Tests einzeln oder nach Modul ausfuehren

## Schritte

1. **Einzelner Test:**
   ```bash
   cd /home/cmdshadow/shadowops-bot
   .venv/bin/pytest tests/unit/test_MODULNAME.py -x -v
   ```

2. **Alle Unit-Tests nacheinander:**
   ```bash
   cd /home/cmdshadow/shadowops-bot
   for f in tests/unit/test_*.py; do
     echo "=== $f ==="
     .venv/bin/pytest "$f" -x -v || break
   done
   ```

3. **Integration-Tests:**
   ```bash
   .venv/bin/pytest tests/integration/ -x -v
   ```

4. **Mit Coverage:**
   ```bash
   .venv/bin/pytest tests/unit/test_MODULNAME.py -x --cov=src --cov-report=term-missing
   ```
