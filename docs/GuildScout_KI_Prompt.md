# 🤖 KI-Prompt für neuen Chat

---

Kopiere den folgenden Text in einen **neuen Chat** um mit der Entwicklung zu starten:

---

# GuildScout Bot - Discord User Ranking System

## 📋 Projektauftrag

Ich möchte einen **Discord Bot in Python** entwickeln, der User in einem Discord-Server **fair und transparent bewertet**, basierend auf:

1. **Mitgliedsdauer** (Wie lange ist der User im Server?)
2. **Aktivität** (Wie viele Nachrichten hat der User geschrieben?)

## 🎯 Hintergrund / Use Case

Ein Content Creator hat zu vielen Zuschauern Zugang zu einer Gaming-Gilde versprochen, aber es gibt weniger Plätze als erwartet. Der Bot soll objektiv ermitteln, wer die Plätze am meisten "verdient" hat, basierend auf Discord-Aktivität und Mitgliedsdauer.

## ✨ Kern-Features

### 1. Haupt-Command: User-Analyse
```
/analyze <rolle> [tage] [top_n]
```

**Funktionalität:**
- Scannt alle User mit einer bestimmten Discord-Rolle (z.B. @Zuschauer)
- Optional: Nur User berücksichtigen, die in den letzten X Tagen aktiv waren
- Optional: Zeige nur Top N User (z.B. Top 50)
- Zählt Messages pro User über alle Text-Channels
- Berechnet Score basierend auf gewichteter Formel
- Gibt Ranking aus (Discord Embed + CSV Export)

### 2. Scoring-Algorithmus

**Formel:**
```python
# Normalisiere Werte auf 0-100 Skala
days_score = (user_days_in_server / max_days_in_dataset) * 100
activity_score = (user_message_count / max_messages_in_dataset) * 100

# Gewichteter Gesamtscore
final_score = (days_score * 0.4) + (activity_score * 0.6)
```

**Gewichtung:**
- 40% = Mitgliedsdauer (Loyalität)
- 60% = Nachrichtenanzahl (Aktivität)

**Wichtig:** Gewichtung soll in `config.yaml` anpassbar sein!

### 3. Export-Formate

**A) Discord Embed (Live im Channel)**
```
📊 User-Ranking für @Zuschauer

🥇 Username#1234
   Score: 95.2 | Dabei seit: 245 Tage | Messages: 1,420

🥈 Username#5678
   Score: 87.3 | Dabei seit: 180 Tage | Messages: 1,230

[Top 25 anzeigen, Rest via CSV]
```

**B) CSV Export (zum Download)**
```csv
Rank,Username,UserID,Score,Days_in_Server,Message_Count,Join_Date
1,User#1234,123456789,95.2,245,1420,2024-03-15
2,User#5678,987654321,87.3,180,1230,2024-06-10
```

### 4. Transparenz-Feature

**User können eigenen Score abfragen:**
```
/my-score

📊 Dein Score: 82.4 von 100

Berechnung:
├─ 📅 Mitgliedsdauer: 180 Tage → Score: 73.5 (40% Gewichtung)
└─ 💬 Aktivität: 1,230 Messages → Score: 88.2 (60% Gewichtung)

Gesamtscore: (73.5 * 0.4) + (88.2 * 0.6) = 82.4
Dein Rang: Platz 12 von 342
```

## 🏗️ Technische Anforderungen

### Tech Stack
```
Sprache: Python 3.11+
Framework: discord.py 2.3+
Database: SQLite (für Message-Count Caching, Performance)
Config: YAML
Export: pandas (CSV), discord.Embed
Logging: Standard Python logging
```

### Projekt-Struktur (Vorschlag)
```
guildscout-bot/
├── src/
│   ├── bot.py                      # Main Entry Point
│   ├── commands/
│   │   ├── analyze.py              # /analyze Command
│   │   ├── my_score.py             # /my-score Command
│   │   └── admin.py                # Admin Commands
│   ├── analytics/
│   │   ├── role_scanner.py         # Scannt User mit Rolle
│   │   ├── activity_tracker.py     # Zählt Messages
│   │   ├── scorer.py               # Score-Berechnung
│   │   └── ranker.py               # Sortierung
│   ├── exporters/
│   │   ├── discord_exporter.py     # Discord Embed
│   │   └── csv_exporter.py         # CSV Generator
│   ├── database/
│   │   └── cache.py                # SQLite Cache
│   └── utils/
│       ├── config.py               # YAML Loader
│       └── logger.py               # Logging
├── config/
│   ├── config.example.yaml
│   └── config.yaml                 # gitignored
├── data/
│   └── cache.db                    # SQLite (gitignored)
├── exports/                        # CSV Exports (gitignored)
├── tests/
├── requirements.txt
├── README.md
└── .gitignore
```

### Config-Struktur (config.yaml)
```yaml
discord:
  token: "YOUR_BOT_TOKEN"
  guild_id: 123456789

scoring:
  weights:
    days_in_server: 0.4      # 40%
    message_count: 0.6       # 60%

  min_messages: 10           # User mit <10 Messages ignorieren
  max_days_lookback: 365     # Nur letzte 365 Tage

analytics:
  cache_ttl: 3600            # Cache Messages für 1h

permissions:
  admin_roles:               # Wer darf /analyze nutzen?
    - 987654321              # Admin Role ID

export:
  max_users_per_embed: 25    # Max User pro Embed

logging:
  level: "INFO"
  file: "logs/guildscout.log"
```

