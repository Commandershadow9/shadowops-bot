---
title: Merge-Policy und Outcome-Tracker
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/008-multi-agent-review-pipeline.md
  - ../../plans/2026-04-14-multi-agent-review-design.md
  - ../jules-workflow/README.md
---

# Merge-Policy und Outcome-Tracker

Phase 4 bringt die Adapter-basierte Merge-Policy in den Mixin-Flow und legt einen
Outcome-Tracker an, der Auto-Merges nach 24 Stunden auf Reverts, CI-Status und
Follow-Up-Fixes prueft.

---

## Phase 4: Merge-Policy + Outcome-Tracker

### Task 4.1: Auto-Merge-Execution im Mixin

**Files:**

- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

Nach Claude-Approval und vor Label-Setzen:

```python
            # Adapter-basierte Merge-Policy
            decision = adapter.merge_policy(review, pr_payload, project=repo)

            if decision == MergeDecision.AUTO and self._auto_merge_enabled():
                merged = await self._gh_auto_merge_squash(owner, repo, pr_number)
                if merged:
                    await self.outcome_tracker.record_auto_merge(
                        row.id, repo, pr_number, "adapter_rule", agent_type=adapter.agent_name,
                    )
                    await self._send_review_embed(..., auto_merged=True)
            else:
                await self._apply_label_and_notify(owner, repo, pr_number, row)
```

**Commit:**

```bash
git commit -m "feat: Auto-Merge-Execution nach Claude-Approval (per Adapter)"
```

---

### Task 4.2: `OutcomeTracker`

**Files:**

- Create: `src/integrations/github_integration/agent_review/outcome_tracker.py`
- Create: `tests/unit/agent_review/test_outcome_tracker.py`

**Skeleton:**

```python
class OutcomeTracker:
    async def record_auto_merge(self, review_id, repo, pr_number, rule_matched, agent_type):
        # Insert row in auto_merge_outcomes mit checked_at=NULL
        ...

    async def check_pending_outcomes(self):
        """Laeuft stuendlich. Fuer Merges > 24h alt: Outcome pruefen."""
        # 1. Hole alle rows wo checked_at IS NULL AND merged_at < now() - 24h
        # 2. Fuer jeden: pruefe Git fuer Revert-Commit, CI-Status
        # 3. Update row
        ...
```

**Scheduled Task in `bot.py`:** `@tasks.loop(minutes=60)` ruft `check_pending_outcomes()`.

**Commit:**

```bash
git commit -m "feat: OutcomeTracker + stuendlicher Check fuer Auto-Merges"
```
