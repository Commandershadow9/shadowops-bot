# Patch Notes Pipeline v6 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ersetze die 5800-Zeilen Mixin-basierte Patch Notes Pipeline durch ein State-Machine-Package mit klarer Stufen-Architektur, deterministischer Commit-Gruppierung und crash-resilientem State.

**Architecture:** Neues Package `src/patch_notes/` mit 5-Stufen State Machine (Collect → Classify → Generate → Validate → Distribute). Ein `PipelineContext`-Dataclass trägt alle Daten. Alte Mixins bleiben parallel lauffähig via Feature-Flag `patch_notes.engine: v6`.

**Tech Stack:** Python 3.12, discord.py 2.7, asyncio, aiosqlite (changelog_db), psycopg2/asyncpg (agent_learning), jsonschema, pytest

**Design-Doc:** `docs/design/patch-notes-v6.md`

**Konventionen:**
- `pythonpath = src` in pytest.ini — Imports relativ zu `src/`
- Tests: `tests/unit/test_*.py`, ein Test-File pro Modul
- Tests EINZELN ausführen (8 GB VPS): `pytest tests/unit/test_NAME.py -x -q`
- Commits: `feat|fix|refactor|test|docs: Beschreibung`

---

## Task 1: Package-Skelett + PipelineContext

**Files:**
- Create: `src/patch_notes/__init__.py`
- Create: `src/patch_notes/context.py`
- Create: `src/patch_notes/stages/__init__.py`
- Create: `src/patch_notes/templates/__init__.py`
- Test: `tests/unit/test_pipeline_context.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_pipeline_context.py
"""Tests für PipelineContext — Dataclass + Serialisierung."""
import json
import pytest
from patch_notes.context import PipelineContext, PipelineState


def test_context_creation_with_defaults():
    ctx = PipelineContext(
        project="mayday_sim",
        project_config={"patch_notes": {"type": "gaming"}},
        raw_commits=[{"message": "feat: test", "sha": "abc123"}],
        trigger="webhook",
    )
    assert ctx.project == "mayday_sim"
    assert ctx.state == PipelineState.PENDING
    assert ctx.version == ""
    assert ctx.groups == []
    assert ctx.error is None


def test_context_serialization_roundtrip():
    ctx = PipelineContext(
        project="guildscout",
        project_config={"patch_notes": {"type": "saas"}},
        raw_commits=[{"message": "fix: bug", "sha": "def456"}],
        trigger="cron",
    )
    ctx.version = "2.5.1"
    ctx.state = PipelineState.CLASSIFYING

    data = ctx.to_dict()
    assert isinstance(data, dict)
    assert data["project"] == "guildscout"
    assert data["version"] == "2.5.1"
    assert data["state"] == PipelineState.CLASSIFYING.value

    restored = PipelineContext.from_dict(data)
    assert restored.project == "guildscout"
    assert restored.version == "2.5.1"
    assert restored.state == PipelineState.CLASSIFYING


def test_context_json_serializable():
    ctx = PipelineContext(
        project="zerodox",
        project_config={},
        raw_commits=[],
        trigger="manual",
    )
    json_str = json.dumps(ctx.to_dict())
    assert isinstance(json_str, str)
    assert "zerodox" in json_str


def test_pipeline_state_ordering():
    assert PipelineState.PENDING.value < PipelineState.COLLECTING.value
    assert PipelineState.COLLECTING.value < PipelineState.GENERATING.value
    assert PipelineState.COMPLETED.value > PipelineState.DISTRIBUTING.value
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_pipeline_context.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'patch_notes'`

**Step 3: Create package skeleton + context.py**

```python
# src/patch_notes/__init__.py
"""Patch Notes Pipeline v6 — State Machine Architektur."""

# src/patch_notes/stages/__init__.py
"""Pipeline-Stufen (Collect, Classify, Generate, Validate, Distribute)."""

# src/patch_notes/templates/__init__.py
"""Config-driven Prompt-Templates (gaming, saas, devops)."""
```

```python
# src/patch_notes/context.py
"""PipelineContext — Zentrales Datenobjekt das durch alle Stufen fließt."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Any


class PipelineState(IntEnum):
    PENDING = 0
    COLLECTING = 1
    CLASSIFYING = 2
    GENERATING = 3
    VALIDATING = 4
    DISTRIBUTING = 5
    COMPLETED = 6
    FAILED = 7


@dataclass
class PipelineContext:
    # ── Input ──
    project: str
    project_config: dict
    raw_commits: list[dict]
    trigger: str  # "webhook" | "cron" | "manual" | "polling"

    # ── Stufe 1: COLLECT ──
    enriched_commits: list[dict] = field(default_factory=list)
    git_stats: dict = field(default_factory=dict)

    # ── Stufe 2: CLASSIFY ──
    groups: list[dict] = field(default_factory=list)
    version: str = ""
    version_source: str = ""
    team_credits: list[dict] = field(default_factory=list)
    update_size: str = "normal"
    previous_version_content: str = ""

    # ── Stufe 3: GENERATE ──
    prompt: str = ""
    ai_result: dict | str | None = None
    ai_engine_used: str = ""
    variant_id: str = ""
    generation_time_s: float = 0.0

    # ── Stufe 4: VALIDATE ──
    title: str = ""
    tldr: str = ""
    web_content: str = ""
    changes: list[dict] = field(default_factory=list)
    seo_keywords: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ── Stufe 5: DISTRIBUTE ──
    sent_message_ids: list[list] = field(default_factory=list)

    # ── State Machine ──
    state: PipelineState = PipelineState.PENDING
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    # ── Metriken ──
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-serialisierbares Dict. Enum → int."""
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> PipelineContext:
        """Reconstruct from JSON dict."""
        data = dict(data)
        data["state"] = PipelineState(data.get("state", 0))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/unit/test_pipeline_context.py -x -q`
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/patch_notes/ tests/unit/test_pipeline_context.py
git commit -m "feat: patch_notes v6 — Package-Skelett + PipelineContext mit State Machine"
```

---

## Task 2: State-Persistenz + Pipeline-Orchestrator

**Files:**
- Create: `src/patch_notes/state.py`
- Create: `src/patch_notes/pipeline.py`
- Test: `tests/unit/test_pipeline_state.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_pipeline_state.py
"""Tests für Pipeline State-Persistenz und Orchestrator."""
import json
import pytest
from pathlib import Path
from patch_notes.state import PipelineStateStore
from patch_notes.context import PipelineContext, PipelineState


