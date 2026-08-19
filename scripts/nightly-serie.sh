#!/usr/bin/env bash
#
# nightly-serie.sh — Wie viele Nightly-Läufe sind ununterbrochen rot?
#
# Gibt drei Zeilen `KEY=WERT` aus:
#
#     SERIE=14
#     ZUSTAND=auffaellig          # ok | auffaellig | unbekannt
#     LETZTER_GRUENER=2026-08-05  # leer, wenn keiner in der Abfrage
#
# ─── Warum es das braucht (ZERODOX #2467) ──────────────────────────────────
#
# Am 19.08.2026 gemessen: "Web Quality Nightly" war 14 von 15 Läufen rot,
# ununterbrochen seit dem 06.08. Kein einziger grüner Lauf in zwei Wochen. Die
# Fehler waren echt — vier von sechs Shards mit konkret scheiternden Tests —,
# und **keiner davon war als Issue erfasst**.
#
# Der Workflow alarmiert bei jedem Fehlschlag in Discord. Das ist ein
# EREIGNIS-Signal: "heute Nacht war es rot". In der vierzehnten Nacht sah es
# genauso aus wie in der ersten.
#
# "14 Nächte in Folge" ist eine andere Aussage als "eine Nacht" — und die sendet
# heute niemand. Ein Dauerzustand, der wie ein Einzelvorfall gemeldet wird, wird
# zu Hintergrundrauschen. Derselbe Fehlermodus wie beim Einmal-Alarm des
# runner-vm-disk-Watchdogs (#2425), nur eine Ebene höher.
#
# Nutzung:
#   scripts/nightly-serie.sh                      # fragt gh ab
#   NIGHTLY_LAEUFE_JSON='[...]' scripts/…         # vorgegebene Liste (Tests)

set -euo pipefail

readonly WORKFLOW="${NIGHTLY_WORKFLOW:-Web Quality Nightly}"
readonly REPO="${NIGHTLY_REPO:-Commandershadow9/ZERODOX}"
readonly LIMIT="${NIGHTLY_LIMIT:-20}"
# Ab wann gilt eine Serie als Befund. Zwei rote Nächte können eine echte
# Regression sein, die gerade jemand behebt; ab der dritten sieht niemand mehr
# hin.
readonly SCHWELLE="${NIGHTLY_SERIE_SCHWELLE:-3}"

if [[ -n "${NIGHTLY_LAEUFE_JSON:-}" ]]; then
    laeufe="$NIGHTLY_LAEUFE_JSON"
else
    laeufe="$(gh run list --repo "$REPO" --workflow "$WORKFLOW" --limit "$LIMIT" \
              --json conclusion,createdAt 2>/dev/null)" || laeufe='[]'
fi

# SERIE = abgeschlossene Läufe seit dem letzten grünen.
#
# Bewusst NICHT "Anzahl roter Läufe": Die Frage, die dieser Watchdog beantworten
# soll, lautet "wie lange ist schon nichts mehr durchgelaufen?" — und darauf
# antwortet der Abstand zum letzten Erfolg, nicht die Zahl der Fehlschläge.
#
# ⚠️ `cancelled` zählt deshalb mit, ohne selbst ein Fehlschlag zu sein: Er
# beweist keinen Erfolg. Wer ihn überspringt, meldet eine kürzere Serie, als
# tatsächlich seit dem letzten grünen Lauf vergangen ist. Genau so ein Eintrag
# stand am 15.08. mitten in der 14-Tage-Serie.
#
# Noch laufende Einträge (`conclusion == null`) zählen dagegen NICHT mit — sie
# könnten in Minuten grün werden, und ein Ergebnis, das es noch nicht gibt, darf
# keine Aussage tragen.
serie="$(jq -r '
  [ .[] | .conclusion ]
  | reduce .[] as $c ({fertig: false, n: 0};
      if .fertig then .
      elif $c == "success" then .fertig = true
      elif $c == null then .              # läuft noch: überspringen
      else .n += 1                        # failure ODER cancelled
      end)
  | .n
' <<<"$laeufe" 2>/dev/null || echo 0)"

# Gab es überhaupt ein verwertbares Ergebnis? Eine leere Abfrage heisst NICHT
# "alles gut" — sie kann bedeuten, dass der Workflow abgeschaltet wurde oder
# umbenannt ist. Ein stilles "ok" wäre hier die schlimmere Antwort.
ergebnisse="$(jq '[ .[] | select(.conclusion == "success" or .conclusion == "failure") ] | length' \
              <<<"$laeufe" 2>/dev/null || echo 0)"

letzter_gruener="$(jq -r '
  [ .[] | select(.conclusion == "success") ] | first | .createdAt // "" | .[0:10]
' <<<"$laeufe" 2>/dev/null || echo "")"

if [[ "$ergebnisse" -eq 0 ]]; then
    zustand="unbekannt"
elif [[ "$serie" -ge "$SCHWELLE" ]]; then
    zustand="auffaellig"
else
    zustand="ok"
fi

echo "SERIE=${serie}"
echo "ZUSTAND=${zustand}"
echo "LETZTER_GRUENER=${letzter_gruener}"
