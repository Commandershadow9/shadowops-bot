# Discord-Struktur: übergreifend und projektbezogen trennen

Stand: 11.09.2026 · Server „DEV Commandershadow" (`1438065435496157267`)

## Anlass

Ein zweiter Mensch kommt dazu (Partner, `238749198923726859`), und es sollen
weitere Projekte betreut werden. Der Server soll dabei übersichtlich bleiben und
trennen zwischen dem, was alle angeht, und dem, was nur ein Projekt betrifft.

## Befund

Gemessen am 11.09.2026: 10 Kategorien, 41 Textkanäle, 9 Mitglieder.

### Das eigentliche Problem ist kein Mengenproblem

Der Server ist nach **zwei unvereinbaren Prinzipien gleichzeitig** sortiert.
Manche Kategorien ordnen nach Funktion (Security Monitoring, SEO, Backups),
andere nach Projekt (ZERODOX Funnel, Co-Dev MayDay). Dasselbe Projekt liegt
deshalb an mehreren Stellen:

| Projekt | verteilt über |
| --- | --- |
| GuildScout | `⚡-guildscout` (Security), `updates-guildscout` (Updates & CI), Archivkanal |
| MayDay | `🎮-mayday-sim` (System & Projekte), `updates-mayday_sim` (Updates & CI), eigene Kategorie mit 4 Kanälen |
| ZERODOX | Updates und CI (Updates & CI), Funnel (eigene Kategorie) |

Wer wissen will, was mit GuildScout los ist, muss drei Kategorien absuchen. Eine
halb durchgeführte Trennung ist schlechter als gar keine, weil unklar bleibt,
nach welchem Prinzip zu suchen ist.

### Weitere Fundstücke

- Kategorien mit nur einem Kanal: Backups, Server & Wartung, ZERODOX Funnel
- `deploy-events` und `🚀-deployment-log` melden beide Deploys
- `📢 Updates & CI` ist mit 9 Kanälen faktisch projektbezogen, aber funktional
  einsortiert
- Der Voice-Kanal `Allgemein` hängt in keiner Kategorie
- avunex.de kommt auf dem Server gar nicht vor

## ⚠️ Die Struktur wird vom Bot erzeugt, nicht von Hand gepflegt

**Das ist der wichtigste Punkt dieses Dokuments.**

`src/bot.py:188-191` legt bei jedem Start vier Kategorien an, falls sie fehlen.
`_ensure_channel` prüft anschließend für jeden verwalteten Kanal:

```python
if dc_channel.category_id != category.id:
    await dc_channel.edit(category=category)
```

Ein Kanal, der in eine andere Kategorie verschoben wurde, wird beim nächsten
Botstart **zurückgeschoben**. Eine gelöschte Kategorie wird **neu angelegt**.

Wer die Ordnung nur in Discord ändert, verliert sie beim nächsten Neustart —
ohne Fehlermeldung, ohne erkennbare Ursache. Jede Strukturänderung gehört
deshalb zuerst in den Bot-Code.

## Zielstruktur

Oben steht, was projektübergreifend gilt. Darunter je Projekt **eine** Kategorie,
in der alles zu diesem Projekt liegt.

```
ÜBERGREIFEND
  📋 Allgemein              allgemein · notizen · heute-dran · dev-handoff · dev-alerts
  🔐 Betrieb & Sicherheit   uptime-alerts · critical · crowdsec · docker ·
                            security-briefing · server-update · backup-dashboard ·
                            bot-status · customer-alerts · deployment-log
  🤖 KI-Werkstatt           approvals · ai-learning · orchestrator
  🔍 SEO                    seo-audits · seo-search-console · seo-fixes · seo-drift

PRO PROJEKT
  🟣 ZERODOX                updates · ci · akquise-funnel
  🟢 GuildScout             updates · alerts
  🔵 MayDay Sim             updates · monitoring · deploy
  🟠 AVUNEX      (neu)      updates · ci
  ⚙️ Eigene Werkzeuge       updates-shadowops · updates-agents ·
                            cmdshadow-design-updates · updates-database-ports

  🗄️ Archiv                 stillgelegte Kanäle mit erhaltenswertem Verlauf
```