@pytest.fixture
def store(tmp_path):
    return PipelineStateStore(data_dir=tmp_path)


@pytest.fixture
def sample_ctx():
    return PipelineContext(
        project="shadowops-bot",
        project_config={"patch_notes": {"type": "devops"}},
        raw_commits=[{"message": "fix: test", "sha": "aaa111"}],
        trigger="manual",
    )


def test_persist_and_load(store, sample_ctx):
    sample_ctx.state = PipelineState.CLASSIFYING
    sample_ctx.version = "5.1.0"
    store.persist(sample_ctx)
    loaded = store.load("shadowops-bot")
    assert loaded is not None
    assert loaded.state == PipelineState.CLASSIFYING
    assert loaded.version == "5.1.0"


def test_load_nonexistent_returns_none(store):
    assert store.load("nonexistent") is None


def test_cleanup_completed(store, sample_ctx):
    sample_ctx.state = PipelineState.COMPLETED
    store.persist(sample_ctx)
    store.cleanup_completed()
    assert store.load("shadowops-bot") is None


def test_incomplete_runs(store, sample_ctx):
    sample_ctx.state = PipelineState.GENERATING
    store.persist(sample_ctx)
    runs = store.get_incomplete_runs()
    assert len(runs) == 1
    assert runs[0].project == "shadowops-bot"
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_pipeline_state.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'patch_notes.state'`

**Step 3: Implement state.py + pipeline.py**

```python
# src/patch_notes/state.py
"""Persistenter Pipeline-State — crash-resilient via JSON."""

import json
import logging
from pathlib import Path
from patch_notes.context import PipelineContext, PipelineState

logger = logging.getLogger('shadowops')


class PipelineStateStore:
    def __init__(self, data_dir: Path):
        self.runs_dir = data_dir / 'pipeline_runs'
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, project: str) -> Path:
        safe = project.replace('/', '_').replace(' ', '_')
        return self.runs_dir / f"{safe}.json"

    def persist(self, ctx: PipelineContext) -> None:
        path = self._path(ctx.project)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(ctx.to_dict(), ensure_ascii=False, default=str))
        tmp.replace(path)

    def load(self, project: str) -> PipelineContext | None:
        path = self._path(project)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return PipelineContext.from_dict(data)
        except Exception as e:
            logger.warning(f"Pipeline-State korrupt für {project}: {e}")
            return None

    def cleanup_completed(self) -> int:
        removed = 0
        for path in self.runs_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                state = data.get("state", 0)
                if state in (PipelineState.COMPLETED, PipelineState.FAILED):
                    path.unlink()
                    removed += 1
            except Exception:
                pass
        return removed

    def get_incomplete_runs(self) -> list[PipelineContext]:
        runs = []
        for path in self.runs_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                state = data.get("state", 0)
                if state not in (PipelineState.COMPLETED, PipelineState.FAILED, PipelineState.PENDING):
                    runs.append(PipelineContext.from_dict(data))
            except Exception:
                pass
        return runs
```

```python
# src/patch_notes/pipeline.py
"""PatchNotePipeline — State Machine Orchestrator."""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from patch_notes.context import PipelineContext, PipelineState
from patch_notes.state import PipelineStateStore

logger = logging.getLogger('shadowops')


