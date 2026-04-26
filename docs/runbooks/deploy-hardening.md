# Deploy-Hardening: post_deploy_command via deploy.sh

## Wann zu lesen

- Beim Hinzufügen einer neuen Site zur ShadowOps-Bot Auto-Deploy-Pipeline
- Beim Review der `config/config.yaml` `<site>.deploy.post_deploy_command`-Werte
- Nach einem ungewollten Production-Outage durch Auto-Deploy

## Das Pattern

**Falsch (gefährlich):**
```yaml
post_deploy_command: cd /path/to/project && docker compose build && docker compose up -d
```

**Richtig (mit Defense-in-Depth):**
```yaml
post_deploy_command: bash /path/to/project/scripts/deploy.sh --skip-e2e
```

## Warum

Der `post_deploy_command` läuft bei JEDEM `main`-Push, ohne weitere Prüfung.
Bei direktem `docker compose build/up` werden ALLE Schutzmechanismen umgangen:

| Schicht | direktes docker compose | bash deploy.sh |
|---------|------------------------|----------------|
| Pre-Flight CI-Status-Check | ❌ | ✅ |
| Lint (npm run lint) | ❌ | ✅ |
| Unit-Tests | ❌ | ✅ |
| E2E-Tests | ❌ | ⚠️ optional via flag |
| Health-Check | ❌ | ✅ |
| CSP/Smoke-Test | ❌ | ✅ |
| Auto-Rollback bei Failure | ❌ | ✅ |
| Discord-Deploy-Notify mit Status | ⚠️ generisch | ✅ detailliert |

## Lehre aus dem ZERODOX-CSP-Outage 2026-04-13/14

**Was passiert ist:**

1. SEO-Agent generierte Auto-Fix mit syntaktisch kaputtem Code (typografische
   Anführungszeichen in `web/src/lib/blog-data.ts`)
2. Auto-Merge ohne CI-Check landete den Bug auf `main`
3. ShadowOps-Bot Webhook triggerte `post_deploy_command`
4. Direkter `docker compose build` baute ein kaputtes Image
5. `docker compose up -d` ersetzte den Container
6. **Niemand merkte den Fehler**, weil keine Pipeline-Schritte den Build-Fehler
   gemeldet haben (Lint hätte ihn sofort gefangen)
7. Ein **separater** CSP-Bug auf `/onboarding` blieb dadurch 11 Tage unbemerkt
   im Onboarding-Funnel — keine Buchungen möglich

**Mit `bash scripts/deploy.sh --skip-e2e`:**

Schritt 1 (Lint) hätte den Quote-Bug sofort gefangen → Exit 1 → Discord-Alert
"Deploy fehlgeschlagen: Lint". Container blieb auf altem Image. User hätte den
Bug in Minuten statt Tagen behoben. Der CSP-Bug auf `/onboarding` wäre durch
Schritt 7 (CSP-Smoke) erkannt worden → Auto-Rollback in <60s.

## Konfiguration für eine neue Site

Wenn du eine neue Site mit Auto-Deploy einrichtest:

1. **Im Site-Repo:** Ein `scripts/deploy.sh`-Skript anlegen mit:
   - Pre-Flight CI-Status-Check (`gh api repos/.../commits/<sha>/check-runs`)
   - Build-Schritte
   - Health-Check
   - Smoke-Tests passend zur Site
   - Auto-Rollback-Logik
   - Discord-Notify
   - Flags: `--skip-e2e`, `--skip-tests`, `--no-cache`

2. **In `config/config.yaml`:**
   ```yaml
   <site_name>:
     deploy:
       enabled: true
       branch: main
       post_deploy_command: bash /path/to/site/scripts/deploy.sh --skip-e2e
   ```

3. **Test:** Manuell via `bash scripts/deploy.sh --skip-e2e` ausführen, um
   sicherzugehen dass das Skript clean durchläuft, BEVOR der Webhook live geht.

## Flags-Übersicht

| Flag | Zweck | Wann nutzen |
|------|-------|-------------|
| `--skip-e2e` | Playwright + Test-DB überspringen | Auto-Deploy (zu langsam für Webhook) |
| `--skip-tests` | Lint+Unit+E2E ALLE überspringen | NUR Hotfix-Notfall, manuell mit Bedacht |
| `--no-cache` | Docker ohne Layer-Cache | Wenn Cache-Probleme vermutet |
| (kein Flag) | Volle Pipeline | Manueller Deploy mit Zeit |

## Warum nicht `--skip-tests` standardmäßig?

`--skip-tests` würde sogar Lint überspringen. Beim ZERODOX-Outage wäre das
fatal gewesen — Lint hätte den Quote-Bug gefangen, ohne Lint wäre der
Bug-Container weiter gebaut worden.

`--skip-e2e` ist OK weil:
- Lint + Unit (~9s) bleiben aktiv
- CSP-Smoke + Auto-Rollback (~5s) bleiben aktiv
- E2E-Tests dauern 5-10 Min und blockieren Webhook-Latenz

## Verwandt

- `runbooks/multi-agent-review.md` — Pre-Merge-Checks für PRs
- `operations/customer-server-setup.md` — Initial-Setup für neue Sites
- ZERODOX `docs/SECURITY_CSP.md` — Defense-in-Depth-Strategie

---

**Erstellt:** 2026-04-26 nach dem ZERODOX-CSP-Outage als Lessons-Learned-Doku.
