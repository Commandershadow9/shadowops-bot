#!/usr/bin/env bash
#
# notbetrieb-spiegel.sh: Spiegelt alles, was der Notbetrieb auf HEL1 braucht (ZERODOX #5629).
# Läuft alle 5 min auf dem Hauptserver (systemd --user, über `sg docker`).
#
#   impressen/  Kunden-Impressum-Schnappschüsse aus zerodox-maintenance (#4262).
#               rsync --delete: Was hier verschwindet (Impressum depubliziert oder
#               gesperrt), verschwindet beim nächsten Lauf auch auf HEL1
#               (DSGVO, zweite Speicherstelle, siehe DSGVO_LOESCHKONZEPT).
#   seiten/     Notseite, dazu Impressum und Datenschutz von ZERODOX, aus der Live-Seite
#               herausgelöst (Positivliste, ohne Skripte). Schlägt das Abrufen fehl,
#               bleibt der letzte gute Stand liegen.
#   tls/        Zertifikat zerodox.de/www aus Traefik (acme.json). LE erneuert 30 Tage
#               vor Ablauf, die Kopie hat im Ernstfall also mindestens 30 Tage Restlaufzeit.
#   stand       Zeitstempel des letzten Laufs mit vollständig gelesenen Impressen; der
#               Umschalter auf HEL1 meldet, wenn er > 1 h alt ist.
#
# Ziel: notbetrieb@10.8.0.12 mit eigenem Schlüssel; auf HEL1 erzwingt `rrsync` das
# Zielverzeichnis /srv/notbetrieb/daten (der Schlüssel kann nichts anderes).
set -uo pipefail
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARBEIT="${NOTBETRIEB_ARBEIT:-$HOME/.local/state/notbetrieb-spiegel}"
SCHLUESSEL="${NOTBETRIEB_SCHLUESSEL:-$HOME/.ssh/id_ed25519_notbetrieb_spiegel}"
ZIEL="${NOTBETRIEB_ZIEL:-notbetrieb@10.8.0.12}"
HAUPT_IP="${NOTFALL_HAUPT_IP:-88.99.160.210}"
D="$ARBEIT/daten"
umask 022
mkdir -p "$D/impressen" "$D/seiten" "$D/tls" "$ARBEIT/tmp"
fehler=0

# ① Kunden-Impressen: komplett neu einlesen, nur gültige Slug-Dateien übernehmen
neu="$(mktemp -d "$ARBEIT/tmp/imp.XXXX")"
if docker exec zerodox-maintenance tar -C /srv/impressum-schnappschuesse -cf - . 2>/dev/null \
     | tar -C "$neu" -xf - --no-same-owner --no-same-permissions 2>/dev/null; then
  find "$neu" -mindepth 1 -regextype posix-extended \( -type f ! -regex '.*/[a-z0-9][a-z0-9-]{0,99}\.html' -o ! -type f \) -delete 2>/dev/null
  find "$neu" -mindepth 1 -type d -empty -delete 2>/dev/null
  rsync -a --delete "$neu/" "$D/impressen/"
  chmod 644 "$D"/impressen/*.html 2>/dev/null || true
else
  echo "Schnappschüsse nicht lesbar (zerodox-maintenance?)"; fehler=1
fi
rm -rf "$neu"

# ② Seiten
install -m 644 "$HIER/notseite.html" "$D/seiten/notseite.html"
for s in impressum:Impressum datenschutz:Datenschutz; do
  pfad="${s%%:*}"; titel="${s#*:}"; roh="$ARBEIT/tmp/$pfad.roh"; aus="$ARBEIT/tmp/$pfad.html"
  if curl -sf -m 30 --connect-to "::$HAUPT_IP:" -o "$roh" "https://zerodox.de/$pfad" \
     && /usr/bin/python3 -I "$HIER/seite_extrahieren.py" "$titel" "$roh" "$HIER/seite-vorlage.html" >"$aus"; then
    install -m 644 "$aus" "$D/seiten/$pfad.html"
  else
    echo "$pfad: Abruf/Extraktion fehlgeschlagen, letzter Stand bleibt"
  fi
  rm -f "$roh" "$aus"
done

# ③ Zertifikat
acme="$ARBEIT/tmp/acme.json"
( umask 077
  if docker exec sicherheitsdienst-traefik cat /letsencrypt/acme.json >"$acme" 2>/dev/null; then
    zert="$(jq -r '[.[].Certificates[]? | select(.domain.main=="zerodox.de")][0] // empty' "$acme")"
    if [ -n "$zert" ]; then
      jq -r '.certificate' <<<"$zert" | base64 -d >"$ARBEIT/tmp/fullchain.pem"
      jq -r '.key' <<<"$zert" | base64 -d >"$ARBEIT/tmp/privkey.pem"
      if openssl x509 -in "$ARBEIT/tmp/fullchain.pem" -noout -checkend 86400 >/dev/null 2>&1 \
         && [ "$(openssl x509 -in "$ARBEIT/tmp/fullchain.pem" -noout -pubkey | sha256sum)" \
            = "$(openssl pkey -in "$ARBEIT/tmp/privkey.pem" -pubout 2>/dev/null | sha256sum)" ]; then
        install -m 600 "$ARBEIT/tmp/fullchain.pem" "$D/tls/fullchain.pem"
        install -m 600 "$ARBEIT/tmp/privkey.pem" "$D/tls/privkey.pem"
      else
        echo "Zertifikat ungültig oder Schlüssel passt nicht, letzter Stand bleibt"
      fi
    else
      echo "kein Zertifikat für zerodox.de in acme.json"
    fi
  else
    echo "acme.json nicht lesbar"
  fi
  rm -f "$acme" "$ARBEIT/tmp/fullchain.pem" "$ARBEIT/tmp/privkey.pem" )

# Nur bei vollständig gelesenen Impressen erneuern: Bleibt der Stand stehen, meldet der
# Umschalter nach 1 h „Spiegel veraltet“. Sonst blieben depublizierte Impressen still auf HEL1.
[ "$fehler" = 0 ] && date +%s >"$D/stand"

# ④ Übertragen
if ! rsync -a --delete --delay-updates -e "ssh -p 47822 -i $SCHLUESSEL -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15" \
     "$D/" "$ZIEL:"; then
  echo "rsync nach HEL1 fehlgeschlagen"; fehler=1
fi
echo "Impressen: $(find "$D/impressen" -name '*.html' | wc -l) · Zertifikat bis: $(openssl x509 -in "$D/tls/fullchain.pem" -noout -enddate 2>/dev/null | cut -d= -f2)"
exit "$fehler"
