# Scan-Agent Dedup + Pipeline Foundation — Phase 0 & 1

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Dedup-Bug im Scan-Agent fixen (fingerprint-basiert statt text-basiert), Idempotenz-Check fuer zukuenftige Auto-Fixes schaffen, Token-Tracking reparieren, Blog-PR-Stau aufloesen — alles mit Learning-DB-Anbindung, damit das System aus echten Merge-Entscheidungen lernt.

**Architecture:**
- **Fingerprint-basierte Dedup:** `finding_fingerprint` als deterministischer SHA1 aus `(category, affected_project, normalized_files, signature_keywords)`. Ersetzt den Titel-Match, der bei semantisch gleichen aber anders formulierten Findings versagt.
- **Learning-Loop:** `agent_feedback`-Tabelle in `agent_learning` DB wird bei jeder Merge/Split-Entscheidung beschrieben. Ein neuer Discord-Slash-Command `/mark-duplicate <finding_id> <parent_id>` erlaubt User-Feedback, das in den Fingerprint-Algorithmus zurueckfliesst.
- **Idempotenz fuer Auto-Fix:** `fix_attempts_v2.event_signature` wird wieder aktiv genutzt (seit 2026-04-11 stumm). Vor jedem Fix: Query "identische signature in 24h versucht?" → skip.
- **Zero-Downtime-Migration:** Neue Spalten als nullable, Fingerprint per Backfill-Skript, dann Hard-Switch auf Fingerprint-Pfad.

**Tech Stack:**
- Python 3.12, asyncpg (bestehend), PostgreSQL 17 (security_analyst + agent_learning DBs auf Port 5433)
- pytest mit Fixture `async_db_pool` (bestehend in `tests/conftest.py`)
- hashlib stdlib fuer SHA1-Fingerprints, keine neuen Dependencies

**Out-of-Scope (Folge-Plaene):**
- Auto-Merge scharfstellen (`agent_review.auto_merge.enabled=true`) — separater Plan nach 7 Tagen Dedup-Stabilitaet
- Secrets-Rotation-Auto-Fix — erst nach Phase 3a/3b stabil
- Jules-Review-Quality-Loop (abandoned/revision_requested Postmortem) — eigener Plan

---

## Phase 0 — Dedup-Fix mit Learning-Integration

### Task 0.1: Fingerprint-Funktion (pure, testbar)

**Files:**
- Create: `src/integrations/security_engine/fingerprint.py`
- Test: `tests/unit/security_engine/test_fingerprint.py`

**Rationale:** Pure Funktion ohne DB-Abhaengigkeiten. Wird sowohl im Scan-Agent als auch in Migrations-Skript wiederverwendet.

**Step 1: Write the failing test**

```python
# tests/unit/security_engine/test_fingerprint.py
import pytest
from integrations.security_engine.fingerprint import (
    compute_finding_fingerprint, normalize_files, extract_signature_keywords
)


class TestNormalizeFiles:
    def test_sorts_and_lowercases(self):
        assert normalize_files(["src/B.py", "src/a.py"]) == ("src/a.py", "src/b.py")

    def test_empty(self):
        assert normalize_files([]) == ()

    def test_none(self):
        assert normalize_files(None) == ()

    def test_strips_whitespace(self):
        assert normalize_files(["  src/a.py  "]) == ("src/a.py",)


class TestExtractSignatureKeywords:
    def test_extracts_tech_terms(self):
        text = "ImageMagick Security-Updates auf Debian-Host aussetzen"
        kws = extract_signature_keywords(text)
        assert "imagemagick" in kws
        assert "debian" in kws

    def test_ignores_stopwords(self):
        text = "Die aktuelle Situation ist nicht optimal"
        assert extract_signature_keywords(text) == ()

    def test_max_three_keywords(self):
        text = "imagemagick debian ubuntu redhat alpine container docker"
        assert len(extract_signature_keywords(text)) == 3


class TestComputeFingerprint:
    def test_same_category_project_files_same_fingerprint(self):
        fp1 = compute_finding_fingerprint(
            category="dependencies",
            affected_project="infrastructure",
            affected_files=["Dockerfile"],
            title="ImageMagick Security-Updates auf Debian-Host",
        )
        fp2 = compute_finding_fingerprint(
            category="dependencies",
            affected_project="infrastructure",
            affected_files=["Dockerfile"],
            title="Debian Security-Update: ImageMagick",  # andere Formulierung, gleiches Problem
        )
        assert fp1 == fp2

    def test_different_project_different_fingerprint(self):
        fp1 = compute_finding_fingerprint("dependencies", "infrastructure", ["Dockerfile"], "X imagemagick")
        fp2 = compute_finding_fingerprint("dependencies", "guildscout", ["Dockerfile"], "X imagemagick")
        assert fp1 != fp2

    def test_different_category_different_fingerprint(self):
        fp1 = compute_finding_fingerprint("dependencies", "p", [], "x imagemagick")
        fp2 = compute_finding_fingerprint("secrets", "p", [], "x imagemagick")
        assert fp1 != fp2

    def test_fingerprint_is_40char_hex(self):
        fp = compute_finding_fingerprint("cat", "proj", [], "title")
        assert len(fp) == 40
        int(fp, 16)  # darf nicht werfen

    def test_order_independent_files(self):
        fp1 = compute_finding_fingerprint("c", "p", ["a.py", "b.py"], "t imagemagick")
        fp2 = compute_finding_fingerprint("c", "p", ["b.py", "a.py"], "t imagemagick")
        assert fp1 == fp2
```