class PatchNotePipeline:
    """5-Stufen State Machine für Patch Notes Generierung."""

    def __init__(self, data_dir: Path, bot=None):
        self.state_store = PipelineStateStore(data_dir)
        self.bot = bot

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Führe Pipeline aus. Bei Crash: Restart ab letztem State."""
        from patch_notes.stages.collect import collect
        from patch_notes.stages.classify import classify
        from patch_notes.stages.generate import generate
        from patch_notes.stages.validate import validate
        from patch_notes.stages.distribute import distribute

        stages = [
            (PipelineState.COLLECTING, collect),
            (PipelineState.CLASSIFYING, classify),
            (PipelineState.GENERATING, generate),
            (PipelineState.VALIDATING, validate),
            (PipelineState.DISTRIBUTING, distribute),
        ]

        ctx.started_at = datetime.now(timezone.utc).isoformat()
        pipeline_start = time.monotonic()

        for target_state, stage_fn in stages:
            if ctx.state >= target_state:
                logger.info(f"[v6] Skipping {target_state.name} (already at {ctx.state.name})")
                continue

            ctx.state = target_state
            self.state_store.persist(ctx)
            logger.info(f"[v6] {ctx.project} → {target_state.name}")

            try:
                await stage_fn(ctx, self.bot)
            except Exception as e:
                ctx.state = PipelineState.FAILED
                ctx.error = f"{target_state.name}: {e}"
                self.state_store.persist(ctx)
                logger.error(f"[v6] {ctx.project} FAILED in {target_state.name}: {e}")
                raise

        ctx.state = PipelineState.COMPLETED
        ctx.completed_at = datetime.now(timezone.utc).isoformat()
        ctx.metrics["pipeline_total_time_s"] = round(time.monotonic() - pipeline_start, 2)
        self.state_store.persist(ctx)
        logger.info(
            f"[v6] {ctx.project} v{ctx.version} COMPLETED in {ctx.metrics['pipeline_total_time_s']}s"
        )
        return ctx

    async def resume_incomplete(self) -> list[PipelineContext]:
        """Setze nach Bot-Restart abgebrochene Runs fort."""
        results = []
        for ctx in self.state_store.get_incomplete_runs():
            logger.info(f"[v6] Resuming {ctx.project} from {PipelineState(ctx.state).name}")
            try:
                result = await self.run(ctx)
                results.append(result)
            except Exception as e:
                logger.error(f"[v6] Resume failed for {ctx.project}: {e}")
        return results
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/unit/test_pipeline_state.py -x -q`
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/patch_notes/state.py src/patch_notes/pipeline.py tests/unit/test_pipeline_state.py
git commit -m "feat: patch_notes v6 — State-Persistenz + Pipeline-Orchestrator"
```

---

## Task 3: Versionierung (EINE Quelle)

**Files:**
- Create: `src/patch_notes/versioning.py`
- Test: `tests/unit/test_pn_versioning.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_pn_versioning.py
"""Tests für DB-basierte SemVer-Versionierung."""
import sqlite3
import pytest
from pathlib import Path
from patch_notes.versioning import calculate_version, get_last_db_version, ensure_unique


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "changelogs.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE changelogs (
            project TEXT, version TEXT, title TEXT, content TEXT,
            tldr TEXT, changes TEXT, stats TEXT, seo_keywords TEXT,
            seo_description TEXT, language TEXT, published_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (project, version)
        )
    """)
    conn.execute("INSERT INTO changelogs (project, version, title) VALUES ('test', '0.18.0', 'old')")
    conn.execute("INSERT INTO changelogs (project, version, title) VALUES ('test', '0.19.0', 'prev')")
    conn.commit()
    conn.close()
    return path


def test_get_last_version(db_path):
    v = get_last_db_version("test", db_path)
    assert v == "0.19.0"


def test_get_last_version_no_project(db_path):
    v = get_last_db_version("nonexistent", db_path)
    assert v is None


def test_feature_bump(db_path):
    groups = [{"tag": "FEATURE", "theme": "Neues Feature"}]
    version, source = calculate_version("test", groups, db_path)
    assert version == "0.20.0"
    assert source == "semver"


def test_bugfix_bump(db_path):
    groups = [{"tag": "BUGFIX", "theme": "Fix"}]
    version, source = calculate_version("test", groups, db_path)
    assert version == "0.19.1"
    assert source == "semver"


def test_breaking_bump(db_path):
    groups = [{"tag": "BREAKING", "theme": "Breaking Change"}]
    version, source = calculate_version("test", groups, db_path)
    assert version == "1.0.0"
    assert source == "semver"


def test_new_project_fallback(db_path):
    groups = [{"tag": "FEATURE"}]
    version, source = calculate_version("brand_new", groups, db_path)
    assert version == "0.1.0"
    assert source == "fallback"


def test_collision_bumps_patch(db_path):
    # 0.19.0 existiert schon → nächste freie
    unique = ensure_unique("0.19.0", "test", db_path)
    assert unique == "0.19.1"


def test_infra_only_patch_bump(db_path):
    groups = [{"tag": "INFRASTRUCTURE"}, {"tag": "DOCS"}]
    version, source = calculate_version("test", groups, db_path)
    assert version == "0.19.1"
    assert source == "semver"
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_pn_versioning.py -x -q`
Expected: FAIL

**Step 3: Implement versioning.py**

```python
# src/patch_notes/versioning.py
"""Versionierung — EINE Quelle: Changelog-DB + SemVer."""

import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger('shadowops')

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / 'data' / 'changelogs.db'


def get_last_db_version(project: str, db_path: Path = _DEFAULT_DB) -> str | None:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT version FROM changelogs "
                "WHERE project = ? AND version NOT LIKE 'patch.%' "
                "ORDER BY created_at DESC LIMIT 1",
                (project,),
            ).fetchone()
        if row and re.match(r'^\d+\.\d+\.\d+$', row[0]):
            return row[0]
    except Exception as e:
        logger.warning(f"Version-DB-Fehler für {project}: {e}")
    return None


def ensure_unique(version: str, project: str, db_path: Path = _DEFAULT_DB) -> str:
    if not db_path.exists():
        return version
    try:
        with sqlite3.connect(str(db_path)) as conn:
            existing = {
                r[0] for r in conn.execute(
                    "SELECT version FROM changelogs WHERE project = ?", (project,)
                ).fetchall()
            }
    except Exception:
        return version

    if version not in existing:
        return version

    parts = version.split('.')
    major, minor = int(parts[0]), int(parts[1])
    patch = int(parts[2])
    for _ in range(100):
        patch += 1
        candidate = f"{major}.{minor}.{patch}"
        if candidate not in existing:
            return candidate
    return version


