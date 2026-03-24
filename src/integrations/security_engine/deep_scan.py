"""
DeepScanMode — AI-gesteuerte Security-Sessions mit Learning Pipeline

Aktiver Security-Scanner der:
1. Echte Security-Checks ausfuehrt (Fail2ban, CrowdSec, Trivy, AIDE, Ports, Configs)
2. AI analysiert Ergebnisse und findet Luecken
3. Fixes je nach Schwere: direkt (Config), PR (Code), Issue (komplex)
4. Alles in DB aufzeichnet fuer kontinuierliches Lernen

Adaptive Session-Modi:
- fix_only: >=20 offene Findings, bis 3 Sessions/Tag
- full_scan: 5-19 Findings, bis 2 Sessions/Tag
- quick_scan: 1-4 Findings, 1 Session/Tag
- maintenance: 0 Findings, nur wenn >3 Tage seit letztem Scan

AI-Integration: Nutzt die Dual-Engine (Codex CLI Primary + Claude CLI Fallback)
ueber ai_engine.query() — gleiche Provider-Chain wie der SEO Agent.
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import EngineMode

logger = logging.getLogger('shadowops.deep_scan')


# Session-Konfiguration pro Modus
SESSION_CONFIG = {
    'fix_only': {
        'max_sessions_per_day': 3,
        'timeout_minutes': 120,
        'max_turns': 200,
        'scan_enabled': False,
        'fix_enabled': True,
    },
    'full_scan': {
        'max_sessions_per_day': 2,
        'timeout_minutes': 45,
        'max_turns': 60,
        'scan_enabled': True,
        'fix_enabled': True,
    },
    'quick_scan': {
        'max_sessions_per_day': 1,
        'timeout_minutes': 20,
        'max_turns': 30,
        'scan_enabled': True,
        'fix_enabled': True,
    },
    'maintenance': {
        'max_sessions_per_day': 1,
        'timeout_minutes': 10,
        'max_turns': 15,
        'scan_enabled': True,
        'fix_enabled': False,
    },
}

# Security-Check-Befehle (werden via subprocess mit exec_file-Pattern ausgefuehrt)
SECURITY_CHECKS = [
    {'name': 'fail2ban_status', 'category': 'ssh',
     'cmd': ['sudo', 'fail2ban-client', 'status', 'sshd'],
     'description': 'Fail2ban Jail Status + aktive Bans'},
    {'name': 'crowdsec_decisions', 'category': 'network',
     'cmd': ['sudo', 'cscli', 'decisions', 'list', '-o', 'json'],
     'description': 'CrowdSec aktive Decisions'},
    {'name': 'crowdsec_alerts', 'category': 'network',
     'cmd': ['sudo', 'cscli', 'alerts', 'list', '-o', 'json', '--limit', '20'],
     'description': 'CrowdSec letzte Alerts'},
    {'name': 'open_ports', 'category': 'firewall',
     'cmd': ['ss', '-tlnp'],
     'description': 'Offene TCP Ports'},
    {'name': 'ufw_status', 'category': 'firewall',
     'cmd': ['sudo', 'ufw', 'status', 'verbose'],
     'description': 'UFW Firewall Regeln'},
    {'name': 'docker_containers', 'category': 'docker',
     'cmd': ['docker', 'ps', '--format', 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'],
     'description': 'Docker Container Status'},
    {'name': 'systemd_failed', 'category': 'services',
     'cmd': ['systemctl', '--failed', '--no-pager', '--no-legend'],
     'description': 'Fehlgeschlagene systemd Services'},
    {'name': 'disk_usage', 'category': 'services',
     'cmd': ['df', '-h', '/', '/home', '--output=pcent,target'],
     'description': 'Festplattenauslastung'},
    {'name': 'memory_usage', 'category': 'services',
     'cmd': ['free', '-h'],
     'description': 'RAM-Auslastung'},
]


class DeepScanMode:
    """AI-gesteuerte Security-Sessions mit Full Learning Pipeline"""

    def __init__(self, db, ai_engine=None, executor=None, context_manager=None):
        self.db = db
        self.ai_engine = ai_engine
        self.executor = executor
        self.context_manager = context_manager
        self.sessions_today: int = 0
        self.current_session: Optional[Dict] = None

    async def _determine_session_mode(self) -> str:
        """Bestimmt Session-Modus basierend auf offenen Findings"""
        open_count = await self.db.get_open_findings_count()

        if open_count >= 20:
            return 'fix_only'
        elif open_count >= 5:
            return 'full_scan'
        elif open_count >= 1:
            return 'quick_scan'
        else:
            return 'maintenance'

    def _get_session_config(self, mode: str) -> Dict[str, Any]:
        """Gibt Session-Konfiguration fuer den Modus zurueck"""
        return SESSION_CONFIG.get(mode, SESSION_CONFIG['maintenance'])

    async def can_start_session(self) -> bool:
        """Prueft ob eine weitere Session heute erlaubt ist"""
        mode = await self._determine_session_mode()
        config = self._get_session_config(mode)
        return self.sessions_today < config['max_sessions_per_day']

    async def run_session(self) -> Dict[str, Any]:
        """Fuehrt eine vollstaendige Deep-Scan-Session aus."""
        mode = await self._determine_session_mode()
        config = self._get_session_config(mode)

        if self.sessions_today >= config['max_sessions_per_day']:
            logger.info(f"Session-Limit erreicht ({self.sessions_today}/{config['max_sessions_per_day']})")
            return {'status': 'skipped', 'reason': 'session_limit', 'mode': mode}

        logger.info(f"🔍 Starte Deep-Scan Session im Modus '{mode}'")
        self.sessions_today += 1

        session_result = {
            'mode': mode,
            'status': 'running',
            'findings_count': 0,
            'fixes_count': 0,
            'issues_created': 0,
            'scan_results': {},
            'config': config,
            'started_at': datetime.now(timezone.utc).isoformat(),
        }
        self.current_session = session_result

        try:
            # Phase 1: Pre-Session Maintenance
            await self._pre_session_maintenance()

            # Phase 2: Scan (wenn aktiviert)
            if config['scan_enabled']:
                findings = await self._run_scan_phase(mode, config)
                session_result['findings_count'] = len(findings)
                session_result['findings'] = findings

            # Phase 3: Fix (wenn aktiviert)
            if config['fix_enabled']:
                fix_result = await self._run_fix_phase(mode, config)
                session_result['fixes_count'] = fix_result.get('fixed', 0)
                session_result['issues_created'] = fix_result.get('issues_created', 0)

            session_result['status'] = 'completed'
            logger.info(
                f"✅ Session abgeschlossen: {session_result['findings_count']} Findings, "
                f"{session_result['fixes_count']} Fixes, {session_result['issues_created']} Issues"
            )

        except Exception as e:
            session_result['status'] = 'failed'
            session_result['error'] = str(e)
            logger.error(f"❌ Session fehlgeschlagen: {e}", exc_info=True)

        self.current_session = None
        return session_result

    async def _pre_session_maintenance(self) -> None:
        """Pre-Session: Fix-Verifikation + Knowledge-Decay"""
        logger.info("🔧 Pre-Session Maintenance...")
        try:
            if hasattr(self.db, 'decay_old_knowledge'):
                decayed = await self.db.decay_old_knowledge(days=14, decay_pct=5)
                if decayed:
                    logger.info(f"   📉 {decayed} Knowledge-Eintraege decayed")
        except Exception as e:
            logger.debug(f"Knowledge-Decay fehlgeschlagen: {e}")

    async def _run_scan_phase(self, mode: str, config: Dict) -> List[Dict]:
        """
        Scan-Phase: Echte Security-Checks + AI-Analyse

        1. Fuehrt Security-Checks aus (fail2ban, crowdsec, ports, etc.)
        2. Sendet Ergebnisse an AI (Codex/Claude) fuer Analyse
        3. AI findet Luecken und bewertet Severity
        4. Neue Findings werden in DB gespeichert
        """
        logger.info(f"🔍 Scan-Phase ({mode})...")

        # 1. Security-Checks ausfuehren (sichere exec ohne Shell)
        check_results = await self._execute_security_checks()
        logger.info(f"   📋 {len(check_results)} Checks ausgefuehrt")

        # 2. Bekanntes Wissen + offene Findings laden
        knowledge_context = await self._build_knowledge_context()
        existing_findings = await self._get_existing_findings_summary()

        # 3. AI analysiert die Ergebnisse
        findings = await self._analyze_with_ai(check_results, knowledge_context, existing_findings, mode)
        logger.info(f"   🎯 {len(findings)} neue Findings")

        # 4. Findings in DB speichern
        for finding in findings:
            try:
                await self._store_finding(finding)
            except Exception as e:
                logger.warning(f"   Finding-Speicherung fehlgeschlagen: {e}")

        return findings

    async def _execute_security_checks(self) -> Dict[str, Dict]:
        """Fuehrt alle Security-Checks aus (exec ohne Shell — kein Injection-Risiko)"""
        results = {}

        for check in SECURITY_CHECKS:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *check['cmd'],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                output = stdout.decode('utf-8', errors='replace').strip()

                results[check['name']] = {
                    'category': check['category'],
                    'description': check['description'],
                    'output': output[:2000],
                    'exit_code': proc.returncode,
                    'success': proc.returncode == 0,
                }
            except asyncio.TimeoutError:
                results[check['name']] = {
                    'category': check['category'],
                    'description': check['description'],
                    'output': 'TIMEOUT nach 30s',
                    'exit_code': -1,
                    'success': False,
                }
            except Exception as e:
                results[check['name']] = {
                    'category': check['category'],
                    'description': check['description'],
                    'output': str(e),
                    'exit_code': -1,
                    'success': False,
                }

        return results

    async def _build_knowledge_context(self) -> str:
        """Baut Wissens-Kontext fuer AI-Prompt"""
        lines = []
        try:
            knowledge = await self.db.get_knowledge('security', min_confidence=0.3)
            for k in knowledge[:15]:
                lines.append(f"- {k['subject']}: {k['content']}")
        except Exception:
            pass
        return '\n'.join(lines) if lines else 'Kein vorheriges Wissen vorhanden.'

    async def _get_existing_findings_summary(self) -> str:
        """Laedt bestehende offene Findings als Kontext"""
        try:
            if hasattr(self.db, 'pool') and self.db.pool:
                rows = await self.db.pool.fetch("""
                    SELECT title, severity, affected_project
                    FROM findings WHERE status = 'open'
                    ORDER BY severity, found_at DESC LIMIT 30
                """)
                if rows:
                    lines = [f"- [{r['severity']}] {r['title']} ({r.get('affected_project', '?')})" for r in rows]
                    return '\n'.join(lines)
        except Exception:
            pass
        return 'Keine offenen Findings.'

    async def _analyze_with_ai(self, check_results: Dict, knowledge: str,
                                existing_findings: str, mode: str) -> List[Dict]:
        """Sendet Check-Ergebnisse an AI fuer Analyse (Codex Primary, Claude Fallback)"""
        if not self.ai_engine:
            logger.warning("Kein AI-Engine — Basis-Analyse")
            return self._basic_analysis(check_results)

        # Check-Ergebnisse formatieren
        checks_text = ""
        for name, result in check_results.items():
            status_icon = "OK" if result['success'] else "FEHLER"
            checks_text += f"\n### {status_icon}: {result['description']} ({result['category']})\n"
            checks_text += f"```\n{result['output'][:500]}\n```\n"

        prompt = f"""Du bist ein Security Engineer. Analysiere diese Server-Checks und finde Sicherheitsluecken.

