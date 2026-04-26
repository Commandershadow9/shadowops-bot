<!--
  shadowops-bot — Solo-Dev mit hohem KI-Anteil.
  Routine-Worker oeffnen PRs mit Label `status:routine-generated`.
-->

## Was wurde geaendert

## Vorher / Nachher

## Type
- [ ] `fix:`
- [ ] `refactor:`
- [ ] `perf:`
- [ ] `style:`
- [ ] `docs:`
- [ ] `chore:`
- [ ] `feat:` (BREAKING markieren falls API geaendert)

## Risk-Score
- [ ] **Low** — isolierte Aenderung, Tests gruen, keine API-Aenderung
- [ ] **Medium** — beruehrt mehrere Module oder kritisches Modul (ai_engine, orchestrator, knowledge_base, deployment_manager)
- [ ] **High** — DB-Schema, Auth, systemd, Config-Format

## Review-Fokus

## Checkliste
- [ ] CI gruen (pytest + ruff + mypy)
- [ ] Diff <= ~200 Zeilen oder klar gefasste logische Einheit
- [ ] Keine neuen Secrets im Code (alle via Env-Vars)
- [ ] DO-NOT-TOUCH-Liste respektiert (`config/DO-NOT-TOUCH.md`)
- [ ] Tests vorhanden (oder begruendet warum nicht)
- [ ] Doku angepasst (README / CLAUDE.md / docs/)
- [ ] Bei Routine-PR: State-File geupdated (Path im PR-Body)
- [ ] Confidence-Score >= 85% wenn AI-generierter Fix in production-relevantem Modul

## Linked Issues
<!-- Closes #123 -->
