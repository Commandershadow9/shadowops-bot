---
title: ADR-004: systemd statt Docker fuer Bot-Deployment
status: accepted
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

# ADR-004: systemd statt Docker fuer Bot-Deployment

**Status:** Accepted
**Datum:** 2026-03-10
**Kontext:** ShadowOps Bot muss Host-Security-Tools ausfuehren: `cscli` (CrowdSec), `fail2ban-client`, Dateisystem-Monitoring, Log-Dateien lesen, Firewall-Regeln pruefen. Diese Tools benoetigen direkten Host-Zugriff mit root-Rechten (via sudo).

## Entscheidung

Der Bot laeuft direkt auf dem Host als systemd-Service (`shadowops-bot.service`) statt in einem Docker-Container.

- **Service-Typ:** systemd user/system service mit `Restart=on-failure`
- **Logging:** stdout/stderr an journald, zusaetzlich eigene Log-Dateien mit Datums-Namen
- **Abhaengigkeiten:** Python venv auf dem Host, Codex CLI + Claude CLI installiert
- **Zugriff:** Direkter Zugriff auf Host-Dateisystem, systemd-Journale, Security-Tools

## Alternativen

- **Docker mit Volume-Mounts:** Moeglich, aber jedes neue Tool erfordert zusaetzliche Mount-Konfiguration. Log-Pfade, CrowdSec-Socket, fail2ban-Socket — die Mount-Liste waechst staendig.
- **Docker mit `--privileged`:** Gibt dem Container vollen Host-Zugriff, macht Container-Isolation sinnlos und ist ein Sicherheitsrisiko.
- **Docker mit Host-Networking:** Loest nur das Netzwerk-Problem, nicht den Dateisystem-Zugriff.

## Konsequenzen

**Positiv:**
- Einfacher, direkter Zugriff auf alle Host-Security-Tools ohne Mount-Konfiguration.
- systemd managed automatische Restarts bei Crashes (`Restart=on-failure`).
- Logs via `journalctl -u shadowops-bot` sofort verfuegbar.
- Signal-Handling (SIGTERM, SIGUSR1) funktioniert nativ mit systemd.

**Negativ:**
- Keine Container-Isolation — ein Bug im Bot hat theoretisch vollen Host-Zugriff.
- Python-venv und CLI-Tools muessen direkt auf dem Host installiert und gewartet werden.
- Kein deklaratives Deployment wie mit docker-compose — Updates erfordern manuellen Service-Restart.
- Unterscheidet sich vom Deployment-Pattern der anderen Services (GuildScout API, ZERODOX in Docker).