**Step 2: Run test to verify it fails**

```bash
cd /home/cmdshadow/shadowops-bot
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/security_engine/test_fingerprint.py -x -v
```

Expected: FAIL — `ModuleNotFoundError: integrations.security_engine.fingerprint`

**Step 3: Write minimal implementation**

```python
# src/integrations/security_engine/fingerprint.py
"""
Deterministische Fingerprints fuer Security-Findings.

Ersetzt die alte Titel-basierte Dedup (_find_similar_open_finding), die bei
semantisch gleichen aber anders formulierten Findings versagt hat.
"""
from __future__ import annotations
import hashlib
import re
from typing import Optional, Sequence

# Stopwords fuer Signature-Extraktion (DE + EN, alles lowercase)
_STOPWORDS = frozenset({
    "die", "der", "das", "und", "oder", "aber", "nicht", "ist", "sind",
    "den", "dem", "des", "auf", "mit", "fuer", "von", "bei", "zu", "aus",
    "als", "auch", "eine", "einer", "eines", "einem", "einen", "ein",
    "the", "and", "but", "not", "for", "with", "from", "that", "this",
    "aktuelle", "neue", "alte", "aktuell", "neu",
    "optimal", "nicht", "situation", "problem",
    "security", "update", "updates", "fix", "fixes",
    "auszusetzen", "aussetzen", "einspielen", "nachziehen",
})

# Technische Signature-Keywords: Laenge >= 4, kein Stopword, alphanumerisch
_KEYWORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{3,}")


def normalize_files(files: Optional[Sequence[str]]) -> tuple[str, ...]:
    """Dateipfade: strip, lowercase, dedupe, sortiert -> deterministisch."""
    if not files:
        return ()
    normalized = sorted({f.strip().lower() for f in files if f and f.strip()})
    return tuple(normalized)


def extract_signature_keywords(text: str, max_keywords: int = 3) -> tuple[str, ...]:
    """Extrahiert bis zu N technische Keywords (Reihenfolge: Vorkommen)."""
    if not text:
        return ()
    seen: list[str] = []
    for match in _KEYWORD_RE.finditer(text.lower()):
        word = match.group(0).lower()
        if word in _STOPWORDS:
            continue
        if word in seen:
            continue
        seen.append(word)
        if len(seen) >= max_keywords:
            break
    return tuple(seen)


def compute_finding_fingerprint(
    category: str,
    affected_project: str,
    affected_files: Optional[Sequence[str]],
    title: str,
) -> str:
    """
    SHA1-Fingerprint aus (category, project, files, signature_keywords).

    Zwei Findings mit gleichem Fingerprint sind semantisch dasselbe Problem.
    """
    parts = [
        (category or "unknown").strip().lower(),
        (affected_project or "unknown").strip().lower(),
        "|".join(normalize_files(affected_files)),
        "|".join(extract_signature_keywords(title or "")),
    ]
    payload = "\x1f".join(parts)  # ASCII Unit Separator
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
```

**Step 4: Run test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/security_engine/test_fingerprint.py -x -v
```

Expected: PASS — alle 10 Tests gruen

**Step 5: Commit**

```bash
git add src/integrations/security_engine/fingerprint.py tests/unit/security_engine/test_fingerprint.py
git commit -m "feat(security): deterministischer Finding-Fingerprint fuer Dedup"
```

---

### Task 0.2: DB-Migration — `finding_fingerprint` Spalte

**Files:**
- Create: `src/integrations/security_engine/migrations/001_finding_fingerprint.sql`
- Create: `scripts/migrate_add_finding_fingerprint.py`
- Test: manueller Dry-Run gegen security_analyst DB

**Rationale:** Spalte muss nullable sein fuer Zero-Downtime. Backfill in separatem Schritt.

**Step 1: SQL-Migration schreiben**

```sql
-- src/integrations/security_engine/migrations/001_finding_fingerprint.sql
-- Zero-Downtime: nullable Spalte + Index-Concurrently fuer Live-Betrieb
ALTER TABLE findings
  ADD COLUMN IF NOT EXISTS finding_fingerprint text;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_findings_fingerprint_open
  ON findings(finding_fingerprint)
  WHERE status = 'open';

COMMENT ON COLUMN findings.finding_fingerprint IS
  'SHA1 aus category+project+files+keywords; ersetzt Titel-Match fuer Dedup (Plan 2026-04-17)';
```

**Step 2: Migration live anwenden**

```bash
docker exec -e PGPASSWORD=sec_analyst_2026 guildscout-postgres psql \
  -U security_analyst -d security_analyst \
  -f - < src/integrations/security_engine/migrations/001_finding_fingerprint.sql
```

Expected output: `ALTER TABLE`, `CREATE INDEX`, `COMMENT`

**Step 3: Verifikation**

```bash
docker exec -e PGPASSWORD=sec_analyst_2026 guildscout-postgres psql \
  -U security_analyst -d security_analyst \
  -c "\d findings" | grep fingerprint
