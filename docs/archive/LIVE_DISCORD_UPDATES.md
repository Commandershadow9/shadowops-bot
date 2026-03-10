# Live Discord Updates für Orchestrator

## Übersicht

Das ShadowOps Auto-Remediation System zeigt jetzt Live-Updates während der koordinierten Remediation direkt in Discord.

## Features

### 1. Event-Batching Live-Updates (10s Window)

Während der 10-sekündigen Event-Sammelphase sieht der User:
- **Initiale Message**: "Neuer Remediation-Batch gestartet"
- **Live-Countdown**: Alle 2 Sekunden aktualisiert
- **Progress Bar**: Visuelle Darstellung (█████░░░░░)
- **Event-Liste**: Zeigt alle gesammelten Events
- **Verbleibende Zeit**: Live-Countdown bis Batch-Abschluss

**Beispiel:**
```
🔄 Koordinierte Remediation läuft

📦 Sammle Security-Events

• TRIVY: CRITICAL
• CROWDSEC: HIGH

⏱️ 6s verbleibend | Events: 2

████████████░░░░░░░░ 60%

Batch ID: batch_1763326288
```

### 2. KI-Analyse Live-Streaming

Während der 2-3 Minuten KI-Analyse mit Llama3.1:
- **Token-Count**: Live-Updates bei jedem 50. Token
- **Progress Bar**: Basierend auf erwartetem Output (~400 Tokens)
- **ETA**: Geschätzte Restzeit
- **Snippets**: Preview der generierten Inhalte
- **Phase-Erkennung**: Automatische Erkennung welche Phase geplant wird

**Beispiel:**
```
🧠 KI-Analyse läuft

🔍 Analysiere: Phase 2 (Updates)

📊 Tokens: 150 / ~400
⚡ Zeit: 37s | ⏱️ ETA: ~45s

████████████░░░░░░░░ 37%

💬 "Phase 2: Docker Updates - CVEs in Docker Images beheben..."

Batch ID: batch_1763326288
```

### 3. Plan-Fertigstellung

Nach erfolgreichem Abschluss der KI-Analyse:
- **Phasen-Übersicht**: Liste aller Phasen
- **Geschätzte Dauer**: Gesamtdauer in Minuten
- **Confidence**: KI-Confidence-Score

**Beispiel:**
```
✅ Plan erstellt

• Phase 1: Backup
• Phase 2: Docker Updates
• Phase 3: Trivy-Config-Anpassungen

⏱️ Geschätzte Dauer: 30min
🎯 Confidence: 90%

Batch ID: batch_1763326288
```

## Technische Implementierung

### Dateien

**`src/integrations/orchestrator.py`**
- `SecurityEventBatch`: Dataclass erweitert mit `status_message_id` und `status_channel_id`
- `_get_status_channel()`: Holt Discord-Channel für Live-Updates
- `_send_batch_status()`: Sendet/Updated Discord-Embeds
- `_close_batch_after_timeout()`: Live-Countdown während Batching
- `_stream_ai_progress_to_discord()`: Live-Updates während KI-Analyse
- `_create_coordinated_plan()`: Integration mit Streaming-System

**`src/integrations/ai_service.py`**
- `_parse_json_response()`: Erweitert mit `is_coordinated_plan` Parameter
- `_analyze_with_ollama()`: Unterstützt `streaming_state` aus Context
- Token-Streaming: Aktualisiert `streaming_state` bei jedem 50. Token

### Ablauf

1. **Event-Submission** → Initiale Discord-Message
2. **Batching-Phase** → Live-Countdown alle 2s
3. **Batch-Abschluss** → "Batch geschlossen" Message
4. **KI-Analyse Start** → "KI-Analyse startet" Message
5. **Token-Streaming** → Live-Updates alle 5s
6. **Plan-Fertigstellung** → "Plan erstellt" Message mit Phasen
7. **Approval** → Standard Approval-Message (wie vorher)

## Konfiguration

### Discord-Channel

Live-Updates werden im gleichen Channel wie Approval-Requests angezeigt:
- **Channel**: `✋-auto-remediation-approvals`
- **Channel-ID**: `1438503737315299351`

### Update-Intervalle

```python
# Batching-Phase
update_interval = 2  # Sekunden
collection_window = 10  # Sekunden

# KI-Analyse
update_interval = 5  # Sekunden
token_milestone = 50  # Token
expected_tokens = 400  # ~400 Tokens pro Plan
```

### Progress Bars

```python
bar_length = 20  # Zeichen
filled_char = "█"  # Unicode Block
empty_char = "░"  # Unicode Light Shade
```

## Vorteile

1. **Transparenz**: User sieht sofort, dass der Bot arbeitet
2. **Geduld**: User weiß, wie lange es noch dauert
3. **Debugging**: Bei Problemen sieht man sofort, wo es hängt
4. **Vertrauen**: User kann den KI-Prozess live verfolgen
5. **Engagement**: Interaktives Erlebnis statt "schwarze Box"

## Bekannte Limitierungen

### Countdown-Updates während Batching

⚠️ **Problem**: Die 2-Sekunden-Updates während der 10s Batching-Phase erscheinen möglicherweise nicht konsistent.

**Grund**: Die `_close_batch_after_timeout()` Methode wird in einem separaten asyncio.Task ausgeführt und könnte durch andere Tasks blockiert werden.

**Auswirkung**: User sieht die initiale "Batch gestartet" Message und dann die "Batch geschlossen" Message, aber möglicherweise nicht die Zwischenupdates.

**Workaround**: Die wichtigsten Messages (Start, Ende, KI-Analyse) funktionieren zuverlässig.

## Testing

### Manueller Test

1. Bot starten
2. Security-Event triggern (z.B. Trivy-Scan)
3. In Discord Channel `✋-auto-remediation-approvals` beobachten
4. Live-Updates sollten erscheinen:
   - Batch-Start (sofort)
   - Batch geschlossen (nach 10s)
   - KI-Analyse läuft (sofort)
   - Token-Updates (alle 50 Tokens)
   - Plan erstellt (nach ~2min)
   - Approval-Request (sofort)

### Log-Validierung

```bash
tail -f /var/log/shadowops-bot.log | grep -E "Discord-Status|📤|📝"
```

Erwartete Logs:
- `📤 Neue Discord-Status-Message gesendet (ID: ...)`
- `📝 Discord-Status updated (Message ID: ...)`

## Changelog

### Version 2.0 - Live-Updates

**Datum**: 2025-11-16

**Änderungen**:
- ✅ Live-Updates während Event-Batching
- ✅ Live-Updates während KI-Analyse mit Token-Streaming
- ✅ Progress Bars für Batching und KI-Analyse
- ✅ Phase-Erkennung aus KI-Output
- ✅ ETA-Berechnung basierend auf Token-Speed
- ✅ JSON-Parser Fix für koordinierte Pläne
- ✅ Logging für Discord-Message-Operationen

**Betroffene Dateien**:
- `src/integrations/orchestrator.py` (neu: Live-Update-Funktionen)
- `src/integrations/ai_service.py` (erweitert: `is_coordinated_plan` Support)

**Breaking Changes**: Keine

**Migration**: Keine Aktion erforderlich - Live-Updates funktionieren automatisch
