# Postfach-Routing — welcher Watchdog darf zusätzlich ans ZERODOX-Team-Postfach?

> Zusatz-Dokument zu #1983 (Team-Postfach-Anbindung, Spec
> `docs/superpowers/specs/2026-07-24-admin-cockpit-finanzen-postfach-design.md`, Stufe E4).
> Klärt, **warum** ein Watchdog zusätzlich ans Postfach melden darf oder dauerhaft
> Discord-only bleibt — nicht nur, welche Liste welchen Namen trägt. Discord bleibt in
> jedem Fall der bestehende Kanal; das Postfach kommt additiv hinzu (siehe
# lib/postfach-send.sh).

## Das Prinzip (Spec, Abschnitt 3 — "Zwingende Einschränkung zu „Alles ins Postfach"")

> Meldungen, die den **Ausfall von ZERODOX selbst** betreffen, dürfen nicht ausschließlich
> in ein Postfach laufen, das in ZERODOX gehostet wird — genau im Alarmfall wäre der
> Empfänger nicht erreichbar. Für diese Klasse (Erreichbarkeits-Wächter,
> Datenbank-/Speicher-Alarme, externer GitHub-Actions-Wächter) bleibt Discord als bewusst
> unabhängiger Zweitkanal bestehen. Alles Übrige — Backups, Buchungen, Zahlungen, Akquise,
> Support, Post, SEO — zieht vollständig um.

Kurz: Das Postfach lebt **in** ZERODOX. Ein Wächter, dessen Meldung lautet "ZERODOX (oder
eine tragende Säule davon: Datenbank, Speicherplatz, der Bot, der das Postfach befüllt)
ist gerade nicht ansprechbar", darf sich nicht selbst den einzigen Übertragungsweg
abschneiden. Ein Wächter, dessen Meldung ein **fachlicher/redaktioneller** Befund ist
(Doku ist veraltet, ein Score ist gesunken, ein Datensatz ist überfällig) hat dieses
Problem nicht — ZERODOX war beim Erzeugen der Meldung nachweislich online, sonst gäbe es
den Befund gar nicht.

**Prüffrage pro Watchdog:** *Kann die Meldung selbst genau dann verloren gehen, wenn sie am
wichtigsten wäre — weil ZERODOX, seine Datenbank oder der meldende Bot in diesem Moment
nicht erreichbar sind?* Ja → Discord-only. Nein → postfach-fähig.

## Nur bei Befund ins Postfach — keine Kenntnisnahme ohne Anlass

