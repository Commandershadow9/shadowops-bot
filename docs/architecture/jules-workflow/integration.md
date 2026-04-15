---
title: Jules Workflow — Integration
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/007-jules-secops-workflow.md
  - ../../design/jules-workflow.md
  - ./README.md
  - ./detection.md
  - ./review-pipeline.md
  - ./loop-protection.md
  - ./state-and-learning.md
---

# Jules Workflow — Integration

Dieser Abschnitt dokumentiert die Verdrahtung des Jules-Workflows in die bestehende ShadowOps-Bot-Infrastruktur — Mixin-Integration, Core-Handler, ScanAgent-Delegation und Health-Monitoring. Er beschreibt, wie die einzelnen Bausteine (State, Gates, Prompts, Comment-Management) zur Laufzeit zusammenlaufen, wo sie sich in den bestehenden Code einklinken und wie sie feature-geflaggt werden koennen.

## JulesWorkflowMixin (Phase 8 Core)

### Mixin-Skeleton + `should_review()` Gate-Pipeline

**Files:**
- Create: `src/integrations/github_integration/jules_workflow_mixin.py`
- Create: `tests/unit/test_jules_workflow_mixin.py`

**Step 1: Mixin-Skeleton**

```python
# src/integrations/github_integration/jules_workflow_mixin.py
"""
Jules SecOps Workflow Mixin.

Handler-Eintritt für GitHub pull_request und issue_comment Events.
Koordiniert die Gate-Pipeline, den AI-Call und das Comment-Management.

Siehe docs/design/jules-workflow.md §4-§6.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from .jules_gates import (
    ALLOWED_TRIGGERS,
    ReviewDecision,
    check_circuit_breaker,
    gate_cooldown,
    gate_iteration_cap,
    gate_time_cap,
    gate_trigger_whitelist,
)
from .jules_state import JulesReviewRow

logger = logging.getLogger(__name__)


class JulesWorkflowMixin:
    """
    Wird in GitHubIntegration via Mixin-Pattern eingehängt.

    Erwartete Attribute auf self (von core.py bereitgestellt):
    - self.jules_state: JulesState
    - self.jules_learning: JulesLearning
    - self.ai_service: AIEngine
    - self.redis: Redis-Client (async)
    - self.config: Config-Instanz mit .jules_workflow Block
    - self.logger: Logger
    - self.bot: Bot-Referenz (für Discord-Logger)
    """

    async def should_review(
        self, repo: str, pr_number: int, head_sha: str, event_type: str
    ) -> ReviewDecision:
        """
        Gate-Pipeline — billig zuerst, teuer später.
        Gibt ReviewDecision zurück; proceed=True nur bei allen Gates passed.
        """
        cfg = self.config.jules_workflow

        # Gate 0: Feature-Flag
        if not cfg.enabled:
            return ReviewDecision.skip("feature_disabled")

        # Gate 1: Trigger-Whitelist
        blocked = gate_trigger_whitelist(event_type)
        if blocked:
            logger.debug(f"[jules] {repo}#{pr_number} gate=trigger skip={blocked}")
            return ReviewDecision.skip(blocked)

        # Gate 5: Circuit-Breaker (vor DB-Claim — billig)
        is_open, count = await check_circuit_breaker(
            self.redis,
            repo,
            threshold=cfg.circuit_breaker.max_reviews_per_hour,
            ttl_seconds=cfg.circuit_breaker.pause_duration_seconds,
        )
        if is_open:
            logger.warning(
                f"[jules] 🚨 Circuit Breaker OPEN for {repo} (count={count}, skipping)"
            )
            await self._jules_notify_discord_alarm(
                f"Jules Circuit Breaker OPEN für {repo}: {count} Reviews in 1h"
            )
            return ReviewDecision.skip("circuit_breaker_open")

        # Gate 2: Atomic Claim (DB)
        row = await self.jules_state.try_claim_review(
            repo, pr_number, head_sha, self.jules_state.process_id
        )
        if row is None:
            logger.info(f"[jules] {repo}#{pr_number} skip=already_reviewed_or_locked sha={head_sha[:7]}")
            return ReviewDecision.skip("already_reviewed_or_locked")

        # Nach Claim: row ist jetzt im status='reviewing' Lock.
        # Wenn wir danach skip machen, MÜSSEN wir den Lock freigeben.

        # Gate 4: Iteration-Cap
        if (reason := gate_iteration_cap(row, cfg.max_iterations)):
            logger.warning(f"[jules] {repo}#{pr_number} ESCALATE {reason}")
            await self._jules_escalate_to_human(row, reason)
            return ReviewDecision.skip(reason)

        # Gate 6: Time-Cap
        if (reason := gate_time_cap(row, cfg.max_hours_per_pr)):
            logger.warning(f"[jules] {repo}#{pr_number} ESCALATE {reason}")
            await self._jules_escalate_to_human(row, reason)
            return ReviewDecision.skip(reason)

        # Gate 3: Cooldown
        if (reason := gate_cooldown(row, cfg.cooldown_seconds)):
            logger.info(f"[jules] {repo}#{pr_number} skip=cooldown")
            await self.jules_state.release_lock(row.id, row.status_before_reviewing or "revision_requested")
            return ReviewDecision.skip(reason)

        return ReviewDecision.advance(row)

    # Diese Methoden werden in späteren Tasks implementiert —
    # Skeleton jetzt, damit der Mixin importierbar ist
    async def _jules_escalate_to_human(self, row: JulesReviewRow, reason: str) -> None:
        logger.warning(f"[jules] STUB _jules_escalate_to_human row={row.id} reason={reason}")
        await self.jules_state.mark_terminal(row.id, "escalated")

    async def _jules_notify_discord_alarm(self, msg: str) -> None:
        logger.warning(f"[jules] STUB _jules_notify_discord_alarm: {msg}")
```