```

Expected: Zeile `finding_fingerprint | text`

**Step 4: Backfill-Script schreiben**

```python
# scripts/migrate_add_finding_fingerprint.py
"""
Backfill finding_fingerprint fuer alle existierenden Findings.
Idempotent: laeuft nur ueber NULL-Rows.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncpg
from integrations.security_engine.fingerprint import compute_finding_fingerprint


async def main():
    dsn = os.environ.get(
        "SECURITY_ANALYST_DB_URL",
        "postgresql://security_analyst:sec_analyst_2026@127.0.0.1:5433/security_analyst",
    )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            "SELECT id, category, affected_project, affected_files, title "
            "FROM findings WHERE finding_fingerprint IS NULL"
        )
        print(f"Backfill {len(rows)} Findings ...")
        updated = 0
        for r in rows:
            fp = compute_finding_fingerprint(
                category=r["category"],
                affected_project=r["affected_project"] or "",
                affected_files=list(r["affected_files"] or []),
                title=r["title"],
            )
            await pool.execute(
                "UPDATE findings SET finding_fingerprint=$1 WHERE id=$2", fp, r["id"]
            )
            updated += 1
        print(f"{updated} Findings aktualisiert.")

        # Duplikats-Report (zur manuellen Review)
        dupes = await pool.fetch(
            "SELECT finding_fingerprint, COUNT(*) as c, array_agg(id ORDER BY found_at DESC) as ids "
            "FROM findings WHERE status='open' GROUP BY finding_fingerprint HAVING COUNT(*) > 1 "
            "ORDER BY c DESC LIMIT 20"
        )
        if dupes:
            print(f"\n=== {len(dupes)} Fingerprint-Duplikats-Gruppen (open) ===")
            for d in dupes:
                print(f"  fp={d['finding_fingerprint'][:12]}  count={d['c']}  ids={d['ids']}")
        else:
            print("\nKeine Duplikate in open-Findings.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 5: Backfill ausfuehren und commiten**

```bash
cd /home/cmdshadow/shadowops-bot
PYTHONPATH=src .venv/bin/python scripts/migrate_add_finding_fingerprint.py
```

Expected: Liste der gefundenen Duplikate — **nicht automatisch mergen**, nur Report. Der User entscheidet ueber jede Gruppe.

```bash
git add src/integrations/security_engine/migrations/001_finding_fingerprint.sql \
        scripts/migrate_add_finding_fingerprint.py
git commit -m "feat(security): finding_fingerprint Spalte + Backfill-Script"
```

---

### Task 0.3: `_find_similar_open_finding` auf Fingerprint umbauen

**Files:**
- Modify: `src/integrations/security_engine/scan_agent.py:1393-1407`
- Test: `tests/unit/security_engine/test_scan_agent_dedup.py` (neu)

**Rationale:** Ohne diesen Schritt schreibt der Scan-Agent zwar die Fingerprints, nutzt sie aber nicht fuer Dedup.

**Step 1: Failing test schreiben**

```python
# tests/unit/security_engine/test_scan_agent_dedup.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from integrations.security_engine.scan_agent import SecurityScanAgent


@pytest.mark.asyncio
async def test_fingerprint_based_dedup_catches_semantic_dupes(monkeypatch):
    """
    Zwei Findings mit anderem Titel aber gleichem (category, project, files)
    muessen als Duplikat erkannt werden.
    """
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    agent.db.pool = MagicMock()
    agent.db.pool.fetchrow = AsyncMock(return_value={
        "id": 123, "title": "Altes Finding", "github_issue_url": "https://github.com/x/1"
    })

    result = await agent._find_similar_open_finding_by_fingerprint(
        category="dependencies",
        affected_project="infrastructure",
        affected_files=["Dockerfile"],
        title="Neuer Wording-Titel ueber ImageMagick",
    )
    assert result is not None
    assert result["id"] == 123


@pytest.mark.asyncio
async def test_fingerprint_dedup_returns_none_when_no_match(monkeypatch):
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    agent.db.pool = MagicMock()
    agent.db.pool.fetchrow = AsyncMock(return_value=None)

    result = await agent._find_similar_open_finding_by_fingerprint(
        category="x", affected_project="y", affected_files=[], title="z"
    )
    assert result is None
```

**Step 2: Test laufen, muss failen**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/security_engine/test_scan_agent_dedup.py -x -v
```

Expected: FAIL — Methode existiert noch nicht.

**Step 3: Methode implementieren + Call-Sites anpassen**

In `src/integrations/security_engine/scan_agent.py`:

```python
# Import am Dateikopf ergaenzen (suche nach "from .fingerprint" — wenn nicht da, anlegen)
from .fingerprint import compute_finding_fingerprint

# Neue Methode direkt nach _find_similar_open_finding einfuegen:
async def _find_similar_open_finding_by_fingerprint(
    self,
    category: str,
    affected_project: str,
    affected_files,
    title: str,
) -> Optional[Dict]:
    """Fingerprint-basierte Dedup. Ersetzt die alte Titel-Methode."""
    fp = compute_finding_fingerprint(category, affected_project, affected_files, title)
    row = await self.db.pool.fetchrow(
        "SELECT id, title, github_issue_url, finding_fingerprint FROM findings "
        "WHERE status='open' AND finding_fingerprint=$1 LIMIT 1",
        fp,
    )
    return dict(row) if row else None
```

**Call-Site in `scan_agent.py` anpassen** (Zeile ~1100):

```python
# Vorher:
#   title = finding.get('title', 'Unbenannt')
#   existing = await self._find_similar_open_finding(title)

# Nachher:
title = finding.get('title', 'Unbenannt')
existing = await self._find_similar_open_finding_by_fingerprint(
    category=finding.get('category', 'unknown'),
    affected_project=finding.get('affected_project', '') or '',
    affected_files=list(finding.get('affected_files') or []),
    title=title,
)
if existing:
    duplicates_skipped += 1
    logger.info(
        "[scan-agent] Finding '%s' dedupliziert gegen #%d",
        title[:60], existing["id"],
    )
    # Feedback an Learning-DB (Task 0.5 liefert die Methode)
    if hasattr(self, "learning_bridge") and self.learning_bridge and self.learning_bridge.is_connected:
        await self.learning_bridge.record_dedup_decision(
            parent_id=existing["id"], new_title=title, project=finding.get('affected_project')
        )
    continue
```

Und in `INSERT INTO findings` (Zeile ~1129) den Fingerprint mitschreiben:

```python
fp = compute_finding_fingerprint(
    finding.get('category', 'unknown'),
    finding.get('affected_project', '') or '',
    list(finding.get('affected_files') or []),
    title,
)
await self.db.pool.execute("""
    INSERT INTO findings (severity, category, title, description, session_id,
        affected_project, affected_files, fix_type, github_issue_url, finding_fingerprint)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