def calculate_version(
    project: str, groups: list[dict], db_path: Path = _DEFAULT_DB
) -> tuple[str, str]:
    last = get_last_db_version(project, db_path)
    if not last:
        return ("0.1.0", "fallback")

    has_breaking = any(g.get("tag") == "BREAKING" for g in groups)
    has_feature = any(g.get("tag") == "FEATURE" for g in groups)

    parts = last.split('.')
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if has_breaking:
        new = f"{major + 1}.0.0"
    elif has_feature:
        new = f"{major}.{minor + 1}.0"
    else:
        new = f"{major}.{minor}.{patch + 1}"

    return (ensure_unique(new, project, db_path), "semver")
```

**Step 4: Run test**

Run: `source .venv/bin/activate && pytest tests/unit/test_pn_versioning.py -x -q`
Expected: 8 passed

**Step 5: Commit**

```bash
git add src/patch_notes/versioning.py tests/unit/test_pn_versioning.py
git commit -m "feat: patch_notes v6 — DB-basierte SemVer-Versionierung (1 Quelle statt 5)"
```

---

## Task 4: Commit-Gruppierung

**Files:**
- Create: `src/patch_notes/grouping.py`
- Test: `tests/unit/test_pn_grouping.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_pn_grouping.py
"""Tests für deterministische Commit-Gruppierung."""
import pytest
from patch_notes.grouping import classify_commit, group_commits


def test_classify_feature():
    assert classify_commit({"message": "feat(auth): add CASL"}) == "FEATURE"


def test_classify_scoped_feature():
    assert classify_commit({"message": "feat(resilience): circuit breaker"}) == "FEATURE"


def test_classify_bugfix():
    assert classify_commit({"message": "fix: broken login"}) == "BUGFIX"


def test_classify_docs():
    assert classify_commit({"message": "docs: update README"}) == "DOCS"


def test_classify_design_doc():
    assert classify_commit({"message": "docs: Design-Doc für Phase 2"}) == "DESIGN_DOC"


def test_classify_breaking():
    assert classify_commit({"message": "feat!: remove old API"}) == "BREAKING"


def test_classify_refactor():
    assert classify_commit({"message": "refactor(events): cleanup"}) == "IMPROVEMENT"


def test_classify_with_pr_label_override():
    commit = {"message": "chore: stuff", "pr_labels": ["feature"]}
    assert classify_commit(commit) == "FEATURE"


def test_classify_design_doc_label():
    commit = {"message": "feat: implement X", "pr_labels": ["design-doc"]}
    assert classify_commit(commit) == "DESIGN_DOC"


def test_group_by_scope():
    commits = [
        {"message": "feat(auth): CASL builder", "sha": "a1"},
        {"message": "feat(auth): useAbility hook", "sha": "a2"},
        {"message": "feat(auth): requireAbility", "sha": "a3"},
        {"message": "feat(events): EventStore", "sha": "b1"},
        {"message": "feat(events): RedisEventStore", "sha": "b2"},
        {"message": "fix: typo", "sha": "c1"},
    ]
    groups = group_commits(commits)
    themes = {g["scope"] for g in groups}
    assert "auth" in themes
    assert "events" in themes
    auth_group = next(g for g in groups if g["scope"] == "auth")
    assert len(auth_group["commits"]) == 3
    assert auth_group["tag"] == "FEATURE"


def test_group_player_facing():
    commits = [
        {"message": "feat(auth): game:play permission", "sha": "a1"},
        {"message": "feat(events): CQRS migration", "sha": "b1"},
    ]
    groups = group_commits(commits)
    auth_group = next(g for g in groups if g["scope"] == "auth")
    events_group = next(g for g in groups if g["scope"] == "events")
    assert auth_group["is_player_facing"] is True
    assert events_group["is_player_facing"] is False


def test_group_no_cap():
    """Alle 200 Commits müssen in Gruppen erscheinen."""
    commits = [{"message": f"feat(scope{i % 10}): change {i}", "sha": f"s{i}"} for i in range(200)]
    groups = group_commits(commits)
    total = sum(len(g["commits"]) for g in groups)
    assert total == 200


def test_group_summary_generated():
    commits = [
        {"message": "feat(ui): neuer Button", "sha": "a1"},
        {"message": "feat(ui): Modal redesign", "sha": "a2"},
    ]
    groups = group_commits(commits)
    ui_group = next(g for g in groups if g["scope"] == "ui")
    assert ui_group["summary"] != ""
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_pn_grouping.py -x -q`
Expected: FAIL

**Step 3: Implement grouping.py**

```python
# src/patch_notes/grouping.py
"""Deterministische Commit-Gruppierung — ALLE Commits, kein Cap."""

import re
import logging
from collections import defaultdict

logger = logging.getLogger('shadowops')

# PR-Label → Tag Override
LABEL_TO_TAG = {
    'feature': 'FEATURE', 'bugfix': 'BUGFIX', 'security': 'BUGFIX',
    'performance': 'IMPROVEMENT', 'infrastructure': 'INFRASTRUCTURE',
    'content': 'FEATURE', 'design-doc': 'DESIGN_DOC', 'breaking': 'BREAKING',
    'dependencies': 'DEPS', 'seo': 'IMPROVEMENT', 'gameplay': 'FEATURE',
    'ui': 'FEATURE',
}

# Scopes die als player-facing gelten
PLAYER_FACING_SCOPES = {
    'auth', 'play', 'ui', 'hooks', 'content', 'generator', 'voice',
    'gameplay', 'shop', 'lobby', 'notruf', 'einsatz', 'fahrzeug',
    'wache', 'leitstelle', 'szenario', 'admin', 'cosmetics',
}

