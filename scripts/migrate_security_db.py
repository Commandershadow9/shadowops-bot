#!/usr/bin/env python3
"""Migration: orchestrator_fixes → fix_attempts_v2. Idempotent."""
import psycopg2
import psycopg2.extras

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.utils.config import get_config
DSN = get_config().security_analyst_dsn
if not DSN:
    raise RuntimeError("security_analyst DSN nicht konfiguriert")


def migrate():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Pruefen ob Zieltabelle existiert
    cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'fix_attempts_v2')")
    if not cur.fetchone()['exists']:
        print("fix_attempts_v2 existiert noch nicht — wird von SecurityDB erstellt. Starte erst den Bot.")
        cur.close()
        conn.close()
        return

    # Pruefen ob Quelltabelle existiert
    cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'orchestrator_fixes')")
    if not cur.fetchone()['exists']:
        print("orchestrator_fixes existiert nicht — nichts zu migrieren.")
        cur.close()
        conn.close()
        return

    cur.execute("SELECT COUNT(*) as cnt FROM orchestrator_fixes")
    count = cur.fetchone()['cnt']
    print(f"Migriere {count} Eintraege aus orchestrator_fixes → fix_attempts_v2...")

    cur.execute("""
        INSERT INTO fix_attempts_v2 (event_source, event_type, event_signature, phase_type,
            approach, commands, result, duration_ms, error_message, was_fast_path, engine_mode, metadata, created_at)
        SELECT event_source, event_type, event_source || '_' || event_type, 'fix',
            fix_description, COALESCE(fix_steps, '[]'::jsonb),
            CASE WHEN success THEN 'success' ELSE 'failed' END,
            COALESCE(execution_time_ms, 0), error_message, FALSE, 'reactive',
            COALESCE(metadata, '{}'::jsonb), created_at
        FROM orchestrator_fixes
        WHERE NOT EXISTS (
            SELECT 1 FROM fix_attempts_v2 v2
            WHERE v2.created_at = orchestrator_fixes.created_at
            AND v2.event_source = orchestrator_fixes.event_source)
    """)

    # Alte Tabellen umbenennen (nicht loeschen)
    for table in ['orchestrator_fixes', 'orchestrator_code_changes', 'orchestrator_log_patterns']:
        cur.execute(f"""
            DO $$ BEGIN
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table}')
                AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table}_deprecated')
                THEN ALTER TABLE {table} RENAME TO {table}_deprecated;
                END IF;
            END $$;
        """)

    print("Migration abgeschlossen.")
    cur.close()
    conn.close()


if __name__ == '__main__':
    migrate()