**Diese Regel gilt für jede künftige Umstellung, nicht nur den Piloten** (Korrektur
während der Review des Piloten, #1983):

> Wiederkehrende Prüfungen melden **nur bei Befund** ins Postfach. Ein Lauf ohne Befund
> erzeugt keinen Eintrag.
>
> **Ausnahme:** Läufe, deren Erfolg selbst ein Nachweis ist — Sicherungsläufe etwa, wo
> „Sicherung um 03:00 erfolgreich" eine aufbewahrungswürdige Tatsache ist. Dort ist die
> Kenntnisnahme gewollt.

**Warum:** Ein Wächter, der täglich läuft und auch bei "alles in Ordnung" einen
`KENNTNISNAHME`-Eintrag schreibt, erzeugt 365 Zeilen im Jahr, die niemand liest und die
nichts auslösen. Die Standardansicht des Postfachs filtert auf "offen und ungelesen" —
eine Kenntnisnahme ist zunächst ebenfalls ungelesen und würde dort erscheinen. Die erste
Ansicht, die morgens jemand öffnet, wäre dann voll mit "nichts zu tun"-Meldungen: genau
die zweite Flut, die dieses Vorhaben verhindern soll. Das Postfach zeigt **Arbeitsvorrat**,
kein Betriebsprotokoll — für "lief durch" gibt es weiterhin Discord und die Log-Dateien.

**Der Unterschied zur Ausnahme:** Bei einem Sicherungslauf ist die **Abwesenheit** der
Meldung das Alarmsignal (kein Lauf = etwas ist kaputt) — deshalb muss die Anwesenheit
dokumentiert sein, damit ihr Fehlen überhaupt auffällt. Bei einer Doku-Prüfung, einer
Frische-Prüfung oder einem Drift-Check interessiert dagegen nur der Befund selbst; ihr
Ausbleiben ist der Normalfall und braucht keinen Beleg.

**Praktisch:** Der `postfach_post`-Aufruf gehört ausschließlich in den Befund-Zweig
(`if [ Befund vorhanden ]`), nicht in einen `else`-Zweig für den Normalfall. Der
Discord-Weg bleibt davon unberührt — er darf weiterhin jeden Lauf melden, dort ist der
Throttle/Fingerprint-Mechanismus bereits eingespielt.

## Dauerhaft Discord-only (Erreichbarkeit / DB-Speicher-Alarm / Selbstüberwachung)

| Watchdog | Warum Discord-only |
|---|---|
| `zerodox-watchdog` | Meldet, dass zerodox.de nicht antwortet — genau dann wäre ein dort gehostetes Postfach ebenfalls nicht erreichbar. Der Klassiker aus Abschnitt 3. |
| `memory-watchdog` | RAM-/Swap-Druck auf demselben Server, auf dem auch die ZERODOX-DB und -Web-App laufen — ein Speicher-Alarm im Sinne von Abschnitt 3, Vorstufe zu genau dem Ausfall, den `zerodox-watchdog` erst danach sähe. |
| `disk-hygiene-watchdog` | Eine volle Platte reißt zuerst die Datenbank mit (Schreibfehler), danach die Web-App — der Datenbank-/Speicher-Alarm aus Abschnitt 3 in Reinform. |
| `shadowops-watchdog` | Überwacht den ShadowOps-Bot selbst (`:8766/health`) — der Bot ist der Absender, der Postfach-Meldungen überhaupt erst verschickt. Fällt der Absender aus, kann er auch keine Postfach-Meldung über seinen eigenen Ausfall mehr abliefern. |
| `shadowops-drift-watchdog` | Ergänzt den vorigen um Service-State + Restart-Loop-Erkennung — dieselbe Selbstüberwachungs-Logik, derselbe Grund. |
| `shadowops-backup-test` (`backup-restore-test.sh`, monatlich) | Prüft die Wiederherstellbarkeit **desselben Systems**, das im Ernstfall auch das Postfach trägt. Eine Aussage über "können wir ZERODOX aus dem Backup retten" gehört in den Kanal, der einen bereits durch ZERODOX kaputten Zustand überlebt. |

Zusätzlich außerhalb dieses Repos, aber derselben Klasse: der externe GitHub-Actions-Cron
(`external-uptime.yml`, hosted `ubuntu-latest`), der zerodox.de + guildscout.eu von
**außerhalb** des VPS anpingt — genau der Fall, den kein VPS-interner Wächter (auch kein
Postfach-Eintrag) abdecken kann, wenn der VPS selbst komplett tot ist. Bleibt beim
externen Discord-Webhook, nicht Teil dieser Umstellung.

## Postfach-fähig (redaktionell/fachlich, keine Aussage über Erreichbarkeit)

| Watchdog | Kategorie/Befund | Warum unbedenklich |
|---|---|---|
| `doku-drift-watchdog` | Doku vs. Realität (Port-Map, MEMORY.md-Länge) | **Pilot, bereits umgesetzt** (dieser PR). Rein redaktionell — ZERODOX lief nachweislich, als der Wächter den Vergleich zog. Meldet **nur bei Befund** (siehe Abschnitt oben) — ein Lauf ohne Drift erzeugt keinen Postfach-Eintrag. |
| `ki-cost-watchdog` | Token-/Kosten-Rollup Claude+Codex, Anomalie-Alarm | Reine Kostenbeobachtung, keine Verfügbarkeitsaussage. |
| `security-freshness-watchdog` | Alter des letzten `sec_jobs`-Laufs in der security_analyst-DB | Eine **stehende** DB-Zeile fehlt/ist alt — das ist ein fachlicher Rückstand, kein Ausfall von ZERODOX selbst (ZERODOX-Web und die security_analyst-DB sind getrennte Komponenten). |
| `seo-audit-freshness-watchdog`, `seo-deep-audit-freshness-watchdog`, `seo-output-freshness-watchdog` | Alter des letzten SEO-Audits/-Outputs in der seo_agent-DB | Dieselbe Begründung wie oben: Frische-Prüfung gegen eine Fach-DB, keine Erreichbarkeitsaussage über ZERODOX selbst. |
| `mayday-sim-build-drift-watchdog` | Build-ID auf maydaysim.de vs. `origin/main` HEAD | Redaktioneller Drift (Deploy hinkt Git hinterher), keine Ausfallmeldung — maydaysim.de antwortet ja, nur mit altem Stand. |

## Postfach-fähig, aber erst nach dem Soak des Piloten (Fremdprojekt-Wächter)

Diese Watchdogs prüfen **andere** Projekte oder Komponenten als ZERODOX selbst. Bis zum
Abschluss der Pilot-Review stand hier "noch nicht entschieden" — die Frage ist jetzt geklärt
(Team-Lead-Entscheidung nach Review des Piloten, #1983):

> **Fremdprojekt-Wächter (GuildScout, MayDay, cmdshadow-design, AI-Agent-Framework, Akquise-AI):**
> postfach-fähig, aber **erst nach dem Soak des Piloten**. Das Selbstüberwachungs-Paradox greift bei
> ihnen nicht — sie überwachen andere Systeme, ihre Zustellung hängt nicht am überwachten Objekt.
> Umstellung als eigener, kleiner Schritt pro Projekt, nicht als Sammelaktion.

**Begründung:** Ein Wächter, der meldet "GuildScout antwortet nicht", ist nicht von ZERODOX'
Verfügbarkeit betroffen — ZERODOX läuft, das Postfach ist erreichbar, die Meldung kommt an.
Die Prüffrage von oben beantwortet sich für diese ganze Klasse mit "Nein" (kein
Discord-only-Zwang). Dazu kommt die ausdrückliche Owner-Vorgabe: ZERODOX soll die zentrale
Sammelstelle für die Infrastruktur werden, weil dort Oberfläche, Nutzer und Push-System liegen
— Fremdprojekt-Wächter gehören also grundsätzlich dazu.

**Warum trotzdem nicht in dieser Welle:** #1983 hat bewusst nur den Piloten umgestellt. Erst
muss sich im Betrieb zeigen, dass die Entdopplung greift und das Postfach nicht flutet — sieben
weitere Wächter gleichzeitig scharf zu schalten wäre genau die Sammelauslieferung, die dieses
Konzept vermeiden will. Jede Umstellung erfolgt danach als eigener, kleiner Schritt pro Projekt.

Die Tabelle bleibt als Ausgangspunkt für die jeweilige Einzel-Umstellung erhalten — sie war
zuvor als offene Erlaubnis-Frage formuliert, ist jetzt als Vorbereitungsnotiz zu lesen:

| Watchdog | Warum postfach-fähig (Prüffrage) | Vorbereitung vor der Einzel-Umstellung |
|---|---|---|
| `zerodox-akquise-ai-watchdog` | Prüft `172.19.0.1:9300/health` — die Akquise-AI-Bridge, eine ZERODOX-Nachbarkomponente im selben Docker-Netz. Fällt sie aus, bleiben ZERODOX-Web, -DB und Postfach trotzdem erreichbar — die Prüffrage greift nicht. | Keine besondere — folgt demselben Muster wie der Pilot. |
| `guildscout-watchdog`, `mayday-sim-watchdog`, `mayday-ci-runner-watchdog`, `mayday-scheduler-watchdog` | Reine Erreichbarkeits-Wächter für **andere** Projekte (GuildScout, mayday-sim) — das ZERODOX-Postfach bliebe erreichbar, wenn nur GuildScout/mayday-sim ausfällt. | Keine besondere — folgt demselben Muster wie der Pilot. |
| `ai-agent-framework-watchdog` | Meldet Prozess-State, keine ZERODOX-Verfügbarkeitsaussage. | Prüft **gemischt** `zerodox-support-agent`/`seo-agent` (ZERODOX) **und** `guildscout-feedback-agent` (fremd) — vor der Umstellung klären, ob eine Aufteilung sinnvoll ist, damit ZERODOX- und Fremdprojekt-Anteil getrennt zuordenbar bleiben. |
| `cmdshadow-design-watchdog` | Prüft `cmdshadow-design-healthcheck.service` — ein eigenständiges Tool-Projekt, keine ZERODOX-Verfügbarkeitsaussage. | Keine besondere — folgt demselben Muster wie der Pilot. |

## Referenz

- Sende-Helfer: `scripts/lib/postfach-send.sh`
- Pilot-Umsetzung: `scripts/doku-drift-watchdog.sh`
- Vertrag ZERODOX-Seite: `web/src/app/api/internal/notifications/ingest/route.ts`
- Vollständige Watchdog-Übersicht (Ports/Cycles): `deploy/MONITORING_SETUP.md`,
  `~/.claude/rules/infrastructure.md` (ZERODOX-Repo, Tabelle "Externes Service-Monitoring")
