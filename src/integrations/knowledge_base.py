"""
Knowledge Base for AI Learning - SQLite Backend

Stores all fixes, vulnerabilities, strategies, code changes, and log patterns
for continuous learning and success rate tracking.
"""

import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger('shadowops.knowledge')


class KnowledgeBase:
    """
    Persistent knowledge storage for AI learning

    Tracks:
    - All executed fixes (success/failure)
    - Discovered vulnerabilities
    - Fix strategies with success rates
    - Code changes (Git commits)
    - Log patterns
    """

    def __init__(self, db_path: str = "data/ai_knowledge.db"):
        """
        Initialize Knowledge Base

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = None
        self._initialize_database()

        logger.info(f"📚 Knowledge Base initialized: {self.db_path}")

    def _initialize_database(self):
        """Create database schema if not exists"""
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # Enable dict-like access

        cursor = self.conn.cursor()

        # Table: fixes - All executed fixes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fixes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                event_signature TEXT NOT NULL,
                event_source TEXT NOT NULL,
                event_type TEXT,
                severity TEXT,
                strategy_description TEXT,
                confidence REAL,
                result TEXT CHECK(result IN ('success', 'failure', 'partial')),
                error_message TEXT,
                duration_seconds REAL,
                retry_count INTEGER DEFAULT 0,
                metadata TEXT
            )
        """)

        # Table: vulnerabilities - Discovered vulnerabilities
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vulnerabilities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                source TEXT NOT NULL,
                cve_id TEXT,
                severity TEXT,
                package TEXT,
                version TEXT,
                fixed_version TEXT,
                status TEXT CHECK(status IN ('open', 'fixed', 'wontfix', 'investigating')),
                fix_id INTEGER,
                metadata TEXT,
                FOREIGN KEY (fix_id) REFERENCES fixes(id)
            )
        """)

        # Table: strategies - Fix strategies with success tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                avg_confidence REAL,
                total_duration_seconds REAL DEFAULT 0,
                last_used DATETIME,
                metadata TEXT
            )
        """)

        # Table: code_changes - Git commits reference
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                project TEXT NOT NULL,
                commit_hash TEXT,
                message TEXT,
                author TEXT,
                files_changed INTEGER,
                category TEXT,
                metadata TEXT
            )
        """)

        # Table: log_patterns - Recognized log patterns
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS log_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                tool_name TEXT NOT NULL,
                pattern_type TEXT,
                pattern_text TEXT,
                count INTEGER DEFAULT 1,
                severity TEXT,
                metadata TEXT
            )
        """)

        # Create indexes for faster queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fixes_signature ON fixes(event_signature)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fixes_source ON fixes(event_source)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fixes_result ON fixes(result)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vulnerabilities_cve ON vulnerabilities(cve_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_strategies_type ON strategies(event_type)")

        self.conn.commit()
        logger.info("✅ Knowledge Base schema initialized")

    def record_fix(self, event: Dict[str, Any], strategy: Dict[str, Any],
                   result: str, error_message: Optional[str] = None,
                   duration_seconds: float = 0.0, retry_count: int = 0) -> int:
        """
        Record a fix attempt

        Args:
            event: Event that triggered the fix
            strategy: Fix strategy used
            result: 'success', 'failure', or 'partial'
            error_message: Error message if failed
            duration_seconds: How long the fix took
            retry_count: Number of retries

        Returns:
            Fix ID
        """
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO fixes (
                event_signature, event_source, event_type, severity,
                strategy_description, confidence, result, error_message,
                duration_seconds, retry_count, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.get('signature', 'unknown'),
            event.get('source', 'unknown'),
            event.get('event_type', 'unknown'),
            event.get('severity', 'UNKNOWN'),
            strategy.get('description', ''),
            strategy.get('confidence', 0.0),
            result,
            error_message,
            duration_seconds,
            retry_count,
            json.dumps(event.get('details', {}))
        ))

        fix_id = cursor.lastrowid
        self.conn.commit()

        # Update strategy statistics
        self._update_strategy_stats(
            strategy.get('description', 'unknown'),
            event.get('event_type', 'unknown'),
            result == 'success',
            strategy.get('confidence', 0.0),
            duration_seconds
        )

        logger.info(f"📝 Recorded fix #{fix_id}: {result}")
        return fix_id

    def _update_strategy_stats(self, strategy_name: str, event_type: str,
                                success: bool, confidence: float, duration: float):
        """Update strategy success/failure statistics"""
        cursor = self.conn.cursor()

        # Check if strategy exists
        cursor.execute(
            "SELECT id, success_count, failure_count, avg_confidence, total_duration_seconds FROM strategies WHERE strategy_name = ? AND event_type = ?",
            (strategy_name, event_type)
        )
        row = cursor.fetchone()

        if row:
            # Update existing
            new_success = row[1] + (1 if success else 0)
            new_failure = row[2] + (0 if success else 1)
            total_attempts = new_success + new_failure
            new_avg_conf = ((row[3] * (total_attempts - 1)) + confidence) / total_attempts
            new_duration = row[4] + duration

            cursor.execute("""
                UPDATE strategies
                SET success_count = ?, failure_count = ?, avg_confidence = ?,
                    total_duration_seconds = ?, last_used = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_success, new_failure, new_avg_conf, new_duration, row[0]))
        else:
            # Create new
            cursor.execute("""
                INSERT INTO strategies (
                    strategy_name, event_type, success_count, failure_count,
                    avg_confidence, total_duration_seconds, last_used
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                strategy_name, event_type,
                1 if success else 0,
                0 if success else 1,
                confidence,
                duration
            ))

        self.conn.commit()

    def get_success_rate(self, event_signature: str = None, event_source: str = None,
                         days: int = 30) -> Dict[str, Any]:
        """
        Calculate success rate for events

        Args:
            event_signature: Optional event signature filter
            event_source: Optional source filter (trivy, fail2ban, etc.)
            days: Look back this many days

        Returns:
            Dict with success statistics
        """
        cursor = self.conn.cursor()

        since = datetime.now() - timedelta(days=days)

        query = "SELECT result, COUNT(*) FROM fixes WHERE timestamp >= ?"
        params = [since.isoformat()]

        if event_signature:
            query += " AND event_signature = ?"
            params.append(event_signature)

        if event_source:
            query += " AND event_source = ?"
            params.append(event_source)

        query += " GROUP BY result"

        cursor.execute(query, params)
        results = cursor.fetchall()

        stats = {
            'success': 0,
            'failure': 0,
            'partial': 0,
            'total': 0,
            'success_rate': 0.0
        }

        for row in results:
            result_type = row[0]
            count = row[1]
            stats[result_type] = count
            stats['total'] += count

        if stats['total'] > 0:
            stats['success_rate'] = stats['success'] / stats['total']

        return stats

    def get_best_strategies(self, event_type: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Get best performing strategies for an event type

        Args:
            event_type: Type of event
            limit: Max number of strategies to return

        Returns:
            List of strategy dicts sorted by success rate
        """
        cursor = self.conn.cursor()

        cursor.execute("""
            SELECT
                strategy_name,
                success_count,
                failure_count,
                avg_confidence,
                total_duration_seconds,
                last_used,
                (CAST(success_count AS REAL) / (success_count + failure_count)) as success_rate
            FROM strategies
            WHERE event_type = ? AND (success_count + failure_count) >= 3
            ORDER BY success_rate DESC, avg_confidence DESC
            LIMIT ?
        """, (event_type, limit))

        strategies = []
        for row in cursor.fetchall():
            strategies.append({
                'strategy_name': row[0],
                'success_count': row[1],
                'failure_count': row[2],
                'avg_confidence': row[3],
                'avg_duration': row[4] / (row[1] + row[2]),
                'last_used': row[5],
                'success_rate': row[6]
            })

        return strategies

    def record_vulnerability(self, vuln: Dict[str, Any], fix_id: Optional[int] = None) -> int:
        """Record a discovered vulnerability"""
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO vulnerabilities (
                source, cve_id, severity, package, version, fixed_version, status, fix_id, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            vuln.get('source', 'unknown'),
            vuln.get('cve_id'),
            vuln.get('severity', 'UNKNOWN'),
            vuln.get('package'),
            vuln.get('version'),
            vuln.get('fixed_version'),
            'open',
            fix_id,
            json.dumps(vuln)
        ))

        vuln_id = cursor.lastrowid
        self.conn.commit()

        return vuln_id

    def get_learning_summary(self, days: int = 30) -> Dict[str, Any]:
        """
        Get learning summary statistics

        Args:
            days: Look back this many days

        Returns:
            Dict with summary statistics
        """
        cursor = self.conn.cursor()
        since = datetime.now() - timedelta(days=days)

        # Total fixes
        cursor.execute("SELECT COUNT(*) FROM fixes WHERE timestamp >= ?", (since.isoformat(),))
        total_fixes = cursor.fetchone()[0]

        # Success rate
        success_stats = self.get_success_rate(days=days)

        # Top strategies
        cursor.execute("""
            SELECT strategy_name, success_count, failure_count
            FROM strategies
            ORDER BY (success_count + failure_count) DESC
            LIMIT 5
        """)
        top_strategies = cursor.fetchall()

        # Vulnerabilities
        cursor.execute("SELECT COUNT(*) FROM vulnerabilities WHERE timestamp >= ?", (since.isoformat(),))
        total_vulns = cursor.fetchone()[0]

        return {
            'period_days': days,
            'total_fixes': total_fixes,
            'success_stats': success_stats,
            'top_strategies': [
                {
                    'name': s[0],
                    'success': s[1],
                    'failure': s[2],
                    'success_rate': s[1] / (s[1] + s[2]) if (s[1] + s[2]) > 0 else 0
                }
                for s in top_strategies
            ],
            'total_vulnerabilities': total_vulns
        }

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("📚 Knowledge Base connection closed")


# Singleton instance
_kb_instance: Optional[KnowledgeBase] = None


def get_knowledge_base(db_path: str = "data/ai_knowledge.db") -> KnowledgeBase:
    """Get singleton Knowledge Base instance"""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase(db_path)
    return _kb_instance