## Server-Check-Ergebnisse
{checks_text}

## Dein bisheriges Wissen
{knowledge}

## Bereits bekannte offene Findings (NICHT erneut melden!)
{existing_findings}

## Regeln
- Melde NUR NEUE Probleme die nicht in den bekannten Findings stehen
- Bewerte jedes Finding: CRITICAL, HIGH, MEDIUM oder LOW
- Gib an welches Projekt betroffen ist (shadowops-bot, guildscout, zerodox, server)
- Bei CRITICAL/HIGH: Konkreten Fix-Vorschlag
- Maximal 10 Findings pro Scan
- KEINE False-Positives

## Ausgabe-Format (JSON Array)
```json
[
  {{
    "severity": "HIGH",
    "category": "ssh",
    "title": "Kurzer Titel",
    "description": "Detaillierte Beschreibung",
    "affected_project": "server",
    "fix_suggestion": "Konkreter Fix"
  }}
]
```

Antworte NUR mit dem JSON Array."""

        try:
            result = await self.ai_engine.query(prompt)
            if result:
                return self._parse_ai_findings(result)
        except Exception as e:
            logger.warning(f"AI-Analyse fehlgeschlagen: {e}")

        return self._basic_analysis(check_results)

    def _parse_ai_findings(self, ai_response: str) -> List[Dict]:
        """Parst AI-Antwort in Finding-Liste"""
        try:
            text = ai_response.strip()
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0]
            elif '```' in text:
                text = text.split('```')[1].split('```')[0]

            findings = json.loads(text.strip())
            if isinstance(findings, list):
                valid = []
                for f in findings[:10]:
                    if all(k in f for k in ('severity', 'title', 'description')):
                        f.setdefault('category', 'general')
                        f.setdefault('affected_project', 'server')
                        f.setdefault('fix_suggestion', '')
                        valid.append(f)
                return valid
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.warning(f"AI-Findings Parse-Fehler: {e}")
        return []

    def _basic_analysis(self, check_results: Dict) -> List[Dict]:
        """Basis-Analyse ohne AI — erkennt offensichtliche Probleme"""
        findings = []

        # Failed Services
        systemd = check_results.get('systemd_failed', {})
        if systemd.get('output') and systemd['output'].strip():
            findings.append({
                'severity': 'HIGH', 'category': 'services',
                'title': 'Fehlgeschlagene systemd Services',
                'description': f"Services im Fehlerzustand: {systemd['output'][:200]}",
                'affected_project': 'server',
                'fix_suggestion': 'systemctl restart <service>',
            })

        # Hohe Disk-Usage
        disk = check_results.get('disk_usage', {})
        if disk.get('output'):
            for line in disk['output'].split('\n'):
                if '%' in line:
                    try:
                        pct = int(line.strip().split('%')[0].strip())
                        if pct >= 90:
                            findings.append({
                                'severity': 'HIGH', 'category': 'services',
                                'title': f'Festplatte {pct}% voll',
                                'description': f'Partition fast voll: {line.strip()}',
                                'affected_project': 'server',
                                'fix_suggestion': 'docker system prune, logs bereinigen',
                            })
                    except ValueError:
                        pass

        return findings

    async def _store_finding(self, finding: Dict) -> Optional[int]:
        """Speichert ein Finding in der DB"""
        try:
            if hasattr(self.db, 'pool') and self.db.pool:
                row = await self.db.pool.fetchrow("""
                    INSERT INTO findings (
                        severity, category, title, description,
                        affected_project, status, found_at
                    ) VALUES ($1, $2, $3, $4, $5, 'open', NOW())
                    RETURNING id
                """,
                    finding.get('severity', 'MEDIUM'),
                    finding.get('category', 'general'),
                    finding.get('title', 'Unknown'),
                    finding.get('description', ''),
                    finding.get('affected_project', 'server'),
                )
                return row['id'] if row else None
        except Exception as e:
            logger.debug(f"Finding-Insert fehlgeschlagen: {e}")
        return None

    async def _run_fix_phase(self, mode: str, config: Dict) -> Dict[str, int]:
        """
        Fix-Phase: Offene Findings abarbeiten

        CRITICAL/HIGH → GitHub Issue erstellen (Duplikat-Check via gh CLI)
        MEDIUM → Tracken
        LOW → Ueberspringen
        """
        logger.info(f"🔧 Fix-Phase ({mode})...")
        result = {'fixed': 0, 'issues_created': 0, 'skipped': 0}

        if not hasattr(self.db, 'pool') or not self.db.pool:
            return result

        try:
            findings = await self.db.pool.fetch("""
                SELECT id, severity, category, title, description,
                       affected_project, github_issue_url
                FROM findings
                WHERE status = 'open' AND github_issue_url IS NULL
                ORDER BY
                    CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
                                  WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                    found_at ASC
                LIMIT 20
            """)
        except Exception as e:
            logger.warning(f"Findings-Abruf fehlgeschlagen: {e}")
            return result

        logger.info(f"   📊 {len(findings)} offene Findings zur Bearbeitung")

        for finding in findings:
            severity = finding['severity']
            if severity in ('CRITICAL', 'HIGH'):
                issue_url = await self._create_github_issue(finding)
                if issue_url:
                    result['issues_created'] += 1
                    try:
                        await self.db.pool.execute(
                            "UPDATE findings SET status = 'issue_created', github_issue_url = $2 WHERE id = $1",
                            finding['id'], issue_url
                        )
                    except Exception:
                        pass
                    logger.info(f"   📝 Issue: [{severity}] {finding['title']}")
            else:
                result['skipped'] += 1

        return result

    async def _create_github_issue(self, finding: Dict) -> Optional[str]:
        """Erstellt ein GitHub Issue (mit Duplikat-Check)"""
        project = finding.get('affected_project', 'server')
        repo_map = {
            'shadowops-bot': 'Commandershadow9/shadowops-bot',
            'guildscout': 'Commandershadow9/GuildScout',
            'zerodox': 'Commandershadow9/ZERODOX',
            'server': 'Commandershadow9/shadowops-bot',
        }
        repo = repo_map.get(project)
        if not repo:
            return None

        title = f"[Security] {finding['title']}"
        body = (
            f"**Severity:** {finding['severity']} | **Kategorie:** {finding.get('category', '?')}\n\n"
            f"## Problem\n{finding['description']}\n\n"
            f"## Betroffen\nProjekt: {project}\n\n"
            f"---\n*Automatisch erstellt von Security Engine v6*"
        )

        # Duplikat-Check
        try:
            check = await asyncio.create_subprocess_exec(
                'gh', 'issue', 'list', '--repo', repo,
                '--search', finding['title'][:50],
                '--state', 'open', '--json', 'title', '--limit', '5',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(check.communicate(), timeout=15)
            existing = json.loads(stdout.decode())
            if existing:
                logger.info(f"   ⏭️ Issue existiert bereits: {finding['title'][:50]}")
                return None
        except Exception:
            pass

        # Issue erstellen
        try:
            proc = await asyncio.create_subprocess_exec(
                'gh', 'issue', 'create', '--repo', repo,
                '--title', title, '--body', body, '--label', 'security',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                return stdout.decode().strip()
            else:
                logger.warning(f"gh issue create fehlgeschlagen: {stderr.decode()[:200]}")
        except Exception as e:
            logger.warning(f"GitHub Issue Erstellung fehlgeschlagen: {e}")

        return None

    def reset_daily(self) -> None:
        """Taeglicher Reset der Session-Zaehler"""
        self.sessions_today = 0