""", finding.get('severity', 'info'), finding.get('category', 'unknown'),
    title, finding.get('description', ''), session_id,
    finding.get('affected_project'), finding.get('affected_files'),
    fix_type, github_issue_url, fp)
```

Die alte `_find_similar_open_finding` Methode BLEIBT (als Fallback + fuer Backward-Compat), wird aber von keinem Caller mehr genutzt. Loeschung in Folge-Plan.

**Step 4: Test gruen machen + bestehende Tests nicht brechen**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/security_engine/test_scan_agent_dedup.py -x -v
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_scan_agent.py -x -v
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_scan_agent_jules_classification.py -x -v
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_scan_agent_jules_delegation.py -x -v
```

Expected: Alle PASS.

**Step 5: Commit**

```bash
git add src/integrations/security_engine/scan_agent.py \
        tests/unit/security_engine/test_scan_agent_dedup.py
git commit -m "feat(security): fingerprint-basierte Finding-Dedup (ersetzt Titel-Match)"
```

---

### Task 0.4: Auto-Fix-Idempotenz via `fix_attempts_v2.event_signature`

**Files:**
- Modify: `src/integrations/security_engine/scan_agent.py` — neue Methode `_was_fix_attempted_recently`
- Test: `tests/unit/security_engine/test_scan_agent_idempotency.py`

**Rationale:** `fix_attempts_v2` wird seit 2026-04-11 nicht mehr geschrieben (letzter Eintrag vom 11.04.). Vor Auto-Fix-Scharfstellung muss eine Idempotenz-Barriere rein: „Gleicher Fingerprint, gleicher Fix-Typ, in den letzten 24h versucht? → skip".

**Step 1: Failing test**

```python
# tests/unit/security_engine/test_scan_agent_idempotency.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone

from integrations.security_engine.scan_agent import SecurityScanAgent


@pytest.mark.asyncio
async def test_idempotency_blocks_repeat_fix_within_24h():
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    recent = datetime.now(timezone.utc) - timedelta(hours=3)
    agent.db.pool = MagicMock()
    agent.db.pool.fetchval = AsyncMock(return_value=recent)

    blocked = await agent._was_fix_attempted_recently(
        fingerprint="abc123" * 6 + "abcd", fix_type="apt_upgrade", cooldown_hours=24
    )
    assert blocked is True


@pytest.mark.asyncio
async def test_idempotency_allows_after_cooldown():
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    agent.db.pool = MagicMock()
    agent.db.pool.fetchval = AsyncMock(return_value=old)

    blocked = await agent._was_fix_attempted_recently(
        fingerprint="x" * 40, fix_type="apt_upgrade", cooldown_hours=24
    )
    assert blocked is False


@pytest.mark.asyncio
async def test_idempotency_no_previous_attempt():
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    agent.db.pool = MagicMock()
    agent.db.pool.fetchval = AsyncMock(return_value=None)

    blocked = await agent._was_fix_attempted_recently("x" * 40, "apt_upgrade")
    assert blocked is False
```

**Step 2: Run, expect FAIL**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/security_engine/test_scan_agent_idempotency.py -x -v
```

**Step 3: Implementation**

```python
# In scan_agent.py, neue Methode:
async def _was_fix_attempted_recently(
    self, fingerprint: str, fix_type: str, cooldown_hours: int = 24
) -> bool:
    """True wenn in den letzten N Stunden bereits ein Fix-Versuch mit identischer
    (fingerprint, fix_type) Kombination lief — egal ob erfolgreich oder nicht."""
    if not fingerprint or not fix_type:
        return False
    last_attempt = await self.db.pool.fetchval("""
        SELECT MAX(created_at) FROM fix_attempts_v2
        WHERE event_signature=$1 AND approach=$2
          AND created_at > NOW() - ($3 || ' hours')::interval
    """, fingerprint, fix_type, str(cooldown_hours))
    return last_attempt is not None
```

**Step 4: Tests gruen**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/security_engine/test_scan_agent_idempotency.py -x -v
```

**Step 5: Commit**

```bash
git add src/integrations/security_engine/scan_agent.py \
        tests/unit/security_engine/test_scan_agent_idempotency.py
git commit -m "feat(security): Idempotenz-Check gegen Fix-Wiederholung im 24h-Fenster"
```

---

### Task 0.5: LearningBridge — Dedup-Feedback-Methoden

**Files:**
- Modify: `src/integrations/security_engine/learning_bridge.py` — 2 neue Methoden
- Test: `tests/unit/security_engine/test_learning_bridge_dedup.py`

**Rationale:** Jede Dedup-Entscheidung (automatisch oder manuell via Slash-Command in Task 0.6) wird in `agent_feedback` geschrieben. Daraus kann der Scan-Agent mittelfristig lernen, welche Fingerprints wirklich identisch sind.

**Step 1: Failing test**

```python
# tests/unit/security_engine/test_learning_bridge_dedup.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from integrations.security_engine.learning_bridge import LearningBridge


@pytest.mark.asyncio
async def test_record_dedup_decision_writes_to_agent_feedback():
    lb = LearningBridge.__new__(LearningBridge)
    lb.pool = MagicMock()
    lb.pool.execute = AsyncMock()

    await lb.record_dedup_decision(parent_id=123, new_title="neuer titel", project="zerodox")

    lb.pool.execute.assert_awaited_once()
    args = lb.pool.execute.call_args[0]
    assert "agent_feedback" in args[0]
    # reference_id ist die parent_id als string
    assert "123" in args


@pytest.mark.asyncio
async def test_record_manual_merge_writes_feedback():
    lb = LearningBridge.__new__(LearningBridge)
    lb.pool = MagicMock()
    lb.pool.execute = AsyncMock()

    await lb.record_manual_merge(
        parent_id=100, child_id=101, user_id=42, user_name="christian", project="zerodox"
    )

    lb.pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_dedup_noop_when_disconnected():
    lb = LearningBridge.__new__(LearningBridge)
    lb.pool = None  # nicht verbunden
    # darf nicht werfen
    await lb.record_dedup_decision(parent_id=1, new_title="x", project="y")
    await lb.record_manual_merge(1, 2, 3, "u", "p")
```

**Step 2: Test failen lassen**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/security_engine/test_learning_bridge_dedup.py -x -v
```

**Step 3: Implementation in `learning_bridge.py`** (am Ende der Klasse einfuegen):

```python
async def record_dedup_decision(
    self, parent_id: int, new_title: str, project: Optional[str] = None
) -> None:
    """Schreibt eine Auto-Dedup-Entscheidung in agent_feedback."""
    if not self.pool:
        return
    try:
        await self.pool.execute(
            """INSERT INTO agent_feedback
               (agent, project, reference_id, feedback_type, metadata)
               VALUES ($1, $2, $3, $4, $5)""",
            "security-scan-agent",
            project or "infrastructure",
            str(parent_id),
            "auto_dedup_merge",
            json.dumps({"new_title": new_title[:200]}),
        )
    except Exception as e:
        logger.warning("record_dedup_decision failed: %s", e)


async def record_manual_merge(
    self,
    parent_id: int,
    child_id: int,
    user_id: int,
    user_name: str,
    project: Optional[str] = None,
) -> None:
    """User hat manuell zwei Findings als Duplikat markiert (via /mark-duplicate)."""
    if not self.pool:
        return
    try:
        await self.pool.execute(
            """INSERT INTO agent_feedback
               (agent, project, reference_id, feedback_type, user_id, user_name,
                score_delta, metadata)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            "security-scan-agent",
            project or "infrastructure",
            str(parent_id),
            "manual_dedup_merge",
            user_id,
            user_name,
            1,  # positives Signal: Dedup war korrekt
            json.dumps({"child_id": child_id}),
        )
    except Exception as e:
        logger.warning("record_manual_merge failed: %s", e)
