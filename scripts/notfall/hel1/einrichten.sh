#!/usr/bin/env bash
#
# einrichten.sh — richtet Wächter + Notfall-Agent auf HEL1 ein (ZERODOX #5627). Idempotent.
#
# Aufruf vom Hauptserver (kopiert sich selbst samt Dateien):
#   scp -r scripts/notfall/hel1 zerodox-ci-hel1:/tmp/notfall-hel1
#   scp ~/.local/share/claude/versions/<v> zerodox-ci-hel1:/tmp/notfall-hel1/claude
#   ssh zerodox-ci-hel1 bash /tmp/notfall-hel1/einrichten.sh
#
# Legt an: Benutzer `notfall` (ohne Login-Shell-Rechte darüber hinaus, kein sudo, kein docker),
# /opt/notfall (root-eigen, für notfall nur lesbar), /var/lib/notfall (Zustand),
# /etc/notfall/{notfall,claude}.env (0640 root:notfall; Werte trägt der Betreiber ein),
# SSH-Schlüssel + Host-Alias `notfall-haupt`, systemd-Dienst notfall-waechter.service.
set -euo pipefail
QUELLE="$(cd "$(dirname "$0")" && pwd)"

id notfall >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/notfall --shell /usr/sbin/nologin notfall
install -d -m 0755 -o root -g root /opt/notfall /opt/notfall/bin
install -m 0755 -o root -g root "$QUELLE/notfall-waechter.sh" "$QUELLE/notfall-agent.sh" "$QUELLE/notfall-ssh" /opt/notfall/
install -m 0644 -o root -g root "$QUELLE/notfall-prompt.md" /opt/notfall/
[ -f "$QUELLE/claude" ] && install -m 0755 -o root -g root "$QUELLE/claude" /opt/notfall/bin/claude
install -d -m 0750 -o notfall -g notfall /var/lib/notfall /var/lib/notfall/agent /var/lib/notfall/.ssh
install -d -m 0750 -o root -g notfall /etc/notfall
for f in notfall.env claude.env; do
  [ -f "/etc/notfall/$f" ] || install -m 0640 -o root -g notfall /dev/null "/etc/notfall/$f"
done

# SSH: eigener Schlüssel, fester Host-Alias. Der Hauptserver erzwingt command=notfall-wrapper.
if [ ! -f /var/lib/notfall/.ssh/id_ed25519 ]; then
  sudo -u notfall ssh-keygen -q -t ed25519 -N '' -C notfall-hel1 -f /var/lib/notfall/.ssh/id_ed25519
fi
cat >/var/lib/notfall/.ssh/config <<'EOF'
Host notfall-haupt
  HostName 10.8.0.1
  Port 47822
  User cmdshadow
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  BatchMode yes
  ConnectTimeout 8
  StrictHostKeyChecking yes
EOF
chown notfall:notfall /var/lib/notfall/.ssh/config; chmod 0600 /var/lib/notfall/.ssh/config
if ! sudo -u notfall ssh-keygen -F '[10.8.0.1]:47822' -f /var/lib/notfall/.ssh/known_hosts >/dev/null 2>&1; then
  ssh-keyscan -p 47822 -t ed25519 10.8.0.1 2>/dev/null >>/var/lib/notfall/.ssh/known_hosts
  chown notfall:notfall /var/lib/notfall/.ssh/known_hosts
fi

cat >/etc/systemd/system/notfall-waechter.service <<'EOF'
[Unit]
Description=Notfall-Wächter — Außenprüfung des Hauptservers, Notfall-Agent, Totalausfall-Eskalation (ZERODOX #5627)
After=network-online.target wg-quick@wg0.service
Wants=network-online.target

[Service]
Type=simple
User=notfall
Group=notfall
ExecStart=/opt/notfall/notfall-waechter.sh
Restart=always
RestartSec=30
# Der Agent (Kindprozess) darf bis zu 15 min laufen; Ressourcen begrenzt,
# damit ein Notfall-Lauf die CI-Runner nicht verdrängt — und umgekehrt.
MemoryMax=2G
CPUWeight=200
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/notfall
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now notfall-waechter.service
echo "Öffentlicher Schlüssel für ~cmdshadow/.ssh/authorized_keys auf dem Hauptserver:"
cat /var/lib/notfall/.ssh/id_ed25519.pub