Wichtig: Der Code nutzt `row.status_before_reviewing`, was es noch nicht gibt. Das ist ein Platzhalter für den Cooldown-Fall — wir brauchen eigentlich den Status **vor** dem Claim, den die atomic UPDATE überschrieben hat. Das ist ein Bug in meiner Formulierung — korrekter Weg: wir kennen den Status vor dem Claim nicht, weil RETURNING \* nur den neuen Zustand zurückgibt. Wir setzen stattdessen auf `revision_requested` als sicheren Default, weil das der häufigste "non-pending-non-terminal" Status ist.

Fix: Ersetze die Zeile
```python
await self.jules_state.release_lock(row.id, row.status_before_reviewing or "revision_requested")
```
mit
```python
# Cooldown greift — Lock freigeben, Status zurück auf revision_requested
# (der häufigste Grund für Gate-Eintritt; pending wird beim ersten Review erreicht)
prev = "pending" if row.iteration_count == 0 else "revision_requested"
await self.jules_state.release_lock(row.id, prev)
```

**Step 2: Basis-Tests**

```python
# tests/unit/test_jules_workflow_mixin.py
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import fakeredis.aioredis

from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
from src.integrations.github_integration.jules_state import JulesReviewRow


def _row(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=1, repo="X", pr_number=1, issue_number=None, finding_id=None,
        status="reviewing", last_reviewed_sha=None, iteration_count=0,
        last_review_at=None, lock_acquired_at=now, lock_owner="w",
        review_comment_id=None, last_review_json=None, last_blockers=None,
        tokens_consumed=0, created_at=now, updated_at=now,
        closed_at=None, human_override=False,
    )
    defaults.update(overrides)
    return JulesReviewRow(**defaults)


class _Cfg:
    """Minimal config mock — was self.config.jules_workflow liefern würde."""
    class circuit_breaker:
        max_reviews_per_hour = 20
        pause_duration_seconds = 3600
    enabled = True
    max_iterations = 5
    cooldown_seconds = 300
    max_hours_per_pr = 2


class _Harness(JulesWorkflowMixin):
    """Bare-bones Instanz für Tests."""
    def __init__(self, jules_state, redis):
        self.jules_state = jules_state
        self.redis = redis
        self.config = MagicMock()
        self.config.jules_workflow = _Cfg()
        self.config.jules_workflow.circuit_breaker = _Cfg.circuit_breaker
        self.bot = MagicMock()


@pytest_asyncio.fixture
async def harness():
    redis = fakeredis.aioredis.FakeRedis()
    state = MagicMock()
    state.process_id = "test-worker"
    state.try_claim_review = AsyncMock(return_value=None)
    state.mark_terminal = AsyncMock()
    state.release_lock = AsyncMock()
    return _Harness(state, redis)


@pytest.mark.asyncio
async def test_should_review_skips_when_disabled(harness):
    harness.config.jules_workflow.enabled = False
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "feature_disabled"


@pytest.mark.asyncio
async def test_should_review_skips_issue_comment(harness):
    """REGRESSION: PR #123 Hauptursache."""
    d = await harness.should_review("X", 1, "sha", "issue_comment:created")
    assert d.proceed is False
    assert d.reason == "blocked_trigger"
    harness.jules_state.try_claim_review.assert_not_called()


@pytest.mark.asyncio
async def test_should_review_skips_when_claim_fails(harness):
    harness.jules_state.try_claim_review.return_value = None
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "already_reviewed_or_locked"


@pytest.mark.asyncio
async def test_should_review_escalates_on_max_iterations(harness):
    harness.jules_state.try_claim_review.return_value = _row(iteration_count=5)
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "max_iterations"
    harness.jules_state.mark_terminal.assert_called_once()


@pytest.mark.asyncio
async def test_should_review_escalates_on_timeout(harness):
    harness.jules_state.try_claim_review.return_value = _row(
        created_at=datetime.now(timezone.utc) - timedelta(hours=3)
    )
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "timeout_per_pr"


@pytest.mark.asyncio
async def test_should_review_skips_on_cooldown(harness):
    harness.jules_state.try_claim_review.return_value = _row(
        last_review_at=datetime.now(timezone.utc) - timedelta(seconds=60)
    )
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "cooldown"
    harness.jules_state.release_lock.assert_called_once()


@pytest.mark.asyncio
async def test_should_review_advances_on_fresh_row(harness):
    harness.jules_state.try_claim_review.return_value = _row(iteration_count=0)
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is True
    assert d.row is not None
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 7 passed. Falls 1 Test wegen `row.status_before_reviewing` fehlschlägt, stelle sicher, dass du den im Step 1 angegebenen Fix angewendet hast.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py tests/unit/test_jules_workflow_mixin.py
git commit -m "feat: JulesWorkflowMixin.should_review — Gate-Pipeline mit Escalation"
```

---