```

**Step 4: Tests gruen**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/security_engine/test_learning_bridge_dedup.py -x -v
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_learning_bridge.py -x -v
```

**Step 5: Commit**

```bash
git add src/integrations/security_engine/learning_bridge.py \
        tests/unit/security_engine/test_learning_bridge_dedup.py
git commit -m "feat(learning): Dedup-Entscheidungen in agent_feedback persistieren"
```

---

### Task 0.6: Discord-Slash-Command `/mark-duplicate`

**Files:**
- Modify: `src/cogs/admin.py` — neuer Command
- Test: `tests/unit/cogs/test_admin_mark_duplicate.py`

**Rationale:** User sieht in der Praxis beim PR-Review oft, dass der Scan-Agent zwei Findings erzeugt hat, die eigentlich dasselbe sind. Dieser Command macht es einen Klick-Aufwand — und das Feedback wandert in die Learning-DB.

**Step 1: Failing test**

```python
# tests/unit/cogs/test_admin_mark_duplicate.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from cogs.admin import AdminCog


@pytest.mark.asyncio
async def test_mark_duplicate_merges_findings():
    cog = AdminCog.__new__(AdminCog)
    cog.bot = MagicMock()
    cog.bot.security_engine = MagicMock()
    cog.bot.security_engine.scan_agent = MagicMock()
    cog.bot.security_engine.scan_agent.db = MagicMock()
    cog.bot.security_engine.scan_agent.db.pool = MagicMock()
    cog.bot.security_engine.scan_agent.db.pool.execute = AsyncMock()
    cog.bot.security_engine.scan_agent.db.pool.fetchrow = AsyncMock(
        return_value={"id": 101, "title": "child", "status": "open"}
    )
    cog.bot.security_engine.scan_agent.learning_bridge = MagicMock()
    cog.bot.security_engine.scan_agent.learning_bridge.record_manual_merge = AsyncMock()

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.user.name = "christian"
    interaction.response.send_message = AsyncMock()

    await cog.mark_duplicate.callback(cog, interaction, parent_id=100, child_id=101)

    # Child wird als duplicate_of markiert, nicht geloescht
    cog.bot.security_engine.scan_agent.db.pool.execute.assert_any_await(
        "UPDATE findings SET status='duplicate_of', fixed_at=NOW() WHERE id=$1", 101
    )
    # Learning-Feedback wurde geschrieben
    cog.bot.security_engine.scan_agent.learning_bridge.record_manual_merge.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()
```

