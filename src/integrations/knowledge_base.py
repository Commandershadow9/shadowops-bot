"""
Knowledge Base for AI Learning - PostgreSQL Backend

Speichert alle Fixes, Vulnerabilities, Strategien, Code-Änderungen und Log-Patterns
für kontinuierliches Lernen und Erfolgsraten-Tracking.

Nutzt die bestehende security_analyst DB (Port 5433) mit psycopg2.
Bestehende Tabellen: orchestrator_fixes, orchestrator_strategies, threat_patterns
Neue Tabellen: orchestrator_plans, orchestrator_vulnerabilities, orchestrator_code_changes, orchestrator_log_patterns
"""

import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging

import psycopg2
import psycopg2.extras

logger = logging.getLogger('shadowops.knowledge')


class KnowledgeBase:
    """
    Persistente Wissensspeicherung für AI-Learning (PostgreSQL)

    Tracks:
    - Alle ausgeführten Fixes (Erfolg/Misserfolg)
    - Entdeckte Vulnerabilities
    - Fix-Strategien mit Erfolgsraten
    - Code-Änderungen (Git Commits)
    - Log-Patterns
    - Koordinierte Remediations-Pläne
    """

    def __init__(self, dsn: str = "dbname=security_analyst user=security_analyst password=sec_analyst_2026 host=127.0.0.1 port=5433"):
        """
        Initialize Knowledge Base

        Args:
            dsn: PostgreSQL DSN-String für psycopg2.connect()
        """
        self.dsn = dsn
        self.conn = None
        self._initialize_database()

        logger.info(f"Knowledge Base initialisiert (PostgreSQL): {dsn.split('host=')[0]}...")

    def _initialize_database(self):
        """Erstellt DB-Schema falls nicht vorhanden.

        Bestehende Tabellen (orchestrator_fixes, orchestrator_strategies, threat_patterns)
        werden durch IF NOT EXISTS nicht berührt.
        Neue Tabellen werden erstellt.
        """
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = True

        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # --- Bestehende Tabellen (IF NOT EXISTS schützt) ---

        # Tabelle: orchestrator_fixes - Alle ausgeführten Fixes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orchestrator_fixes (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                event_source TEXT NOT NULL,
                fix_description TEXT,
                fix_steps JSONB,
                success BOOLEAN DEFAULT FALSE,
                execution_time_ms INTEGER,
                error_message TEXT,
                metadata JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Tabelle: orchestrator_strategies - Fix-Strategien mit Erfolgs-Tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orchestrator_strategies (
                id SERIAL PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                approach TEXT,
                success_rate REAL DEFAULT 0.0,
                times_used INTEGER DEFAULT 0,
                times_succeeded INTEGER DEFAULT 0,
                avg_execution_time_ms INTEGER,
                last_used_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(strategy_name, event_type)
            )
        """)

        # Tabelle: threat_patterns - Erkannte Bedrohungsmuster
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS threat_patterns (
                id SERIAL PRIMARY KEY,
                pattern_type TEXT NOT NULL,
                pattern TEXT NOT NULL,
                severity TEXT DEFAULT 'medium',
                description TEXT,
                mitigation TEXT,
                times_seen INTEGER DEFAULT 1,
                last_seen_at TIMESTAMPTZ DEFAULT NOW(),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(pattern_type, pattern)
            )
        """)

        # --- Neue Tabellen ---

        # Tabelle: orchestrator_vulnerabilities - Entdeckte Vulnerabilities
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orchestrator_vulnerabilities (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                source TEXT NOT NULL,
                cve_id TEXT,
                severity TEXT,
                package TEXT,
                version TEXT,
                fixed_version TEXT,
                status TEXT CHECK(status IN ('open', 'fixed', 'wontfix', 'investigating')),
                fix_id INTEGER REFERENCES orchestrator_fixes(id),
                metadata JSONB
            )
        """)

        # Tabelle: orchestrator_code_changes - Git Commits Referenz
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orchestrator_code_changes (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                project TEXT NOT NULL,
                commit_hash TEXT,
                message TEXT,
                author TEXT,
                files_changed INTEGER,
                category TEXT,
                metadata JSONB
            )
        """)

        # Tabelle: orchestrator_log_patterns - Erkannte Log-Patterns
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orchestrator_log_patterns (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                tool_name TEXT NOT NULL,
                pattern_type TEXT,
                pattern_text TEXT,
                count INTEGER DEFAULT 1,
                severity TEXT,
                metadata JSONB
            )
        """)

        # Tabelle: orchestrator_plans - Koordinierte Remediations-Pläne (AI-generiert)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orchestrator_plans (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                batch_id TEXT NOT NULL,
                event_sources TEXT NOT NULL,
                event_types TEXT,
                description TEXT NOT NULL,
                confidence REAL,
                phases JSONB NOT NULL,
                rollback_plan TEXT,
                estimated_minutes INTEGER,
                result TEXT CHECK(result IN ('success', 'failure', 'partial', 'pending')),
                ai_model TEXT,
                duration_seconds REAL
            )
        """)

        # Indexes für schnellere Abfragen
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orchestrator_fixes_source ON orchestrator_fixes(event_source)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orchestrator_fixes_created ON orchestrator_fixes(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orchestrator_vulns_cve ON orchestrator_vulnerabilities(cve_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orchestrator_vulns_status ON orchestrator_vulnerabilities(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orchestrator_plans_sources ON orchestrator_plans(event_sources)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orchestrator_plans_result ON orchestrator_plans(result)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orchestrator_log_patterns_tool ON orchestrator_log_patterns(tool_name)")

        logger.info("Knowledge Base Schema initialisiert (PostgreSQL)")

    def record_fix(self, event: Dict[str, Any], strategy: Dict[str, Any],
                   result: str, error_message: Optional[str] = None,
                   duration_seconds: float = 0.0, retry_count: int = 0) -> int:
        """
        Zeichnet einen Fix-Versuch auf

        Args:
            event: Event das den Fix ausgelöst hat
            strategy: Verwendete Fix-Strategie
            result: 'success', 'failure' oder 'partial'
            error_message: Fehlermeldung bei Misserfolg
            duration_seconds: Dauer des Fixes in Sekunden
            retry_count: Anzahl der Wiederholungsversuche

        Returns:
            Fix ID
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Mapping: result-String → boolean für bestehende DB-Spalte
        success = result == 'success'
        execution_time_ms = int(duration_seconds * 1000)

        # Metadata enthält Details + retry_count + event_signature + confidence
        meta = event.get('details', {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, ValueError):
                meta = {'raw': meta}
        meta['retry_count'] = retry_count
        meta['event_signature'] = event.get('signature', 'unknown')
        meta['severity'] = event.get('severity', 'UNKNOWN')
        meta['confidence'] = strategy.get('confidence', 0.0)
        meta['result_detail'] = result  # 'success', 'failure', 'partial'

        cursor.execute("""
            INSERT INTO orchestrator_fixes (
                event_type, event_source, fix_description, fix_steps,
                success, execution_time_ms, error_message, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            event.get('event_type', 'unknown'),
            event.get('source', 'unknown'),
            strategy.get('description', ''),
            json.dumps(strategy.get('steps', []), ensure_ascii=False),
            success,
            execution_time_ms,
            error_message,
            json.dumps(meta, ensure_ascii=False, default=str),
        ))

        fix_id = cursor.fetchone()['id']

        # Strategie-Statistiken aktualisieren
        self._update_strategy_stats(
            strategy.get('description', 'unknown'),
            event.get('event_type', 'unknown'),
            success,
            strategy.get('confidence', 0.0),
            duration_seconds
        )

        logger.info(f"Fix #{fix_id} aufgezeichnet: {result}")
        return fix_id

    def _update_strategy_stats(self, strategy_name: str, event_type: str,
                                success: bool, confidence: float, duration: float):
        """Aktualisiert Strategie-Erfolgs-/Misserfolgsstatistiken"""
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        execution_time_ms = int(duration * 1000)

        # Prüfe ob Strategie existiert
        cursor.execute(
            "SELECT id, times_used, times_succeeded, avg_execution_time_ms FROM orchestrator_strategies WHERE strategy_name = %s AND event_type = %s",
            (strategy_name, event_type)
        )
        row = cursor.fetchone()

        if row:
            # Bestehende Strategie aktualisieren
            new_times_used = row['times_used'] + 1
            new_times_succeeded = row['times_succeeded'] + (1 if success else 0)
            new_success_rate = new_times_succeeded / new_times_used if new_times_used > 0 else 0.0

            # Gleitender Durchschnitt der Ausführungszeit
            old_avg = row['avg_execution_time_ms'] or 0
            new_avg = int(((old_avg * row['times_used']) + execution_time_ms) / new_times_used)

            cursor.execute("""
                UPDATE orchestrator_strategies
                SET times_used = %s, times_succeeded = %s, success_rate = %s,
                    avg_execution_time_ms = %s, last_used_at = NOW()
                WHERE id = %s
            """, (new_times_used, new_times_succeeded, new_success_rate, new_avg, row['id']))
        else:
            # Neue Strategie anlegen
            initial_rate = 1.0 if success else 0.0
            cursor.execute("""
                INSERT INTO orchestrator_strategies (
                    strategy_name, event_type, approach, success_rate,
                    times_used, times_succeeded, avg_execution_time_ms, last_used_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (
                strategy_name, event_type,
                f"confidence={confidence:.2f}",
                initial_rate,
                1,
                1 if success else 0,
                execution_time_ms,
            ))

    def get_success_rate(self, event_signature: str = None, event_source: str = None,
                         days: int = 30) -> Dict[str, Any]:
        """
        Berechnet Erfolgsrate für Events

        Args:
            event_signature: Optionaler Event-Signatur-Filter
            event_source: Optionaler Quell-Filter (trivy, fail2ban, etc.)
            days: Zeitraum in Tagen

        Returns:
            Dict mit Erfolgsstatistiken
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        since = datetime.now() - timedelta(days=days)

        # Wir nutzen die metadata->>'result_detail' für granulare Ergebnisse
        # und die success-Spalte als Basis
        query = """
            SELECT
                COALESCE(metadata->>'result_detail', CASE WHEN success THEN 'success' ELSE 'failure' END) as result,
                COUNT(*) as cnt
            FROM orchestrator_fixes
            WHERE created_at >= %s
        """
        params: list = [since]

        if event_signature:
            query += " AND metadata->>'event_signature' = %s"
            params.append(event_signature)

        if event_source:
            query += " AND event_source = %s"
            params.append(event_source)

        query += " GROUP BY 1"

        cursor.execute(query, params)
        results = cursor.fetchall()

        stats = {
            'successful_attempts': 0,
            'failed_attempts': 0,
            'partial_attempts': 0,
            'total_attempts': 0,
            'success_rate': 0.0
        }

        for row in results:
            result_type = row['result']
            count = row['cnt']

            if result_type == 'success':
                stats['successful_attempts'] = count
            elif result_type == 'failure':
                stats['failed_attempts'] = count
            elif result_type == 'partial':
                stats['partial_attempts'] = count

            stats['total_attempts'] += count

        if stats['total_attempts'] > 0:
            stats['success_rate'] = stats['successful_attempts'] / stats['total_attempts']

        return stats

    def get_best_strategies(self, event_type: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Beste Strategien für einen Event-Typ abrufen

        Args:
            event_type: Typ des Events
            limit: Maximale Anzahl zurückzugebender Strategien

        Returns:
            Liste von Strategie-Dicts sortiert nach Erfolgsrate
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT
                strategy_name,
                times_succeeded,
                (times_used - times_succeeded) as failure_count,
                success_rate,
                avg_execution_time_ms,
                last_used_at,
                times_used
            FROM orchestrator_strategies
            WHERE event_type = %s AND times_used >= 3
            ORDER BY success_rate DESC, times_used DESC
            LIMIT %s
        """, (event_type, limit))

        strategies = []
        for row in cursor.fetchall():
            times_used = row['times_used'] or 1
            avg_exec_ms = row['avg_execution_time_ms'] or 0
            strategies.append({
                'strategy_name': row['strategy_name'],
                'success_count': row['times_succeeded'],
                'failure_count': row['failure_count'],
                'avg_confidence': row['success_rate'],  # Kompatibilität: avg_confidence war die Rate
                'avg_duration': avg_exec_ms / 1000.0,  # ms → Sekunden
                'last_used': str(row['last_used_at']) if row['last_used_at'] else None,
                'success_rate': row['success_rate']
            })

        return strategies

    def record_vulnerability(self, vuln: Dict[str, Any], fix_id: Optional[int] = None) -> int:
        """Zeichnet eine entdeckte Vulnerability auf"""
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            INSERT INTO orchestrator_vulnerabilities (
                source, cve_id, severity, package, version, fixed_version, status, fix_id, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            vuln.get('source', 'unknown'),
            vuln.get('cve_id'),
            vuln.get('severity', 'UNKNOWN'),
            vuln.get('package'),
            vuln.get('version'),
            vuln.get('fixed_version'),
            'open',
            fix_id,
            json.dumps(vuln, ensure_ascii=False, default=str),
        ))

        vuln_id = cursor.fetchone()['id']

        return vuln_id

    def get_learning_summary(self, days: int = 30) -> Dict[str, Any]:
        """
        Lern-Zusammenfassung abrufen

        Args:
            days: Zeitraum in Tagen

        Returns:
            Dict mit Zusammenfassungsstatistiken
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        since = datetime.now() - timedelta(days=days)

        # Gesamtzahl Fixes
        cursor.execute("SELECT COUNT(*) as cnt FROM orchestrator_fixes WHERE created_at >= %s", (since,))
        total_fixes = cursor.fetchone()['cnt']

        # Erfolgsrate
        success_stats = self.get_success_rate(days=days)

        # Top-Strategien
        cursor.execute("""
            SELECT strategy_name, times_succeeded, (times_used - times_succeeded) as failure_count, times_used
            FROM orchestrator_strategies
            ORDER BY times_used DESC
            LIMIT 5
        """)
        top_strategies = cursor.fetchall()

        # Vulnerabilities
        cursor.execute("SELECT COUNT(*) as cnt FROM orchestrator_vulnerabilities WHERE created_at >= %s", (since,))
        total_vulns = cursor.fetchone()['cnt']

        return {
            'period_days': days,
            'total_fixes': total_fixes,
            'success_stats': success_stats,
            'top_strategies': [
                {
                    'name': s['strategy_name'],
                    'success': s['times_succeeded'],
                    'failure': s['failure_count'],
                    'success_rate': s['times_succeeded'] / s['times_used'] if s['times_used'] > 0 else 0
                }
                for s in top_strategies
            ],
            'total_vulnerabilities': total_vulns
        }

    def record_plan(self, batch_id: str, event_sources: List[str],
                    event_types: List[str], plan: Dict[str, Any],
                    ai_model: str = 'unknown') -> int:
        """
        Speichert einen koordinierten Remediations-Plan

        Args:
            batch_id: Batch-ID
            event_sources: Quellen (fail2ban, crowdsec, trivy, ...)
            event_types: Event-Typen (ban, threat, vulnerability, ...)
            plan: Der vollständige Plan (description, phases, confidence, ...)
            ai_model: Welches AI-Modell den Plan erstellt hat

        Returns:
            Plan-ID
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        phases_json = json.dumps(plan.get('phases', []), ensure_ascii=False, default=str)

        cursor.execute("""
            INSERT INTO orchestrator_plans (
                batch_id, event_sources, event_types, description,
                confidence, phases, rollback_plan, estimated_minutes,
                result, ai_model
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
            RETURNING id
        """, (
            batch_id,
            ','.join(event_sources),
            ','.join(event_types),
            plan.get('description', ''),
            plan.get('confidence', 0.0),
            phases_json,
            plan.get('rollback_plan', ''),
            plan.get('estimated_duration_minutes', 0),
            ai_model,
        ))

        plan_id = cursor.fetchone()['id']

        logger.info(f"Plan #{plan_id} gespeichert: {plan.get('description', '')[:80]}")
        return plan_id

    def update_plan_result(self, plan_id: int, result: str,
                           duration_seconds: float = 0.0):
        """Aktualisiert das Ergebnis eines Plans nach Ausführung"""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE orchestrator_plans SET result = %s, duration_seconds = %s WHERE id = %s",
            (result, duration_seconds, plan_id)
        )

    def get_similar_plans(self, event_sources: List[str], limit: int = 3,
                          days: int = 90) -> List[Dict[str, Any]]:
        """
        Findet ähnliche frühere Pläne basierend auf Event-Quellen.

        Args:
            event_sources: Aktuelle Event-Quellen
            limit: Max Anzahl zurückgegebener Pläne
            days: Zeitraum in Tagen

        Returns:
            Liste der relevantesten früheren Pläne
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        since = datetime.now() - timedelta(days=days)

        # Suche nach Plänen die mindestens eine gemeinsame Quelle haben
        conditions = " OR ".join(["event_sources LIKE %s" for _ in event_sources])
        params: list = [f"%{src}%" for src in event_sources]
        params.append(since)

        cursor.execute(f"""
            SELECT id, batch_id, event_sources, event_types, description,
                   confidence, phases, result, ai_model, estimated_minutes,
                   duration_seconds, created_at
            FROM orchestrator_plans
            WHERE ({conditions}) AND created_at >= %s
            ORDER BY
                CASE result WHEN 'success' THEN 0 WHEN 'partial' THEN 1 ELSE 2 END,
                confidence DESC
            LIMIT %s
        """, params + [limit])

        plans = []
        for row in cursor.fetchall():
            # phases ist bereits JSONB — psycopg2 gibt es als Python-Objekt zurück
            phases = row['phases']
            if isinstance(phases, str):
                try:
                    phases = json.loads(phases)
                except (json.JSONDecodeError, ValueError):
                    phases = []
            elif phases is None:
                phases = []

            plans.append({
                'id': row['id'],
                'batch_id': row['batch_id'],
                'event_sources': row['event_sources'],
                'event_types': row['event_types'],
                'description': row['description'],
                'confidence': row['confidence'],
                'phases': phases,
                'result': row['result'],
                'ai_model': row['ai_model'],
                'estimated_minutes': row['estimated_minutes'],
                'actual_duration': row['duration_seconds'],
                'timestamp': str(row['created_at']) if row['created_at'] else None,
            })

        return plans

    def get_analyst_findings_for_planning(self, limit: int = 20) -> List[Dict]:
        """Liest offene Findings des Security Analysts für den Orchestrator.

        Verbindet das Analyst-Wissen mit dem Orchestrator-Planungsprozess.
        Findings werden nach Severity (critical zuerst) und Datum sortiert.
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT id, severity, category, title, description, found_at as created_at
            FROM findings WHERE status = 'open'
            ORDER BY
                CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                              WHEN 'medium' THEN 2 ELSE 3 END,
                found_at DESC
            LIMIT %s
        """, (limit,))
        return [dict(r) for r in cursor.fetchall()]

    def close(self):
        """Datenbankverbindung schließen"""
        if self.conn:
            self.conn.close()
            logger.info("Knowledge Base Verbindung geschlossen")


# Singleton-Instanz
_kb_instance: Optional[KnowledgeBase] = None


def get_knowledge_base(dsn: str = "dbname=security_analyst user=security_analyst password=sec_analyst_2026 host=127.0.0.1 port=5433") -> KnowledgeBase:
    """Singleton Knowledge Base Instanz abrufen"""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase(dsn)
    return _kb_instance
