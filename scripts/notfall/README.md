# Notfall-Selbstheilung (ZERODOX #5627)

Anlass: Am 12.11.2026 lieferten alle Domains 70 min lang 404. Ein Docker-Update in der Nacht hatte die
Socket-Proxies auf eine tote `docker.sock`-Inode gesetzt. Erkannt wurde der Ausfall nach 5 min,
**gehandelt hat niemand**, weil es Nacht war. Ziel: Fällt etwas aus und niemand reagiert, holt sich
das System selbst zurück. Dabei richtet die Rettung nie mehr Schaden an als der Ausfall.

```
Hauptserver                                        HEL1 (10.8.0.12)
──────────                                         ────────────────
docker-socket-heiler (.path + 1-min-Timer)         notfall-waechter.service (Schleife 60 s, User notfall)
zerodox-/mcp-watchdog (5 min)                        ├─ OK / TEIL / TOT / BLIND
  └─ 2× down → WATCHDOG_HEAL_CMD                     ├─ TEIL ≥10 min → notfall-agent.sh (claude -p, 1×/Vorfall, 15 min)
       └─ notfall-heiler.sh  (Stufe 1)               │     └─ /opt/notfall/notfall-ssh <befehl>
notfall-wrapper.sh  ◄── ssh command= ────────────────┘            (keine Optionen, feste ssh-Optionen)
  (Positivliste, protokolliert)                       ├─ TOT ≥10 min → ntfy dringend + Knopf „Ich übernehme“
                                                      └─ +10 min ohne Knopf → Hetzner-Robot-Reset (1×/Vorfall)
```

## Dateien

| Datei | Läuft auf | Zweck |
|---|---|---|
| `notfall-heiler.sh` | Hauptserver | Stufe 1: Socket-Heiler, Traefik-Providerfehler → Proxy neu, gestoppte Container starten, `zerodox-web` unhealthy → restart, Außenprüfung. `TROCKEN=1` |
| `../service-watchdog.sh` | Hauptserver | `WATCHDOG_HEAL_CMD`: ab dem 2. Down-Lauf, höchstens 1× je 10 min, Ergebnis in den Discord-Alarm |
| `notfall-wrapper.sh` | Hauptserver | **die Sicherheitsgrenze**: einzige Tür von HEL1 herein (`command=` in `authorized_keys`) |
| `notfall-probelauf.sh` | Hauptserver | `--pruefen` monatlich per Timer (ohne Ausfall), `--echt` von Hand tagsüber |
| `docker-update-wartung.sh` | Hauptserver | einziger Weg für Docker-Updates: 08–20 Uhr, `apt-mark hold` danach wieder gesetzt |
| `hel1/notfall-waechter.sh` | HEL1 | Außenprüfung + Eskalation (Zustand in `/var/lib/notfall`) |
| `hel1/notfall-agent.sh` + `notfall-prompt.md` | HEL1 | headless Claude, nur `notfall-ssh` erlaubt |
| `hel1/notfall-ssh` | HEL1 | fester ssh-Starter, lässt keine Optionen durch |
| `hel1/einrichten.sh` | HEL1 | idempotente Einrichtung (Benutzer, Dateien, Schlüssel, Dienst) |

## Schranken des Agenten (drei, unabhängig)

1. **Claude-CLI:** `--tools Bash`, `--allowedTools "Bash(/opt/notfall/notfall-ssh:*)"`, `--permission-mode dontAsk`,
   `--strict-mcp-config`, `--setting-sources ""`, Umgebung leer (`env -i`), also kein Robot-Passwort und kein Webhook.
2. **HEL1:** Benutzer `notfall` ohne sudo und ohne docker. Das Binary liegt root-eigen in `/opt/notfall/bin`.
3. **Hauptserver:** `authorized_keys` erzwingt `notfall-wrapper.sh` mit `from="10.8.0.12",restrict`. Erlaubt sind nur
   Lesen und Umkehrbares an einer **Positivliste** von Containern. Datenbanken, Caches, `down`, `pull`, `apt`, `ufw`
   und Dateiänderungen sind nicht erreichbar.

⚠️ **Nie `Bash(ssh notfall-haupt:*)` erlauben.** GNU-getopt parst Optionen auch nach dem Host. Mit
`ssh notfall-haupt -oProxyCommand=…` liefe beliebiger Code auf HEL1 (Befund der Sicherheitsprüfung 12.11.).

## Geheimnisse auf HEL1 (`/etc/notfall/`, 0640 root:notfall)

- `notfall.env`: `NTFY_TOPIC`, `DISCORD_WEBHOOK`, `ROBOT_USER`, `ROBOT_PASS`, `ROBOT_SERVER_NUMBER`.
  Ohne die Robot-Werte wird nur alarmiert.
- `claude.env`: `CLAUDE_CODE_OAUTH_TOKEN` (Hauptabo, `claude setup-token`). Der Agent läuft ohne Proxy-Port. Sein
  Verbrauch erscheint deshalb **nicht** in `ki usage`. Bei höchstens 1 Lauf je Vorfall ist das hingenommen.

ntfy.sh ist ein Fremddienst. Dorthin gehen nur neutrale Zustandssätze und die Ergebniszeile des Agenten,
nie Log-Auszüge. Der volle Bericht geht nach Discord und liegt unter `/var/lib/notfall/agent-bericht-*.txt`.

## Betrieb

