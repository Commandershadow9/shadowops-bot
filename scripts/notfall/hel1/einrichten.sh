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
#
# ssh-config und known_hosts liegen in /opt/notfall/ssh/ (root, 0644), NICHT im Heim von
# `notfall`: Der Agent läuft als `notfall` und dürfte sonst Ziel und Hostschlüssel umschreiben.
# ⚠️ Offener Folgeschritt: Agent und Wächter laufen noch unter DEMSELBEN Benutzer. Der Agent
# könnte damit den Zustand des Wächters (/var/lib/notfall) und /etc/notfall/notfall.env lesen.
# Die Trennung (eigener Benutzer für den Agenten) ist noch nicht umgesetzt; InaccessiblePaths
# in der Unit geht nicht, weil der Wächter selbst die env braucht.
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
SSHDIR=/opt/notfall/ssh
install -d -m 0755 -o root -g root "$SSHDIR"
cat >"$SSHDIR/config.neu" <<'EOF'
Host notfall-haupt
  HostName 10.8.0.1
  Port 47822
  User cmdshadow
  IdentityFile /var/lib/notfall/.ssh/id_ed25519
  IdentitiesOnly yes
  BatchMode yes
  ConnectTimeout 8
  StrictHostKeyChecking yes
  UserKnownHostsFile /opt/notfall/ssh/known_hosts
  GlobalKnownHostsFile /dev/null
EOF
chown root:root "$SSHDIR/config.neu"; chmod 0644 "$SSHDIR/config.neu"; mv "$SSHDIR/config.neu" "$SSHDIR/config"
# Hostschlüssel NUR aus einem frischen Scan, geprüft gegen den fest hinterlegten Fingerabdruck
# des Hauptservers. Die alte known_hosts im Heim von notfall wird bewusst nicht übernommen:
# Genau dieser Benutzer (der Agent) könnte sie beschreiben oder durch einen Symlink ersetzen.
# Bei Neuaufsetzen des Hauptservers: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub dort.
HAUPT_HOSTKEY_FP="${HAUPT_HOSTKEY_FP:-SHA256:r/c+WwuhVMZ5j6sZM6XA9Q5yOBOsaqPbd548zenw/Mo}"
scan="$(ssh-keyscan -p 47822 -t ed25519 10.8.0.1 2>/dev/null | grep -E '^\[10\.8\.0\.1\]:47822 ssh-ed25519 ' | head -1)"
if [ -n "$scan" ] && [ "$(ssh-keygen -lf - <<<"$scan" | awk '{print $2}')" = "$HAUPT_HOSTKEY_FP" ]; then
  printf '%s\n' "$scan" >"$SSHDIR/known_hosts.neu" && mv "$SSHDIR/known_hosts.neu" "$SSHDIR/known_hosts"
else
  echo "FEHLER: Hostschlüssel des Hauptservers passt nicht zu $HAUPT_HOSTKEY_FP — known_hosts unverändert" >&2
  [ -f "$SSHDIR/known_hosts" ] || exit 1
fi
[ -L /var/lib/notfall/.ssh ] || rm -f /var/lib/notfall/.ssh/known_hosts
chown root:root "$SSHDIR/known_hosts"; chmod 0644 "$SSHDIR/known_hosts"
# Die alte, für notfall schreibbare Konfiguration wird nicht mehr gelesen (notfall-ssh nutzt -F).
[ -L /var/lib/notfall/.ssh ] || rm -f /var/lib/notfall/.ssh/config  # nie einem Symlink des Agenten folgen

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
