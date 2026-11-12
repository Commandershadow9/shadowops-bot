#!/usr/bin/env bash
#
# einrichten-hel1.sh: Richtet den Notbetrieb auf HEL1 ein oder aktualisiert ihn (ZERODOX #5629).
# Idempotent. Aufruf als root auf HEL1, aus einer Kopie dieses Verzeichnisses:
#
#   bash einrichten-hel1.sh <spiegel-pubkey-datei>
#
# Der DNS-Token kommt NICHT über dieses Skript, sondern getrennt per stdin direkt in
# /etc/notbetrieb/notbetrieb.env (siehe README). Fehlt er, läuft der Umschalter, schaltet aber
# nichts um und meldet das beim ersten Versuch.
set -euo pipefail
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBKEY="${1:?Pfad zum öffentlichen Spiegel-Schlüssel fehlt}"
HEL1_IP="157.180.55.95"
IMAGE="nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6"

# ① Benutzer: eigene Shell nötig, weil rrsync als erzwungener Befehl über sie läuft.
id notbetrieb >/dev/null 2>&1 || useradd --system --home-dir /var/lib/notbetrieb --create-home --shell /bin/bash notbetrieb
UID_NB="$(id -u notbetrieb)"; GID_NB="$(id -g notbetrieb)"
passwd -l notbetrieb >/dev/null 2>&1 || true

install -d -m 755 -o root -g root /opt/notbetrieb /srv/notbetrieb
install -d -m 755 -o notbetrieb -g notbetrieb /srv/notbetrieb/daten
install -d -m 750 -o root -g notbetrieb /etc/notbetrieb
install -d -m 700 -o notbetrieb -g notbetrieb /var/lib/notbetrieb /var/lib/notbetrieb/.ssh

install -m 755 -o root -g root "$HIER/notbetrieb-umschalter.sh" /opt/notbetrieb/notbetrieb-umschalter.sh
# Verzeichnis statt Einzeldatei einbinden: Ein Datei-Bind-Mount hält die alte Inode fest,
# ein Update per install käme im Container nie an (dieselbe Falle wie docker.sock, #5627).
install -d -m 755 -o root -g root /opt/notbetrieb/conf
install -m 644 -o root -g root "$HIER/nginx-notbetrieb.conf" /opt/notbetrieb/conf/default.conf
rm -f /opt/notbetrieb/nginx-notbetrieb.conf

# ② Konfiguration: ntfy/Discord vom Notfall-Wächter übernehmen, Token bleibt unangetastet.
ENV=/etc/notbetrieb/notbetrieb.env
if [ ! -f "$ENV" ]; then
  ( umask 077; : >"$ENV" ); chown notbetrieb:notbetrieb "$ENV"; chmod 600 "$ENV"
fi
for k in NTFY_TOPIC DISCORD_WEBHOOK; do
  if ! grep -q "^$k=" "$ENV"; then
    grep "^$k=" /etc/notfall/notfall.env >>"$ENV" 2>/dev/null || true
  fi
done

# ③ Spiegel-Schlüssel: kann nur nach /srv/notbetrieb/daten schreiben, nur vom Hauptserver.
key="$(tr -d '\r\n' <"$PUBKEY")"
[[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || { echo "kein ed25519-Schlüssel"; exit 1; }
printf 'command="/usr/bin/rrsync /srv/notbetrieb/daten",from="10.8.0.1",restrict %s\n' "$key" >/var/lib/notbetrieb/.ssh/authorized_keys
chown notbetrieb:notbetrieb /var/lib/notbetrieb/.ssh/authorized_keys; chmod 600 /var/lib/notbetrieb/.ssh/authorized_keys

# ④ Webserver: unprivilegiert, read-only, ohne Capabilities.
if [ -f /srv/notbetrieb/daten/tls/fullchain.pem ]; then
  docker rm -f notbetrieb-web >/dev/null 2>&1 || true
  docker run -d --name notbetrieb-web --restart unless-stopped \
    --user "$UID_NB:$GID_NB" --read-only --tmpfs /tmp:size=16m \
    --cap-drop ALL --security-opt no-new-privileges --memory 128m --pids-limit 64 \
    -p "$HEL1_IP:80:8080" -p "$HEL1_IP:443:8443" \
    -v /opt/notbetrieb/conf:/etc/nginx/conf.d:ro \
    -v /srv/notbetrieb/daten:/srv/daten:ro \
    "$IMAGE" >/dev/null
  echo "notbetrieb-web gestartet"
else
  echo "Noch keine Spiegeldaten: Webserver wird beim nächsten Aufruf gestartet (erst Spiegel laufen lassen)."
fi

# ⑤ Dienste: Umschalter + tägliches nginx-Reload (Zertifikat wechselt etwa alle 60 Tage).
cat >/etc/systemd/system/notbetrieb-umschalter.service <<'EOF'
[Unit]
Description=Notbetrieb zerodox.de: DNS-Umschaltung bei Totalausfall des Hauptservers (ZERODOX #5629)
After=network-online.target
Wants=network-online.target

[Service]
User=notbetrieb
Group=notbetrieb
ExecStart=/opt/notbetrieb/notbetrieb-umschalter.sh
Restart=always
RestartSec=30
MemoryMax=256M
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/notbetrieb
CapabilityBoundingSet=CAP_NET_RAW
AmbientCapabilities=CAP_NET_RAW

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/notbetrieb-reload.service <<'EOF'
[Unit]
Description=Notbetrieb: nginx lädt das gespiegelte Zertifikat neu (ZERODOX #5629)
[Service]
Type=oneshot
ExecStart=/usr/bin/docker exec notbetrieb-web nginx -s reload
EOF
cat >/etc/systemd/system/notbetrieb-reload.timer <<'EOF'
[Unit]
Description=Notbetrieb: nginx täglich neu laden
[Timer]
OnCalendar=*-*-* 05:20:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now notbetrieb-reload.timer >/dev/null
systemctl enable notbetrieb-umschalter.service >/dev/null
# Ohne Spiegeldaten noch nicht starten, sonst meldet er sofort „Spiegel veraltet“.
if [ -f /srv/notbetrieb/daten/stand ]; then
  systemctl restart notbetrieb-umschalter.service
fi
echo "Umschalter: $(systemctl is-active notbetrieb-umschalter)"
