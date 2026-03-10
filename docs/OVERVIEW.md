# 📚 ShadowOps Dokumentations-Übersicht

## 🎯 Haupt-Dokumentation

### **START HIER** 👈
- **[README.md](./README.md)** - Projekt-Übersicht und Quick Start
- **[ACTIVE_SECURITY_GUARDIAN.md](./ACTIVE_SECURITY_GUARDIAN.md)** - **HAUPTDOKU** für v3.0 Implementation
  - Vollständige Architektur
  - Alle Komponenten im Detail
  - Workflow-Beispiele
  - Konfiguration & Troubleshooting

## 📖 Zusätzliche Dokumentation

### Setup & Installation
- **[QUICKSTART.md](./QUICKSTART.md)** - Schnellstart-Anleitung
- **[config/config.example.yaml](./config/config.example.yaml)** - Konfigurationstemplate

### Features & Systeme
- **[docs/SECURITY_ANALYST.md](./docs/SECURITY_ANALYST.md)** - **NEU:** Autonomer AI Security Engineer (v4.0)
- **[docs/AUTO_REMEDIATION.md](./docs/AUTO_REMEDIATION.md)** - Auto-Remediation System (v2.0)
  - ⚠️ Teilweise veraltet, siehe ACTIVE_SECURITY_GUARDIAN.md für v3.0
- **[HYBRID_AI_SYSTEM.md](./HYBRID_AI_SYSTEM.md)** - Hybrid AI Architektur (Ollama + Claude + OpenAI)
  - ⚠️ Veraltet — Ollama entfernt, jetzt Dual-Engine (Codex + Claude CLI)
- **[LIVE_DISCORD_UPDATES.md](./LIVE_DISCORD_UPDATES.md)** - Live Status Updates Implementation

### Entwicklung & Historie
- **[CHANGELOG.md](./CHANGELOG.md)** - Version History
- **[UPDATE_GUIDE.md](./UPDATE_GUIDE.md)** - Update-Anleitung
- **[IMPLEMENTATION_COMPLETE.md](./IMPLEMENTATION_COMPLETE.md)** - v2.0 Hybrid AI Implementation
  - ⚠️ Veraltet für v3.0, siehe ACTIVE_SECURITY_GUARDIAN.md

### Multi-Server & Spezial-Setup
- **[docs/MULTI-SERVER-SETUP.md](./docs/MULTI-SERVER-SETUP.md)** - Multi-Server Deployment

## 🗂️ Kontext-Dateien (RAG System)

### System Kontext
- **[context/system/infrastructure.md](./context/system/infrastructure.md)** - DO-NOT-TOUCH Regeln, System-Infos

### Projekt-Kontexte
- **[context/projects/shadowops-bot.md](./context/projects/shadowops-bot.md)** - Dieser Bot
- **[context/projects/guildscout.md](./context/projects/guildscout.md)** - GuildScout Bot
- **[context/projects/sicherheitstool.md](./context/projects/sicherheitstool.md)** - Production Security Tool

## 🎓 Lern-Reihenfolge (Empfohlen)

1. **README.md** - Überblick verschaffen
2. **ACTIVE_SECURITY_GUARDIAN.md** - Vollständige v3.0 Architektur verstehen
3. **config/config.example.yaml** - Konfiguration anpassen
4. **QUICKSTART.md** - Bot starten
5. **Bei Bedarf**: Spezifische Dokumentation (Hybrid AI, Multi-Server, etc.)

## ⚠️ Veraltete Dokumentation

Diese Dokumente beschreiben frühere Versionen und sind teilweise überholt:
- IMPLEMENTATION_COMPLETE.md (v2.0) → Siehe ACTIVE_SECURITY_GUARDIAN.md für v3.0
- docs/AUTO_REMEDIATION.md (v2.0) → Siehe ACTIVE_SECURITY_GUARDIAN.md für vollständige Fixer

## 🆕 Version 4.0 Highlights

**Neu in v4.0:**
- ✅ Dual AI Engine (Codex CLI + Claude CLI mit Fallback)
- ✅ ServerAssistant (Daily Housekeeping + Weekly Report)
- ✅ **Security Analyst** — Autonomer AI Security Engineer
  - Freies Denken, nicht Checklisten
  - Autonome Claude Code Sessions (max 25 Turns)
  - Activity-basiert (startet nur wenn User idle)
  - Eigene Postgres-DB für akkumuliertes Wissen
  - Health-Snapshots mit Rollback-Erkennung
  - Discord-Briefings + GitHub Issues

Alle Details zum Security Analyst in **[docs/SECURITY_ANALYST.md](./docs/SECURITY_ANALYST.md)**!

**v3.0 (Active Security Guardian):**
- ✅ Echte Fix-Execution (keine Placeholders mehr!)
- ✅ 4 vollständige Fixer-Module (Trivy, CrowdSec, Fail2ban, AIDE)
- ✅ Backup & Rollback System
- ✅ Impact-Analyse
- ✅ Service Management
- ✅ Koordinierte Multi-Event Remediation

Alle Details in **ACTIVE_SECURITY_GUARDIAN.md**!