**Ehrlich zur Wirkung:** Das sind weiterhin rund zehn Kategorien. Der Gewinn ist
nicht die Zahl, sondern dass jede Frage genau einen Ort hat — und dass ein neues
Projekt eine Kategorie bekommt statt Kanäle in vier bestehende zu streuen.

`seo-reports` wird zu `seo-drift` umbenannt: Der Kanal meldet Code-Drift des
SEO-Workers, nicht Audit-Berichte. Der Name legte eine Dublette zu `seo-audits`
nahe, die inhaltlich keine ist.

## Rollen und Rechte

Sichtbarkeit wird **an der Kategorie** gesetzt, nicht am einzelnen Kanal. Kanäle
erben. Das ist der Pflegbarkeitsgewinn: Ein neuer Kanal in einer Projektkategorie
hat automatisch die richtigen Rechte.

| Rolle | Zweck |
| --- | --- |
| `@team` | alle Menschen. Sieht die übergreifenden Kategorien |
| `@p-avunex`, `@p-zerodox`, `@p-guildscout`, `@p-mayday` | je Projekt eine Rolle |
| `@bots` | Bots und Webhooks. Brauchen Schreibrecht in ihren Zielkanälen |

Rechte je Kategorie:

| Kategorietyp | `@everyone` | `@team` | Projektrolle | `@bots` |
| --- | --- | --- | --- | --- |
| übergreifend | kein Zugriff | lesen/schreiben | — | schreiben |
| projektbezogen | kein Zugriff | kein Zugriff | lesen/schreiben | schreiben |

Der Partner erhält `@team` plus die Projektrollen, an denen er arbeitet —
zunächst `@p-avunex`.

⚠️ Der Bot braucht Schreibrecht in **allen** Zielkanälen. Wird `@bots` bei einer
Kategorie vergessen, schweigt die betroffene Meldung stillschweigend; genau so
entstehen die stillen Kanäle, die dieser Server bereits hat.

## Werkzeuge

| Vorgang | Discord-MCP | ShadowOps-Bot |
| --- | --- | --- |
| Kategorie anlegen/löschen | ja | ja |
| Kanal anlegen | ohne Kategoriezuweisung | mit Kategorie |
| Kanal verschieben | nein | ja (`edit(category=…)`) |
| Rollen und Rechte setzen | nein | ja (`PermissionOverwrite`) |

Der MCP allein trägt den Umbau nicht. Der Bot setzt in
`src/integrations/customer_server_setup.py:98-117` bereits Rechte-Overwrites für
Kundenserver — dasselbe Muster passt auf die Projektkategorien.

## Umsetzungsreihenfolge

1. **Bot-Code anpassen.** Kategorienliste und Kanalzuordnung in `src/bot.py` auf
   die Zielstruktur bringen. Ohne diesen Schritt springt alles Weitere beim
   nächsten Neustart zurück.
2. **Rollen anlegen** und Rechte an den Kategorien setzen.
3. **Kanäle verschieben** in die Zielkategorien.
4. **Löschen**, was freigegeben ist (Liste unten).
5. **AVUNEX-Kategorie anlegen** samt Kanälen für Updates und CI.
6. **Partner einladen**, `@team` und `@p-avunex` vergeben.
7. **Neustart des Bots als Probe** — die Struktur muss unverändert bleiben.
   Verschiebt der Bot etwas zurück, ist Schritt 1 unvollständig.

## Löschliste

Freigegeben zum Löschen, weil leer oder ohne Wert und nirgends in Konfiguration
oder Code referenziert:

| Kanal | Begründung |
| --- | --- |
| `🤖-agent-reviews` | 0 Nachrichten, nicht referenziert |
| `dev-live-state` | 1 Nachricht vom 28.05.2026, Hook liefert nicht mehr |
| `🏗️-runner-vm` | 2 Nachrichten vom 01.05.2026, reines Einrichtungsprotokoll |

