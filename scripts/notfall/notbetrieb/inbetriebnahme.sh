#!/usr/bin/env bash
#
# inbetriebnahme.sh: Nimmt den Notbetrieb (ZERODOX #5629) in einem Zug in Betrieb.
# Aufruf auf dem Hauptserver als cmdshadow, idempotent:
#
#   bash ~/shadowops-bot/scripts/notfall/notbetrieb/inbetriebnahme.sh
#
# Schritte: Spiegel-Schlüssel → HEL1 einrichten → erster Spiegel → Webserver starten →
# sicherstellen, dass KEIN DNS-Token auf HEL1 liegt → TTL von zerodox.de auf 60 s →
# Probelauf über notbetrieb-test.zerodox.de (vom Hauptserver aus) → Spiegel-Timer → Endkontrolle.
#
# ⚠️ Der DNS-Token gehört NICHT nach HEL1: HEL1 ist CI-Host, die Runner sind in der docker-Gruppe
# und damit root-äquivalent; der Token könnte die ganze Zone samt MX übernehmen. Ohne Token
# meldet der Umschalter „von Hand umschalten“ (README, Sicherheitsgrenzen).
# Der Token geht an curl nur über stdin (-K -), nie als Prozessargument.
# Am Kunden-DNS (zerodox.de A) ändert das Skript NUR die TTL, nicht das Ziel.
set -euo pipefail
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEL1="${NOTFALL_HEL1:-zerodox-ci-hel1}"
KEY="$HOME/.ssh/id_ed25519_notbetrieb_spiegel"
TOKEN_DATEI="$HOME/.config/hetzner/zerodox-cloud-api-token"
ZONE_ID=1395844
schritt() { printf '\n── %s\n' "$*"; }

schritt "1/8 Spiegel-Schlüssel"
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C notbetrieb-spiegel -f "$KEY"
echo "ok: $KEY"

schritt "2/8 HEL1 einrichten (Benutzer notbetrieb, Umschalter, nginx-Konfiguration)"
ssh "$HEL1" 'rm -rf /root/notbetrieb-einrichtung && mkdir -p /root/notbetrieb-einrichtung'
scp -q "$HIER/einrichten-hel1.sh" "$HIER/notbetrieb-umschalter.sh" "$HIER/nginx-notbetrieb.conf" "$KEY.pub" \
  "$HEL1:/root/notbetrieb-einrichtung/"
ssh "$HEL1" 'bash /root/notbetrieb-einrichtung/einrichten-hel1.sh /root/notbetrieb-einrichtung/id_ed25519_notbetrieb_spiegel.pub'
# Hostschlüssel für den Spiegel-Benutzer bekannt machen (gleicher Host wie der root-Zugang)
ssh-keygen -F '[10.8.0.12]:47822' >/dev/null || ssh-keyscan -p 47822 10.8.0.12 2>/dev/null >>"$HOME/.ssh/known_hosts"

schritt "3/8 Erster Spiegel"
sg docker -c "$HIER/notbetrieb-spiegel.sh"

schritt "4/8 Webserver auf HEL1 starten (braucht die Spiegeldaten)"
ssh "$HEL1" 'bash /root/notbetrieb-einrichtung/einrichten-hel1.sh /root/notbetrieb-einrichtung/id_ed25519_notbetrieb_spiegel.pub'

schritt "5/8 DNS-Token: bewusst NICHT auf HEL1 (ein vorhandener wird entfernt)"
ssh "$HEL1" 'set -e; f=/etc/notbetrieb/notbetrieb.env
  if grep -q "^HCLOUD_TOKEN=" "$f"; then
    { grep -v "^HCLOUD_TOKEN=" "$f" || true; } >"$f.neu"
    chown notbetrieb:notbetrieb "$f.neu"; chmod 600 "$f.neu"; mv "$f.neu" "$f"
    systemctl restart notbetrieb-umschalter; echo "Token von HEL1 entfernt"
  else
    echo "kein Token auf HEL1: ok"
  fi'

schritt "6/8 TTL zerodox.de A → 60 s (Ziel bleibt unverändert)"
# printf ist ein Builtin: Der Token erscheint in keiner Prozessliste.
code=$(printf 'header = "Authorization: Bearer %s"\n' "$(tr -d '\r\n' <"$TOKEN_DATEI")" \
  | curl -s -K - -o /dev/null -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -d '{"ttl":60}' \
    "https://api.hetzner.cloud/v1/zones/$ZONE_ID/rrsets/@/A/actions/change_ttl")
echo "change_ttl: HTTP $code"

schritt "7/8 Probelauf ohne Kunden (notbetrieb-test.zerodox.de, vom Hauptserver aus)"
# Token nur als Umgebung dieses einen Aufrufs (nicht in ps sichtbar), Zustand in einem Wegwerf-Verzeichnis.
probe_state="$(mktemp -d)"
HCLOUD_TOKEN="$(tr -d '\r\n' <"$TOKEN_DATEI")" NOTBETRIEB_STATE="$probe_state" \
  bash "$HIER/notbetrieb-umschalter.sh" test-dns
rm -rf "$probe_state"

schritt "8/8 Spiegel-Timer auf dem Hauptserver"
systemctl --user link "$HOME/shadowops-bot/deploy/notbetrieb-spiegel.service" "$HOME/shadowops-bot/deploy/notbetrieb-spiegel.timer" >/dev/null 2>&1 || true
systemctl --user daemon-reload
systemctl --user enable --now notbetrieb-spiegel.timer
systemctl --user list-timers --no-pager | grep notbetrieb || true

schritt "Endkontrolle"
for p in / /impressum /datenschutz "/creator/$(ssh "$HEL1" 'ls /srv/notbetrieb/daten/impressen | head -1 | sed s/.html$//')/impressum"; do
  printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' --connect-to ::157.180.55.95: "https://zerodox.de$p")" "$p"
done
ssh "$HEL1" 'sudo -u notbetrieb /opt/notbetrieb/notbetrieb-umschalter.sh status'
echo "DNS zerodox.de (autoritativ): $(dig +noall +answer A zerodox.de @hydrogen.ns.hetzner.com)"
echo; echo "Fertig. Erwartet: / 503, /impressum 200, /datenschutz 200, Kunden-Impressum 200, modus leer/normal, token=nein, DNS weiter 88.99.160.210."
