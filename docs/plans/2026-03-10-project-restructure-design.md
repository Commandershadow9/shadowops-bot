# ShadowOps Bot — Projekt-Restrukturierung

**Datum:** 2026-03-10
**Status:** Approved
**Ziel:** Enterprise-Level Projektstruktur nach GuildScout/ZERODOX-Muster

## Motivation

Das Projekt hat nach v4.0 (Dual-Engine AI) eine professionelle Codebase, aber die
Projektmanagement-Struktur (Docs, Rules, Memory, Root-Dateien) ist gewachsen ohne Plan.
GuildScout und ZERODOX haben bereits saubere Strukturen mit pfad-gefilterten Rules,
Skills, Agents und Topic-basierter Memory.

## Scope

1. **Root aufraumen** — Veraltete Markdown-Dateien archivieren, Mull loschen
2. **CLAUDE.md** — Projekt-Instruktionen fur AI-Assistenten
3. **.claude/rules/** — Pfad-gefilterte Regeln (6 Dateien)
4. **.claude/skills/** — Workflow-Skills (deploy, test, health-check)
5. **.claude/agents/** — Custom Subagents (reviewer, debugger, test-runner)
6. **Memory** — Topic-basierte Memory-Dateien (6 Dateien)
7. **docs/** — Reorganisation + ADRs (5 Architecture Decision Records)
8. **Cleanup** — venv/, package.json, htmlcov/, .gitignore

## Nicht im Scope

- Code-Refactoring
- Neue Features
- Test-Aenderungen
