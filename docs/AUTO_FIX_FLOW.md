Auto-Fix Flow (Reaction-basiert)
================================

Aktueller Stand (Safe Mode mit Patch + lokalen Commits, optional Draft-PR)
- Vorschläge kommen aus dem Code-Learning (Coverage/Test-Lücken, Import-Zyklen, Hotspots).
- Channel: `🔎-ai-code-scans` (auto angelegt/verschoben). Emojis: ✅ umsetzen, 🧪 nur Tests/Analyse, ❌ verwerfen (Admins laut Config).
- Safe-Guards: Pfad-Whitelist (Projektpfade), dirty Git bricht ab, Patch-Size-Limit (12k), Timeout pro Kommando, keine Deploys/Merges.
- Pipeline bei ✅:
  - Neuer Branch `ai-fix/<slug>` (nur bei clean Git).
  - LLM-Patch (Unified Diff). `git apply` → Abbruch bei Fehler.
  - Tests/Lint heuristisch pro Projekt (npm test/pytest, npm run lint wenn Script; KI-Tests werden angehängt).
  - Wenn Patch angewandt + Tests grün: git commit, git push origin <branch>, Draft-PR via GitHub-Token (falls vorhanden, base=main). Diff-Stat und Testergebnisse im PR-Body.
  - Ergebnis-Embed (Branch, Patch-Status, Diff-Stat, Commit, PR-Link, Tests).
- Pipeline bei 🧪: keine Branch/Patch, nur Tests/Lint, Ergebnis-Embed.
- Persistenz: Vorschläge in `data/auto_fix_proposals.json`; Trend-Daten in `data/learning_trends.json`.
- Log-Insights alle 2h + Anomalie-Alerts; täglicher gepinnter Trend-Report in `🧠-ai-learning`.
- Research Fetcher (Allowlist): PyPI, npm, GitHub API/Raw; begrenzte Größe/Timeout; Discord-Logging aller Fetches. Kein freies Browsing.

Bekannte Limitierungen
- Tests/Lint heuristisch (profilebasiert); können danebenliegen.
- LLM-Patch kann fehlschlagen/ungenau sein; bricht bei `git apply`-Fehlern ab.
- PR-API läuft nur, wenn `GITHUB_TOKEN` gesetzt und origin=GitHub; base ist aktuell `main` (nicht autodetektiert).
- Keine Deploys, kein Merge.

ToDo / Nächste Schritte
1) Patch-Genauigkeit erhöhen:
   - Kontext verbessern (relevante Files/Logs/Fehler/Tests).
   - Patches auf betroffene Dateien einschränken; große Patches stückeln.
2) Per-Projekt Test-/Lint-Profile weiter schärfen:
   - Optional configurable via Config.
3) Artifacts/Logs:
   - Test-/Lint-Ausgaben als Files anhängen, Diff-Summary (Top N Dateien) ins Embed packen.
4) Mehr Signal in Vorschlägen:
   - Import-Zyklen/Hotspots/Low-Coverage mit Pfaden/Links.
   - Empfohlene zusätzliche Tests sichtbarer.
5) Optional: Rollback/Abbruch:
   - Bei Patch-Fehlern optional auto-reset im Branch (oder Hinweis).
6) Coverage/Quality-Gates:
   - Coverage-Schwellwerte → kein Push/PR, Hinweis ins Action Board.
7) Rate-Limits/Locks:
   - Pipeline-Locks, Drosselung bei mehreren Vorschlägen.

Hinweise zu Sicherheit
- Keine Änderungen außerhalb der Projektpfade.
- Kein Merge nach main, kein Deploy.
- Dirty Working Tree → Abbruch.
- Patch-Size begrenzt (12k Zeichen).
