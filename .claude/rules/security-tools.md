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
