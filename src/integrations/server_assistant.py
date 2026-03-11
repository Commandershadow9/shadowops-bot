"""
Server Assistant — Intelligenter Server-Assistent fuer ShadowOps Bot

Ersetzt das alte Learning-System (200+ sinnlose AI-Calls/Tag) durch:
1. Taegliches Housekeeping (lokal, 0 Token)
2. Woechentlicher Security Intelligence Report (1 AI-Call)
3. Git-Push Security Review (event-getrieben, nur bei Bedarf)

Prinzip: "AI nur wenn was passiert ist"
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import discord

logger = logging.getLogger('shadowops.assistant')

# Dateimuster die bei Git-Push ein Security-Review ausloesen
SECURITY_PATTERNS = [
    '.env', 'docker-compose', 'dockerfile',
    'auth', 'middleware', 'login', 'session', 'token', 'secret',
    'config.yaml', 'config.json', '.htaccess',
    'package.json', 'requirements.txt', 'go.mod', 'go.sum',
    'pyproject.toml', 'cargo.toml',
    '.github/workflows', 'makefile',
    'firewall', 'ufw', 'iptables', 'nginx', 'traefik',
    'ssl', 'cert', 'key.pem',
]


class ServerAssistant:
    """
    Intelligenter Server-Assistent.

    - Taegliche Checks sind LOKAL (0 Token)
    - AI wird nur fuer den Wochenbericht genutzt (1 Call)
    - Git-Push Reviews sind event-getrieben (nur bei Security-Changes)
    """

    def __init__(self, bot, config, ai_service):
        self.bot = bot
        self.config = config
        self.ai_service = ai_service
        self.is_running = False

        self.daily_task = None
        self.weekly_task = None

        # Scheduling
        self.daily_hour = 6   # 06:00
        self.weekly_day = 0   # Montag
        self.weekly_hour = 7  # 07:00

        # Thresholds
        self.disk_warn_pct = 80
        self.disk_crit_pct = 90
        self.mem_warn_pct = 85

        # CLI-Version-Tracking (meldet nur bei Aenderungen)
        self._last_cli_versions: Dict[str, str] = {}

        # Channel (ai_learning ist jetzt frei, oder bot_status)
        self.channel_id = (
            config.channels.get('ai_learning', 0)
            or config.channels.get('bot_status', 0)
        )

        logger.info("Server Assistant initialisiert")

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.daily_task = asyncio.create_task(self._daily_loop())
        self.weekly_task = asyncio.create_task(self._weekly_loop())
        logger.info(
            f"Server Assistant gestartet "
            f"(Daily {self.daily_hour}:00, Weekly Mo {self.weekly_hour}:00)"
        )

    async def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        for task in [self.daily_task, self.weekly_task]:
            if task and not task.done():
                task.cancel()
        logger.info("Server Assistant gestoppt")

    # ================================================================
    # SCHEDULING
    # ================================================================

    async def _wait_until(self, hour: int, weekday: int = None):
        """Warte bis zur naechsten Ausfuehrungszeit"""
        now = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)

        if weekday is not None:
            days_ahead = weekday - now.weekday()
            if days_ahead < 0 or (days_ahead == 0 and now >= target):
                days_ahead += 7
            target += timedelta(days=days_ahead)
        elif now >= target:
            target += timedelta(days=1)

        wait_secs = (target - now).total_seconds()
        logger.info(
            f"Naechster Run in {wait_secs/3600:.1f}h "
            f"({target.strftime('%a %d.%m. %H:%M')})"
        )
        await asyncio.sleep(wait_secs)

    # ================================================================
    # DAILY HOUSEKEEPING (lokal, 0 Token)
    # ================================================================

    async def _daily_loop(self):
        await asyncio.sleep(60)  # Startup-Delay
        while self.is_running:
            try:
                await self._wait_until(self.daily_hour)
                await self._run_daily_checks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily check Fehler: {e}", exc_info=True)
            await asyncio.sleep(3600)  # Doppel-Run verhindern

    async def _run_daily_checks(self):
        logger.info("Starte taegliche Server-Checks...")
        issues = []
        info = []
        actions = []

        # 1. Disk Space
        disk = await self._check_disk()
        issues.extend(disk.get('issues', []))
        info.extend(disk.get('info', []))

        # 2. Memory & Swap
        mem = await self._check_memory()
        issues.extend(mem.get('issues', []))
        info.extend(mem.get('info', []))

        # 3. Docker Health
        docker = await self._check_docker()
        issues.extend(docker.get('issues', []))
        info.extend(docker.get('info', []))

        # 4. Service Status
        svc = await self._check_services()
        issues.extend(svc.get('issues', []))
        info.extend(svc.get('info', []))

        # 5. Grosse Log-Dateien
        logs = await self._check_large_logs()
        issues.extend(logs.get('issues', []))

        # 6. Docker Dangling Cleanup (sicher, taeglich)
        cleaned = await self._docker_daily_cleanup()
        if cleaned:
            actions.append(cleaned)

        # 7. Container-Restart-Checks
        restarts = await self._check_container_restarts()
        issues.extend(restarts.get('issues', []))

        # 8. CLI-Version-Updates (Codex + Claude)
        cli_updates = await self._check_cli_versions()
        info.extend(cli_updates.get('info', []))

        if issues or actions:
            await self._send_daily_report(issues, info, actions)
        else:
            logger.info("Daily check: Alles in Ordnung")

    # -- Einzelne Checks --

    async def _check_disk(self) -> Dict:
        output = await self._cmd("df -h / --output=pcent,avail | tail -1")
        result = {'issues': [], 'info': []}
        if not output:
            return result
        parts = output.split()
        if not parts:
            return result
        try:
            pct = int(parts[0].replace('%', ''))
        except ValueError:
            return result
        avail = parts[1] if len(parts) > 1 else '?'
        if pct >= self.disk_crit_pct:
            result['issues'].append(f"KRITISCH: Disk {pct}% voll (nur {avail} frei)")
        elif pct >= self.disk_warn_pct:
            result['issues'].append(f"Disk {pct}% voll ({avail} frei)")
        else:
            result['info'].append(f"Disk: {pct}% belegt, {avail} frei")
        return result

    async def _check_memory(self) -> Dict:
        output = await self._cmd("free -m | grep Mem")
        result = {'issues': [], 'info': []}
        if not output:
            return result
        parts = output.split()
        if len(parts) < 3:
            return result
        total, used = int(parts[1]), int(parts[2])
        pct = (used / total) * 100 if total else 0

        swap_line = await self._cmd("free -m | grep Swap")
        swap_used = 0
        if swap_line:
            sp = swap_line.split()
            swap_used = int(sp[2]) if len(sp) >= 3 else 0

        swap_info = f", Swap: {swap_used} MB" if swap_used > 100 else ""
        if pct >= self.mem_warn_pct:
            result['issues'].append(f"RAM hoch: {pct:.0f}% ({used}/{total} MB){swap_info}")
        else:
            result['info'].append(f"RAM: {pct:.0f}% ({used}/{total} MB){swap_info}")
        return result

    async def _check_docker(self) -> Dict:
        result = {'issues': [], 'info': []}
        output = await self._cmd(
            'docker ps --format "{{.Names}}|{{.Status}}" 2>/dev/null'
        )
        if not output:
            return result
        running = 0
        for line in output.strip().split('\n'):
            if '|' not in line:
                continue
            name, status = line.split('|', 1)
            running += 1
            if 'Restarting' in status:
                result['issues'].append(f"Container '{name}' restartet staendig!")
        result['info'].append(f"Docker: {running} Container aktiv")
        return result

    async def _check_container_restarts(self) -> Dict:
        """Pruefe ob Container ungewoehnlich oft neu gestartet wurden"""
        result = {'issues': []}
        output = await self._cmd(
            'docker inspect --format "{{.Name}}|{{.RestartCount}}" '
            '$(docker ps -q) 2>/dev/null'
        )
        if not output:
            return result
        for line in output.strip().split('\n'):
            if '|' not in line:
                continue
            name, count_str = line.split('|', 1)
            name = name.lstrip('/')
            try:
                count = int(count_str)
            except ValueError:
                continue
            if count > 5:
                result['issues'].append(
                    f"Container '{name}' hat {count} Restarts"
                )
        return result

    async def _check_services(self) -> Dict:
        result = {'issues': [], 'info': []}
        user_services = [
            'guildscout-bot',
            'guildscout-feedback-agent',
            'zerodox-support-agent',
            'seo-agent',
        ]
        running = 0
        # XDG_RUNTIME_DIR nötig weil Bot als System-Service läuft,
        # aber User-Services über den User-D-Bus abgefragt werden müssen
        for svc in user_services:
            status = await self._cmd(
                f"XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active {svc} 2>/dev/null"
            )
            if status == 'active':
                running += 1
            else:
                result['issues'].append(f"Service '{svc}' ist {status or 'nicht aktiv'}")

        # System-Level Services
        for svc in ['shadowops-bot', 'earlyoom']:
            status = await self._cmd(f"systemctl is-active {svc} 2>/dev/null")
            if status != 'active':
                result['issues'].append(f"System-Service '{svc}' ist {status or 'nicht aktiv'}")

        result['info'].append(f"Services: {running}/{len(user_services)} User-Services aktiv")
        return result

    async def _check_large_logs(self) -> Dict:
        result = {'issues': []}
        output = await self._cmd(
            'find /var/log /home/cmdshadow/logs -name "*.log" -size +100M '
            '-exec ls -lh {} \\; 2>/dev/null | head -5'
        )
        if not output:
            return result
        for line in output.strip().split('\n'):
            parts = line.split()
            if len(parts) >= 9:
                result['issues'].append(f"Grosse Log: {parts[-1]} ({parts[4]})")
        return result

    async def _docker_daily_cleanup(self) -> Optional[str]:
        """Dangling Images taeglich entfernen (sicher)"""
        output = await self._cmd("docker image prune -f 2>/dev/null")
        if output and 'Total reclaimed space' in output:
            for line in output.split('\n'):
                if 'Total reclaimed space' in line:
                    space = line.split(':')[-1].strip()
                    if space != '0B':
                        return f"Docker Cleanup: {space} freigegeben"
        return None

    # -- Daily Report senden --

    async def _send_daily_report(self, issues, info, actions):
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.warning("Daily Report Channel nicht gefunden")
            return

        has_crit = any('KRITISCH' in i for i in issues)
        color = 0xFF0000 if has_crit else 0xFFA500 if issues else 0x00FF00

        embed = discord.Embed(
            title="Daily Server Check",
            color=color,
            timestamp=datetime.now()
        )

        if issues:
            embed.add_field(
                name="Probleme",
                value='\n'.join(f"- {i}" for i in issues[:10]),
                inline=False
            )
        if actions:
            embed.add_field(
                name="Aufgeraeumt",
                value='\n'.join(f"- {a}" for a in actions[:5]),
                inline=False
            )
        if info:
            embed.add_field(
                name="Status",
                value='\n'.join(f"- {i}" for i in info[:8]),
                inline=False
            )

        embed.set_footer(text="Server Assistant | Daily Check")

        try:
            await channel.send(embed=embed)
            logger.info(f"Daily Report: {len(issues)} Issues, {len(actions)} Actions")
        except Exception as e:
            logger.error(f"Daily Report senden fehlgeschlagen: {e}")

    # ================================================================
    # WEEKLY SECURITY INTELLIGENCE REPORT (1 AI-Call)
    # ================================================================

    async def _weekly_loop(self):
        await asyncio.sleep(120)  # Startup-Delay
        while self.is_running:
            try:
                await self._wait_until(self.weekly_hour, weekday=self.weekly_day)
                await self._generate_weekly_report()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Weekly Report Fehler: {e}", exc_info=True)
            await asyncio.sleep(3600)

    async def _generate_weekly_report(self):
        logger.info("Generiere woechentlichen Security Intelligence Report...")

        # Alle Daten LOKAL sammeln (0 Token)
        data = {}
        data['uptime'] = await self._cmd("uptime -p")
        data['disk'] = await self._cmd("df -h / --output=pcent,avail,size | tail -1")
        data['memory'] = await self._cmd("free -h | head -3")
        data['crowdsec'] = await self._get_crowdsec_summary()
        data['fail2ban'] = await self._get_fail2ban_summary()
        data['docker_ps'] = await self._cmd(
            "docker ps --format '{{.Names}}: {{.Status}}' 2>/dev/null"
        )
        data['docker_df'] = await self._cmd("docker system df 2>/dev/null")
        data['services'] = await self._get_service_summary()
        data['git'] = await self._get_git_summary()
        data['cert_gs'] = await self._cmd(
            'echo | openssl s_client -connect guildscout.eu:443 '
            '-servername guildscout.eu 2>/dev/null '
            '| openssl x509 -noout -enddate 2>/dev/null'
        )
        data['cert_zd'] = await self._cmd(
            'echo | openssl s_client -connect zerodox.de:443 '
            '-servername zerodox.de 2>/dev/null '
            '| openssl x509 -noout -enddate 2>/dev/null'
        )
        data['updates'] = await self._cmd(
            "apt list --upgradable 2>/dev/null | grep -vc 'Listing' || echo 0"
        )
        data['cleanup_log'] = await self._cmd(
            "tail -5 /home/cmdshadow/logs/server-cleanup.log 2>/dev/null"
        )
        data['top_cpu'] = await self._cmd(
            "ps aux --sort=-pcpu | head -4 | awk '{print $11, $3\"%\"}'"
        )
        data['top_mem'] = await self._cmd(
            "ps aux --sort=-rss | head -4 | awk '{print $11, $6/1024\"MB\"}'"
        )

        # Strukturierte Zusammenfassung
        summary = self._format_weekly(data)

        # EIN AI-Call
        prompt = (
            "Du bist Security-Analyst fuer einen Produktiv-VPS "
            "(Debian 12, 6 Kerne, 8 GB RAM) mit GuildScout, ZERODOX, "
            "und diversen Agents.\n\n"
            "Analysiere diesen Wochenbericht und liefere:\n"
            "1. **Sicherheitsbewertung** (1-10, 10 = perfekt)\n"
            "2. **Top 3 Risiken** (konkret, mit Handlungsempfehlung)\n"
            "3. **Top 3 Empfehlungen** (was diese Woche getan werden sollte)\n"
            "4. **Auffaelligkeiten** (ungewoehnliche Muster/Trends)\n"
            "5. **Positives** (1-2 Punkte, was gut laeuft)\n\n"
            "Sei konkret und actionable. Keine Floskeln. Auf Deutsch.\n\n"
            f"--- WOCHENBERICHT ---\n{summary}\n--- ENDE ---"
        )

        analysis = None
        try:
            analysis = await self.ai_service.get_ai_analysis(
                prompt=prompt, context="", use_critical_model=False
            )
        except Exception as e:
            logger.error(f"AI-Analyse fuer Wochenbericht fehlgeschlagen: {e}")

        await self._send_weekly_report(data, analysis)

    # -- Daten-Sammler fuer Weekly Report --

    async def _get_crowdsec_summary(self) -> str:
        output = await self._cmd(
            "sudo cscli decisions list -o json 2>/dev/null", timeout=15
        )
        if not output:
            return "Keine aktiven Decisions"
        try:
            decisions = json.loads(output)
            if not decisions:
                return "0 aktive Decisions"
            types = {}
            for d in decisions:
                t = d.get('type', 'unknown')
                types[t] = types.get(t, 0) + 1
            return (
                f"{len(decisions)} aktive Decisions: "
                + ", ".join(f"{v}x {k}" for k, v in types.items())
            )
        except (json.JSONDecodeError, TypeError):
            return "CrowdSec aktiv (Daten nicht parsbar)"

    async def _get_fail2ban_summary(self) -> str:
        output = await self._cmd(
            "sudo fail2ban-client status 2>/dev/null", timeout=10
        )
        if not output:
            return "Fail2ban nicht erreichbar"
        jails_line = [l for l in output.split('\n') if 'Jail list' in l]
        if not jails_line:
            return output[:200]
        jails = [
            j.strip() for j in jails_line[0].split(':')[-1].split(',') if j.strip()
        ]
        stats = []
        for jail in jails[:5]:
            stat = await self._cmd(
                f"sudo fail2ban-client status {jail} 2>/dev/null", timeout=5
            )
            banned = '0'
            total = '0'
            for line in (stat or '').split('\n'):
                if 'Currently banned' in line:
                    banned = line.split(':')[-1].strip()
                elif 'Total banned' in line:
                    total = line.split(':')[-1].strip()
            stats.append(f"{jail}: {banned} aktuell, {total} gesamt")
        return f"{len(jails)} Jails. " + "; ".join(stats)

    async def _get_service_summary(self) -> str:
        services = {
            'guildscout-bot': 'user',
            'guildscout-feedback-agent': 'user',
            'zerodox-support-agent': 'user',
            'seo-agent': 'user',
            'shadowops-bot': 'system',
            'earlyoom': 'system',
        }
        lines = []
        for svc, level in services.items():
            if level == 'user':
                status = await self._cmd(
                    f"XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active {svc} 2>/dev/null"
                )
            else:
                status = await self._cmd(
                    f"systemctl is-active {svc} 2>/dev/null"
                )
            lines.append(f"  {svc}: {status or 'unknown'}")
        return '\n'.join(lines)

    async def _get_git_summary(self) -> str:
        projects = {
            'GuildScout': '/home/cmdshadow/GuildScout',
            'ZERODOX': '/home/cmdshadow/ZERODOX',
        }
        lines = []
        for name, path in projects.items():
            count = await self._cmd(
                f'git -C {path} log --oneline --since="7 days ago" '
                f'2>/dev/null | wc -l'
            )
            last = await self._cmd(
                f'git -C {path} log -1 --format="%ar: %s" 2>/dev/null'
            )
            lines.append(
                f"  {name}: {(count or '0').strip()} Commits (7d), "
                f"letzter: {(last or 'n/a')[:60]}"
            )
        return '\n'.join(lines)

    def _format_weekly(self, data: Dict) -> str:
        sections = [
            f"SYSTEM: {data.get('uptime', 'n/a')}",
            f"DISK: {data.get('disk', 'n/a')}",
            f"MEMORY:\n{data.get('memory', 'n/a')}",
            f"TOP CPU:\n{data.get('top_cpu', 'n/a')}",
            f"TOP MEM:\n{data.get('top_mem', 'n/a')}",
            f"CROWDSEC: {data.get('crowdsec', 'n/a')}",
            f"FAIL2BAN: {data.get('fail2ban', 'n/a')}",
            f"DOCKER CONTAINER:\n{data.get('docker_ps', 'n/a')}",
            f"DOCKER DISK:\n{data.get('docker_df', 'n/a')}",
            f"SERVICES:\n{data.get('services', 'n/a')}",
            f"GIT (7 TAGE):\n{data.get('git', 'n/a')}",
            f"SSL GUILDSCOUT: {data.get('cert_gs', 'n/a')}",
            f"SSL ZERODOX: {data.get('cert_zd', 'n/a')}",
            f"APT UPDATES: {data.get('updates', '0')} verfuegbar",
            f"LETZTER CLEANUP:\n{data.get('cleanup_log', 'n/a')}",
        ]
        return '\n\n'.join(sections)

    async def _send_weekly_report(self, data: Dict, analysis: Optional[str]):
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.warning("Weekly Report Channel nicht gefunden")
            return

        embed = discord.Embed(
            title="Security Intelligence Report (Woche)",
            color=0x5865F2,
            timestamp=datetime.now()
        )

        # Eckdaten
        facts = [
            f"Uptime: {data.get('uptime', 'n/a')}",
            f"Disk: {data.get('disk', 'n/a')}",
            f"CrowdSec: {data.get('crowdsec', 'n/a')}",
            f"Fail2ban: {data.get('fail2ban', 'n/a')}",
            f"SSL GS: {data.get('cert_gs', 'n/a')}",
            f"SSL ZD: {data.get('cert_zd', 'n/a')}",
            f"Updates: {data.get('updates', '0')} verfuegbar",
        ]
        embed.add_field(
            name="Eckdaten",
            value='\n'.join(facts),
            inline=False
        )

        # AI-Analyse
        if analysis:
            # Discord Embed Felder haben 1024 Zeichen Limit
            chunks = [analysis[i:i+1024] for i in range(0, len(analysis), 1024)]
            for idx, chunk in enumerate(chunks[:3]):
                label = "AI-Analyse" if idx == 0 else f"AI-Analyse ({idx+1})"
                embed.add_field(name=label, value=chunk, inline=False)
        else:
            embed.add_field(
                name="AI-Analyse",
                value="Fehlgeschlagen — manuelle Pruefung empfohlen",
                inline=False
            )

        embed.set_footer(text="Server Assistant | Weekly Intelligence Report")

        try:
            await channel.send(embed=embed)
            logger.info("Weekly Security Report gesendet")
        except Exception as e:
            logger.error(f"Weekly Report senden fehlgeschlagen: {e}")

    # ================================================================
    # GIT PUSH SECURITY REVIEW (event-getrieben, 0-n Token)
    # ================================================================

    async def review_push_security(
        self, repo_name: str, commits: List[Dict]
    ):
        """
        Von GitHubIntegration aufgerufen bei Git-Push.
        Loest AI-Review nur aus wenn security-relevante Dateien betroffen.
        """
        # Geaenderte Dateien aus Commits extrahieren
        changed = set()
        for c in commits:
            changed.update(c.get('added', []))
            changed.update(c.get('modified', []))
            changed.update(c.get('removed', []))

        # Gegen Security-Patterns pruefen
        security_files = []
        for f in changed:
            f_lower = f.lower()
            if any(p in f_lower for p in SECURITY_PATTERNS):
                security_files.append(f)

        if not security_files:
            return  # Keine security-relevanten Aenderungen

        logger.info(
            f"Security-relevante Aenderungen in {repo_name}: "
            f"{len(security_files)} Dateien"
        )

        commit_msgs = '\n'.join(
            f"- {c.get('message', '?').split(chr(10))[0][:80]}"
            for c in commits[:10]
        )
        files_list = '\n'.join(f"- {f}" for f in security_files[:15])

        prompt = (
            f"Security-Review fuer Git-Push in '{repo_name}':\n\n"
            f"Geaenderte sicherheitsrelevante Dateien:\n{files_list}\n\n"
            f"Commits:\n{commit_msgs}\n\n"
            "Pruefe auf:\n"
            "1. Moegliche Secrets-Leaks (.env, API Keys)\n"
            "2. Neue Endpoints ohne Auth\n"
            "3. Docker/Port-Aenderungen\n"
            "4. Unsichere Dependency-Updates\n"
            "5. Permission-Aenderungen\n\n"
            "Antworte NUR wenn echtes Risiko. "
            "Bei unbedenklich antworte exakt 'OK'.\n"
            "Auf Deutsch, kurz und konkret."
        )

        try:
            review = await self.ai_service.get_ai_analysis(
                prompt=prompt, context="", use_critical_model=False
            )
            if review and review.strip().upper() != 'OK' and len(review) > 10:
                await self._send_security_review(
                    repo_name, security_files, review
                )
        except Exception as e:
            logger.error(f"Push Security Review fehlgeschlagen: {e}")

    async def _send_security_review(
        self, repo_name: str, files: List[str], review: str
    ):
        # Security Reviews gehen in den Critical Channel
        channel = self.bot.get_channel(
            self.config.channels.get('critical', 0) or self.channel_id
        )
        if not channel:
            return

        embed = discord.Embed(
            title=f"Security Review: Push in {repo_name}",
            description=review[:2048],
            color=0xFF6600,
            timestamp=datetime.now()
        )
        embed.add_field(
            name="Betroffene Dateien",
            value='\n'.join(f"`{f}`" for f in files[:10]),
            inline=False
        )
        embed.set_footer(text="Server Assistant | Git Security Review")

        try:
            await channel.send(embed=embed)
            logger.info(f"Security Review fuer {repo_name} gesendet")
        except Exception as e:
            logger.error(f"Security Review senden fehlgeschlagen: {e}")

    # ================================================================
    # MANUELLE TRIGGER (fuer Discord-Commands oder CLI)
    # ================================================================

    async def run_daily_now(self):
        """Manueller Trigger fuer Daily Check"""
        await self._run_daily_checks()

    async def run_weekly_now(self):
        """Manueller Trigger fuer Weekly Report"""
        await self._generate_weekly_report()

    # ================================================================
    # CLI-VERSION-TRACKING
    # ================================================================

    async def _check_cli_versions(self) -> Dict:
        """Prueft Codex + Claude CLI-Versionen und meldet Aenderungen.

        Laeuft taeglich, meldet aber nur wenn sich eine Version aendert.
        """
        result: Dict[str, List] = {'info': []}

        checks = {
            'codex': 'codex --version 2>/dev/null',
            'claude': '/home/cmdshadow/.local/bin/claude --version 2>/dev/null',
        }

        for name, cmd in checks.items():
            version = await self._cmd(cmd, timeout=10)
            if not version:
                continue

            # Nur erste Zeile (z.B. "codex-cli 0.104.0")
            version = version.split('\n')[0].strip()

            old_version = self._last_cli_versions.get(name)
            self._last_cli_versions[name] = version

            if old_version and old_version != version:
                result['info'].append(
                    f"CLI-Update: **{name}** `{old_version}` -> `{version}`"
                )
                logger.info(
                    "CLI-Update erkannt: %s %s -> %s",
                    name, old_version, version,
                )
            elif not old_version:
                # Erster Run — nur loggen, nicht melden
                logger.info("CLI-Version getrackt: %s = %s", name, version)

        return result

    # ================================================================
    # HILFSFUNKTIONEN
    # ================================================================

    async def _cmd(self, cmd: str, timeout: int = 30) -> str:
        """Shell-Befehl ausfuehren und Output zurueckgeben"""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return stdout.decode().strip()
        except asyncio.TimeoutError:
            logger.debug(f"Command Timeout: {cmd[:60]}")
            return ""
        except Exception as e:
            logger.debug(f"Command Fehler: {cmd[:60]}: {e}")
            return ""