**Step 2: Test laufen lassen, faellt**

**Step 3: Implementation in `src/cogs/admin.py`**

```python
# Hinzufuegen zu AdminCog:
@app_commands.command(
    name="mark-duplicate",
    description="Markiert ein Finding als Duplikat eines anderen (Learning-Feedback)",
)
@app_commands.describe(
    parent_id="ID des beibehaltenen Findings",
    child_id="ID des Findings, das als Duplikat markiert wird",
)
async def mark_duplicate(
    self, interaction: discord.Interaction, parent_id: int, child_id: int
):
    if parent_id == child_id:
        await interaction.response.send_message(
            "parent_id und child_id muessen unterschiedlich sein.", ephemeral=True
        )
        return
    engine = getattr(self.bot, "security_engine", None)
    if not engine or not getattr(engine, "scan_agent", None):
        await interaction.response.send_message(
            "Security-Engine nicht verfuegbar.", ephemeral=True
        )
        return
    agent = engine.scan_agent
    child = await agent.db.pool.fetchrow(
        "SELECT id, title, status, affected_project FROM findings WHERE id=$1", child_id
    )
    if not child:
        await interaction.response.send_message(
            f"Finding #{child_id} nicht gefunden.", ephemeral=True
        )
        return
    await agent.db.pool.execute(
        "UPDATE findings SET status='duplicate_of', fixed_at=NOW() WHERE id=$1", child_id
    )
    lb = getattr(agent, "learning_bridge", None)
    if lb and lb.is_connected:
        await lb.record_manual_merge(
            parent_id=parent_id,
            child_id=child_id,
            user_id=interaction.user.id,
            user_name=interaction.user.name,
            project=child["affected_project"],
        )
    await interaction.response.send_message(
        f"Finding #{child_id} als Duplikat von #{parent_id} markiert. Learning-Feedback gespeichert.",
        ephemeral=True,
    )
```

**Check-Constraint-Erweiterung (nur wenn `duplicate_of` noch nicht erlaubt):**

```bash
docker exec -e PGPASSWORD=sec_analyst_2026 guildscout-postgres psql \
  -U security_analyst -d security_analyst \
  -c "ALTER TABLE findings DROP CONSTRAINT IF EXISTS findings_status_check;
      ALTER TABLE findings ADD CONSTRAINT findings_status_check
        CHECK (status IN ('open','fixed','dismissed','false_positive','duplicate_of'));"
```

**Step 4: Test gruen + Bot-Command-Sync pruefen**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/cogs/test_admin_mark_duplicate.py -x -v
```

Nach Bot-Restart: `/mark-duplicate 100 101` in Discord testen, Response-Text erscheint.

**Step 5: Commit**

```bash
git add src/cogs/admin.py tests/unit/cogs/test_admin_mark_duplicate.py
git commit -m "feat(admin): /mark-duplicate Slash-Command mit Learning-DB-Feedback"
```

---

### Task 0.7: Duplikats-Aufraeumen-Skript

**Files:**
- Create: `scripts/cleanup_finding_duplicates.py`

**Rationale:** Einmaliger, manueller Run. Zeigt dem User die vorhandenen Fingerprint-Gruppen mit >1 open Finding, wartet auf Bestaetigung, markiert die juengeren als `duplicate_of`. Ist KEIN Cron-Job — nur zum Aufraeumen des Ist-Zustands.

**Step 1-5: Script schreiben, trockenlauf, mit `--apply` scharf**

```python
# scripts/cleanup_finding_duplicates.py
"""Duplikate-Aufraeumer.
Nutzung:
  --dry-run (default)  : Report
  --apply              : markiert juengere als 'duplicate_of', behaelt juengstes pro Gruppe
"""
import argparse, asyncio, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import asyncpg