# Scope → menschenlesbares Theme
SCOPE_TO_THEME = {
    'auth': 'Berechtigungen & Rollen', 'play': 'Gameplay',
    'ui': 'Benutzeroberfläche', 'hooks': 'Frontend-Logik',
    'events': 'Event-System', 'cqrs': 'Daten-Architektur',
    'resilience': 'Stabilität & Ausfallsicherheit',
    'observability': 'Monitoring & Metriken',
    'docker': 'Infrastruktur', 'ci': 'Build & Deploy',
    'db': 'Datenbank', 'content': 'Inhalte',
    'generator': 'Content-Generierung', 'voice': 'Sprachausgabe',
    'infra': 'Infrastruktur', 'security': 'Sicherheit',
    'migration': 'Daten-Migration', 'projections': 'Daten-Projektion',
}

_DESIGN_DOC_PATTERNS = re.compile(
    r'design.doc|implementierungsplan|architecture.*design|design.*architecture',
    re.IGNORECASE,
)

_CONVENTIONAL_RE = re.compile(
    r'^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<desc>.+)'
)


def classify_commit(commit: dict) -> str:
    """Klassifiziere einen Commit. PR-Labels haben Vorrang."""
    labels = commit.get('pr_labels', [])
    for label in labels:
        tag = LABEL_TO_TAG.get(label.lower())
        if tag:
            return tag

    msg = commit.get('message', '').split('\n')[0]
    m = _CONVENTIONAL_RE.match(msg)
    if not m:
        return 'OTHER'

    ctype = m.group('type').lower()
    is_breaking = bool(m.group('breaking'))

    if is_breaking:
        return 'BREAKING'
    if ctype == 'feat':
        return 'FEATURE'
    if ctype == 'fix':
        return 'BUGFIX'
    if ctype == 'docs':
        if _DESIGN_DOC_PATTERNS.search(msg):
            return 'DESIGN_DOC'
        return 'DOCS'
    if ctype in ('refactor', 'perf', 'style', 'chore', 'build'):
        return 'IMPROVEMENT'
    if ctype == 'test':
        return 'TEST'
    if ctype == 'revert':
        return 'REVERT'
    return 'OTHER'


def _extract_scope(commit: dict) -> str:
    """Extrahiere Scope aus Conventional Commit oder gib 'misc' zurück."""
    msg = commit.get('message', '').split('\n')[0]
    m = _CONVENTIONAL_RE.match(msg)
    if m and m.group('scope'):
        return m.group('scope').lower()
    return '_misc'


def _build_summary(commits: list[dict]) -> str:
    """Erzeuge eine kompakte Zusammenfassung aus Commit-Messages."""
    titles = []
    for c in commits[:5]:
        msg = c.get('message', '').split('\n')[0]
        m = _CONVENTIONAL_RE.match(msg)
        desc = m.group('desc').strip() if m else msg
        titles.append(desc)
    summary = '; '.join(titles)
    if len(commits) > 5:
        summary += f' (+{len(commits) - 5} weitere)'
    return summary


def group_commits(commits: list[dict]) -> list[dict]:
    """Gruppiere ALLE Commits nach Scope/Thema. Kein Cap."""
    # 1. Jeden Commit klassifizieren
    for c in commits:
        c['_tag'] = classify_commit(c)
        c['_scope'] = _extract_scope(c)

    # 2. Nach Scope gruppieren
    scope_buckets: dict[str, list[dict]] = defaultdict(list)
    for c in commits:
        scope_buckets[c['_scope']].append(c)

    # 3. Gruppen bauen
    groups = []
    for scope, bucket in scope_buckets.items():
        tags = [c['_tag'] for c in bucket]
        # Dominanter Tag: häufigster, bei Gleichstand FEATURE > BUGFIX > rest
        tag_priority = ['BREAKING', 'FEATURE', 'BUGFIX', 'IMPROVEMENT',
                        'INFRASTRUCTURE', 'TEST', 'DOCS', 'DESIGN_DOC', 'DEPS',
                        'REVERT', 'OTHER']
        dominant = max(set(tags), key=lambda t: (tags.count(t), -tag_priority.index(t) if t in tag_priority else -99))

        # Player-facing?
        is_pf = scope in PLAYER_FACING_SCOPES
        # Labels aus allen Commits aggregieren
        all_labels = []
        for c in bucket:
            all_labels.extend(c.get('pr_labels', []))

        groups.append({
            'theme': SCOPE_TO_THEME.get(scope, scope.replace('_', ' ').title()),
            'tag': dominant,
            'scope': scope,
            'commits': bucket,
            'summary': _build_summary(bucket),
            'is_player_facing': is_pf,
            'pr_labels': list(set(all_labels)),
        })

    # 4. Sortieren: Player-Facing zuerst, dann nach Commit-Anzahl (absteigend)
    groups.sort(key=lambda g: (not g['is_player_facing'], -len(g['commits'])))
    return groups
