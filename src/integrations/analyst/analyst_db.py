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

        # ── Fix-Effektivitaet (welche Ansaetze funktionieren?) ──
        try:
            fix_eff = await self.get_fix_effectiveness()
            if fix_eff:
                parts.append("## Fix-Effektivitaet (90 Tage)\n")
                for cat, stats in fix_eff.items():
                    reg_warn = f" ⚠️{stats['regressions']} Regressionen" if stats['regressions'] else ""
                    parts.append(
                        f"- **{cat}:** {stats['success_rate']}% Erfolg "
                        f"({stats['successes']}/{stats['total']}){reg_warn}"
                    )
                parts.append("")
        except Exception:
            pass

        # ── Coverage-Luecken (was wurde lange nicht geprueft?) ──
        try:
            gaps = await self.get_coverage_gaps(max_age_days=7)
            if gaps:
                parts.append("## Scan-Luecken (>7 Tage nicht geprueft)\n")
                for g in gaps:
                    parts.append(f"- **{g['area']}** — zuletzt vor {g['days_ago']} Tagen")
                parts.append("")
        except Exception:
            pass

        # ── Finding-Qualitaet ──
        try:
            fp_stats = await self.get_false_positive_rate()
            if fp_stats['total_assessed'] > 0:
                parts.append("## Finding-Qualitaet (90 Tage)\n")
                parts.append(f"- **Bewertet:** {fp_stats['total_assessed']} Findings")
                parts.append(f"- **False Positives:** {fp_stats['false_positives']} ({fp_stats['false_positive_rate']}%)")
                parts.append(f"- **Ø Confidence:** {fp_stats['avg_confidence']}")
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

    # ─────────────────────────────────────────────
    # Fix Attempts — Jeden Fix-Versuch aufzeichnen
    # ─────────────────────────────────────────────

    async def record_fix_attempt(
        self,
        finding_id: int,
        session_id: int,
        approach: str,
        result: str,
        commands_used: Optional[List[str]] = None,
        side_effects: Optional[str] = None,
        error_message: Optional[str] = None,
        execution_time_s: Optional[int] = None,
    ) -> int:
        """Fix-Versuch aufzeichnen.

        Args:
            finding_id: Zugehoeriges Finding
            session_id: Aktuelle Session
            approach: Beschreibung des Ansatzes
            result: 'success', 'failure', 'partial'
            commands_used: Liste ausgefuehrter Befehle
            side_effects: Beobachtete Nebeneffekte
            error_message: Fehlermeldung bei Failure
            execution_time_s: Dauer in Sekunden

        Returns:
            ID des Fix-Attempts
        """
        row = await self.pool.fetchrow(
            """INSERT INTO fix_attempts
               (finding_id, session_id, approach, commands_used, result,
                side_effects, error_message, execution_time_s)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id""",
            finding_id, session_id, approach, commands_used, result,
            side_effects, error_message, execution_time_s,
        )
        fix_id = row['id']
        logger.info(
            "Fix-Attempt #%d fuer Finding #%d: %s (%s)",
            fix_id, finding_id, approach[:60], result,
        )
        return fix_id

    async def get_unverified_fixes(self, days: int = 14, limit: int = 10) -> List[Dict]:
        """Erfolgreiche Fixes die noch nicht verifiziert wurden.

        Holt Fixes der letzten N Tage die:
        - result = 'success'
        - noch nie verifiziert (verified_at IS NULL) ODER laenger als 3 Tage her

        Returns:
            Liste der Fix-Attempts mit Finding-Infos
        """
        rows = await self.pool.fetch(
            """SELECT fa.id, fa.finding_id, fa.approach, fa.commands_used,
                      fa.created_at, fa.verified_at, fa.still_valid,
                      f.title as finding_title, f.category, f.affected_project,
                      f.affected_files
               FROM fix_attempts fa
               JOIN findings f ON f.id = fa.finding_id
               WHERE fa.result = 'success'
                 AND fa.created_at >= NOW() - make_interval(days => $1)
                 AND (fa.verified_at IS NULL
                      OR fa.verified_at < NOW() - INTERVAL '3 days')
               ORDER BY fa.created_at DESC
               LIMIT $2""",
            days, limit,
        )
        return [dict(r) for r in rows]

    async def record_verification(
        self,
        fix_attempt_id: int,
        session_id: int,
        still_valid: bool,
        check_method: Optional[str] = None,
        regression_details: Optional[str] = None,
    ):
        """Verifikations-Ergebnis speichern.

        Aktualisiert auch den Fix-Attempt selbst (verified_at, still_valid).
        """
        now = datetime.now(timezone.utc)

        # Verifikations-Eintrag
        await self.pool.execute(
            """INSERT INTO fix_verifications
               (fix_attempt_id, session_id, still_valid, check_method,
                regression_details, checked_at)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            fix_attempt_id, session_id, still_valid, check_method,
            regression_details, now,
        )

        # Fix-Attempt aktualisieren
        await self.pool.execute(
            """UPDATE fix_attempts
               SET verified_at = $1, still_valid = $2
               WHERE id = $3""",
            now, still_valid, fix_attempt_id,
        )

        status = "OK" if still_valid else "REGRESSIERT"
        logger.info("Fix #%d verifiziert: %s", fix_attempt_id, status)

    async def get_fix_effectiveness(self) -> Dict:
        """Fix-Effektivitaet nach Kategorie berechnen.

        Returns:
            Dict mit Erfolgsraten pro Kategorie
        """
        rows = await self.pool.fetch(
            """SELECT f.category,
                      COUNT(*) as total,
                      COUNT(*) FILTER (WHERE fa.result = 'success') as successes,
                      COUNT(*) FILTER (WHERE fa.still_valid = FALSE) as regressions
               FROM fix_attempts fa
               JOIN findings f ON f.id = fa.finding_id
               WHERE fa.created_at >= NOW() - INTERVAL '90 days'
               GROUP BY f.category
               ORDER BY total DESC"""
        )
        result = {}
        for r in rows:
            total = r['total']
            successes = r['successes']
            rate = round(successes / total * 100) if total > 0 else 0
            result[r['category']] = {
                'total': total,
                'successes': successes,
                'regressions': r['regressions'],
                'success_rate': rate,
            }
        return result

    async def reopen_finding(self, finding_id: int, reason: str):
        """Finding wieder oeffnen wegen Regression.

        Setzt status zurueck auf 'open' und loggt den Grund.
        """
        await self.pool.execute(
            """UPDATE findings
               SET status = 'open', fixed_at = NULL
               WHERE id = $1""",
            finding_id,
        )
        logger.warning("Finding #%d re-opened: %s", finding_id, reason)

    # ─────────────────────────────────────────────
    # Finding Quality — Qualitaetsbewertung
    # ─────────────────────────────────────────────

    async def assess_finding_quality(
        self,
        finding_id: int,
        is_actionable: bool = True,
        is_false_positive: bool = False,
        false_positive_reason: Optional[str] = None,
        discovery_method: Optional[str] = None,
        confidence_score: float = 0.5,
        assessed_by: str = 'analyst',
    ):
        """Finding-Qualitaet bewerten (Upsert).

        Wird vom Analyst nach der Analyse aufgerufen um seine
        eigenen Findings zu bewerten.
        """
        await self.pool.execute(
            """INSERT INTO finding_quality
               (finding_id, is_actionable, is_false_positive, false_positive_reason,
                discovery_method, confidence_score, assessed_at, assessed_by)
               VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7)
               ON CONFLICT (finding_id)
               DO UPDATE SET
                   is_actionable = $2, is_false_positive = $3,
                   false_positive_reason = $4, discovery_method = $5,
                   confidence_score = $6, assessed_at = NOW(), assessed_by = $7""",
            finding_id, is_actionable, is_false_positive,
            false_positive_reason, discovery_method, confidence_score, assessed_by,
        )
        if is_false_positive:
            logger.info("Finding #%d als False Positive bewertet: %s", finding_id, false_positive_reason)

    async def get_false_positive_rate(self) -> Dict:
        """False-Positive-Rate berechnen (letzte 90 Tage)."""
        row = await self.pool.fetchrow(
            """SELECT
                   COUNT(*) as total,
                   COUNT(*) FILTER (WHERE is_false_positive) as false_positives,
                   AVG(confidence_score) as avg_confidence
               FROM finding_quality
               WHERE assessed_at >= NOW() - INTERVAL '90 days'"""
        )
        total = row['total'] or 0
        fps = row['false_positives'] or 0
        return {
            'total_assessed': total,
            'false_positives': fps,
            'false_positive_rate': round(fps / total * 100) if total > 0 else 0,
            'avg_confidence': round(float(row['avg_confidence'] or 0.5), 2),
        }

    # ─────────────────────────────────────────────
    # Scan Coverage — Abdeckungs-Tracking
    # ─────────────────────────────────────────────

    async def record_scan_coverage(
        self,
        session_id: int,
        areas: List[Dict],
    ):
        """Scan-Abdeckung speichern.

        Args:
            session_id: Aktuelle Session
            areas: Liste von {area, checked, depth, notes}
        """
        for a in areas:
            await self.pool.execute(
                """INSERT INTO scan_coverage
                   (session_id, area, checked, depth, notes)
                   VALUES ($1, $2, $3, $4, $5)""",
                session_id,
                a.get('area', 'unknown'),
                a.get('checked', True),
                a.get('depth', 'basic'),
                a.get('notes'),
            )
        logger.debug("Scan-Coverage fuer Session %d: %d Bereiche", session_id, len(areas))

    async def get_coverage_gaps(self, max_age_days: int = 7) -> List[Dict]:
        """Bereiche die seit mehr als N Tagen nicht geprueft wurden.

        Vergleicht alle bekannten Bereiche mit deren letztem Check-Datum.

        Returns:
            Liste von {area, last_checked, days_ago}
        """
        rows = await self.pool.fetch(
            """SELECT area, MAX(checked_at) as last_checked
               FROM scan_coverage
               WHERE checked = TRUE
               GROUP BY area
               HAVING MAX(checked_at) < NOW() - make_interval(days => $1)
               ORDER BY MAX(checked_at) ASC""",
            max_age_days,
        )
        result = []
        now = datetime.now(timezone.utc)
        for r in rows:
            days_ago = (now - r['last_checked']).days
            result.append({
                'area': r['area'],
                'last_checked': r['last_checked'].isoformat(),
                'days_ago': days_ago,
            })
        return result

    # ─────────────────────────────────────────────
    # Knowledge Confidence Decay
    # ─────────────────────────────────────────────

    async def decay_knowledge_confidence(self, decay_after_days: int = 14, decay_rate: float = 0.05):
        """Confidence von altem Wissen reduzieren.

        Knowledge-Eintraege die laenger als N Tage nicht verifiziert wurden
        verlieren pro Lauf etwas Confidence. Minimum: 0.2 (wird nie geloescht).

        Args:
            decay_after_days: Ab wann Decay einsetzt
            decay_rate: Um wie viel pro Lauf (0.05 = 5%)
        """
        result = await self.pool.execute(
            """UPDATE knowledge
               SET confidence = GREATEST(0.2, confidence - $1)
               WHERE last_verified < NOW() - make_interval(days => $2)
                 AND confidence > 0.2""",
            decay_rate, decay_after_days,
        )
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            logger.info("Confidence-Decay: %d Knowledge-Eintraege reduziert (-%s%%)", count, int(decay_rate * 100))

    # ─────────────────────────────────────────────
    # Scan-Plan — Datengetriebener Scan-Fokus
    # ─────────────────────────────────────────────

    # Standard-Scan-Bereiche die der Analyst abdecken sollte
    SCAN_AREAS = [
        'firewall',       # UFW-Regeln, offene Ports, iptables
        'ssh',            # sshd_config, Auth-Methoden, Brute-Force
        'docker',         # Container-Security, Trivy, Netzwerke
        'permissions',    # Dateirechte, Ownership, SUID/SGID
        'packages',       # System-Updates, CVEs, apt
        'services',       # systemd, laufende Prozesse, Ports
        'logs',           # Verdaechtige Muster, Angriffe, Fehler
        'network',        # Bind-Adressen, DNS, TLS, Traefik
        'credentials',    # .env-Dateien, API-Keys, Secrets
        'dependencies',   # npm/pip Schwachstellen, veraltete Pakete
    ]

    async def build_scan_plan(self) -> str:
        """Erstellt einen priorisierten, datengetriebenen Scan-Plan.

        Analysiert:
        1. Coverage-Luecken (was wurde lange nicht gecheckt?)
        2. Finding-Hotspots (welche Kategorien hatten die meisten Findings?)
        3. Regressionen (wo sind Fixes gescheitert?)
        4. Git-Aktivitaet (welche Projekte wurden kuerzlich geaendert?)

        Returns:
            Formatierter Markdown-String fuer den Analyst-Prompt
        """
        priority_items = []

        # ── 1. Coverage-Luecken → hoechste Prioritaet ──
        try:
            gaps = await self.get_coverage_gaps(max_age_days=5)
            for g in gaps:
                priority_items.append({
                    'area': g['area'],
                    'reason': f"Seit {g['days_ago']} Tagen nicht geprueft",
                    'priority': 1,
                })
        except Exception:
            pass

        # ── 2. Regressierte Fixes → etwas ist kaputt ──
        try:
            regressions = await self.pool.fetch(
                """SELECT DISTINCT f.category, f.title
                   FROM fix_attempts fa
                   JOIN findings f ON f.id = fa.finding_id
                   WHERE fa.still_valid = FALSE
                     AND fa.created_at >= NOW() - INTERVAL '30 days'
                   LIMIT 5"""
            )
            for r in regressions:
                priority_items.append({
                    'area': r['category'],
                    'reason': f"Regression: {r['title'][:60]}",
                    'priority': 1,
                })
        except Exception:
            pass

        # ── 3. Finding-Hotspots → wo treten Probleme haeufig auf? ──
        try:
            hotspots = await self.pool.fetch(
                """SELECT category, COUNT(*) as cnt
                   FROM findings
                   WHERE found_at >= NOW() - INTERVAL '60 days'
                   GROUP BY category
                   ORDER BY cnt DESC
                   LIMIT 5"""
            )
            for h in hotspots:
                priority_items.append({
                    'area': h['category'],
                    'reason': f"{h['cnt']} Findings in 60 Tagen — Hotspot",
                    'priority': 2,
                })
        except Exception:
            pass

        # ── 4. Git-Activity → geaenderte Projekte brauchen Re-Scan ──
        try:
            active_projects = await self.pool.fetch(
                """SELECT subject, content FROM knowledge
                   WHERE category = 'project_activity'
                     AND subject LIKE '%_git_activity'
                     AND content LIKE '%Security-Commits%'
                   ORDER BY confidence DESC"""
            )
            for p in active_projects:
                # Projekte mit Security-Commits → priorisieren
                if 'Security-Commits. ' in p['content']:
                    sec_part = p['content'].split('Security-Commits. ')[0]
                    # Anzahl Security-Commits extrahieren
                    parts = sec_part.split(', ')
                    for part in parts:
                        if 'Security' in part:
                            try:
                                sec_count = int(part.split()[0])
                                if sec_count > 0:
                                    project_name = p['subject'].replace('_git_activity', '')
                                    priority_items.append({
                                        'area': f"project:{project_name}",
                                        'reason': f"{sec_count} Security-Commits — Aenderungen verifizieren",
                                        'priority': 2,
                                    })
                            except ValueError:
                                pass
        except Exception:
            pass

        # ── 5. Standard-Bereiche die noch nie gecheckt wurden ──
        try:
            checked_areas = await self.pool.fetch(
                "SELECT DISTINCT area FROM scan_coverage WHERE checked = TRUE"
            )
            checked_set = {r['area'] for r in checked_areas}
            for area in self.SCAN_AREAS:
                if area not in checked_set:
                    priority_items.append({
                        'area': area,
                        'reason': 'Noch nie geprueft',
                        'priority': 1,
                    })
        except Exception:
            # Wenn scan_coverage leer ist → alle Bereiche sind "neu"
            pass

        if not priority_items:
            return "Keine spezifischen Prioritaeten — fuehre eine vollstaendige Analyse durch."

        # Deduplizieren nach area (hoechste Prioritaet gewinnt)
        seen = {}
        for item in priority_items:
            area = item['area']
            if area not in seen or item['priority'] < seen[area]['priority']:
                seen[area] = item
        deduped = sorted(seen.values(), key=lambda x: (x['priority'], x['area']))

        # Formatieren
        lines = ["Priorisierte Scan-Reihenfolge:\n"]
        for i, item in enumerate(deduped, 1):
            urgency = "🔴" if item['priority'] == 1 else "🟡"
            lines.append(f"{i}. {urgency} **{item['area']}** — {item['reason']}")

        return "\n".join(lines)