async def main(apply: bool):
    dsn = os.environ.get(
        "SECURITY_ANALYST_DB_URL",
        "postgresql://security_analyst:sec_analyst_2026@127.0.0.1:5433/security_analyst",
    )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        groups = await pool.fetch("""
            SELECT finding_fingerprint,
                   array_agg(id ORDER BY found_at DESC) as ids,
                   array_agg(title ORDER BY found_at DESC) as titles
            FROM findings
            WHERE status='open' AND finding_fingerprint IS NOT NULL
            GROUP BY finding_fingerprint HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
        """)
        total_to_merge = 0
        for g in groups:
            ids = g["ids"]
            titles = g["titles"]
            parent, children = ids[0], ids[1:]
            print(f"\nFingerprint {g['finding_fingerprint'][:12]}")
            print(f"  BEHALTEN #{parent}: {titles[0][:70]}")
            for cid, ct in zip(children, titles[1:]):
                print(f"  merge    #{cid}: {ct[:70]}")
            total_to_merge += len(children)
            if apply:
                for cid in children:
                    await pool.execute(
                        "UPDATE findings SET status='duplicate_of', fixed_at=NOW() "
                        "WHERE id=$1",
                        cid,
                    )
        print(f"\n{len(groups)} Gruppen, {total_to_merge} Findings als Duplikat markiert." if apply
              else f"\nDRY-RUN: wuerde {total_to_merge} Findings mergen. Mit --apply ausfuehren.")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
```

Ausfuehrung:

```bash
# Dry-Run zuerst
PYTHONPATH=src .venv/bin/python scripts/cleanup_finding_duplicates.py
# Nach Review vom User:
PYTHONPATH=src .venv/bin/python scripts/cleanup_finding_duplicates.py --apply
```

Commit:

```bash
git add scripts/cleanup_finding_duplicates.py
git commit -m "chore(security): einmaliges Aufraeumen von Finding-Duplikaten"
```

---

## Phase 1 — Quick Wins

### Task 1.1: Token-Tracking fuer Jules-Reviews

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py` — nach AI-Call token aus result extrahieren
- Modify: `src/integrations/ai_engine.py:review_pr` — usage im Return-Dict zurueckgeben
- Test: `tests/unit/test_jules_token_tracking.py`

**Rationale:** Aktueller Bug: `jules_pr_reviews.tokens_consumed=0` bei allen 17 Reviews. Claude CLI gibt im `--output-format json` Token-Zahlen zurueck, aber der `--output-format text` Pfad (aktuell aktiv) nicht. Wir muessen die Token-Usage separat aus stderr parsen ODER auf JSON-Format umstellen.

**Recherche-Schritt zuerst (5 min):** `ai_engine.review_pr()` lesen, feststellen ob CLI-JSON oder -text genutzt wird und wo die Usage-Info hinterher landen koennte.

```bash
grep -n "review_pr\|--output-format\|tokens\|usage" src/integrations/ai_engine.py | head -20
```

**Steps dann analog 0.1 (TDD):** Failing test → Implementation → Green → Commit.

Konkrete Test-Idee:

```python
# tests/unit/test_jules_token_tracking.py
@pytest.mark.asyncio
async def test_review_pr_returns_token_usage_from_claude_cli(monkeypatch):
    # Mock subprocess to return a known JSON blob with usage stats
    # Assert: result["usage"]["input_tokens"] > 0
    ...
```

**Commit-Message:** `fix(jules): Token-Usage aus Claude-CLI parsen und in DB persistieren`

---

### Task 1.2: Token-Tracking fuer Scan-Agent

**Files:**
- Modify: `src/integrations/ai_engine.py` — `sessions.tokens_used` Wert zurueckgeben
- Modify: `src/integrations/security_engine/scan_agent.py` — bei `session ended` Token-Wert schreiben

**Rationale:** Gleiches Problem wie 1.1, anderer Caller. Codex CLI (Primary) und Claude CLI (Fallback) beide betroffen. Codex hat eigenen Stats-Parser, der aktuell nicht den `sessions`-Record befuellt.

Steps analog. Test prueft, dass nach Scan-Session `tokens_used > 0` in DB steht.

**Commit:** `fix(security-scan): Codex/Claude Token-Usage in sessions.tokens_used schreiben`

---

### Task 1.3: Patch-Notes Pipeline-Metric-Felder

**Files:**
- Modify: `src/patch_notes/stages/distribute.py:679` — Metric-JSON richtig bauen

**Rationale:** `ai_engine: "unknown"` und `pipeline_total_time_s: 0` sind leer, weil der Context die Werte nicht durchreicht. Trivial-Fix aber hoher Monitoring-Wert.

**Schritt:** Locate der `METRICS|patch_notes_pipeline|` Konstruktion, fehlende Context-Felder ergaenzen (AI-Engine-Name aus `context.ai_engine_used`, Total-Time aus `context.started_at` Delta).

**Commit:** `fix(patch-notes): ai_engine + pipeline_total_time_s in Metrics-Output`

---

### Task 1.4: Blog-PR-Stau aufloesen

**Files:**
- Create: `scripts/merge_approved_blog_prs.py`

