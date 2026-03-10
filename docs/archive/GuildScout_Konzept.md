# 🎯 GuildScout Bot - Konzept & Spezifikation

## 📋 Projektziel

Ein Discord-Bot für **faire User-Bewertung** basierend auf Aktivität und Mitgliedsdauer. Ziel: Objective Entscheidungshilfe für limitierte Community-Plätze (z.B. Gaming-Gilden).

---

## 🎮 Use Case

Ein Content Creator hat zu vielen Zuschauern Gildenzugang versprochen, aber nur limitierte Plätze. Der Bot soll **fair und transparent** ermitteln, wer die Plätze am meisten "verdient" hat, basierend auf:

1. **Wie lange ist der User im Discord?** (Loyalität)
2. **Wie aktiv ist der User?** (Engagement = Message Count)

---

## ✨ Kern-Features

### 1. User-Analyse nach Rolle
```
/analyze <rolle> [tage] [top_n]
```
- Scannt alle User mit einer bestimmten Rolle
- Optional: Nur die letzten X Tage berücksichtigen
- Optional: Zeige nur Top N User

### 2. Scoring-Algorithmus

**Score-Formel:**
```python
# Normalisierte Werte (0-100)
days_score = (days_in_server / max_days) * 100
activity_score = (message_count / max_messages) * 100

# Gewichteter Gesamtscore
final_score = (days_score * 0.4) + (activity_score * 0.6)
```

**Gewichtung:**
- 40% Mitgliedsdauer (Loyalität)
- 60% Nachrichtenanzahl (Aktivität)

**Konfigurierbar:** Gewichtung soll in Config anpassbar sein

### 3. Multi-Format Export

**A) Discord Embed (Live-View)**
```
📊 User-Ranking für Rolle @Zuschauer

🥇 User#1234
   Score: 95.2 | Dabei seit: 245 Tage | Messages: 1,420

🥈 User#5678
   Score: 87.3 | Dabei seit: 180 Tage | Messages: 1,230

🥉 User#9012
   Score: 82.1 | Dabei seit: 300 Tage | Messages: 890
```

**B) CSV Export**
```csv
Rank,Username,UserID,Score,Days_in_Server,Message_Count,Join_Date
1,User#1234,123456789,95.2,245,1420,2024-03-15
2,User#5678,987654321,87.3,180,1230,2024-06-10
```

**C) Web-Dashboard (Optional Phase 2)**
- Sortierbare Tabelle
- Filter-Optionen
- Export-Button

### 4. Transparenz & Fairness

**Wichtig für User-Akzeptanz:**
- Zeige Berechnungsformel öffentlich an
- Jeder User kann seinen eigenen Score abfragen: `/my-score`
- Admin kann Gewichtung anpassen: `/set-weights <days_weight> <activity_weight>`

---

## 🏗️ Technische Architektur

### Tech Stack
```
Language: Python 3.11+
Framework: discord.py 2.3+
Database: SQLite (für Message-Count Caching)
Config: YAML
Export: pandas (CSV), discord.Embed
Optional: Flask (Web-Dashboard)
```

### Projekt-Struktur
```
guildscout-bot/
├── src/
│   ├── bot.py                      # Main Bot Entry Point
│   ├── commands/
│   │   ├── analyze.py              # /analyze Command
│   │   ├── my_score.py             # /my-score Command
│   │   └── admin.py                # Admin Commands (/set-weights)
│   ├── analytics/
│   │   ├── role_scanner.py         # Scannt User mit Rolle X
│   │   ├── activity_tracker.py     # Zählt Messages (mit Caching)
│   │   ├── scorer.py               # Score-Berechnung
│   │   └── ranker.py               # Sortierung & Ranking
│   ├── exporters/
│   │   ├── discord_exporter.py     # Discord Embed Formatter
│   │   ├── csv_exporter.py         # CSV Generator
│   │   └── web_exporter.py         # [Optional] Web Dashboard
│   ├── database/
│   │   ├── cache.py                # SQLite Cache für Message Counts
│   │   └── models.py               # Data Models
│   └── utils/
│       ├── config.py               # YAML Config Loader
│       ├── logger.py               # Logging Setup
│       └── validators.py           # Input Validation
├── config/
│   ├── config.example.yaml
│   └── config.yaml                 # gitignored
├── data/
│   └── cache.db                    # SQLite Cache (gitignored)
├── exports/
│   └── [generated CSV files]       # gitignored
├── tests/
│   ├── test_scorer.py
│   └── test_analytics.py
├── requirements.txt
├── README.md
└── .gitignore
```

---

## ⚙️ Konfiguration

### config.yaml
```yaml
discord:
  token: "YOUR_BOT_TOKEN"
  guild_id: 123456789

scoring:
  weights:
    days_in_server: 0.4      # 40% Gewichtung
    message_count: 0.6       # 60% Gewichtung

  # Optionale Limits
  max_days_lookback: 365     # Nur letzte 365 Tage zählen
  min_messages: 10           # User mit <10 Messages ignorieren

analytics:
  cache_ttl: 3600            # Cache Messages für 1h (Performance)
  batch_size: 100            # Fetch Messages in Batches

permissions:
  admin_roles:               # Wer darf /analyze nutzen?
    - 987654321              # Admin Role ID
    - 123123123              # Moderator Role ID

export:
  csv_delimiter: ","
  csv_encoding: "utf-8"
  max_users_per_embed: 25    # Max Users pro Discord Embed

logging:
  level: "INFO"              # DEBUG, INFO, WARNING, ERROR
  file: "logs/guildscout.log"
```