### `handle_pr_event` — PR-Event-Eintritt

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`
- Modify: `tests/unit/test_jules_workflow_mixin.py`

**Step 1: Tests für `handle_jules_pr_event`**

```python
@pytest.mark.asyncio
async def test_handle_jules_pr_event_ignores_non_jules_pr(harness):
    """PR ohne jules-Label und ohne Fixes-Reference wird ignoriert."""
    harness._jules_is_jules_pr = AsyncMock(return_value=False)

    payload = {
        "action": "opened",
        "pull_request": {
            "number": 1,
            "head": {"sha": "abc"},
            "user": {"login": "SomeHuman"},
            "body": "",
            "labels": [],
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness.jules_state.try_claim_review.assert_not_called()


@pytest.mark.asyncio
async def test_handle_jules_pr_event_calls_should_review(harness):
    """PR mit jules-Label triggert should_review."""
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    harness.should_review = AsyncMock(return_value=MagicMock(proceed=False, reason="cooldown"))
    harness._jules_run_review_pipeline = AsyncMock()

    payload = {
        "action": "opened",
        "pull_request": {
            "number": 1,
            "head": {"sha": "abc123"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "Fixes #42",
            "labels": [{"name": "jules"}],
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness.should_review.assert_called_once_with("X", 1, "abc123", "pull_request:opened")
    harness._jules_run_review_pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_handle_jules_pr_event_runs_pipeline_on_proceed(harness):
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    fake_row = _row()
    harness.should_review = AsyncMock(return_value=MagicMock(proceed=True, row=fake_row))
    harness._jules_run_review_pipeline = AsyncMock()

    payload = {
        "action": "synchronize",
        "pull_request": {
            "number": 1,
            "head": {"sha": "def456"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "Fixes #42",
            "labels": [{"name": "jules"}],
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness._jules_run_review_pipeline.assert_called_once()
```

**Step 2: Implementation im Mixin**

Hänge an `JulesWorkflowMixin`:

```python
    async def handle_jules_pr_event(self, payload: Dict[str, Any]) -> None:
        """
        Eintrittspunkt für pull_request Events.
        Wird von WebhookMixin dispatcher aufgerufen (vor dem normalen EventHandlersMixin).
        Returns ohne Aktion wenn's kein Jules-PR ist — der normale Handler läuft danach.
        """
        try:
            action = payload.get("action", "")
            pr = payload.get("pull_request") or {}
            repo = (payload.get("repository") or {}).get("name", "")
            pr_number = pr.get("number")
            head_sha = (pr.get("head") or {}).get("sha", "")

            if not (repo and pr_number and head_sha):
                return

            # Event-Type für Gate 1
            event_type = f"pull_request:{action}"
            if event_type not in ALLOWED_TRIGGERS:
                logger.debug(f"[jules] {repo}#{pr_number} action={action} not in ALLOWED_TRIGGERS")
                return

            # Ist das überhaupt ein Jules-PR? (siehe detection.md)
            is_jules = await self._jules_is_jules_pr(pr, repo)
            if not is_jules:
                return

            logger.info(f"[jules] Detected Jules PR {repo}#{pr_number} sha={head_sha[:7]} action={action}")

            decision = await self.should_review(repo, pr_number, head_sha, event_type)
            if not decision.proceed:
                logger.info(f"[jules] {repo}#{pr_number} skip reason={decision.reason}")
                return

            await self._jules_run_review_pipeline(
                repo=repo, pr_number=pr_number, head_sha=head_sha,
                pr_payload=pr, row=decision.row,
            )
        except Exception:
            logger.exception("[jules] handle_jules_pr_event crashed")
            # NIE eine Exception zurück zum Webhook-Handler — Webhook-200-Immer-Regel

    # _jules_is_jules_pr siehe detection.md — Label/Author/Body-Marker-Kriterien.

    async def _jules_run_review_pipeline(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        pr_payload: Dict[str, Any],
        row: JulesReviewRow,
    ) -> None:
        """STUB — wird in Task 8.3 implementiert."""
        logger.info(f"[jules] STUB _jules_run_review_pipeline {repo}#{pr_number}")
        # Lock freigeben damit Tests nicht hängen
        await self.jules_state.release_lock(row.id, "pending")
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 10 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py tests/unit/test_jules_workflow_mixin.py
git commit -m "feat: handle_jules_pr_event — PR-Event-Eintritt mit Jules-Detection"
```

---

### `_jules_run_review_pipeline` — der AI-Review-Lauf

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Implementation — ersetze den STUB aus Task 8.2**

```python
    async def _jules_run_review_pipeline(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        pr_payload: Dict[str, Any],
        row: JulesReviewRow,
    ) -> None:
        """
        Führt den eigentlichen Review durch:
        1. Diff holen via gh
        2. Finding-Kontext + Learning-Kontext laden
        3. ai_engine.review_pr() aufrufen
        4. Verdict anwenden → Comment posten/editieren
        5. DB-State aktualisieren, Lock freigeben
        """
        cfg = self.config.jules_workflow
        owner = (pr_payload.get("base") or {}).get("repo", {}).get("owner", {}).get("login") or \
                (pr_payload.get("head") or {}).get("repo", {}).get("owner", {}).get("login") or \
                "Commandershadow9"

        iteration = row.iteration_count + 1

        try:
            # Dry-Run-Mode: kein AI-Call, kein Write
            if getattr(cfg, "dry_run", False):
                logger.info(f"[jules] DRY-RUN {repo}#{pr_number} iter={iteration}")
                await self.jules_state.release_lock(row.id, "revision_requested")
                return

            # 1. Diff holen
            diff = await self._jules_fetch_pr_diff(owner, repo, pr_number)
            if diff is None:
                await self._jules_escalate_to_human(row, "diff_fetch_failed")
                return

            # 2. Finding-Kontext
            finding_ctx = await self._jules_load_finding_context(row.finding_id)
            # Falls kein Finding: degraded mode, nutze PR-Body
            if finding_ctx is None:
                finding_ctx = {
                    "title": (pr_payload.get("title") or "n/a"),
                    "severity": "medium",
                    "description": (pr_payload.get("body") or "")[:2000],
                    "category": "code_fix",
                    "cve": None,
                }

            # 3. Learning-Kontext (tolerant — Fehler darf Review nicht blocken)
            knowledge = []
            examples = []
            try:
                knowledge = await self.jules_learning.fetch_project_knowledge(
                    repo, limit=cfg.project_knowledge_limit
                )
                examples = await self.jules_learning.fetch_few_shot_examples(
                    repo, limit=cfg.few_shot_examples
                )
            except Exception as e:
                logger.warning(f"[jules] Learning-Context fetch failed (tolerant): {e}")

            # 4. AI-Call
            review = await self.ai_service.review_pr(
                diff=diff,
                finding_context=finding_ctx,
                project=repo,
                iteration=iteration,
                project_knowledge=knowledge,
                few_shot_examples=examples,
                max_diff_chars=cfg.max_diff_chars,
            )

            if review is None:
                await self._jules_escalate_to_human(row, "ai_review_failed")
                return

            # 5. Comment posten/editieren + DB-Update
            await self._jules_post_or_edit_review_comment(
                owner=owner, repo=repo, pr_number=pr_number,
                review=review, row=row, iteration=iteration,
            )

            # SHA + iteration_count updaten
            await self.jules_state.mark_reviewed_sha(row.id, head_sha)
            await self.jules_state.store_review_result(
                row.id, review, review.get("blockers", []), tokens=0
            )

            # Verdict anwenden
            if review["verdict"] == "approved":
                await self._jules_apply_approval(owner, repo, pr_number, row)
                await self.jules_state.release_lock(row.id, "approved")
            else:
                await self.jules_state.release_lock(row.id, "revision_requested")

        except Exception:
            logger.exception(f"[jules] review pipeline crashed for {repo}#{pr_number}")
            try:
                await self.jules_state.release_lock(row.id, "revision_requested")
            except Exception:
                pass
            await self._jules_notify_discord_alarm(
                f"Jules-Review crashed für {repo}#{pr_number}"
            )

    async def _jules_fetch_pr_diff(self, owner: str, repo: str, pr: int) -> Optional[str]:
        """Holt den Diff via gh CLI."""
        repo_slug = f"{owner}/{repo}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "diff", str(pr), "--repo", repo_slug,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.error(f"[jules] gh pr diff failed: {stderr.decode()[:200]}")
                return None
            return stdout.decode()
        except asyncio.TimeoutError:
            logger.error(f"[jules] gh pr diff timeout for {repo_slug}#{pr}")
            return None

    async def _jules_load_finding_context(self, finding_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """Lädt Finding aus security_analyst.findings."""
        if finding_id is None:
            return None
        try:
            async with self.jules_state._pool.acquire() as conn:
                rec = await conn.fetchrow(
                    "SELECT title, severity, description, category, cve FROM findings WHERE id = $1",
                    finding_id,
                )
                return dict(rec) if rec else None
        except Exception as e:
            logger.warning(f"[jules] finding lookup failed: {e}")
            return None

    async def _jules_post_or_edit_review_comment(
        self, *, owner: str, repo: str, pr_number: int,
        review: Dict[str, Any], row: JulesReviewRow, iteration: int,
    ) -> None:
        """STUB — Task 9 implementiert Body-Builder + Post/Edit."""
        logger.info(f"[jules] STUB post/edit review comment {repo}#{pr_number} iter={iteration}")

    async def _jules_apply_approval(
        self, owner: str, repo: str, pr_number: int, row: JulesReviewRow
    ) -> None:
        """STUB — Task 10 implementiert Label-Setzen + Discord-Ping."""
        logger.info(f"[jules] STUB apply approval {repo}#{pr_number}")
```

**Step 2: Smoke-Test**

```bash
python -c "from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin; print('OK')"
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: OK + 10 passed (keine Tests für den neuen Code hinzugefügt, Stubs werden in Phase 9/10 richtig getestet).

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: _jules_run_review_pipeline — AI-Call, Diff-Fetch, Finding-Context"
```

---

*Phase 8 abgeschlossen.*

---

## Handler-Wiring in core.py (Phase 11)

### `JulesWorkflowMixin` in `GitHubIntegration` einhängen

**Files:**
- Modify: `src/integrations/github_integration/core.py`
- Modify: `tests/unit/test_github_integration.py` (nur Import-Check)

**Step 1: Core-Änderung**

Öffne `src/integrations/github_integration/core.py`.

1. Import-Block ergänzen:

```python
from .jules_workflow_mixin import JulesWorkflowMixin
from .jules_state import JulesState
from .jules_learning import JulesLearning
```

2. Klassen-MRO anpassen — `JulesWorkflowMixin` VOR `EventHandlersMixin` einsetzen, damit der Dispatcher-Lookup via `self.event_handlers['pull_request']` standardmäßig weiterhin auf `handle_pr_event` zeigt, aber unser Jules-Handler vorher läuft:

```python
class GitHubIntegration(
    JulesWorkflowMixin,          # NEU (zuerst, damit MRO priorisiert)
    WebhookMixin,
    PollingMixin,
    EventHandlersMixin,
    CIMixin,
    StateMixin,
    GitOpsMixin,
    NotificationsMixin,
    AIPatchNotesMixin,
):
```

3. `__init__` erweitern — nach der bisherigen Initialisierung von `self.state_manager = get_state_manager()`:

```python
        # Jules SecOps Workflow — lazy init (connect() später in setup_hook)
        self._jules_enabled = bool(
            getattr(getattr(self.config, "jules_workflow", None), "enabled", False)
        )
        if self._jules_enabled:
            self.jules_state = JulesState(self.config.security_analyst_dsn)
            self.jules_learning = JulesLearning(self.config.agent_learning_dsn)
        else:
            self.jules_state = None
            self.jules_learning = None
```

4. `event_handlers`-Dict anpassen — die existing Event-Handler-Registrierung bleibt gleich, ABER wir wrappen `handle_pr_event` so, dass es ZUERST `handle_jules_pr_event` aufruft:

Suche die Zeile
```python
'pull_request': self.handle_pr_event,
```
und ersetze sie durch
```python
'pull_request': self._pr_dispatch,
```

Und füge die Dispatch-Methode zur Klasse hinzu (in `core.py` am Ende der Klasse):

```python
    async def _pr_dispatch(self, payload):
        """
        Dispatches pull_request events. Jules-Workflow läuft ZUERST und
        entscheidet ob er sich "angesprochen" fühlt; danach läuft immer
        der normale Handler für Discord-Notifications.
        """
        if self._jules_enabled:
            try:
                await self.handle_jules_pr_event(payload)
            except Exception:
                self.logger.exception("[jules] pr dispatch crashed (continuing)")
        await self.handle_pr_event(payload)
```

5. `setup_hook` / `start()` erweitern — suche den Startup-Flow, wo andere Services connected werden, und füge ein:

```python
        if self._jules_enabled:
            await self.jules_state.connect()
            await self.jules_learning.connect()
            # Stale-Lock-Recovery beim Start
            cleaned = await self.jules_state.recover_stale_locks(timeout_minutes=10)
            if cleaned:
                self.logger.warning(f"[jules] recovered {cleaned} stale locks on startup")
```

**Step 2: Redis-Client verfügbar machen**

`JulesWorkflowMixin` erwartet `self.redis`. Wenn der Bot bereits einen globalen Redis-Client hat, alias das hier:

```python
        # In GitHubIntegration.__init__, nach dem Jules-init-Block:
        self.redis = getattr(self.bot, "redis", None) if hasattr(self, "bot") else None
```

Falls `self.bot` erst später gesetzt wird, passe das in `setup_hook` an (setze `self.redis = self.bot.redis` dort).

Wenn der Bot noch keinen Redis-Client hat, sieh im Rest des Codes nach (`grep -rn "redis.asyncio\|aioredis" src/`) — falls keiner existiert, nutze Fallback:

```python
            import redis.asyncio as aioredis
            self.redis = aioredis.from_url(
                self.config.redis_url or "redis://127.0.0.1:6379/0",
                decode_responses=True,
            )
```

**Step 3: Smoke-Test**

```bash
python -c "from src.integrations.github_integration.core import GitHubIntegration; print('OK')"
```

Erwartet: `OK`. Falls `ImportError` wegen fehlendem Redis-Client: Redis optional machen und Jules-Features deaktivieren wenn Redis fehlt.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/core.py
git commit -m "feat: GitHubIntegration — Jules Workflow-Mixin + Dispatcher-Wiring"
```

---

### Regression-Test für PR #123 Szenario

**Files:**
- Create: `tests/unit/test_jules_pr123_regression.py`

**Step 1: Test schreiben**

```python
# tests/unit/test_jules_pr123_regression.py
"""
REGRESSION TEST für PR #123 Vorfall.

Szenario: 31 issue_comment:created Events kommen rein, jedes ist ein
Bot-eigener Review-Kommentar. Erwartung: KEIN einziger davon triggert
einen Auto-Review. tokens_consumed bleibt 0. should_review wird nie
für die Comments aufgerufen.

Siehe Design-Doc Anhang A.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import fakeredis.aioredis

from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
from src.integrations.github_integration.jules_state import JulesReviewRow


class _Cfg:
    class circuit_breaker:
        max_reviews_per_hour = 20
        pause_duration_seconds = 3600
    enabled = True
    max_iterations = 5
    cooldown_seconds = 300
    max_hours_per_pr = 2


class _Harness(JulesWorkflowMixin):
    def __init__(self):
        self.jules_state = MagicMock()
        self.jules_state.process_id = "test-worker"
        self.jules_state.try_claim_review = AsyncMock(return_value=None)
        self.redis = fakeredis.aioredis.FakeRedis()
        self.config = MagicMock()
        self.config.jules_workflow = _Cfg()
        self.config.jules_workflow.circuit_breaker = _Cfg.circuit_breaker
        self.bot = MagicMock()


@pytest.mark.asyncio
async def test_pr_123_regression_31_issue_comments_zero_reviews():
    """
    Reproduziert den PR #123 Loop: 31 identische issue_comment Events.
    Jedes MUSS vom Trigger-Whitelist-Gate gestoppt werden.
    """
    harness = _Harness()

    # Simuliere 31 Comment-Events
    comment_events = [
        {
            "action": "created",
            "comment": {
                "body": "### 🛡️ Claude Security Review\n\nGENEHMIGT 92%",
                "user": {"login": "Commandershadow9"},
            },
            "issue": {"number": 123, "pull_request": {"html_url": "x"}},
            "repository": {"name": "ZERODOX", "owner": {"login": "x"}},
        }
        for _ in range(31)
    ]

    # In der neuen Architektur gehen issue_comment Events NICHT durch
    # handle_jules_pr_event. Sie gehen durch handle_comment_event (existiert
    # nur für manuellen /review). Daher testen wir direkt should_review:
    for _ in range(31):
        decision = await harness.should_review(
            "ZERODOX", 123, "any_sha", "issue_comment:created"
        )
        assert decision.proceed is False
        assert decision.reason == "blocked_trigger"

    # try_claim_review darf NIE aufgerufen worden sein
    harness.jules_state.try_claim_review.assert_not_called()


@pytest.mark.asyncio
async def test_pr_123_regression_bot_own_comment_not_reviewed():
    """
    Zweiter Regression-Aspekt: Bot-Comments haben Marker-Prefix.
    Der manuelle /review-Handler muss sie ignorieren.
    """
    from src.integrations.github_integration.jules_comment import is_bot_comment

    claude_comment = "### 🛡️ Claude Security Review — Iteration 1 of 5\n\n**Verdict:** 🟢 APPROVED"
    human_comment = "Looks good to me!"

    assert is_bot_comment(claude_comment) is True
    assert is_bot_comment(human_comment) is False


@pytest.mark.asyncio
async def test_pr_123_regression_circuit_breaker_stops_runaway():
    """
    Selbst wenn alle anderen Gates versagen würden: Circuit Breaker
    greift nach 20 Reviews/h und stoppt Jules-Loops global.
    """
    harness = _Harness()
    harness.jules_state.try_claim_review = AsyncMock(return_value=JulesReviewRow(
        id=1, repo="X", pr_number=1, issue_number=None, finding_id=None,
        status="reviewing", last_reviewed_sha=None, iteration_count=0,
        last_review_at=None, lock_acquired_at=datetime.now(timezone.utc),
        lock_owner="w", review_comment_id=None, last_review_json=None,
        last_blockers=None, tokens_consumed=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        closed_at=None, human_override=False,
    ))
    harness._jules_notify_discord_alarm = AsyncMock()

    # Trigger 25 Reviews in kurzer Zeit
    for i in range(25):
        await harness.should_review("X", i, f"sha_{i}", "pull_request:synchronize")

    # Nach Threshold kommt circuit_breaker_open
    d = await harness.should_review("X", 99, "sha_99", "pull_request:synchronize")
    assert d.reason == "circuit_breaker_open"
    harness._jules_notify_discord_alarm.assert_called()
```

**Step 2: Tests — PASS**

```bash
pytest tests/unit/test_jules_pr123_regression.py -x -v
```

Erwartet: 3 passed.

**Step 3: Commit**

```bash
git add tests/unit/test_jules_pr123_regression.py
git commit -m "test: Regression für PR #123 Review-Loop (31 comments → 0 reviews)"
```

---

*Phase 11 abgeschlossen.*

---

## ScanAgent-Integration (Phase 12)

### `FIX_MODE_DECISION` + `classify_fix_mode()` hinzufügen

**Files:**
- Modify: `src/integrations/security_engine/scan_agent.py`
- Create: `tests/unit/test_scan_agent_jules_classification.py`

**Step 1: Test schreiben**

```python
# tests/unit/test_scan_agent_jules_classification.py
import pytest
from unittest.mock import MagicMock

from src.integrations.security_engine.scan_agent import (
    FIX_MODE_DECISION,
    classify_fix_mode,
)


def _finding(category, project="ZERODOX"):
    f = MagicMock()
    f.category = category
    f.project = project
    return f


def test_npm_audit_routes_to_jules():
    assert classify_fix_mode(_finding("npm_audit")) == "jules"


def test_pip_audit_routes_to_jules():
    assert classify_fix_mode(_finding("pip_audit")) == "jules"


def test_dockerfile_routes_to_jules():
    assert classify_fix_mode(_finding("dockerfile")) == "jules"


def test_code_vulnerability_routes_to_jules():
    assert classify_fix_mode(_finding("code_vulnerability")) == "jules"


def test_ufw_routes_to_self_fix():
    assert classify_fix_mode(_finding("ufw")) == "self_fix"


def test_fail2ban_routes_to_self_fix():
    assert classify_fix_mode(_finding("fail2ban")) == "self_fix"


def test_unknown_category_is_human_only():
    assert classify_fix_mode(_finding("mysterious_thing")) == "human_only"


def test_ssh_config_is_human_only_even_if_code():
    assert classify_fix_mode(_finding("ssh_config")) == "human_only"


def test_skipped_project_falls_back_to_human():
    from src.integrations.security_engine.scan_agent import SKIP_ISSUE_PROJECTS
    if "test_skipped" not in SKIP_ISSUE_PROJECTS:
        SKIP_ISSUE_PROJECTS.add("test_skipped")
    assert classify_fix_mode(_finding("npm_audit", project="test_skipped")) == "human_only"
```

**Step 2: Implementation in `scan_agent.py`**

Suche in `src/integrations/security_engine/scan_agent.py` die Stelle wo `SKIP_ISSUE_PROJECTS` oder `PROJECT_REPO_MAP` definiert sind, und füge direkt danach:

```python
# === Jules SecOps Workflow — Fix-Mode-Klassifizierung ===
# Siehe docs/design/jules-workflow.md §9.1

FIX_MODE_DECISION: Dict[str, str] = {
    # Code-Findings → Jules (PR via GitHub-Issue)
    'npm_audit':          'jules',
    'pip_audit':          'jules',
    'dockerfile':         'jules',
    'code_vulnerability': 'jules',

    # Infrastruktur → Self-Fix durch den ScanAgent
    'ufw':                'self_fix',
    'fail2ban':           'self_fix',
    'crowdsec':           'self_fix',
    'aide':               'self_fix',
    'docker_config':      'self_fix',

    # Explizit NICHT automatisiert
    'ssh_config':         'human_only',
    'database_schema':    'human_only',
}


def classify_fix_mode(finding) -> str:
    """
    Gibt 'jules', 'self_fix' oder 'human_only' zurück.

    Priorität:
    1. Projekt in SKIP_ISSUE_PROJECTS → 'human_only' (keine Issue-Erstellung)
    2. Kein Eintrag in PROJECT_REPO_MAP → 'human_only' (kein Repo zum Öffnen)
    3. FIX_MODE_DECISION Lookup
    4. Fallback: 'human_only'
    """
    project = getattr(finding, "project", None)
    category = getattr(finding, "category", None)

    if project in SKIP_ISSUE_PROJECTS:
        return "human_only"

    mode = FIX_MODE_DECISION.get(category, "human_only")
    if mode == "jules":
        if project not in PROJECT_REPO_MAP:
            return "human_only"
    return mode
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_scan_agent_jules_classification.py -x -v
```

Erwartet: 9 passed.

**Step 4: Commit**

```bash
git add src/integrations/security_engine/scan_agent.py tests/unit/test_scan_agent_jules_classification.py
git commit -m "feat: ScanAgent classify_fix_mode — Jules/Self-Fix/Human-only Routing"
```

---

### `build_jules_issue_body()` + Label-Integration

**Files:**
- Modify: `src/integrations/security_engine/scan_agent.py`

**Step 1: Body-Builder als Funktion hinzufügen**

```python
def build_jules_issue_body(finding) -> str:
    """
    Erzeugt den GitHub-Issue-Body für Jules-Delegation.

    Enthält Acceptance-Criteria, Scope-Warnung und explizite Anweisung
    KEIN "Acknowledged"-Comment zu posten (PR #123 Second Line of Defense).
    """
    cve = getattr(finding, "cve", None) or "N/A"
    severity = getattr(finding, "severity", "medium")
    category = getattr(finding, "category", "n/a")
    title = getattr(finding, "title", "Security Finding")
    description = getattr(finding, "description", "(keine Beschreibung)")
    files = getattr(finding, "affected_files", None) or []
    finding_id = getattr(finding, "id", 0)

    files_block = "\n".join(f"- `{f}`" for f in files) if files else "(im Scan-Report)"

    return f"""## 🛡️ Security Finding

**Severity:** {severity.upper()}
**Category:** `{category}`
**CVE:** {cve}

### Problem

{description}

### Betroffene Dateien

{files_block}

### Acceptance Criteria

- [ ] Das spezifische Problem oben ist behoben
- [ ] Keine unrelated Changes (Scope strikt halten!)
- [ ] `npm audit` / `pip audit` zeigt kein Finding mehr für diese CVE
- [ ] Existing Tests laufen noch (`npm run test` / `pytest`)
- [ ] Keine neuen Dependencies ohne Begründung

---

### 🤖 Task for Jules

@google-labs-jules please fix the security issue described above.

**Wichtig — bitte lies das:**

1. **Scope strikt halten** — nur die in "Betroffene Dateien" genannten Dateien anfassen
2. **Kein Refactoring** — auch wenn du "besseren" Code siehst
3. **PR-Body muss `Fixes #N` enthalten** (mit diesem Issue-Number)
4. **Reagiere NICHT mit "Acknowledged" auf Review-Kommentare** — das hat in der Vergangenheit zu Review-Loops geführt
5. Du wirst automatisch von Claude Opus reviewt (strukturiert: Blockers/Suggestions/Nits)
6. Bei Approval → Shadow merged manuell. Max 5 Review-Iterationen, sonst Human-Eskalation.

---
*Auto-created by ShadowOps SecOps Workflow · Finding ID: {finding_id}*
"""
```

**Step 2: Issue-Erstellung im bestehenden Flow erweitern**

Suche die Stelle in `scan_agent.py` wo GitHub-Issues erstellt werden (vermutlich `_create_github_issue` oder ähnlich). Der konkrete Funktionsname kann variieren — suche nach `gh issue create` in der Datei.

Ersetze den Body-Build-Part durch:

```python
        # Fix-Mode bestimmen
        mode = classify_fix_mode(finding)
        if mode == "human_only":
            # Existing Pfad: normaler Issue ohne Jules
            body = self._build_standard_issue_body(finding)
            labels = ["security", f"severity-{finding.severity}"]
        elif mode == "jules":
            body = build_jules_issue_body(finding)
            labels = ["security", "jules", f"severity-{finding.severity}"]
        else:  # self_fix
            # Kein Issue — wird direkt gefixt (existing Flow)
            return None
```

Wichtig: Der `_build_standard_issue_body` Aufruf muss die bestehende Body-Builder-Logik kapseln. Falls heute der Body inline gebaut wird, extrahiere ihn zuerst in eine private Methode:

```python
    def _build_standard_issue_body(self, finding) -> str:
        """Standard Issue-Body für Human/Self-Fix Findings."""
        # ... bestehende Logik hier ...
```

**Step 3: Smoke-Test**

```bash
python -c "from src.integrations.security_engine.scan_agent import build_jules_issue_body, classify_fix_mode; print('OK')"
pytest tests/unit/test_scan_agent_jules_classification.py -x -v
```

**Step 4: Commit**

```bash
git add src/integrations/security_engine/scan_agent.py
git commit -m "feat: build_jules_issue_body + Issue-Label-Routing (jules/self_fix/human_only)"
```

---

### `ensure_pending` Row anlegen wenn Jules-Issue erstellt wird

**Files:**
- Modify: `src/integrations/security_engine/scan_agent.py`

**Step 1: Implementation**

Nach dem erfolgreichen `gh issue create` Aufruf im `mode == "jules"` Branch, lege in der `jules_pr_reviews`-Tabelle eine `pending`-Row an. Da der ScanAgent aber keinen direkten Zugriff auf `JulesState` hat (getrennte Module), verwenden wir den gleichen asyncpg-Pool wie die `findings`-Tabelle.

Direkter SQL-Insert (kein Mixin-Dependency):

```python
    async def _create_jules_pending_row(
        self,
        *,
        repo: str,
        issue_number: int,
        finding_id: int,
    ) -> None:
        """
        Legt einen 'pending' Eintrag in jules_pr_reviews an, damit
        der JulesWorkflowMixin den später öffnenden PR findet.

        pr_number ist zu diesem Zeitpunkt 0 (kein PR bekannt), wird beim
        ersten PR-Event auf den echten PR-Number aktualisiert.
        """
        # WICHTIG: Wir nutzen pr_number=0 als Placeholder, das aber weil UNIQUE (repo, pr_number) wäre
        # ein Konflikt bei mehreren pending-Issues pro Repo.
        # Stattdessen: keinen Row anlegen, sondern ensure_pending beim PR-Open aufrufen (JulesWorkflowMixin).
        pass  # STUB — siehe unten
```

**Design-Entscheidung:** Da `jules_pr_reviews` `UNIQUE (repo, pr_number)` hat, können wir nicht mehrere pending-Rows pro Repo mit `pr_number=0` anlegen. Stattdessen macht `ensure_pending` **zum Zeitpunkt des ersten PR-Webhook-Events** den Insert:

Im `handle_jules_pr_event` (in `jules_workflow_mixin.py`, Task 8.2), direkt nach dem `is_jules`-Check, füge ein:

```python
            # Pending-Row anlegen falls noch nicht vorhanden (PR war neu)
            issue_number = self._jules_extract_fixes_ref(pr.get("body") or "")
            finding_id = await self._jules_lookup_finding_by_issue(repo, issue_number) if issue_number else None
            await self.jules_state.ensure_pending(
                repo=repo,
                pr_number=pr_number,
                issue_number=issue_number,
                finding_id=finding_id,
            )
```

Und füge die Helper-Methoden zum Mixin hinzu:

```python
    def _jules_extract_fixes_ref(self, body: str) -> Optional[int]:
        """Parst 'Fixes #42' aus dem PR-Body."""
        import re
        m = re.search(r"(?:Fixes|Closes|Resolves)\s+#(\d+)", body, re.IGNORECASE)
        return int(m.group(1)) if m else None

    async def _jules_lookup_finding_by_issue(self, repo: str, issue_number: int) -> Optional[int]:
        """Findet finding.id via issue_number im Repo (Best-Effort)."""
        if not self.jules_state or not issue_number:
            return None
        try:
            async with self.jules_state._pool.acquire() as conn:
                rec = await conn.fetchrow(
                    """
                    SELECT id FROM findings
                    WHERE github_issue_number = $1 AND project = $2
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    issue_number, repo,
                )
                return rec["id"] if rec else None
        except Exception:
            return None
```

**Step 2: Smoke-Test**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: ensure_pending beim ersten PR-Event + Fixes-Ref Parser"
```

---

## Health-Endpoint (Phase 13)

### `/health/jules` in `health_server.py`

**Files:**
- Modify: `src/utils/health_server.py`
- Create: `tests/unit/test_health_jules.py`

**Step 1: Test**

```python
# tests/unit/test_health_jules.py
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from src.utils.health_server import HealthCheckServer


@pytest.mark.asyncio
async def test_health_jules_returns_disabled_when_off(aiohttp_client):
    bot = MagicMock()
    bot.config = MagicMock()
    bot.config.jules_workflow = MagicMock(enabled=False)
    bot.github_integration = MagicMock(_jules_enabled=False)

    server = HealthCheckServer(bot)
    client = await aiohttp_client(server._app)
    resp = await client.get("/health/jules")
    assert resp.status == 200
    data = await resp.json()
    assert data["enabled"] is False
    assert data["status"] == "disabled"


@pytest.mark.asyncio
async def test_health_jules_returns_metrics_when_enabled(aiohttp_client):
    bot = MagicMock()
    bot.config = MagicMock()
    bot.config.jules_workflow = MagicMock(enabled=True)

    # Fake jules_state
    gh_int = MagicMock()
    gh_int._jules_enabled = True
    gh_int.jules_state = MagicMock()
    gh_int.jules_state._pool = MagicMock()

    # Mock fetch_health_stats
    async def fake_stats():
        return {
            "active_reviews": 2,
            "pending_prs": 1,
            "escalated_24h": 0,
            "stats_24h": {"total_reviews": 5, "approved": 4, "revisions": 1},
        }
    gh_int.jules_state.fetch_health_stats = fake_stats
    bot.github_integration = gh_int

    server = HealthCheckServer(bot)
    client = await aiohttp_client(server._app)
    resp = await client.get("/health/jules")
    assert resp.status == 200
    data = await resp.json()
    assert data["enabled"] is True
    assert data["active_reviews"] == 2
```

**Step 2: `fetch_health_stats` in `jules_state.py` hinzufügen**

```python
    async def fetch_health_stats(self) -> Dict[str, Any]:
        """Kompakte Health-Metriken für /health/jules."""
        async with self._pool.acquire() as conn:
            active = await conn.fetchval(
                "SELECT COUNT(*) FROM jules_pr_reviews WHERE status = 'reviewing'"
            )
            pending = await conn.fetchval(
                "SELECT COUNT(*) FROM jules_pr_reviews WHERE status = 'pending'"
            )
            escalated_24h = await conn.fetchval(
                """
                SELECT COUNT(*) FROM jules_pr_reviews
                WHERE status = 'escalated' AND closed_at > now() - interval '24 hours'
                """
            )
            stats_24h = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)                                             AS total,
                    COUNT(*) FILTER (WHERE status = 'approved')          AS approved,
                    COUNT(*) FILTER (WHERE status = 'revision_requested') AS revisions,
                    COUNT(*) FILTER (WHERE status = 'merged')            AS merged,
                    COALESCE(SUM(tokens_consumed), 0)                    AS tokens
                FROM jules_pr_reviews
                WHERE updated_at > now() - interval '24 hours'
                """
            )
            last_review = await conn.fetchval(
                "SELECT MAX(last_review_at) FROM jules_pr_reviews"
            )

        return {
            "active_reviews": int(active or 0),
            "pending_prs": int(pending or 0),
            "escalated_24h": int(escalated_24h or 0),
            "stats_24h": {
                "total_reviews": int(stats_24h["total"] or 0),
                "approved": int(stats_24h["approved"] or 0),
                "revisions": int(stats_24h["revisions"] or 0),
                "merged": int(stats_24h["merged"] or 0),
                "tokens_consumed": int(stats_24h["tokens"] or 0),
            },
            "last_review_at": last_review.isoformat() if last_review else None,
        }
```

**Step 3: Endpoint in `health_server.py`**

Suche die Stelle in `src/utils/health_server.py` wo andere Routes definiert werden (z.B. `app.router.add_get('/health', ...)`), und füge hinzu:

```python
        self._app.router.add_get("/health/jules", self._health_jules)
```

Und die Handler-Methode:

```python
    async def _health_jules(self, request):
        """
        Jules SecOps Workflow Health-Endpoint.
        Gibt Metriken aus jules_pr_reviews zurück.
        """
        cfg_enabled = bool(
            getattr(getattr(self.bot.config, "jules_workflow", None), "enabled", False)
        )
        gh = getattr(self.bot, "github_integration", None)
        gh_enabled = bool(gh and getattr(gh, "_jules_enabled", False))

        if not (cfg_enabled and gh_enabled and getattr(gh, "jules_state", None)):
            return web.json_response({
                "enabled": False,
                "status": "disabled",
            })

        try:
            stats = await gh.jules_state.fetch_health_stats()
            return web.json_response({
                "enabled": True,
                "status": "healthy",
                **stats,
            })
        except Exception as e:
            return web.json_response({
                "enabled": True,
                "status": "error",
                "error": str(e),
            }, status=500)
```

**Step 4: Tests**

```bash
pytest tests/unit/test_health_jules.py -x -v
```

Erwartet: 2 passed. Falls fehlschlägt wegen `aiohttp_client` fixture: das ist die `pytest-aiohttp` Fixture — falls nicht installiert, teste manuell via `curl http://127.0.0.1:8766/health/jules` in Phase 15.

**Step 5: Commit**

```bash
git add src/utils/health_server.py src/integrations/github_integration/jules_state.py tests/unit/test_health_jules.py
git commit -m "feat: /health/jules Endpoint mit Metriken aus jules_pr_reviews"
```

---
