"""
AnalystDB — Asyncpg-basierter Datenbank-Layer für den Security Analyst

Stellt CRUD-Operationen für Sessions, Knowledge, Findings, Patterns
und Health-Snapshots bereit. Nutzt asyncpg Connection-Pool für
performante, asynchrone Datenbankzugriffe auf die security_analyst DB.
"""

import json
import logging
import asyncpg
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger('shadowops.analyst_db')

# Verbindungs-DSN für die Security Analyst Datenbank
DSN = 'postgresql://security_analyst:sec_analyst_2026@127.0.0.1:5433/security_analyst'

# Severity-Sortierreihenfolge: critical zuerst
SEVERITY_ORDER = {
    'critical': 0,
    'high': 1,
    'medium': 2,
    'low': 3,
    'info': 4,
}


class AnalystDB:
    """Asyncpg-basierter Datenbank-Layer für den Security Analyst

    Verwaltet einen Connection-Pool und bietet asynchrone Methoden
    für alle DB-Operationen des Analyst-Agents.
    """

    def __init__(self, dsn: str = DSN):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    # ─────────────────────────────────────────────
    # Pool-Management
    # ─────────────────────────────────────────────

    async def connect(self):
        """Connection-Pool erstellen und Verbindung herstellen"""
        if self.pool is not None:
            return

        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=1,
            max_size=3,
            command_timeout=30,
        )
        logger.info("AnalystDB Pool verbunden (%s Connections)", self.pool.get_size())

    async def close(self):
        """Connection-Pool schließen"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("AnalystDB Pool geschlossen")

    # ─────────────────────────────────────────────
    # Sessions
    # ─────────────────────────────────────────────

    async def start_session(self, trigger_type: str) -> int:
        """Neue Analyse-Session starten

        Args:
            trigger_type: Art des Triggers (z.B. 'scheduled', 'manual', 'event')

        Returns:
            ID der neuen Session
        """
        now = datetime.now(timezone.utc)
        row = await self.pool.fetchrow(
            """INSERT INTO sessions (started_at, trigger_type, status)
               VALUES ($1, $2, 'running')
               RETURNING id""",
            now, trigger_type,
        )
        session_id = row['id']
        logger.info("Session %d gestartet (Trigger: %s)", session_id, trigger_type)
        return session_id

    async def end_session(
        self,
        session_id: int,
        summary: str,
        topics: List[str],
        tokens_used: int,
        model: str,
        findings_count: int,
        auto_fixes: int,
        issues_created: int,
    ):
        """Session abschließen und Ergebnisse speichern

        Args:
            session_id: ID der Session
            summary: AI-generierte Zusammenfassung
            topics: Liste untersuchter Themen
            tokens_used: Verbrauchte Token
            model: Verwendetes AI-Modell
            findings_count: Anzahl Findings
            auto_fixes: Anzahl automatischer Fixes
            issues_created: Anzahl erstellter GitHub-Issues
        """
        now = datetime.now(timezone.utc)
        await self.pool.execute(
            """UPDATE sessions
               SET ended_at = $1,
                   ai_summary = $2,
                   topics_investigated = $3,
                   tokens_used = $4,
                   model_used = $5,
                   findings_count = $6,
                   auto_fixes_count = $7,
                   issues_created = $8,
                   status = 'completed'
               WHERE id = $9""",
            now, summary, topics, tokens_used, model,
            findings_count, auto_fixes, issues_created, session_id,
        )
        logger.info(
            "Session %d abgeschlossen: %d Findings, %d Fixes, %d Issues",
            session_id, findings_count, auto_fixes, issues_created,
        )

    async def pause_session(self, session_id: int):
        """Session pausieren (z.B. User kam online)

        Args:
            session_id: ID der Session
        """
        now = datetime.now(timezone.utc)
        await self.pool.execute(
            """UPDATE sessions
               SET ended_at = $1, status = 'paused'
               WHERE id = $2""",
            now, session_id,
        )
        logger.info("Session %d pausiert (User online)", session_id)

    async def count_sessions_today(self) -> int:
        """Anzahl der ERFOLGREICHEN Sessions die heute bereits gelaufen sind.

        Wird beim Start genutzt um _sessions_today korrekt zu initialisieren,
        damit der Counter auch nach Bot-Restarts stimmt.

        Fehlgeschlagene Sessions (0 Findings, 0 Tokens) zaehlen NICHT
        gegen das Tages-Limit, damit bei AI-Ausfaellen Retries moeglich sind.

        Returns:
            Anzahl erfolgreicher Sessions heute
        """
        count = await self.pool.fetchval(
            """SELECT COUNT(*) FROM sessions
               WHERE started_at::date = CURRENT_DATE
                 AND status IN ('completed', 'running')
                 AND (findings_count > 0 OR tokens_used > 0 OR status = 'running')"""
        )
        return count or 0

    async def find_similar_open_finding(self, title: str) -> Optional[Dict]:
        """Sucht ein offenes Finding mit ähnlichem Titel.

        Prüft auf:
        1. Exakter Match (case-insensitive)
        2. Enthaltener Match (neuer Titel enthält alten oder umgekehrt)
        3. Gleiche CVE/Keyword-Referenz (z.B. CVE-2026-1234 in beiden)

        Args:
            title: Titel des neuen Findings

        Returns:
            Dict des ähnlichen Findings oder None
        """
        # 1. Exakter Match
        row = await self.pool.fetchrow(
            """SELECT id, title, github_issue_url FROM findings
               WHERE status = 'open'
                 AND LOWER(title) = LOWER($1)""",
            title,
        )
        if row:
            return dict(row)

        # 2. Enthaltener Match (Substring in beide Richtungen)
        # z.B. "CVE-2026-1234 in postgres" matches "CVE-2026-1234 bleibt aktiv"
        title_lower = title.lower()
        # Kernwörter extrahieren (>= 8 Zeichen, keine Füllwörter)
        keywords = [w for w in title_lower.split() if len(w) >= 8]
        if keywords:
            # Suche nach Findings die mindestens ein langes Keyword teilen
            for kw in keywords[:3]:
                row = await self.pool.fetchrow(
                    """SELECT id, title, github_issue_url FROM findings
                       WHERE status = 'open'
                         AND LOWER(title) LIKE '%' || $1 || '%'
                       LIMIT 1""",
                    kw,
                )
                if row:
                    return dict(row)

        return None

    async def get_open_findings_summary(self, limit: int = 30) -> list:
        """Holt offene Findings als Kurzübersicht für den Analyst-Prompt.

        Damit der Analyst weiß was schon gemeldet wurde und nicht
        dieselben Probleme erneut reported.
        """
        rows = await self.pool.fetch(
            """SELECT id, severity, title, category
               FROM findings WHERE status = 'open'
               ORDER BY found_at DESC LIMIT $1""",
            limit,
        )
        return [dict(r) for r in rows]

    async def close_finding(self, finding_id: int, resolution: str = "auto-resolved") -> None:
        """Schliesst ein Finding als behoben (Alias für mark_finding_fixed)."""
        await self.mark_finding_fixed(finding_id)
        logger.info("Finding #%d geschlossen: %s", finding_id, resolution)

    async def close_stale_findings(self, days: int = 30) -> int:
        """Schliesst Findings die älter als N Tage sind und nie bestätigt wurden.

        Findings die seit 30+ Tagen offen sind und keine GitHub-Issue haben,
        sind wahrscheinlich durch Updates/Patches automatisch behoben.
        """
        result = await self.pool.execute(
            """UPDATE findings SET status = 'fixed', fixed_at = NOW()
               WHERE status = 'open'
                 AND github_issue_url IS NULL
                 AND found_at < NOW() - make_interval(days => $1)""",
            days,
        )
        # Anzahl der geschlossenen Findings aus dem Command-Tag extrahieren
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            logger.info("Auto-Close: %d veraltete Findings geschlossen (>%d Tage)", count, days)
        return count

    async def get_last_session(self) -> Optional[Dict]:
        """Letzte abgeschlossene Session abrufen

        Returns:
            Dict mit Session-Daten oder None
        """
        row = await self.pool.fetchrow(
            """SELECT * FROM sessions
               WHERE status = 'completed'
               ORDER BY ended_at DESC
               LIMIT 1"""
        )
        return dict(row) if row else None

    # ─────────────────────────────────────────────
    # Knowledge
    # ─────────────────────────────────────────────

    async def get_all_knowledge(self) -> List[Dict]:
        """Alle Knowledge-Einträge abrufen

        Returns:
            Liste aller Wissens-Einträge als Dicts
        """
        rows = await self.pool.fetch(
            "SELECT * FROM knowledge ORDER BY category, subject"
        )
        return [dict(r) for r in rows]

    async def upsert_knowledge(
        self,
        category: str,
        subject: str,
        content: str,
        confidence: float = 0.5,
    ):
        """Wissen einfügen oder aktualisieren (ON CONFLICT)

        Args:
            category: Kategorie (z.B. 'infrastructure', 'security')
            subject: Thema innerhalb der Kategorie
            content: Wissensinhalt
            confidence: Konfidenz-Wert 0.0-1.0
        """
        now = datetime.now(timezone.utc)
        await self.pool.execute(
            """INSERT INTO knowledge (category, subject, content, confidence, last_verified, updated_at)
               VALUES ($1, $2, $3, $4, $5, $5)
               ON CONFLICT (category, subject)
               DO UPDATE SET content = $3, confidence = $4, last_verified = $5, updated_at = $5""",
            category, subject, content, confidence, now,
        )
        logger.debug("Knowledge upsert: [%s] %s (confidence=%.2f)", category, subject, confidence)

    # ─────────────────────────────────────────────
    # Findings
    # ─────────────────────────────────────────────

    async def add_finding(
        self,
        severity: str,
        category: str,
        title: str,
        description: str,
        session_id: int,
        affected_project: Optional[str] = None,
        affected_files: Optional[List[str]] = None,
        fix_type: Optional[str] = None,
        github_issue_url: Optional[str] = None,
        auto_fix_details: Optional[str] = None,
        rollback_command: Optional[str] = None,
    ) -> int:
        """Neues Security-Finding speichern

        Args:
            severity: critical, high, medium, low, info
            category: Kategorie (z.B. 'docker', 'firewall', 'permissions')
            title: Kurzbeschreibung des Findings
            description: Ausführliche Beschreibung
            session_id: Zugehörige Session-ID
            affected_project: Betroffenes Projekt (optional)
            affected_files: Liste betroffener Dateien (optional)
            fix_type: Art des Fixes (optional)
            github_issue_url: URL zum GitHub-Issue (optional)
            auto_fix_details: Details zum automatischen Fix (optional)
            rollback_command: Rollback-Befehl (optional)

        Returns:
            ID des neuen Findings
        """
        row = await self.pool.fetchrow(
            """INSERT INTO findings
               (severity, category, title, description, session_id,
                affected_project, affected_files, fix_type,
                github_issue_url, auto_fix_details, rollback_command)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
               RETURNING id""",
            severity, category, title, description, session_id,
            affected_project, affected_files, fix_type,
            github_issue_url, auto_fix_details, rollback_command,
        )
        finding_id = row['id']
        logger.info("Finding #%d gespeichert: [%s] %s", finding_id, severity.upper(), title)
        return finding_id

    async def get_open_findings(self) -> List[Dict]:
        """Alle offenen Findings abrufen, sortiert nach Severity (critical zuerst)

        Returns:
            Liste offener Findings als Dicts
        """
        rows = await self.pool.fetch(
            """SELECT * FROM findings
               WHERE status = 'open'
               ORDER BY
                   CASE severity
                       WHEN 'critical' THEN 0
                       WHEN 'high' THEN 1
                       WHEN 'medium' THEN 2
                       WHEN 'low' THEN 3
                       WHEN 'info' THEN 4
                       ELSE 5
                   END,
                   found_at DESC"""
        )
        return [dict(r) for r in rows]

    async def get_recent_findings(self, days: int = 7) -> List[Dict]:
        """Findings der letzten N Tage abrufen

        Args:
            days: Anzahl Tage zurück (default 7)

        Returns:
            Liste der Findings als Dicts
        """
        rows = await self.pool.fetch(
            """SELECT * FROM findings
               WHERE found_at >= NOW() - make_interval(days => $1)
               ORDER BY found_at DESC""",
            days,
        )
        return [dict(r) for r in rows]

    async def mark_finding_fixed(self, finding_id: int):
        """Finding als behoben markieren + Duplikate mitschliessen.

        Wenn Finding #5 gefixt wird und Finding #10 den gleichen Titel hat,
        wird #10 automatisch auch als gefixt markiert.
        """
        now = datetime.now(timezone.utc)

        # Titel des Findings holen
        title = await self.pool.fetchval(
            "SELECT title FROM findings WHERE id = $1", finding_id,
        )

        if title:
            # Alle offenen Findings mit ähnlichem Titel mitschliessen
            result = await self.pool.execute(
                """UPDATE findings
                   SET status = 'fixed', fixed_at = $1
                   WHERE (id = $2 OR (status = 'open' AND LOWER(LEFT(title, 60)) = LOWER(LEFT($3, 60))))""",
                now, finding_id, title,
            )
            # Anzahl betroffener Rows aus dem Result-Tag parsen
            count = int(result.split()[-1]) if result else 1
            if count > 1:
                logger.info("Finding #%d + %d Duplikate als behoben markiert", finding_id, count - 1)
            else:
                logger.info("Finding #%d als behoben markiert", finding_id)
        else:
            await self.pool.execute(
                "UPDATE findings SET status = 'fixed', fixed_at = $1 WHERE id = $2",
                now, finding_id,
            )
            logger.info("Finding #%d als behoben markiert", finding_id)

    async def get_fixable_findings(self) -> List[Dict]:
        """ALLE offenen Findings die gefixt werden können, dedupliziert und priorisiert.

        Filtert:
        - Status 'open', fix_type != 'info_only'
        - Dedupliziert nach Titel (nur neuestes pro Titel-Gruppe)
        - Findings MIT GitHub-Issue werden übersprungen (bereits getrackt)
        - Nach Severity sortiert, dann nach Projekt gruppiert
        """
        rows = await self.pool.fetch(
            """SELECT DISTINCT ON (LOWER(LEFT(title, 60)))
                      id, severity, category, title, description,
                      fix_type, affected_project, affected_files,
                      github_issue_url
               FROM findings
               WHERE status = 'open'
                 AND fix_type != 'info_only'
               ORDER BY
                   LOWER(LEFT(title, 60)),
                   CASE severity
                       WHEN 'critical' THEN 0
                       WHEN 'high' THEN 1
                       WHEN 'medium' THEN 2
                       WHEN 'low' THEN 3
                       ELSE 4
                   END,
                   found_at DESC"""
        )
        # Re-sort nach Severity + Projekt (DISTINCT ON erzwingt andere Sortierung)
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}
        result = [dict(r) for r in rows]
        result.sort(key=lambda f: (severity_order.get(f['severity'], 5), f.get('affected_project', '')))
        return result

    # ─────────────────────────────────────────────
    # Health Snapshots
    # ─────────────────────────────────────────────

    async def save_health_snapshot(
        self,
        session_id: int,
        services: Dict,
        containers: Dict,
        resources: Dict,
    ):
        """Health-Snapshot speichern (JSONB-Felder)

        Args:
            session_id: Zugehörige Session-ID
            services: Service-Status als Dict
            containers: Docker-Container-Status als Dict
            resources: System-Ressourcen als Dict
        """
        await self.pool.execute(
            """INSERT INTO health_snapshots
               (session_id, services, docker_containers, system_resources)
               VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb)""",
            session_id,
            json.dumps(services, ensure_ascii=False),
            json.dumps(containers, ensure_ascii=False),
            json.dumps(resources, ensure_ascii=False),
        )
        logger.debug("Health-Snapshot für Session %d gespeichert", session_id)

    # ─────────────────────────────────────────────
    # Patterns
    # ─────────────────────────────────────────────

    async def add_pattern(
        self,
        pattern_type: str,
        description: str,
        example: Optional[str] = None,
    ):
        """Pattern speichern oder Zähler erhöhen

        Wenn ein Pattern mit gleichem Typ und Beschreibung existiert,
        wird times_seen erhöht und das Beispiel (falls vorhanden)
        zur JSONB-Liste hinzugefügt.

        Args:
            pattern_type: Art des Patterns (z.B. 'oom_kill', 'port_conflict')
            description: Beschreibung des Patterns
            example: Optionales Beispiel (wird zur JSONB-Liste hinzugefügt)
        """
        now = datetime.now(timezone.utc)

        # Bestehendes Pattern suchen
        existing = await self.pool.fetchrow(
            """SELECT id, examples FROM learned_patterns
               WHERE pattern_type = $1 AND description = $2""",
            pattern_type, description,
        )

        if existing:
            # times_seen erhöhen, Beispiel anhängen
            examples = existing['examples'] if existing['examples'] else []
            if example and example not in examples:
                examples.append(example)

            await self.pool.execute(
                """UPDATE learned_patterns
                   SET times_seen = times_seen + 1,
                       examples = $1::jsonb,
                       updated_at = $2
                   WHERE id = $3""",
                json.dumps(examples, ensure_ascii=False), now, existing['id'],
            )
            logger.debug("Pattern aktualisiert: [%s] %s", pattern_type, description[:50])
        else:
            # Neues Pattern anlegen
            examples = [example] if example else []
            await self.pool.execute(
                """INSERT INTO learned_patterns
                   (pattern_type, description, examples, times_seen, updated_at)
                   VALUES ($1, $2, $3::jsonb, 1, $4)""",
                pattern_type, description,
                json.dumps(examples, ensure_ascii=False), now,
            )
            logger.debug("Neues Pattern gespeichert: [%s] %s", pattern_type, description[:50])

    async def get_patterns(self) -> List[Dict]:
        """Alle Patterns sortiert nach Häufigkeit (absteigend)

        Returns:
            Liste aller Patterns als Dicts
        """
        rows = await self.pool.fetch(
            "SELECT * FROM learned_patterns ORDER BY times_seen DESC"
        )
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────
    # AI-Kontext
    # ─────────────────────────────────────────────

    async def build_ai_context(self) -> str:
        """Kompletten Markdown-Kontext für den AI-Prompt zusammenstellen

        Enthält: Wissen nach Kategorie, offene Findings (Top 20),
        letzte Session-Info, Patterns (Top 10), 30-Tage-Statistik.

        Returns:
            Formatierter Markdown-String
        """
        parts = []

        # ── Wissen nach Kategorie ──
        knowledge = await self.get_all_knowledge()
        if knowledge:
            parts.append("## Akkumuliertes Wissen\n")
            # Nach Kategorie gruppieren
            categories: Dict[str, List[Dict]] = {}
            for entry in knowledge:
                cat = entry['category']
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(entry)

            for cat, entries in sorted(categories.items()):
                parts.append(f"### {cat}\n")
                for e in entries:
                    confidence_pct = int(e['confidence'] * 100) if e['confidence'] else 50
                    parts.append(f"- **{e['subject']}** ({confidence_pct}%): {e['content']}")
                parts.append("")  # Leerzeile

        # Offene Findings werden NICHT hier geladen — sie kommen über
        # ANALYST_CONTEXT_TEMPLATE.{open_findings} in den Prompt (keine Dopplung)

        # ── Letzte Session ──
        last_session = await self.get_last_session()
        if last_session:
            parts.append("## Letzte Analyse-Session\n")
            started = last_session['started_at']
            ended = last_session['ended_at']
            if started and ended:
                duration_min = (ended - started).total_seconds() / 60
                parts.append(f"- **Dauer:** {duration_min:.0f} Minuten")
            parts.append(f"- **Trigger:** {last_session['trigger_type']}")
            parts.append(f"- **Modell:** {last_session.get('model_used', 'unbekannt')}")
            parts.append(f"- **Token:** {last_session.get('tokens_used', 0)}")
            parts.append(f"- **Findings:** {last_session.get('findings_count', 0)}")
            parts.append(f"- **Auto-Fixes:** {last_session.get('auto_fixes_count', 0)}")
            parts.append(f"- **Issues:** {last_session.get('issues_created', 0)}")
            topics = last_session.get('topics_investigated')
            if topics:
                parts.append(f"- **Themen:** {', '.join(topics)}")
            summary = last_session.get('ai_summary')
            if summary:
                parts.append(f"- **Zusammenfassung:** {summary}")
            parts.append("")

        # ── Patterns (Top 10) ──
        patterns = await self.get_patterns()
        if patterns:
            top_patterns = patterns[:10]
            parts.append(f"## Erkannte Patterns (Top 10 von {len(patterns)})\n")
            for p in top_patterns:
                parts.append(f"- **{p['pattern_type']}** ({p['times_seen']}x): {p['description']}")
            parts.append("")

        # ── Orchestrator-Fixes (was wurde automatisch behoben?) ──
        try:
            recent_fixes = await self.pool.fetch(
                """SELECT event_type, event_source, fix_description, success, created_at
                   FROM orchestrator_fixes
                   WHERE created_at >= NOW() - INTERVAL '14 days'
                   ORDER BY created_at DESC LIMIT 10"""
            )
            if recent_fixes:
                parts.append("## Orchestrator-Fixes (letzte 14 Tage)\n")
                for fix in recent_fixes:
                    icon = "✅" if fix['success'] else "❌"
                    parts.append(f"- {icon} [{fix['event_source']}] {fix.get('fix_description', '?')[:100]}")
                parts.append("")
        except Exception:
            pass  # Tabelle existiert vielleicht noch nicht

        # ── IP-Reputation (Angreifer-Übersicht) ──
        try:
            top_threats = await self.pool.fetch(
                """SELECT ip_address::TEXT, total_bans, threat_score, permanent_blocked
                   FROM ip_reputation WHERE total_bans >= 2
                   ORDER BY threat_score DESC LIMIT 5"""
            )
            if top_threats:
                parts.append("## Top-Bedrohungen (IP-Reputation)\n")
                for t in top_threats:
                    icon = "🔒" if t['permanent_blocked'] else "⚠️"
                    parts.append(
                        f"- {icon} `{t['ip_address']}` — {t['total_bans']}x gebannt, "
                        f"Score: {t['threat_score']}/100"
                    )
                parts.append("")
        except Exception:
            pass

        # ── 30-Tage-Statistik ──
        stats = await self._get_30day_stats()
        parts.append("## 30-Tage-Statistik\n")
        parts.append(f"- **Sessions:** {stats['sessions_count']}")
        parts.append(f"- **Findings gesamt:** {stats['findings_total']}")
        parts.append(f"- **Davon behoben:** {stats['findings_fixed']}")
        parts.append(f"- **Offene Findings:** {stats['findings_open']}")
        parts.append(f"- **Auto-Fixes:** {stats['auto_fixes']}")
        parts.append(f"- **Token verbraucht:** {stats['tokens_total']}")
        parts.append("")

        return "\n".join(parts)

    async def _get_30day_stats(self) -> Dict:
        """30-Tage-Statistik für den AI-Kontext berechnen

        Returns:
            Dict mit aggregierten Statistiken
        """
        # Sessions der letzten 30 Tage
        session_stats = await self.pool.fetchrow(
            """SELECT
                   COUNT(*) as cnt,
                   COALESCE(SUM(tokens_used), 0) as tokens,
                   COALESCE(SUM(auto_fixes_count), 0) as fixes
               FROM sessions
               WHERE started_at >= NOW() - INTERVAL '30 days'"""
        )

        # Findings der letzten 30 Tage
        findings_total = await self.pool.fetchval(
            """SELECT COUNT(*) FROM findings
               WHERE found_at >= NOW() - INTERVAL '30 days'"""
        )

        findings_fixed = await self.pool.fetchval(
            """SELECT COUNT(*) FROM findings
               WHERE found_at >= NOW() - INTERVAL '30 days'
                 AND status = 'fixed'"""
        )

        findings_open = await self.pool.fetchval(
            "SELECT COUNT(*) FROM findings WHERE status = 'open'"
        )

        return {
            'sessions_count': session_stats['cnt'],
            'tokens_total': session_stats['tokens'],
            'auto_fixes': session_stats['fixes'],
            'findings_total': findings_total,
            'findings_fixed': findings_fixed,
            'findings_open': findings_open,
        }