## 🔐 Bot Permissions (Discord)

**Minimum Required:**
- Read Messages/View Channels
- Read Message History
- Send Messages
- Embed Links
- Attach Files (für CSV)
- Use Slash Commands

**Privileged Intents:**
- Server Members Intent (zum Lesen der Member-Liste)
- Message Content Intent (zum Zählen der Messages)

## 📈 Performance-Anforderungen

**Problem:** Message-Counting ist langsam bei vielen Usern!

**Lösung:** SQLite Cache implementieren
```python
# Cache-Struktur
{
    "user_id": 123456789,
    "guild_id": 987654321,
    "message_count": 1420,
    "last_updated": "2024-11-14T10:30:00Z",
    "ttl": 3600  # Cache 1 Stunde
}
```

**Ziel:** Analyse von 300+ Usern in <30 Sekunden

## ✅ Acceptance Criteria

Das Projekt ist erfolgreich wenn:

1. **Funktionalität**
   - `/analyze <rolle>` funktioniert und gibt korrektes Ranking aus
   - Score-Berechnung ist mathematisch korrekt
   - CSV Export funktioniert
   - `/my-score` zeigt User ihren Score transparent an

2. **Performance**
   - Bot antwortet in <30 Sekunden bei 300+ Usern
   - Cache reduziert wiederholte API-Calls

3. **Code-Qualität**
   - Modular strukturiert
   - Error Handling implementiert
   - Dokumentiert (README, Docstrings)
   - Config-driven (keine hardcoded Werte)

4. **Usability**
   - Admin kann Bot ohne Code-Kenntnisse bedienen
   - Fehlermeldungen sind klar und hilfreich
   - User verstehen die Score-Berechnung

## 🚀 Development Phases

### Phase 1: MVP (Das will ich JETZT)
- Basic Bot Setup mit discord.py
- `/analyze <rolle>` Command
- Message Counting (kann noch langsam sein)
- Score-Berechnung mit fester Gewichtung (0.4 / 0.6)
- Discord Embed Output (Top 25)
- CSV Export

**Zeitaufwand:** ~4-6 Stunden

### Phase 2: Production-Ready (Nice-to-have später)
- SQLite Caching für Performance
- `/my-score` Command
- Konfigurierbare Gewichtung über YAML
- Admin Commands
- Robustes Error Handling

### Phase 3: Advanced (Optional, falls Zeit)
- Web Dashboard
- Historical Score-Tracking
- Multi-Guild Support

## 📝 Beispiel-Output (Ziel)

**Wenn Admin ausführt:**
```
/analyze @GildenInteressenten top_n:50
```

**Bot antwortet:**
```
📊 Analyse für @GildenInteressenten

🔍 Gescannt: 342 User
⏱️ Dauer: 12 Sekunden
🏆 Top 50 werden angezeigt

Scoring-Formel:
Score = (Tage * 0.4) + (Messages * 0.6)

🥇 Top 10:
1. Username#1234 - Score: 95.2 (245 Tage, 1420 Messages)
2. Username#5678 - Score: 92.8 (380 Tage, 980 Messages)
3. Username#9012 - Score: 89.4 (290 Tage, 1150 Messages)
...

💾 Komplette Liste als CSV: [guildscout_2024-11-14.csv]
```

## 💡 Wichtige Design-Entscheidungen

**Frage 1: Welche Channels zählen?**
→ **Alle Text-Channels** (außer NSFW/Voice). Admin kann später Blacklist in Config hinzufügen.

**Frage 2: Bots ausschließen?**
→ **Ja**, User mit `bot=True` Flag ignorieren.

**Frage 3: Minimal-Threshold?**
→ **Ja**, User mit <10 Messages ignorieren (konfigurierbar).

**Frage 4: Join Date vor Bot-Installation?**
→ **Verwende** `member.joined_at` (Discord speichert das).

## 🎯 Was ich von dir erwarte

**Bitte erstelle:**

1. **Initiales Projekt-Setup**
   - Git Repository initialisieren
   - Projekt-Struktur erstellen
   - `requirements.txt` mit Dependencies
   - `.gitignore` (Token, Cache, Exports)
   - `config.example.yaml`
   - Basis `README.md`

2. **Phase 1 MVP Code**
   - Vollständig funktionsfähiger Bot
   - `/analyze` Command implementiert
   - Score-Berechnung korrekt
   - Discord Embed Output
   - CSV Export
   - Error Handling für häufigste Fehler

3. **Dokumentation**
   - README mit Installation & Setup
   - Code-Kommentare bei komplexer Logik
   - Beispiel-Config

4. **Testing-Anleitung**
   - Wie teste ich den Bot lokal?
   - Welche Test-Schritte sind wichtig?

## 📌 Wichtige Hinweise

- **Code-Stil:** Clean, modular, PEP 8 konform
- **Error Handling:** Bot soll nicht crashen bei fehlenden Permissions
- **Logging:** Wichtige Events loggen (Start, Analyze-Command, Errors)
- **Config:** Alle wichtigen Werte in YAML (keine Magic Numbers im Code)
- **Git:** Sinnvolle Commits mit klaren Messages

## 🚀 Los geht's!

Bitte **starte mit dem Projekt-Setup** und entwickle dann **Phase 1 MVP**.

Wenn du Fragen hast oder Design-Entscheidungen treffen musst, frage mich!

Ich freue mich auf den Bot! 🎉