Zusammenlegen statt löschen: `deploy-events` (still seit 83 Tagen) ist eine
Dublette zu `🚀-deployment-log`; der Verlauf wandert ins Archiv.

Behalten trotz Stille: `archiv-updates-guildscout` (drei Monate Verlauf,
ausdrücklich als Archiv angelegt) und `dev-handoff` — letzteres wird mit dem
Partner wieder gebraucht.

⚠️ Vor jeder Löschung gilt: Steht die Kanal-ID in `config/config.yaml` oder im
Code, darf der Kanal nicht verschwinden, ohne dass der Eintrag mitgeht. Sonst
schreibt der Bot ins Leere.

## Die stillen Kanäle sind kein Aufräumthema

Fünf Kanäle waren seit Monaten still, obwohl in der Konfiguration eingetragen.
Untersucht am 11.09.2026 — vier davon sind Defekte, nicht Überflüssigkeit.

⚠️ **Zur Messmethode:** „Datum der neuesten Nachricht" misst Aktivität nur bei
Kanälen, die Nachrichten anfügen. Ein Kanal, der eine angeheftete Nachricht
*aktualisiert*, sieht danach tot aus, obwohl er im Fünfminutentakt arbeitet.
Genau dieser Fehlschluss wäre bei `📊-dashboard` beinahe passiert.

| Kanal | Befund | Beleg | Entscheidung |
| --- | --- | --- | --- |
| `📊-dashboard` | funktioniert | `bot.py:1994-2112` aktualisiert eine angeheftete Nachricht alle 5 Minuten | so lassen |
| `🚫-fail2ban` | fail2ban nicht installiert | `fail2ban-client` fehlt, `systemctl is-enabled fail2ban` → `not-found`; CrowdSec aktiv | abräumen |
| `⚡-guildscout` | nie eingeschaltet | `GuildScout/bot/src/utils/config.py:305` liest `shadowops.enabled` mit Default `False`; der Schlüssel fehlt in der Konfiguration | eine Zeile ergänzen |
| `🔧-code-fixes` | strukturell stumm | drei Fix-Pfade, nur `self_healing.py` meldet; `orchestrator/executor_mixin.py:891` und `security_engine/fixer_adapters.py` schweigen | Meldung nachziehen |
| `🎮-mayday-sim` | nie verdrahtet | `config.yaml:18` setzt `channels.mayday_sim`, der Schlüssel wird in `src/` nirgends gelesen; fehlt in `bot.py:252-261` | abräumen |

### fail2ban im Einzelnen

Der Bot fragt weiterhin **alle 15 Sekunden** ein Programm ab, das auf diesem
Server nicht existiert, und meldet beim Start „No permissions for
fail2ban-client / Bot needs sudo access without password". Diese Meldung ist
irreführend: Es fehlen keine Rechte, es fehlt das Programm.

`config/config.yaml` (Zeilen 8, 549, 570), `docs/runbooks/discord-routing.md:13`
und der CHANGELOG beschreiben fail2ban weiterhin als aktiv. Wann es vom Server
verschwand, ließ sich nicht ermitteln; die letzte Fehlermeldung vom 11.04.2026
liegt vor dem Serverumzug am 07.06.2026, die Migration scheidet als Ursache also
aus.

Die Absicherung selbst ist nicht betroffen — CrowdSec läuft und meldet. Zu
bereinigen sind Code, Konfiguration und Dokumentation, damit die Doku wieder der
Wirklichkeit entspricht und der Leerlauf aufhört.

### Was daraus für die Struktur folgt

Ein stiller Kanal beantwortet die Frage „wird das gebraucht?" nicht. Er sagt
nur, dass sich nichts meldet — und die Gründe „abgeschaltet", „defekt", „nie
angeschlossen" und „arbeitet anders" sind von außen nicht unterscheidbar.

Deshalb wird beim Aufräumen kein Kanal gelöscht, weil er still ist. Gelöscht
wird, was **nachweislich** keinen Code-Bezug und keinen erhaltenswerten Verlauf
hat.