---

## 🔄 Workflow

### 1. Admin führt Analyse durch
```
/analyze @Zuschauer top_n:50
```

### 2. Bot verarbeitet Daten
```
[1/5] 🔍 Scanne Mitglieder mit Rolle @Zuschauer...
      Gefunden: 342 User

[2/5] 📊 Analysiere Aktivität...
      Message Counts werden gezählt...

[3/5] 🧮 Berechne Scores...
      Score-Formel: (days*0.4) + (messages*0.6)

[4/5] 🏆 Erstelle Ranking...
      Top 50 User werden sortiert...

[5/5] ✅ Fertig!
```

### 3. Bot zeigt Ergebnisse
- Discord Embed mit Top 25
- Button: "💾 Als CSV exportieren"
- Button: "📊 Komplette Liste anzeigen"

### 4. Export-Optionen
- CSV wird generiert und als Datei gesendet
- Optional: Link zu Web-Dashboard

---

## 🔐 Permissions & Security

### Bot Permissions (Minimum Required)
```
✅ Read Messages/View Channels
✅ Read Message History
✅ Send Messages
✅ Embed Links
✅ Attach Files (für CSV)
✅ Use Slash Commands
```

### Privileged Intents
```
✅ Server Members Intent    # Zum Lesen der Member-Liste
✅ Message Content Intent   # Zum Zählen der Messages
```

### Admin-Only Commands
- `/analyze` - Nur für Admin/Moderator Rollen
- `/set-weights` - Nur für Server Owner
- `/clear-cache` - Nur für Admins

### Public Commands
- `/my-score` - Jeder User kann seinen eigenen Score sehen

---

## 📈 Performance-Überlegungen

### Problem: Message-Counting ist langsam
- Discord API Limit: 50 messages per request
- Bei 1000+ Messages pro User dauert das lange

### Lösung 1: Caching (Empfohlen)
```python
# SQLite Cache
{
    "user_id": 123456789,
    "guild_id": 987654321,
    "message_count": 1420,
    "last_updated": "2024-11-14T10:30:00Z",
    "ttl": 3600  # Cache 1 Stunde
}
```

### Lösung 2: Incremental Tracking
- Bot zählt Messages in Echtzeit mit
- Nur initiales Counting ist langsam
- Danach: Live-Updates

### Lösung 3: Sampling (Fallback)
- Statt alle Messages: Sample random 10% der Channels
- Hochrechnen auf Gesamtaktivität
- Schneller, aber weniger akkurat

**Empfehlung:** Start mit Caching, später Incremental Tracking

---

## 🎨 User Experience

### Transparenz-Features

**1. Score-Erklärung**
```
/my-score

📊 Dein Score: 82.4 von 100

Berechnung:
├─ 📅 Mitgliedsdauer: 180 Tage
│  └─ Score: 73.5 (40% Gewichtung)
│
└─ 💬 Aktivität: 1,230 Messages
   └─ Score: 88.2 (60% Gewichtung)

Gesamtscore: (73.5 * 0.4) + (88.2 * 0.6) = 82.4

Du bist auf Platz 12 von 342 Usern! 🎉
```

**2. Fairness-Hinweise**
- Bot zeigt an: "Analysiert vom [Datum] - Nicht live, sondern Snapshot"
- Warnung: "Bots und Server Owner sind ausgeschlossen"
- Info: "Nur Public Channels werden gezählt"

---

## 🚀 Entwicklungs-Phasen

### Phase 1: MVP (Minimum Viable Product)
- [x] Basic Bot Setup mit discord.py
- [x] `/analyze <rolle>` Command
- [x] Message Counting (ohne Cache)
- [x] Score-Berechnung mit fester Gewichtung
- [x] Discord Embed Output
- [x] CSV Export

**Zeitaufwand:** ~4-6 Stunden

### Phase 2: Production-Ready
- [ ] SQLite Caching für Performance
- [ ] `/my-score` Command
- [ ] Konfigurierbare Gewichtung
- [ ] Admin Commands (`/set-weights`)
- [ ] Error Handling & Logging
- [ ] Rate Limit Handling
- [ ] Tests

**Zeitaufwand:** ~6-8 Stunden

### Phase 3: Advanced Features (Optional)
- [ ] Web Dashboard mit Flask
- [ ] Incremental Message Tracking
- [ ] Historical Score-Tracking (Verlauf)
- [ ] Multi-Guild Support
- [ ] Webhook-Integration für Auto-Reports

**Zeitaufwand:** ~10-12 Stunden

---

## 📝 Beispiel-Szenario

