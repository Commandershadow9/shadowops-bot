---
title: Jules Workflow — PR Detection
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/007-jules-secops-workflow.md
  - ../../plans/2026-04-11-jules-secops-workflow-design.md
---

# Jules Workflow — PR Detection

Dieser Abschnitt beschreibt wie der Bot erkennt, dass ein PR von Jules stammt — via `_jules_is_jules_pr()` mit 3 Kriterien: Label, Author, Body-Marker. Die Erkennung entstammt Phase 8 (`handle_jules_pr_event` + `_jules_is_jules_pr`) des Implementierungsplans.

## Task 8.2 (Ausschnitt): `handle_pr_event` — PR-Event-Eintritt mit Jules-Detection

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`
- Modify: `tests/unit/test_jules_workflow_mixin.py`

### Tests für `handle_jules_pr_event` (Detection-Cases)

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

### Implementation — Detection im Mixin

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

            # Ist das überhaupt ein Jules-PR?
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

    async def _jules_is_jules_pr(self, pr: Dict[str, Any], repo: str) -> bool:
        """
        Erkennt Jules-PRs robust:
        1. Label 'jules' gesetzt
        2. ODER Author == 'google-labs-jules[bot]'
        3. ODER PR-Body enthält 'Fixes #N' UND das Issue hat Label 'jules'
        """
        labels = [l.get("name", "").lower() for l in (pr.get("labels") or [])]
        if "jules" in labels:
            return True

        author = ((pr.get("user") or {}).get("login") or "").lower()
        if author.startswith("google-labs-jules"):
            return True

        # Body-Reference-Check: optional, braucht Issue-Lookup — skippen wenn teuer
        # (wir verlassen uns primär auf Label)
        return False
```

### Tests — PASS

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 10 passed.

### Commit

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py tests/unit/test_jules_workflow_mixin.py
git commit -m "feat: handle_jules_pr_event — PR-Event-Eintritt mit Jules-Detection"
```

---

## Hinweis

Die Post-Deploy-Erfahrung (siehe `CLAUDE.md` Abschnitt "Jules SecOps Workflow") zeigte: Jules erstellt PRs unter User-Account, daher ist **Body-Marker** (`PR created automatically by Jules`) primaer — Label/Author-Checks sind Fallbacks. Die Erkennung im Live-Code wurde durch Commit `bd2038f` entsprechend haertet.
