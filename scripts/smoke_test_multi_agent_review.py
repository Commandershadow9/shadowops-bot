#!/usr/bin/env python3
"""Smoke-Test fuer Multi-Agent Review Pipeline.

Laesst jede Pipeline-Stage einzeln gegen echte DB + echte Adapter laufen,
OHNE dass die Live-Pipeline aktiviert werden muss. Reproduzierbar, sicher.

Stages:
  1. Import-Check (alle agent_review Module ladbar)
  2. Detector gegen 6 realistische PR-Samples
  3. Adapter.build_prompt() fuer jeden Non-Jules-Adapter
  4. merge_policy() mit 5 Review-Szenarien
  5. TaskQueue live DB-Round-Trip (enqueue/get_next/mark_released/cleanup)
  6. OutcomeTracker live DB-Round-Trip (record/get_pending/mark_checked/cleanup)
  7. Daily-Digest render (leere und populated DigestData)

Exit-Code: 0 alle ok, 1 bei mindestens einem Fehler.

Usage:
  cd /home/cmdshadow/shadowops-bot
  source .venv/bin/activate
  PYTHONPATH=src python scripts/smoke_test_multi_agent_review.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

# PYTHONPATH setzen wie src-Tests es machen
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def header(title: str):
    print(f"\n{BOLD}{CYAN}━━━ {title} ━━━{RESET}")


def ok(msg: str):
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str, exc: Exception | None = None):
    print(f"  {RED}✗ {msg}{RESET}")
    if exc:
        print(f"    {RED}{type(exc).__name__}: {exc}{RESET}")


def warn(msg: str):
    print(f"  {YELLOW}⚠{RESET} {msg}")


# ──── Stage 1: Imports ─────────────────────────────────────

def stage_imports() -> bool:
    header("Stage 1 — Import-Check")
    modules = [
        "integrations.github_integration.agent_review.adapters.base",
        "integrations.github_integration.agent_review.adapters.jules",
        "integrations.github_integration.agent_review.adapters.seo",
        "integrations.github_integration.agent_review.adapters.codex",
        "integrations.github_integration.agent_review.detector",
        "integrations.github_integration.agent_review.queue",
        "integrations.github_integration.agent_review.jules_api",
        "integrations.github_integration.agent_review.outcome_tracker",
        "integrations.github_integration.agent_review.suggestions_poller",
        "integrations.github_integration.agent_review.discord_embed",
        "integrations.github_integration.agent_review.daily_digest",
        "integrations.github_integration.agent_review.prompts.seo_prompt",
        "integrations.github_integration.agent_review.prompts.codex_prompt",
    ]
    all_ok = True
    for m in modules:
        try:
            __import__(m)
            ok(m)
        except Exception as e:
            fail(m, e)
            all_ok = False
    return all_ok


# ──── Stage 2: Detector ────────────────────────────────────

def stage_detector() -> bool:
    header("Stage 2 — Detector gegen realistische PR-Samples")
    from integrations.github_integration.agent_review.detector import AgentDetector
    from integrations.github_integration.agent_review.adapters.jules import JulesAdapter
    from integrations.github_integration.agent_review.adapters.seo import SeoAdapter
    from integrations.github_integration.agent_review.adapters.codex import CodexAdapter

    d = AgentDetector([JulesAdapter(), SeoAdapter(), CodexAdapter()])

    samples = [
        ("Jules Security PR", {
            "labels": [{"name": "jules"}, {"name": "security"}],
            "user": {"login": "Commandershadow9"},
            "body": "PR created automatically by Jules",
            "head": {"ref": "jules/sec-134"}, "title": "Fix XSS",
        }, "jules"),
        ("SEO Audit PR", {
            "labels": [], "user": {"login": "Commandershadow9"},
            "body": "## 🔍 SEO Audit\n\nzerodox.de",
            "head": {"ref": "seo/zerodox/2026"}, "title": "[SEO] Meta",
        }, "seo"),
        ("Codex Security Fix", {
            "labels": [], "user": {"login": "Commandershadow9"},
            "body": "Fixes finding #42",
            "head": {"ref": "fix/security-findings"}, "title": "fix: SQLi",
        }, "codex"),
        ("Dependabot (should skip)", {
            "labels": [{"name": "dependencies"}],
            "user": {"login": "dependabot[bot]"}, "body": "Bumps lodash",
            "head": {"ref": "dependabot/npm/lodash"}, "title": "chore(deps)",
        }, None),
        ("User PR (should skip)", {
            "labels": [], "user": {"login": "dev"},
            "body": "feat", "head": {"ref": "feature/x"}, "title": "Add X",
        }, None),
        ("SEO+Jules Ambiguitaet", {
            "labels": [], "user": {"login": "Commandershadow9"},
            "body": "## 🔍 SEO Audit",
            "head": {"ref": "jules/unusual"}, "title": "...",
        }, "seo"),
    ]

    all_ok = True
    for name, pr, expected in samples:
        adapter = d.detect(pr)
        got = adapter.agent_name if adapter else None
        if got == expected:
            ok(f"{name:<36} → {got}")
        else:
            fail(f"{name:<36} → got={got}, expected={expected}")
            all_ok = False
    return all_ok


# ──── Stage 3: build_prompt() ──────────────────────────────

def stage_build_prompt() -> bool:
    header("Stage 3 — Adapter.build_prompt() (Non-Jules)")
    from integrations.github_integration.agent_review.adapters.seo import SeoAdapter
    from integrations.github_integration.agent_review.adapters.codex import CodexAdapter

    shared_kwargs = dict(
        diff="--- a/x\n+++ b/x\n+content",
        pr_payload={"title": "test", "files_changed_paths": ["x.md"]},
        finding_context={"title": "t", "severity": "medium", "description": "desc"},
        iteration=1, few_shot=[], knowledge=[],
        project="ZERODOX",
    )

    all_ok = True
    for adapter_cls, domain_keyword in [(SeoAdapter, "SEO"), (CodexAdapter, "Security")]:
        try:
            p = adapter_cls().build_prompt(**shared_kwargs)
            if len(p) < 500:
                fail(f"{adapter_cls.__name__}: prompt zu kurz ({len(p)} chars)")
                all_ok = False
            elif domain_keyword not in p:
                fail(f"{adapter_cls.__name__}: '{domain_keyword}' fehlt im Prompt")
                all_ok = False
            else:
                ok(f"{adapter_cls.__name__:<15} prompt ({len(p)} chars, enthaelt '{domain_keyword}')")
        except Exception as e:
            fail(f"{adapter_cls.__name__}.build_prompt()", e)
            all_ok = False
    return all_ok


# ──── Stage 4: merge_policy() ──────────────────────────────

def stage_merge_policy() -> bool:
    header("Stage 4 — merge_policy() Matrix")
    from integrations.github_integration.agent_review.adapters.jules import JulesAdapter
    from integrations.github_integration.agent_review.adapters.seo import SeoAdapter
    from integrations.github_integration.agent_review.adapters.codex import CodexAdapter
    from integrations.github_integration.agent_review.adapters.base import MergeDecision

    scenarios = [
        ("Jules approved tests-only", JulesAdapter(), {"verdict": "approved"},
         {"files_changed_paths": ["tests/test_x.py"], "additions": 50}, "ZERODOX", MergeDecision.AUTO),
        ("Jules approved security", JulesAdapter(), {"verdict": "approved"},
         {"files_changed_paths": ["src/auth.py"], "labels": [{"name": "security"}], "additions": 30}, "ZERODOX", MergeDecision.MANUAL),
        ("SEO approved content-only", SeoAdapter(), {"verdict": "approved", "scope_check": {"in_scope": True}},
         {"files_changed_paths": ["web/src/content/blog.md"], "additions": 100}, "ZERODOX", MergeDecision.AUTO),
        ("SEO approved touches package.json", SeoAdapter(), {"verdict": "approved", "scope_check": {"in_scope": True}},
         {"files_changed_paths": ["web/src/content/x.md", "web/package.json"], "additions": 50}, "ZERODOX", MergeDecision.MANUAL),
        ("Codex approved (always MANUAL)", CodexAdapter(), {"verdict": "approved"},
         {"files_changed_paths": ["src/auth.py"], "additions": 10}, "ZERODOX", MergeDecision.MANUAL),
    ]

    all_ok = True
    for name, adapter, review, pr, project, expected in scenarios:
        got = adapter.merge_policy(review, pr, project)
        if got == expected:
            ok(f"{name:<40} → {got.value}")
        else:
            fail(f"{name:<40} → got={got.value}, expected={expected.value}")
            all_ok = False
    return all_ok


# ──── Stage 5: TaskQueue live DB ──────────────────────────

async def stage_task_queue() -> bool:
    header("Stage 5 — TaskQueue Live-DB Round-Trip")
    from utils.config import Config
    from integrations.github_integration.agent_review.queue import TaskQueue

    try:
        dsn = Config().security_analyst_dsn
        q = TaskQueue(dsn)
        await q.connect()

        # Cleanup from prior runs
        async with q._pool.acquire() as conn:
            await conn.execute("DELETE FROM agent_task_queue WHERE source='smoke_test'")

        # Enqueue
        tid = await q.enqueue(
            source="smoke_test", priority=2,
            payload={"repo": "test", "prompt": "test"}, project="smoke",
        )
        ok(f"enqueue → task_id={tid}")

        # get_next_batch
        batch = await q.get_next_batch(limit=5)
        assert any(t.id == tid for t in batch)
        ok(f"get_next_batch → {len(batch)} Task(s), enthaelt unsere ID")

        # mark_released
        await q.mark_released(tid, "smoke-session-x")
        async with q._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, released_as FROM agent_task_queue WHERE id=$1", tid,
            )
        assert row["status"] == "released"
        assert row["released_as"] == "smoke-session-x"
        ok(f"mark_released → status={row['status']}, external_id={row['released_as']}")

        # count_by_status
        counts = await q.count_by_status()
        ok(f"count_by_status → {dict(counts)}")

        # Cleanup
        async with q._pool.acquire() as conn:
            await conn.execute("DELETE FROM agent_task_queue WHERE source='smoke_test'")
        ok("Cleanup erfolgreich")

        await q.close()
        return True
    except Exception as e:
        fail("TaskQueue Round-Trip", e)
        traceback.print_exc()
        return False


# ──── Stage 6: OutcomeTracker live DB ─────────────────────

async def stage_outcome_tracker() -> bool:
    header("Stage 6 — OutcomeTracker Live-DB Round-Trip")
    from utils.config import Config
    from integrations.github_integration.agent_review.outcome_tracker import OutcomeTracker

    try:
        dsn = Config().security_analyst_dsn
        t = OutcomeTracker(dsn)
        await t.connect()

        async with t._pool.acquire() as conn:
            await conn.execute("DELETE FROM auto_merge_outcomes WHERE project='smoke_test'")

        oid = await t.record_auto_merge(
            agent_type="jules", project="smoke_test",
            repo="x/smoke", pr_number=999, rule_matched="smoke_approved_0b",
        )
        ok(f"record_auto_merge → outcome_id={oid}")

        # Age the row artificial for get_pending_outcomes
        async with t._pool.acquire() as conn:
            await conn.execute(
                "UPDATE auto_merge_outcomes SET merged_at=now()-interval '25 hours' WHERE id=$1",
                oid,
            )

        pending = await t.get_pending_outcomes(min_age_hours=24)
        assert any(p.id == oid for p in pending)
        ok(f"get_pending_outcomes (24h) → {len(pending)} pending, enthaelt unsere ID")

        await t.mark_checked(oid, reverted=False)
        async with t._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT reverted, checked_at FROM auto_merge_outcomes WHERE id=$1", oid,
            )
        assert row["checked_at"] is not None
        ok(f"mark_checked → checked_at set, reverted={row['reverted']}")

        summary = await t.last_24h_summary()
        ok(f"last_24h_summary → {summary}")

        # Cleanup
        async with t._pool.acquire() as conn:
            await conn.execute("DELETE FROM auto_merge_outcomes WHERE project='smoke_test'")
        ok("Cleanup erfolgreich")

        await t.close()
        return True
    except Exception as e:
        fail("OutcomeTracker Round-Trip", e)
        traceback.print_exc()
        return False


# ──── Stage 7: Daily-Digest render ────────────────────────

def stage_digest() -> bool:
    header("Stage 7 — Daily-Digest Rendering")
    from integrations.github_integration.agent_review.daily_digest import (
        DigestData, render_digest,
    )

    try:
        empty = DigestData(
            reviews_24h=[], auto_merges_24h={"total": 0, "reverted": 0, "pending": 0},
            queue_status={}, pending_manual_merges=0, revert_trend_7d=[],
        )
        out_empty = render_digest(empty)
        assert "Daily Digest" in out_empty
        ok(f"Empty digest → {len(out_empty)} chars")

        populated = DigestData(
            reviews_24h=[
                {"agent_type": "jules", "verdict": "approved", "count": 5},
                {"agent_type": "seo", "verdict": "approved", "count": 3},
            ],
            auto_merges_24h={"total": 8, "reverted": 1, "pending": 2},
            queue_status={"queued": 3, "released": 42, "failed": 1},
            pending_manual_merges=5,
            revert_trend_7d=[
                {"rule_matched": "seo_approved_0b", "total": 10, "reverted": 2, "rate_pct": 20.0},
            ],
        )
        out_pop = render_digest(populated)
        for s in ("| jules |", "| seo |", "**Gesamt:** 8", "PRs", "queued: 3"):
            if s not in out_pop:
                fail(f"Populated digest fehlt: '{s}'")
                return False
        ok(f"Populated digest → {len(out_pop)} chars, alle Sections vorhanden")
        return True
    except Exception as e:
        fail("Digest rendering", e)
        return False


# ──── Main ─────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}ShadowOps Multi-Agent Review Pipeline — Smoke Test{RESET}")
    print(f"Python: {sys.version.split()[0]} · CWD: {ROOT}")

    results = {}
    results["imports"] = stage_imports()
    results["detector"] = stage_detector()
    results["build_prompt"] = stage_build_prompt()
    results["merge_policy"] = stage_merge_policy()
    results["task_queue"] = await stage_task_queue()
    results["outcome_tracker"] = await stage_outcome_tracker()
    results["digest"] = stage_digest()

    header("Gesamt-Ergebnis")
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    for stage, ok_status in results.items():
        icon = f"{GREEN}✓{RESET}" if ok_status else f"{RED}✗{RESET}"
        print(f"  {icon} {stage}")

    print()
    if passed == total:
        print(f"{BOLD}{GREEN}✅ ALLE {total} STAGES GRUEN{RESET}")
        return 0
    else:
        print(f"{BOLD}{RED}❌ {total - passed}/{total} Stage(s) FAILED{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