**Situation:**
- Discord Server: "StreamerXYZ Community"
- Rolle: @GildenInteressenten (342 User)
- Verfügbare Plätze: 50

**Admin führt aus:**
```
/analyze @GildenInteressenten top_n:50
```

**Bot antwortet:**
```
📊 Analyse für @GildenInteressenten

🔍 Gescannt: 342 User
⏱️ Zeitraum: Letzte 365 Tage
🏆 Top 50 werden angezeigt

Scoring-Formel:
Score = (Tage_im_Server * 0.4) + (Nachrichten * 0.6)

🥇 Top 10:
1. User#1234 - Score: 95.2 (245 Tage, 1420 Messages)
2. User#5678 - Score: 92.8 (380 Tage, 980 Messages)
3. User#9012 - Score: 89.4 (290 Tage, 1150 Messages)
...

[Button: 💾 Komplette Liste als CSV]
[Button: 📊 Details anzeigen]
```

**CSV wird exportiert:**
- Admin lädt CSV herunter
- Shared mit Team/Community
- Transparente Entscheidung wer in Gilde kommt

---

## 🧪 Testing-Strategie

### Unit Tests
```python
# test_scorer.py
def test_score_calculation():
    user = {"days": 180, "messages": 1000}
    score = calculate_score(user, weights=(0.4, 0.6))
    assert 0 <= score <= 100

def test_normalization():
    users = [
        {"days": 100, "messages": 500},
        {"days": 300, "messages": 1500}
    ]
    normalized = normalize_scores(users)
    assert normalized[1]["score"] > normalized[0]["score"]
```

### Integration Tests
- Test mit Mock Discord Guild
- Test API Rate Limiting
- Test Cache Behavior

### Manual Testing Checklist
- [ ] Bot auf Test-Server einladen
- [ ] Test-Rolle mit 5-10 Usern erstellen
- [ ] `/analyze` mit verschiedenen Parametern testen
- [ ] CSV Export validieren
- [ ] Permission-Tests (Admin-only Commands)
- [ ] Edge Cases: User ohne Messages, neue User, etc.

---

## 🐛 Error Handling

### Mögliche Fehler & Lösungen

**1. "Bot hat keine Permission für Message History"**
```python
try:
    messages = await channel.history(limit=100).flatten()
except discord.Forbidden:
    logger.error(f"No permission for channel {channel.name}")
    # Skip channel, zähle nur accessible channels
```

**2. "Rolle nicht gefunden"**
```python
role = discord.utils.get(guild.roles, name=role_name)
if not role:
    return await interaction.followup.send(
        "❌ Rolle nicht gefunden! Bitte prüfe den Namen.",
        ephemeral=True
    )
```

**3. "Rate Limit exceeded"**
```python
import asyncio

async def fetch_messages_with_retry(channel, limit):
    for attempt in range(3):
        try:
            return await channel.history(limit=limit).flatten()
        except discord.HTTPException as e:
            if e.status == 429:  # Rate Limited
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
            else:
                raise
```

---

## 📖 README-Vorlage

Das fertige Projekt sollte ein README haben mit:
- Feature-Übersicht
- Installation Guide
- Bot-Permissions Setup
- Config-Beispiel
- Command-Liste
- FAQ: "Warum ist mein Score so niedrig?"
- Troubleshooting

---

## 🎯 Success Criteria

Das Projekt ist erfolgreich wenn:

✅ **Funktionalität**
- Bot kann User nach Rolle scannen
- Score-Berechnung funktioniert korrekt
- Export in Discord & CSV funktioniert

✅ **Performance**
- Analyse von 300+ Usern in <30 Sekunden
- Bot antwortet zuverlässig auf Commands

✅ **Usability**
- Admin kann Bot ohne Code-Kenntnisse bedienen
- User verstehen ihre Scores (Transparenz)
- Fehler werden klar kommuniziert

✅ **Code-Qualität**
- Modular & erweiterbar
- Dokumentiert (Docstrings, Comments)
- Error Handling implementiert

---

## 💡 Offene Fragen (für Implementierung klären)

1. **Message-Counting:**
   - Alle Channels oder nur bestimmte? (z.B. kein #bot-spam)
   - Nur Text oder auch Voice-Activity?
   - Gelöschte Messages zählen?

2. **Fairness:**
   - Sollen Bots ausgeschlossen werden?
   - Sollen Admins/Mods ausgeschlossen werden?
   - Minimal Message-Threshold? (z.B. User mit <10 Messages ignorieren)

3. **Zeitraum:**
   - Gesamte Server-Historie oder letzte X Tage?
   - Soll join_date vor Bot-Installation berücksichtigt werden?

4. **Edge Cases:**
   - Was wenn User Server verlassen hat aber Rolle hatte?
   - Was bei User-Rename?
   - Was bei Bot-Offline Zeit (Messages nicht gezählt)?

---

## 🚀 Nächste Schritte

1. Neuen Chat starten
2. Prompt aus diesem Dokument kopieren (siehe unten)
3. Repository initialisieren
4. Phase 1 MVP entwickeln
5. Testen auf Test-Server
6. Beim Content Creator deployen

**Let's build this! 🎉**