```

**Step 4: Run test**

Run: `source .venv/bin/activate && pytest tests/unit/test_pn_grouping.py -x -q`
Expected: 12 passed

**Step 5: Commit**

```bash
git add src/patch_notes/grouping.py tests/unit/test_pn_grouping.py
git commit -m "feat: patch_notes v6 — Deterministische Commit-Gruppierung (kein Cap, alle Commits)"
```

---

## Task 5: Template-System (gaming / saas / devops)

**Files:**
- Create: `src/patch_notes/templates/base.py`
- Create: `src/patch_notes/templates/gaming.py`
- Create: `src/patch_notes/templates/saas.py`
- Create: `src/patch_notes/templates/devops.py`
- Test: `tests/unit/test_pn_templates.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_pn_templates.py
"""Tests für config-driven Templates."""
import pytest
from patch_notes.templates import get_template
from patch_notes.templates.base import BaseTemplate
from patch_notes.templates.gaming import GamingTemplate
from patch_notes.templates.saas import SaaSTemplate
from patch_notes.templates.devops import DevOpsTemplate
from patch_notes.context import PipelineContext


@pytest.fixture
def gaming_ctx():
    return PipelineContext(
        project="mayday_sim",
        project_config={"patch_notes": {
            "type": "gaming", "language": "de",
            "target_audience": "Gamer", "project_description": "Leitstellensim",
        }},
        raw_commits=[], trigger="cron",
        groups=[
            {"tag": "FEATURE", "theme": "BOS-Funk", "scope": "gameplay",
             "commits": [{"message": "feat: BOS radio"}], "summary": "Funkverkehr",
             "is_player_facing": True, "pr_labels": []},
            {"tag": "INFRASTRUCTURE", "theme": "Event-System", "scope": "events",
             "commits": [{"message": "feat(events): store"}] * 30, "summary": "CQRS",
             "is_player_facing": False, "pr_labels": []},
        ],
        version="0.21.0", update_size="major",
    )


def test_get_template_gaming():
    t = get_template("gaming")
    assert isinstance(t, GamingTemplate)


def test_get_template_saas():
    t = get_template("saas")
    assert isinstance(t, SaaSTemplate)


def test_get_template_devops():
    t = get_template("devops")
    assert isinstance(t, DevOpsTemplate)


def test_get_template_unknown_falls_back():
    t = get_template("unknown_type")
    assert isinstance(t, BaseTemplate)


def test_gaming_categories():
    t = GamingTemplate()
    cats = t.categories()
    assert "Neuer Content" in cats
    assert "Gameplay-Verbesserungen" in cats


def test_saas_tone():
    t = SaaSTemplate()
    tone = t.tone_instruction()
    assert "sachlich" in tone.lower() or "professionell" in tone.lower()


def test_build_prompt_contains_groups(gaming_ctx):
    t = GamingTemplate()
    prompt = t.build_prompt(gaming_ctx)
    assert "BOS-Funk" in prompt
    assert "Event-System" in prompt
    assert "mayday_sim" in prompt
    assert "0.21.0" in prompt


def test_build_prompt_player_facing_first(gaming_ctx):
    t = GamingTemplate()
    prompt = t.build_prompt(gaming_ctx)
    pf_pos = prompt.index("Spieler-/Nutzer-relevante")
    infra_pos = prompt.index("Infrastruktur / Backend")
    assert pf_pos < infra_pos


