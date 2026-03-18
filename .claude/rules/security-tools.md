---
paths:
  - "src/integrations/fixers/**"
  - "src/integrations/event_watcher.py"
  - "src/integrations/crowdsec.py"
  - "src/integrations/fail2ban.py"
  - "src/integrations/aide.py"
  - "src/integrations/docker.py"
  - "src/integrations/docker_image_analyzer.py"
---
# Security Tools Integration

## Event-Flow
1. EventWatcher scannt periodisch (Trivy/CrowdSec/Fail2ban/AIDE)
2. Neue Events → Orchestrator (Batching: 10s Fenster, max 10 Events)
3. Orchestrator → AI-Analyse → Fix-Strategie
4. ApprovalMode entscheidet: auto/paranoid/dry-run
5. Fixer fuehrt Commands aus (mit Backup + Rollback)

## Scan-Intervalle
| Tool | Intervall | Zweck |
|------|-----------|-------|
| Trivy | 6h | Docker Image Vulnerabilities |
| CrowdSec | 30s | Realtime Threat Detection |
| Fail2ban | 15s | SSH/Auth Brute Force |
| AIDE | 15min | File Integrity Monitoring |

## Approval-Modes
| Modus | Verhalten |
|-------|-----------|
| paranoid | Jede Aktion braucht Discord-Approval (AKTUELL AKTIV) |
| auto | LOW/MEDIUM automatisch, HIGH/CRITICAL braucht Approval |
| dry-run | Nur Analyse, keine Ausfuehrung |

## Fixer-Module
| Datei | Tool | Aktionen |
|-------|------|----------|
| `trivy_fixer.py` | Trivy | Docker Image Updates, Base Image Upgrades |
| `crowdsec_fixer.py` | CrowdSec | IP-Bans, Bouncer-Config |
| `fail2ban_fixer.py` | Fail2ban | Jail-Config, Ban-Management |
| `aide_fixer.py` | AIDE | DB-Update, Whitelist-Management |

## Wichtig
- Alle Fixer brauchen `sudo` fuer Tool-Zugriff (cscli, fail2ban-client)
- Commands werden NIEMALS mit `shell=True` ausgefuehrt
- Jeder Fix erstellt Backup unter `/tmp/shadowops_backups/`
- Seen-Events Cache: `logs/seen_events.json` (verhindert Doppel-Alerts)

## GitHub Issue-Hygiene (Vorfall 2026-03-18)

> Security-Audit-Sessions haben 14 Issues erstellt, davon 9 Duplikate (5x Backup, 4x Health-Endpoints).
> `seen_events.json` verhindert Doppel-Alerts, aber NICHT doppelte GitHub-Issues.

### VOR Issue-Erstellung — PFLICHT:
1. **Duplikat-Check:** `gh issue list -R <repo> --state open --search "<Kernbegriff>"` ausfuehren
2. **Falls offenes Issue existiert:** Kommentar ergaenzen, KEIN neues Issue
3. **Konsolidierung:** Mehrere Findings zum gleichen Fix = EIN konsolidiertes Issue
   - Beispiel: "Backup umask", "Backup chmod", "Backup .gitignore" → 1 Backup-Haertungs-Issue
4. **Fix-Status pruefen:** Offene PRs durchsuchen ob Finding bereits adressiert wird
   - `gh pr list -R <repo> --state open --search "<Kernbegriff>"`
