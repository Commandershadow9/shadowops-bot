# 🔧 Update Guide: AI Service Fix (v2.0.0 → v2.0.1)

## Problem
Nach dem v2.0.0 Update schlägt die KI-Analyse mit folgendem Fehler fehl:
```
❌ KI-Analyse fehlgeschlagen
Keine Fix-Strategie konnte generiert werden.
```

**Root Cause:** HTTP client conflict - httpx 0.28+ ist inkompatibel mit OpenAI/Anthropic clients.

---

## ✅ Lösung: Server Update

### Schritt 1: Bot stoppen
```bash
pkill -f "venv/bin/python3.*bot.py"
```

### Schritt 2: Code pullen
```bash
cd /home/cmdshadow/shadowops-bot
git pull origin main
```

### Schritt 3: Dependencies aktualisieren
```bash
# httpx downgraden auf kompatible Version
venv/bin/pip install 'httpx<0.28' --force-reinstall

# Oder komplett neu installieren
venv/bin/pip install -r requirements.txt --force-reinstall
```

### Schritt 4: Verify Fix
```bash
venv/bin/python3 -c "
import anthropic
import openai

try:
    anthropic.AsyncAnthropic(api_key='test')
    openai.AsyncOpenAI(api_key='test')
    print('✅ AI clients working!')
except Exception as e:
    print(f'❌ Still broken: {e}')
"
```

### Schritt 5: Bot neu starten
```bash
cd /home/cmdshadow/shadowops-bot
nohup venv/bin/python3 src/bot.py > /tmp/shadowops-bot.log 2>&1 &
```

### Schritt 6: Logs checken
```bash
tail -f logs/shadowops_$(date +%Y%m%d).log | grep -E "KI-Analyse|AI Service|Confidence"
```

---

## ✅ Erwartetes Ergebnis

### Vorher (v2.0.0 - Broken):
```
❌ KI-Analyse fehlgeschlagen
📊 Progress: ▰▰▰▰▰▰▰▰▰▰ 100%
💭 KI-Reasoning: Keine Fix-Strategie konnte generiert werden.
```

### Nachher (v2.0.1 - Fixed):
```
🤖 KI-Analyse läuft...
⏳ Status: 🧠 KI analysiert Sicherheitslücke...
📊 Progress: ▰▰▰▰▰▱▱▱▱▱ 50%
💭 KI-Reasoning: Claude/GPT untersucht CVEs, Packages, Risiken...

✅ Fix-Strategie entwickelt
📊 Progress: ▰▰▰▰▰▰▰▰▰▰ 100%
💭 KI-Reasoning:
**Confidence:** 92%
**Beschreibung:** Update vulnerable packages to latest stable versions
**Steps:** 5 Schritte geplant
```

---

## 🎯 Was jetzt funktioniert

✅ **Echte AI-Analyse** statt Fallback:
- OpenAI GPT-4o und Anthropic Claude analysieren Security Events
- CVE-Research, Package-Analyse, Risk-Assessment

✅ **Realistische Confidence-Scores:**
- 85-95% bei klaren Fixes (Package Updates, bekannte CVEs)
- 70-85% bei komplexen Situationen (File Integrity, unbekannte Patterns)
- <70% bei unsicheren Fixes (manuelle Intervention empfohlen)

✅ **Live-Status-Updates:**
- Echtzeit-Progress-Bar während Analyse
- KI-Reasoning sichtbar ("Analysiere CVE-Details...", "Entwickle Fix-Plan...")
- User sieht WAS die KI gerade macht

✅ **Intelligente Approval-Requests:**
- Detaillierte Event-Info + AI-Analyse
- Konkrete Fix-Steps mit Commands
- Risk-Assessment & Rollback-Plan

---

## 📝 Notes

- **httpx Version:** Jetzt permanent auf `<0.28` gepinnt in requirements.txt
- **Kompatibilität:** Getestet mit discord.py 2.3.2, openai 1.54.0, anthropic 0.39.0
- **Keine Breaking Changes:** Code-Änderungen sind rein dependency-related

---

## 🐛 Troubleshooting

### Problem: "AsyncClient.__init__() got an unexpected keyword argument 'proxies'"
**Lösung:** httpx ist noch auf 0.28+
```bash
venv/bin/pip install 'httpx==0.27.2' --force-reinstall
```

### Problem: Bot startet nicht
**Lösung:** Logs checken
```bash
tail -50 logs/shadowops_$(date +%Y%m%d).log
```

### Problem: AI-Keys nicht konfiguriert
**Lösung:** Config-File prüfen
```bash
# In config/config.yaml
ai:
  openai:
    enabled: true
    api_key: "sk-..."
    model: "gpt-4o"
  anthropic:
    enabled: true
    api_key: "sk-ant-..."
    model: "claude-3-5-sonnet-20241022"
```

---

## 🚀 Ready!

Nach dem Update solltest du in Discord sehen:

1. 🚨 **Event erkannt** → Alert in Channel (wie vorher)
2. 🤖 **KI-Analyse läuft** → Live-Updates mit Progress (NEU: funktioniert!)
3. ✅ **Fix-Strategie entwickelt** → Realistischer Confidence-Score (NEU: 85-95% statt 70%)
4. 📋 **Approval-Request** → Detaillierte Info + AI-Reasoning
5. 🛡️ **User entscheidet** → Approve/Deny

**Der Bot ist jetzt vollständig funktionsfähig!** 🎉