**Rationale:** 10 OPEN Blog-PRs mit `claude-approved` Label + MERGEABLE-Status warten seit 14.–16.04. Script merged sie kontrolliert mit Dry-Run-First.

```python
# scripts/merge_approved_blog_prs.py
"""Merged alle OPEN ZERODOX-PRs mit 'claude-approved' Label + MERGEABLE.
   Sicherheit: Pfad-Check (nur content/blog/*), max 15 Dateien, Dry-Run default.
"""
import subprocess, json, argparse, sys

REPO = "Commandershadow9/ZERODOX"
MAX_FILES = 15
ALLOWED_PATH_PREFIXES = ("content/blog/", "src/pages/blog/", "content/data/")


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, check=True)
    return r.stdout


def main(apply: bool):
    prs = json.loads(gh([
        "pr", "list", "--repo", REPO, "--state", "open",
        "--label", "claude-approved", "--limit", "30",
        "--json", "number,mergeable,files,title",
    ]))
    to_merge, skipped = [], []
    for p in prs:
        if p["mergeable"] != "MERGEABLE":
            skipped.append((p["number"], "not mergeable"))
            continue
        files = [f["path"] for f in p.get("files", [])]
        if len(files) > MAX_FILES:
            skipped.append((p["number"], f"too many files ({len(files)})"))
            continue
        bad = [f for f in files if not f.startswith(ALLOWED_PATH_PREFIXES)]
        if bad:
            skipped.append((p["number"], f"path not whitelisted: {bad[:2]}"))
            continue
        to_merge.append(p)
    print(f"=== WILL MERGE ({len(to_merge)}) ===")
    for p in to_merge:
        print(f"  #{p['number']} {p['title'][:60]}")
    print(f"\n=== SKIP ({len(skipped)}) ===")
    for num, reason in skipped:
        print(f"  #{num}: {reason}")
    if not apply:
        print("\nDry-Run. Mit --apply scharf schalten.")
        return
    for p in to_merge:
        try:
            gh(["pr", "merge", str(p["number"]), "--repo", REPO, "--squash", "--delete-branch"])
            print(f"  merged #{p['number']}")
        except subprocess.CalledProcessError as e:
            print(f"  FAIL #{p['number']}: {e.stderr[:200]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    asyncio.run if False else main(ap.parse_args().apply)
```

Ausfuehrung:

```bash
python3 scripts/merge_approved_blog_prs.py          # Dry-Run
python3 scripts/merge_approved_blog_prs.py --apply  # Nach Review
```

**Commit:** `chore(ops): Skript zum kontrollierten Merge des Blog-PR-Staus`

---

## Verifikation & Rollout

### Nach Phase 0

1. Bot-Restart: `sudo systemctl restart shadowops-bot`
2. Logs beobachten (5 Min): `journalctl -u shadowops-bot -f | grep -iE "dedup|fingerprint|scan-agent"`
3. Naechsten Scan (00:00 Uhr) auswerten:
   ```sql
   SELECT COUNT(*) FROM findings WHERE finding_fingerprint IS NULL AND found_at > NOW() - INTERVAL '24h';
   -- Erwartung: 0
   ```
4. Duplikate-Gruppen-Report 24/48/72h:
   ```sql
   SELECT COUNT(*) FROM (
     SELECT finding_fingerprint FROM findings WHERE status='open'
     GROUP BY finding_fingerprint HAVING COUNT(*) > 1
   ) x;
   -- Erwartung: 0
   ```

### Nach Phase 1

1. Token-Dashboard: `SELECT SUM(tokens_consumed) FROM jules_pr_reviews WHERE created_at > NOW() - INTERVAL '24h';` — Erwartung: > 0
2. Metric-Log: `grep "METRICS|patch_notes_pipeline" logs/shadowops_$(date +%Y%m%d).log` — `ai_engine` darf nicht mehr `"unknown"` sein
3. Blog-PR-Stau: `gh pr list --repo Commandershadow9/ZERODOX --label claude-approved --state open` — Erwartung: leer

### Abort-Kriterien (Rollback)

- Wenn Scan-Agent nach Deploy >5% mehr Findings erzeugt (Dedup zu locker) → Revert Task 0.3
- Wenn Idempotenz-Check False-Positives erzeugt (lebende Fixes werden geblockt) → Cooldown auf 1h reduzieren oder Revert 0.4
- Wenn `/mark-duplicate` Command in Discord Exceptions wirft → Command deregistrieren via `/sync`

---

## Rueckblick-Checkliste nach 7 Tagen

Bevor Phase 2 (SeoAdapter Auto-Merge) startet:

- [ ] `SELECT COUNT(*) FROM findings WHERE status='open' GROUP BY finding_fingerprint HAVING COUNT(*)>1` ergibt 0 ueber 7 Tage hinweg
- [ ] `agent_feedback` hat mindestens 5 Dedup-Events (`feedback_type IN ('auto_dedup_merge','manual_dedup_merge')`) — System lernt aktiv
- [ ] Token-Zahlen in `jules_pr_reviews` und `sessions` sind plausibel (nicht 0, nicht extrem hoch)
- [ ] Keine neuen `Exception`-Eintraege in `journalctl -u shadowops-bot`
- [ ] Blog-PR-Stau bleibt leer (User merged neue taeglich manuell ODER Phase 2 startet)