def test_length_limits_scale():
    t = GamingTemplate()
    small = t.length_limits("small")
    major = t.length_limits("major")
    assert major["max"] > small["max"]
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_pn_templates.py -x -q`
Expected: FAIL

**Step 3: Implement template modules**

Die Template-Implementation ist umfangreich. Kern-Dateien:

- `src/patch_notes/templates/__init__.py` — `get_template()` Factory
- `src/patch_notes/templates/base.py` — `BaseTemplate` mit `build_prompt()`, `_groups_section()`, `_system_instruction()`, `_rules_section()`
- `src/patch_notes/templates/gaming.py` — `GamingTemplate(BaseTemplate)` mit Gaming-spezifischen Kategorien, Ton, Badges, Limits
- `src/patch_notes/templates/saas.py` — `SaaSTemplate(BaseTemplate)` für GuildScout/ZERODOX
- `src/patch_notes/templates/devops.py` — `DevOpsTemplate(BaseTemplate)` für ShadowOps/AI-Agent

Wichtig: `build_prompt()` in `BaseTemplate` baut den Prompt aus klar benannten Sektionen. Jede Sektion ist eine eigene Methode. Templates überschreiben nur `categories()`, `tone_instruction()`, `badges()`, `length_limits()`.

Die Klassifizierungs-Regeln (`_CLASSIFICATION_RULES_DE/EN` aus dem alten Code) werden in `_rules_section()` integriert — einmal geschrieben, alle Templates profitieren.

**Step 4: Run test**

Run: `source .venv/bin/activate && pytest tests/unit/test_pn_templates.py -x -q`
Expected: 10 passed

**Step 5: Commit**

```bash
git add src/patch_notes/templates/ tests/unit/test_pn_templates.py
git commit -m "feat: patch_notes v6 — Config-driven Templates (gaming/saas/devops)"
```

---

## Task 6: Stufe 1 — Collect (Commits + PR-Daten)

**Files:**
- Create: `src/patch_notes/stages/collect.py`
- Test: `tests/unit/test_pn_collect.py`

Portiert die Logik aus `ai_patch_notes_mixin.py`:
- `_enrich_commits_with_pr_data()` → `enrich_with_pr_data()`
- `_build_code_changes_context()` → `collect_git_stats()`
- Body-Noise-Entfernung (Co-Authored-By, Signed-off-by)

**Kern-Verhalten:**
- `gh pr view --json labels,body` für jeden Commit (mit Cache)
- PR-Labels werden in `commit['pr_labels']` gespeichert
- Body wird in `commit['pr_body']` gespeichert
- Git-Stats via `git diff --stat` gesammelt
- Output: `ctx.enriched_commits`, `ctx.git_stats`

**Step 5: Commit**

```bash
git add src/patch_notes/stages/collect.py tests/unit/test_pn_collect.py
git commit -m "feat: patch_notes v6 — Stufe 1 Collect (PR-Daten, Git-Stats)"
```

---

## Task 7: Stufe 2 — Classify (Gruppierung + Version + Credits)

**Files:**
- Create: `src/patch_notes/stages/classify.py`
- Test: `tests/unit/test_pn_classify.py`

Orchestriert die bereits erstellten Module:
- Ruft `grouping.group_commits()` auf
- Ruft `versioning.calculate_version()` auf
- Extrahiert Team-Credits (portiert aus `_enrich_changes_with_git_authors()`)
- Bestimmt Update-Größe
- Lädt vorherige Version-Content für Duplikat-Guard

**Step 5: Commit**

```bash
git add src/patch_notes/stages/classify.py tests/unit/test_pn_classify.py
git commit -m "feat: patch_notes v6 — Stufe 2 Classify (Gruppierung, Version, Credits)"
```

---

## Task 8: Stufe 3 — Generate (Template + AI-Call)

**Files:**
- Create: `src/patch_notes/stages/generate.py`
- Test: `tests/unit/test_pn_generate.py`

Portiert:
- Template-Auswahl via `get_template(project_config.patch_notes.type)`
- A/B-Varianten-Auswahl (aus `learning.py`)
- Prompt-Bau via `template.build_prompt(ctx)`
- Feature-Branch-Teasers (portiert aus `_collect_feature_branch_teasers()`)
- AI-Call via `ai_service.generate_structured_patch_notes()` (bestehende API!)
- Structured-Output-Parsing + Schema-Validierung
- Circuit Breaker für AI-Calls

**Wichtig:** Diese Stufe nutzt `self.bot.github_integration.ai_service` — der Bot wird via `ctx`/`bot` Parameter durchgereicht. Kein Mixin-Zugriff.

**Step 5: Commit**

```bash
git add src/patch_notes/stages/generate.py tests/unit/test_pn_generate.py
git commit -m "feat: patch_notes v6 — Stufe 3 Generate (Template + AI-Call + A/B-Testing)"
```

---

## Task 9: Stufe 4 — Validate (Safety-Checks)

**Files:**
- Create: `src/patch_notes/stages/validate.py`
- Modify: `src/patch_notes/sanitizer.py` (portiert aus `content_sanitizer.py`)
- Test: `tests/unit/test_pn_validate.py`

5 unabhängige Check-Funktionen:
1. `check_feature_count(ctx)` — AI-Features ≤ echte Feature-Gruppen × 2
2. `check_design_doc_leaks(ctx)` — Keywords aus DESIGN_DOC-Gruppen nicht in Features
3. `strip_ai_version(ctx)` — Generisches SemVer-Regex, setzt `ctx.title`
4. `sanitize_content(ctx)` — Portierter ContentSanitizer
5. `normalize_umlauts(ctx)` — ae→ä, oe→ö, ue→ü

Plus: `extract_display_content(ctx)` — Titel, TL;DR, Web-Content aus `ctx.ai_result` extrahieren.

**Step 5: Commit**

```bash
git add src/patch_notes/stages/validate.py src/patch_notes/sanitizer.py tests/unit/test_pn_validate.py
git commit -m "feat: patch_notes v6 — Stufe 4 Validate (5 Safety-Checks, Sanitizer)"
```

---

## Task 10: Stufe 5 — Distribute (Discord + DB + Learning)

**Files:**
- Create: `src/patch_notes/stages/distribute.py`
- Create: `src/patch_notes/learning.py`
- Test: `tests/unit/test_pn_distribute.py`

Portiert:
- Discord Embed-Bau (aus `_build_unified_embed()`, `_build_description()`, `_build_footer()`)
- Channel-Sending (Internal, Customer, External) mit Message-ID-Tracking
- Changelog-DB Upsert (bestehende `ChangelogDB` Klasse, nicht neu bauen!)
- Web-Export File-Backup (bestehender `PatchNotesWebExporter`, nicht neu bauen!)
- Feedback-Buttons (bestehende `PatchNotesFeedbackView`, nicht neu bauen!)
- Learning: `PatchNotesLearning` konsolidiert (A/B + Feedback + Examples)
- Rollback: `retract_patch_notes()` portiert
- Pipeline-Metriken loggen (`METRICS|patch_notes_pipeline|{json}`)

**Wichtig:** Nutzt bestehende Klassen (`ChangelogDB`, `PatchNotesWebExporter`, `PatchNotesFeedbackView`) direkt — kein Neuschreiben!

**Step 5: Commit**

```bash
git add src/patch_notes/stages/distribute.py src/patch_notes/learning.py tests/unit/test_pn_distribute.py
git commit -m "feat: patch_notes v6 — Stufe 5 Distribute (Discord, DB, Learning, Rollback)"
```

---

## Task 11: Integration — Feature-Flag + Caller-Anbindung

**Files:**
- Modify: `src/integrations/github_integration/core.py` — Pipeline-Init
- Modify: `src/integrations/github_integration/notifications_mixin.py:23-258` — v6-Dispatch
- Test: `tests/unit/test_pn_integration.py`

**Was passiert:**

1. In `core.py.__init__()`: `self.patch_notes_pipeline = None` hinzufügen
2. In `core.py` async startup: Pipeline initialisieren wenn `patch_notes.engine == 'v6'`
3. In `notifications_mixin._send_push_notification()` (Zeile 23): Prüfe Feature-Flag
   - `v6`: Erstelle `PipelineContext`, rufe `pipeline.run(ctx)` auf
   - `v5` (default): Bestehende Logik unverändert

```python
# Am Anfang von _send_push_notification():
engine = patch_config.get('engine', 'v5')
if engine == 'v6':
    from patch_notes.pipeline import PatchNotePipeline
    from patch_notes.context import PipelineContext
    ctx = PipelineContext(
        project=repo_name,
        project_config=project_config,
        raw_commits=commits,
        trigger='webhook' if not skip_batcher else 'manual',
    )
    pipeline = PatchNotePipeline(
        data_dir=Path(__file__).resolve().parent.parent.parent.parent / 'data',
        bot=self.bot,
    )
    await pipeline.run(ctx)
    return
