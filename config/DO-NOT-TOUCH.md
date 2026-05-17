# DO-NOT-TOUCH — kritische Pfade und Dateien

> Diese Datei listet alles auf, was KI-Tools (Claude Code, Codex, Routine-Worker) **niemals automatisch verändern** dürfen.
> Sie wird von `.routines/prompts/guardian.md`, `.routines/prompts/cleanup.md` und `.github/PULL_REQUEST_TEMPLATE.md` referenziert.
> Bei Verstoss: PR ablehnen, Issue mit `status:needs-info` öffnen.

## 1. Secrets und Runtime-State (NIEMALS committen oder editieren)

| Pfad | Warum tabu |
|------|-----------|
| `config/config.yaml` | Enthält Discord-Token, GitHub-Token, API-Keys, DB-Passwörter |
| `config/config.yaml.bak.*` | Backups von Live-Configs mit Secrets |
| `.env` / `.env.*` | Environment-Variablen mit Credentials |
| `data/` | Runtime-Daten (JSON-State, SQLite-Caches, AI-Outputs) |
| `logs/` | Bot-Logs (kann Stack-Traces mit Secrets enthalten) |
| `~/.secrets-passphrase` | AES-Schluessel fuer Backup-Verschluesselung |

**Editieren ist nur erlaubt:**
- `config/config.example.yaml` (Template ohne Secrets — darf gepflegt werden)
- `config/safe_upgrades.yaml` (Versions-Whitelist — darf gepflegt werden)
- `config/logrotate.conf` (Rotation-Regeln — darf gepflegt werden)

## 2. Production-Services (kein KI-Restart, keine systemd-Aenderungen)

| Service / Datei | Anweisung |
|-----------------|-----------|
| `shadowops-bot.service` (systemd) | Niemals via Worker-PR aendern — Server-State, nicht Repo-State |
| `deploy/shadowops-bot.service` | Aenderungen nur mit Maintainer-Approval und Restart-Plan |
| `scripts/restart.sh` | Production-Restart-Pfad — Aenderungen nur reviewt |
| Branch-Protection `main` | Niemals `enforce_admins: true` (sperrt Solo-Dev aus) |

## 3. Sicherheits-/Auto-Remediation-Pfade (Vorfaelle in der Vergangenheit)

| Pfad | Schutz-Grund |
|------|--------------|
| `src/integrations/security_engine/scan_agent.py` — `_JULES_DELEGATABLE_CATEGORIES`, `_JULES_KNOWN_PROJECTS`, `PROTECTED_PORT_BINDINGS` | 4-stufige Safety vor Auto-Delegation — keine Stufe entfernen |
| `src/integrations/github_integration/event_handlers_mixin.py:72-81` | Hardcoded Block gegen Direct-Push-Auto-Deploy (PR #135 / Finding #131) |
| `src/integrations/fixers/walg_fixer.py` — `self.checksums` | SHA256 von WAL-G Releases — niemals ohne Verifikation aendern |
| `src/patch_notes/stages/validate.py` — `check_feature_count()`, `check_design_doc_leaks()`, `strip_ai_version()` | Anti-Halluzinations-Gates der Patch-Notes-Pipeline — nicht deaktivieren |
| `src/patch_notes/templates/base.py` — `_CLASSIFICATION_RULES_DE/EN` | DE+EN-Regeln, MUESSEN immer angehaengt sein |

## 4. Datenbank-Migrationen (immer mit Maintainer-Approval)

| Datenbank | Tabu |
|-----------|------|
| `security_analyst` (PostgreSQL Port 5432, Container) | DROP TABLE / ALTER auf `fix_attempts_v2`, `fix_verifications`, `finding_quality`, `scan_coverage`, `remediation_status`, `jules_pr_reviews` |
| `agent_learning` (PostgreSQL Port 5432) | DROP TABLE / ALTER auf `agent_knowledge`, `prompt_variants`, Learning-Tables |
| `seo_agent` (PostgreSQL Port 5433) | DROP TABLE / ALTER auf `seo_fix_impact` und Agent-Tabellen |

**Migrationen sind erlaubt:** ueber strukturierte Schema-Files (`*.sql` mit Klartext-Diff), nie als adhoc-Query.

## 5. Cross-Service Konsumenten (Vorfaelle 2026-03-17 und 2026-03-18)

Aenderungen an Shared-Services in dieser Liste muessen ALLE Konsumenten pruefen:

- **Redis-Auth-Passwort** → `~/agents/scripts/seo-audit-cron.sh`, SEO Agent `config.yaml`
- **Port-Bindings 172.17.0.1** → NICHT auf `127.0.0.1` aendern (Container koennen Host sonst nicht erreichen)
- **PostgreSQL-User** → ZERODOX, GuildScout, SEO Agent
- **Traefik Routing** → alle hinter Traefik liegenden Domains
- **UFW SSH-Port 47822** → nur VPN-Range `10.8.0.0/24` (Brute-Force-Vorfaelle)

Checkliste vor Auth-Aenderungen:
```bash
grep -r "redis-cli\|redis://\|5433\|6379" ~/agents/ ~/shadowops-bot/scripts/
```

## 6. Was DARF angefasst werden (Klarheit fuer KI-Tools)

- Code in `src/` ausserhalb der oben gelisteten Schutz-Stellen
- Tests in `tests/`
- Doku (`README.md`, `CLAUDE.md`, `docs/`, `.claude/rules/`)
- Templates (`.github/PULL_REQUEST_TEMPLATE.md`, `.github/workflows/*.yml`)
- Patch-Notes-Templates in `src/patch_notes/templates/`
- Worker-Prompts in `.routines/prompts/*.md`

## Aenderungs-Protokoll

| Datum | Wer | Was |
|-------|-----|-----|
| 2026-05-17 | Claude (Cleanup-Session) | Initial-Anlage als Antwort auf Issue #225 |
