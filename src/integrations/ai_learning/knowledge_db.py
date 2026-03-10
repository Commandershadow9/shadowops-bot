"""
Knowledge DB - Persistente SQLite-Datenbank für das AI Learning System

Speichert Erkenntnisse, Security-Events, Health-Snapshots, Fix-Historie
und langfristige Patterns in einer strukturierten SQLite-Datenbank.

Ersetzt die JSON-basierten Dateien durch eine performante,
abfragbare Datenbank mit automatischem Cleanup.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger('shadowops.knowledge_db')

# Datenbankpfad: data/knowledge.db (neben der bestehenden ai_knowledge.db)
DB_PATH = Path(__file__).parent.parent.parent / "data" / "knowledge.db"


class KnowledgeDB:
    """Persistente Knowledge-Datenbank für AI Learning

    Thread-safe SQLite-Backend mit CRUD-Operationen für:
    - Insights (gelernte Erkenntnisse)
    - Security Events (Bans, Angriffe, CVEs)
    - Health Snapshots (Projekt-Verfügbarkeit)
    - Fix History (was wurde versucht, was hat funktioniert)
    - Learned Patterns (langfristige Muster)
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Datenbank initialisieren und Schema erstellen"""
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        # WAL-Modus für bessere Concurrent-Performance
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

        cursor = self.conn.cursor()

        # Erkenntnisse/Insights die das System gelernt hat
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                insight_id TEXT UNIQUE NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                project TEXT,
                source TEXT,
                data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Security Events (Bans, Angriffe, CVEs)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                source_ip TEXT,
                details TEXT,
                resolved BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Projekt Health Snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS health_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                is_online BOOLEAN NOT NULL,
                response_time_ms REAL,
                uptime_pct REAL,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Fix-Historie (was wurde versucht, was hat funktioniert)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fix_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT,
                fix_type TEXT,
                description TEXT NOT NULL,
                commands_json TEXT,
                success BOOLEAN,
                confidence REAL,
                ai_model TEXT,
                duration_seconds REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Langzeit-Patterns (extrahiert aus Insights/Events)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS learned_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                frequency INTEGER DEFAULT 1,
                last_seen TIMESTAMP,
                data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Indizes für häufige Abfragen
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_insights_category
            ON insights(category)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_insights_created
            ON insights(created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_security_events_type
            ON security_events(event_type, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_security_events_severity
            ON security_events(severity)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_health_project_time
            ON health_snapshots(project_name, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fix_history_project
            ON fix_history(project, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_patterns_type
            ON learned_patterns(pattern_type)
        """)

        self.conn.commit()
        logger.info(f"Knowledge DB initialisiert: {self.db_path}")

    def close(self):
        """Datenbankverbindung schließen"""
        if self.conn:
            self.conn.close()
            self.conn = None

    # ─────────────────────────────────────────────
    # CRUD: Insights
    # ─────────────────────────────────────────────

    def add_insight(
        self,
        insight_id: str,
        category: str,
        title: str,
        description: str,
        confidence: float = 0.5,
        project: Optional[str] = None,
        source: Optional[str] = None,
        data: Optional[Dict] = None
    ) -> int:
        """Neue Erkenntnis speichern

        Args:
            insight_id: Eindeutige ID (z.B. "git_pattern_001")
            category: git_pattern, code_pattern, security_trend, system_behavior
            title: Kurztitel der Erkenntnis
            description: Ausführliche Beschreibung
            confidence: Konfidenz-Wert 0.0-1.0
            project: Betroffenes Projekt (optional)
            source: Welcher Loop/Agent hat es entdeckt (optional)
            data: Zusätzliche strukturierte Daten (optional)

        Returns:
            ID des eingefügten Datensatzes
        """
        data_json = json.dumps(data, ensure_ascii=False) if data else None

        cursor = self.conn.execute(
            """INSERT OR REPLACE INTO insights
               (insight_id, category, title, description, confidence, project, source, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (insight_id, category, title, description, confidence, project, source, data_json)
        )
        self.conn.commit()
        logger.debug(f"Insight gespeichert: [{category}] {title} (confidence={confidence})")
        return cursor.lastrowid

    # ─────────────────────────────────────────────
    # CRUD: Security Events
    # ─────────────────────────────────────────────

    def add_security_event(
        self,
        event_type: str,
        severity: str,
        source_ip: Optional[str] = None,
        details: Optional[str] = None
    ) -> int:
        """Security-Event speichern

        Args:
            event_type: fail2ban_ban, crowdsec_decision, trivy_scan, aide_change
            severity: LOW, MEDIUM, HIGH, CRITICAL
            source_ip: Quell-IP (optional)
            details: Beschreibung/Details (optional)

        Returns:
            ID des eingefügten Datensatzes
        """
        cursor = self.conn.execute(
            """INSERT INTO security_events
               (event_type, severity, source_ip, details)
               VALUES (?, ?, ?, ?)""",
            (event_type, severity, source_ip, details)
        )
        self.conn.commit()
        logger.debug(f"Security-Event gespeichert: [{severity}] {event_type}")
        return cursor.lastrowid

    # ─────────────────────────────────────────────
    # CRUD: Health Snapshots
    # ─────────────────────────────────────────────

    def add_health_snapshot(
        self,
        project_name: str,
        is_online: bool,
        response_time_ms: Optional[float] = None,
        uptime_pct: Optional[float] = None,
        error: Optional[str] = None
    ) -> int:
        """Health-Snapshot eines Projekts speichern

        Args:
            project_name: Name des Projekts (z.B. "guildscout", "zerodox")
            is_online: Ob der Service erreichbar ist
            response_time_ms: Antwortzeit in Millisekunden (optional)
            uptime_pct: Uptime-Prozent (optional)
            error: Fehlermeldung bei Ausfall (optional)

        Returns:
            ID des eingefügten Datensatzes
        """
        cursor = self.conn.execute(
            """INSERT INTO health_snapshots
               (project_name, is_online, response_time_ms, uptime_pct, error)
               VALUES (?, ?, ?, ?, ?)""",
            (project_name, is_online, response_time_ms, uptime_pct, error)
        )
        self.conn.commit()
        return cursor.lastrowid

    # ─────────────────────────────────────────────
    # CRUD: Fix History
    # ─────────────────────────────────────────────

    def add_fix_result(
        self,
        project: Optional[str],
        fix_type: Optional[str],
        description: str,
        commands: Optional[List[str]] = None,
        success: Optional[bool] = None,
        confidence: Optional[float] = None,
        ai_model: Optional[str] = None,
        duration: Optional[float] = None
    ) -> int:
        """Fix-Ergebnis speichern

        Args:
            project: Betroffenes Projekt (optional)
            fix_type: auto_fix, manual, orchestrator
            description: Was wurde gemacht
            commands: Liste der ausgeführten Befehle (optional)
            success: Ob der Fix erfolgreich war (optional)
            confidence: AI-Konfidenz des Fixes (optional)
            ai_model: Verwendetes AI-Modell (optional)
            duration: Dauer in Sekunden (optional)

        Returns:
            ID des eingefügten Datensatzes
        """
        commands_json = json.dumps(commands, ensure_ascii=False) if commands else None

        cursor = self.conn.execute(
            """INSERT INTO fix_history
               (project, fix_type, description, commands_json, success, confidence, ai_model, duration_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (project, fix_type, description, commands_json, success, confidence, ai_model, duration)
        )
        self.conn.commit()
        logger.debug(f"Fix-Ergebnis gespeichert: [{fix_type}] {description[:50]}... (success={success})")
        return cursor.lastrowid

    # ─────────────────────────────────────────────
    # CRUD: Learned Patterns
    # ─────────────────────────────────────────────

    def add_or_update_pattern(
        self,
        pattern_type: str,
        title: str,
        description: str,
        data: Optional[Dict] = None
    ) -> int:
        """Pattern hinzufügen oder aktualisieren (Frequenz erhöhen)

        Wenn ein Pattern mit gleichem Typ und Titel existiert, wird die
        Frequenz erhöht und last_seen/updated_at aktualisiert.

        Args:
            pattern_type: attack_pattern, fix_pattern, performance_pattern
            title: Titel des Patterns
            description: Beschreibung
            data: Zusätzliche Daten (optional)

        Returns:
            ID des Datensatzes
        """
        data_json = json.dumps(data, ensure_ascii=False) if data else None
        now = datetime.now(timezone.utc).isoformat()

        # Prüfen ob Pattern bereits existiert
        existing = self.conn.execute(
            "SELECT id, frequency FROM learned_patterns WHERE pattern_type = ? AND title = ?",
            (pattern_type, title)
        ).fetchone()

        if existing:
            # Frequenz erhöhen, last_seen und updated_at aktualisieren
            new_freq = existing["frequency"] + 1
            self.conn.execute(
                """UPDATE learned_patterns
                   SET frequency = ?, last_seen = ?, updated_at = ?,
                       description = ?, data_json = COALESCE(?, data_json)
                   WHERE id = ?""",
                (new_freq, now, now, description, data_json, existing["id"])
            )
            self.conn.commit()
            logger.debug(f"Pattern aktualisiert: [{pattern_type}] {title} (freq={new_freq})")
            return existing["id"]
        else:
            # Neues Pattern anlegen
            cursor = self.conn.execute(
                """INSERT INTO learned_patterns
                   (pattern_type, title, description, frequency, last_seen, data_json)
                   VALUES (?, ?, ?, 1, ?, ?)""",
                (pattern_type, title, description, now, data_json)
            )
            self.conn.commit()
            logger.debug(f"Neues Pattern gespeichert: [{pattern_type}] {title}")
            return cursor.lastrowid

    # ─────────────────────────────────────────────
    # Abfrage-Methoden
    # ─────────────────────────────────────────────

    def get_recent_insights(
        self,
        limit: int = 20,
        category: Optional[str] = None
    ) -> List[Dict]:
        """Letzte Erkenntnisse abrufen

        Args:
            limit: Maximale Anzahl (default 20)
            category: Optional nach Kategorie filtern

        Returns:
            Liste von Insight-Dicts
        """
        if category:
            rows = self.conn.execute(
                """SELECT * FROM insights
                   WHERE category = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (category, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM insights
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,)
            ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def get_security_summary(self, hours: int = 24) -> Dict:
        """Security-Zusammenfassung der letzten X Stunden

        Args:
            hours: Zeitraum in Stunden (default 24)

        Returns:
            Dict mit {total_events, by_type, by_severity, top_ips}
        """
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        # Gesamt-Anzahl
        total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM security_events WHERE created_at >= ?",
            (since,)
        ).fetchone()["cnt"]

        # Nach Typ gruppiert
        by_type_rows = self.conn.execute(
            """SELECT event_type, COUNT(*) as cnt
               FROM security_events WHERE created_at >= ?
               GROUP BY event_type ORDER BY cnt DESC""",
            (since,)
        ).fetchall()
        by_type = {row["event_type"]: row["cnt"] for row in by_type_rows}

        # Nach Severity gruppiert
        by_severity_rows = self.conn.execute(
            """SELECT severity, COUNT(*) as cnt
               FROM security_events WHERE created_at >= ?
               GROUP BY severity ORDER BY cnt DESC""",
            (since,)
        ).fetchall()
        by_severity = {row["severity"]: row["cnt"] for row in by_severity_rows}

        # Top IPs
        top_ips_rows = self.conn.execute(
            """SELECT source_ip, COUNT(*) as cnt
               FROM security_events
               WHERE created_at >= ? AND source_ip IS NOT NULL
               GROUP BY source_ip ORDER BY cnt DESC LIMIT 10""",
            (since,)
        ).fetchall()
        top_ips = {row["source_ip"]: row["cnt"] for row in top_ips_rows}

        return {
            "total_events": total,
            "hours": hours,
            "by_type": by_type,
            "by_severity": by_severity,
            "top_ips": top_ips
        }

    def get_health_trend(
        self,
        project_name: str,
        hours: int = 24
    ) -> List[Dict]:
        """Health-Zeitreihe für ein Projekt

        Args:
            project_name: Name des Projekts
            hours: Zeitraum in Stunden (default 24)

        Returns:
            Liste von Health-Snapshot-Dicts, chronologisch sortiert
        """
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        rows = self.conn.execute(
            """SELECT * FROM health_snapshots
               WHERE project_name = ? AND created_at >= ?
               ORDER BY created_at ASC""",
            (project_name, since)
        ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def get_fix_success_rate(
        self,
        project: Optional[str] = None
    ) -> Dict:
        """Erfolgsrate der Fixes berechnen

        Args:
            project: Optional nach Projekt filtern

        Returns:
            Dict mit {total, successful, failed, unknown, rate}
        """
        if project:
            base_query = "FROM fix_history WHERE project = ?"
            params = (project,)
        else:
            base_query = "FROM fix_history"
            params = ()

        total = self.conn.execute(
            f"SELECT COUNT(*) as cnt {base_query}", params
        ).fetchone()["cnt"]

        successful = self.conn.execute(
            f"SELECT COUNT(*) as cnt {base_query} {'AND' if project else 'WHERE'} success = 1",
            params
        ).fetchone()["cnt"]

        failed = self.conn.execute(
            f"SELECT COUNT(*) as cnt {base_query} {'AND' if project else 'WHERE'} success = 0",
            params
        ).fetchone()["cnt"]

        unknown = total - successful - failed
        rate = round(successful / total, 4) if total > 0 else 0.0

        return {
            "total": total,
            "successful": successful,
            "failed": failed,
            "unknown": unknown,
            "rate": rate,
            "project": project
        }

    def get_learned_patterns(
        self,
        pattern_type: Optional[str] = None
    ) -> List[Dict]:
        """Gelernte Patterns abrufen

        Args:
            pattern_type: Optional nach Typ filtern

        Returns:
            Liste von Pattern-Dicts, sortiert nach Frequenz (absteigend)
        """
        if pattern_type:
            rows = self.conn.execute(
                """SELECT * FROM learned_patterns
                   WHERE pattern_type = ?
                   ORDER BY frequency DESC""",
                (pattern_type,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM learned_patterns ORDER BY frequency DESC"
            ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def get_knowledge_stats(self) -> Dict:
        """Gesamtstatistik für Discord-Embed

        Returns:
            Dict mit Zählerständen aller Tabellen und Highlights
        """
        insights_total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM insights"
        ).fetchone()["cnt"]

        insights_by_category = {}
        for row in self.conn.execute(
            "SELECT category, COUNT(*) as cnt FROM insights GROUP BY category"
        ).fetchall():
            insights_by_category[row["category"]] = row["cnt"]

        security_total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM security_events"
        ).fetchone()["cnt"]

        security_unresolved = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM security_events WHERE resolved = 0"
        ).fetchone()["cnt"]

        health_total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM health_snapshots"
        ).fetchone()["cnt"]

        fixes_total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM fix_history"
        ).fetchone()["cnt"]

        fix_rate = self.get_fix_success_rate()

        patterns_total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM learned_patterns"
        ).fetchone()["cnt"]

        top_pattern = self.conn.execute(
            "SELECT title, frequency FROM learned_patterns ORDER BY frequency DESC LIMIT 1"
        ).fetchone()

        return {
            "insights": {
                "total": insights_total,
                "by_category": insights_by_category
            },
            "security": {
                "total": security_total,
                "unresolved": security_unresolved
            },
            "health_snapshots": health_total,
            "fixes": {
                "total": fixes_total,
                "success_rate": fix_rate["rate"],
                "successful": fix_rate["successful"],
                "failed": fix_rate["failed"]
            },
            "patterns": {
                "total": patterns_total,
                "top_pattern": {
                    "title": top_pattern["title"],
                    "frequency": top_pattern["frequency"]
                } if top_pattern else None
            }
        }

    # ─────────────────────────────────────────────
    # Cleanup
    # ─────────────────────────────────────────────

    def prune_old_data(self, days: int = 90):
        """Alte Daten bereinigen, Patterns und Insights behalten

        Löscht Health-Snapshots und Security-Events älter als X Tage.
        Insights und Learned Patterns werden NICHT gelöscht (langfristiges Wissen).

        Args:
            days: Alter in Tagen (default 90)
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        deleted_health = self.conn.execute(
            "DELETE FROM health_snapshots WHERE created_at < ?", (cutoff,)
        ).rowcount

        deleted_security = self.conn.execute(
            "DELETE FROM security_events WHERE created_at < ? AND resolved = 1", (cutoff,)
        ).rowcount

        deleted_fixes = self.conn.execute(
            "DELETE FROM fix_history WHERE created_at < ?", (cutoff,)
        ).rowcount

        self.conn.commit()

        # Datenbank komprimieren nach großem Cleanup
        if deleted_health + deleted_security + deleted_fixes > 100:
            self.conn.execute("VACUUM")

        logger.info(
            f"Cleanup abgeschlossen ({days} Tage): "
            f"{deleted_health} Snapshots, {deleted_security} Security-Events, "
            f"{deleted_fixes} Fix-Einträge gelöscht"
        )

        return {
            "deleted_health_snapshots": deleted_health,
            "deleted_security_events": deleted_security,
            "deleted_fix_history": deleted_fixes,
            "cutoff_date": cutoff
        }

    # ─────────────────────────────────────────────
    # Hilfsmethoden
    # ─────────────────────────────────────────────

    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        """SQLite Row in ein normales Dict umwandeln

        Parst data_json und commands_json automatisch zurück in Python-Objekte.
        """
        d = dict(row)

        # JSON-Felder automatisch parsen
        for json_field in ("data_json", "commands_json"):
            if json_field in d and d[json_field] is not None:
                try:
                    d[json_field] = json.loads(d[json_field])
                except (json.JSONDecodeError, TypeError):
                    pass  # Bei fehlerhaftem JSON den String beibehalten

        return d

    def __del__(self):
        """Verbindung beim Garbage Collection schließen"""
        self.close()


# ─────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────

_db_instance: Optional[KnowledgeDB] = None


def get_knowledge_db() -> KnowledgeDB:
    """Singleton-Instanz der Knowledge DB zurückgeben

    Returns:
        KnowledgeDB Instanz (wird beim ersten Aufruf erstellt)
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = KnowledgeDB()
    return _db_instance