# ... bestehende v5-Logik bleibt unverändert ...
```

**Step 5: Commit**

```bash
git add src/integrations/github_integration/core.py src/integrations/github_integration/notifications_mixin.py tests/unit/test_pn_integration.py
git commit -m "feat: patch_notes v6 — Feature-Flag Integration (v5/v6 Dispatch)"
```

---

## Task 12: Config-Migration + Erstes Projekt aktivieren

**Files:**
- Modify: `config/config.example.yaml` — v6-Config-Beispiele hinzufügen

**Steps:**
1. In `config/config.example.yaml` pro Projekt `patch_notes.engine: v6` und `patch_notes.type` dokumentieren
2. In der echten `config.yaml`: **shadowops-bot** auf `engine: v6` setzen (niedrigstes Risiko)
3. Bot neustarten: `scripts/restart.sh --pull`
4. Manuell testen: `/release-notes shadowops-bot` im Discord
5. Logs prüfen: `journalctl -u shadowops-bot --since "5 min ago" | grep "\[v6\]"`
6. Wenn erfolgreich: nächstes Projekt aktivieren

**Rollout-Reihenfolge:**
1. `shadowops-bot` (DevOps-Template, eigenes Projekt)
2. `ai-agent-framework` (DevOps-Template, wenig Traffic)
3. `guildscout` (SaaS-Template)
4. `zerodox` (SaaS-Template, Kunden-relevant — vorsichtig!)
5. `mayday_sim` (Gaming-Template, höchste Komplexität — zuletzt!)

**Step 5: Commit**

```bash
git add config/config.example.yaml
git commit -m "docs: patch_notes v6 — Config-Beispiele + Migration Guide"
```

---

## Task 13: Cleanup — Alte Mixins ausdünnen

**Timing:** Erst nach 2 Wochen fehlerfreiem v6-Betrieb auf allen Projekten.

**Files:**
- Modify: `src/integrations/github_integration/ai_patch_notes_mixin.py` — Patch-Notes-Methoden entfernen
- Modify: `src/integrations/github_integration/notifications_mixin.py` — v5-Logik entfernen
- Delete: `src/integrations/prompt_ab_testing.py` (konsolidiert in `patch_notes/learning.py`)
- Modify: `src/integrations/github_integration/core.py` — Unused Imports entfernen

**NICHT löschen:**
- `patch_notes_batcher.py` — bleibt eigenständig (wird von v6 weiter genutzt)
- `patch_notes_web_exporter.py` — wird von v6 Stufe 5 direkt genutzt
- `patch_notes_feedback.py` — Discord Views werden von v6 Stufe 5 genutzt
- `changelog_db.py` — wird von v6 Stufe 5 direkt genutzt
- `content_sanitizer.py` — bleibt als Fallback neben `patch_notes/sanitizer.py`

**Step 5: Commit**

```bash
git commit -m "refactor: patch_notes — v5 Mixin-Logik entfernt (v6 aktiv auf allen Projekten)"
```

---

## Zusammenfassung

| Task | Dateien | Beschreibung | Abhängigkeit |
|------|---------|-------------|--------------|
| 1 | context.py, __init__.py | Package-Skelett + PipelineContext | — |
| 2 | state.py, pipeline.py | State-Persistenz + Orchestrator | Task 1 |
| 3 | versioning.py | DB-basierte SemVer | Task 1 |
| 4 | grouping.py | Commit-Gruppierung | Task 1 |
| 5 | templates/*.py | Gaming/SaaS/DevOps Templates | Task 1, 4 |
| 6 | stages/collect.py | Stufe 1: PR-Daten, Stats | Task 1 |
| 7 | stages/classify.py | Stufe 2: Gruppierung + Version | Task 3, 4 |
| 8 | stages/generate.py | Stufe 3: Template + AI | Task 5 |
| 9 | stages/validate.py | Stufe 4: Safety-Checks | Task 1 |
| 10 | stages/distribute.py, learning.py | Stufe 5: Discord + DB + Learning | Task 1, 9 |
| 11 | core.py, notifications_mixin.py | Feature-Flag Integration | Task 2, 6-10 |
| 12 | config.yaml | Rollout Projekt für Projekt | Task 11 |
| 13 | Cleanup alte Mixins | Nach 2 Wochen | Task 12 |

**Geschätzte Dateien:** ~15 neue, ~3 modifizierte
**Geschätzte Zeilen:** ~2500-3000 (vs. 5839 heute)
**Geschätzte Tests:** ~60-80 neue Tests