```bash
# Zustand auf HEL1
ssh zerodox-ci-hel1 'systemctl status notfall-waechter --no-pager; ls /var/lib/notfall; journalctl -u notfall-waechter -n 30 --no-pager'
# Was hat der Agent auf dem Hauptserver getan?
cat ~/.local/state/notfall-wrapper.log
# Prüfen ohne Ausfall / Echtlauf (tagsüber, Betreiber da)
scripts/notfall/notfall-probelauf.sh --pruefen
scripts/notfall/notfall-probelauf.sh --echt
# Agent von Hand auslösen (Probelauf Stufe 2)
ssh zerodox-ci-hel1 'sudo -u notfall env -i HOME=/var/lib/notfall PATH=/usr/bin:/bin /opt/notfall/notfall-agent.sh'
# Not-Aus Stufe 2/3
ssh zerodox-ci-hel1 'systemctl stop notfall-waechter'
```

Aktualisieren von HEL1 nach einer Änderung hier: `scp -r scripts/notfall/hel1 zerodox-ci-hel1:/tmp/notfall-hel1 && ssh zerodox-ci-hel1 bash /tmp/notfall-hel1/einrichten.sh`.

## Notbetrieb bei Totalausfall des Hauptservers (#5629)

Raucht der Hauptserver vollständig ab (Hardware, Rechenzentrum), zeigt `zerodox.de` nach
≥ 10 min auf HEL1. Dort bleiben online: eine **Notseite** (503 mit `Retry-After`, Hinweis
„Störung, Daten gesichert, Post wird angenommen, Kontakt/Presse info@zerodox.de“),
**Impressum** und **Datenschutz** von ZERODOX (Kopie, 200) und alle **Kunden-Impressen**
`/creator/<slug>/impressum` (Schnappschüsse aus #4262, 200, `noindex`). Portal, Login,
Zahlungen und API liefern 503. Es gibt keine Datenbank auf HEL1, also auch keine Schreibvorgänge.

| Teil | Wo | Was |
|---|---|---|
| `notbetrieb-spiegel.sh` | Hauptserver, Timer alle 5 min | Impressen (`rsync --delete`), Rechtsseiten (Positivlisten-Extraktor), LE-Zertifikat aus Traefik → `notbetrieb@10.8.0.12` (Schlüssel per `rrsync` auf `/srv/notbetrieb/daten` begrenzt) |
| `notbetrieb-web` | HEL1, Docker | `nginx-unprivileged`, read-only, `cap-drop ALL`, als Benutzer `notbetrieb`, `157.180.55.95:80/443` |
| `notbetrieb-umschalter.service` | HEL1, Benutzer `notbetrieb` | setzt `zerodox.de A` auf HEL1 bzw. zurück; Token in `/etc/notbetrieb/notbetrieb.env` (0600, **nicht** für den KI-Agenten lesbar) |
| `notbetrieb-reload.timer` | HEL1, täglich 05:20 | nginx lädt das gespiegelte Zertifikat neu |

**Regeln des Umschalters** (im Code verdrahtet, nicht konfigurierbar):
- Geändert wird nur `@ A` (`www` ist CNAME darauf), nur zwischen `88.99.160.210` und `157.180.55.95`. MX und alle anderen Einträge nie.
- TOT = kein Ping über WireGuard und öffentlich **und** keine HTTP-Antwort der Haupt-IP. Antwortet der Rechner irgendwie, sind Stufe 1/2 zuständig.
- Umschalten nach 10 min TOT, wenn der vorletzte Hop vor dem Hauptserver im Traceroute antwortet (Strecke bis ins Rechenzentrum intakt), sonst nach 30 min.
- Nicht umschalten, wenn HEL1 blind ist, `/etc/notbetrieb/sperre` existiert, im DNS ein dritter Wert steht oder HEL1 die Notseite selbst nicht ausliefert.
- Zurück erst nach 10 min durchgehend `200` von `zerodox.de/api/health` an der Haupt-IP. ntfy + Discord bei beidem.
- Wächter, Heiler und Wrapper messen die Domains fest an der Haupt-IP (`--connect-to`). Sonst würde die Notseite sie nach der Rückkehr täuschen.

**Zertifikat:** Ein eigenes Zertifikat (DNS-01) ist nicht nötig. Das LE-Zertifikat wird mitgespiegelt; LE erneuert 30 Tage vor Ablauf, die Kopie hat also im Ernstfall mindestens 30 Tage Restlaufzeit.

**Inbetriebnahme (einmalig, Betreiber):** `bash ~/shadowops-bot/scripts/notfall/notbetrieb/inbetriebnahme.sh`

**Bedienung auf HEL1:**
```bash
sudo -u notbetrieb /opt/notbetrieb/notbetrieb-umschalter.sh status    # Zustand
sudo -u notbetrieb /opt/notbetrieb/notbetrieb-umschalter.sh an        # von Hand auf HEL1
sudo -u notbetrieb /opt/notbetrieb/notbetrieb-umschalter.sh aus       # von Hand zurück
sudo -u notbetrieb /opt/notbetrieb/notbetrieb-umschalter.sh test-dns  # Probelauf über notbetrieb-test.zerodox.de
touch /etc/notbetrieb/sperre                                          # Automatik aus (z. B. geplante Wartung)
```
⚠️ Wer `an` von Hand bei gesundem Hauptserver auslöst, wird nach 10 min automatisch zurückgeschaltet.
