"""
Jules Learning — Nightly Batch Job.
Klassifiziert abgeschlossene Reviews, schreibt jules_review_examples.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def classify_outcome(row) -> str:
    status = getattr(row, "status", "")
    override = getattr(row, "human_override", False)
    rating = getattr(row, "feedback_rating", None)

    if override and status == "approved":
        return "missed_issue"
    if status == "merged":
        return "false_positive" if (rating is not None and rating < 0) else "approved_clean"
    if status == "revision_requested" and rating is not None and rating < 0:
        return "good_catch"
    return "approved_clean"


async def run_nightly_batch(jules_state_pool, learning_pool, logger_channel=None) -> Dict[str, int]:
    counts = {"classified": 0, "examples_written": 0}
    try:
        async with jules_state_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT r.*, f.rating AS feedback_rating
                FROM jules_pr_reviews r
                LEFT JOIN LATERAL (
                    SELECT rating FROM agent_feedback
                    WHERE agent_name = 'jules_reviewer'
                      AND reference_id = r.pr_number::text
                    ORDER BY created_at DESC LIMIT 1
                ) f ON true
                WHERE r.updated_at > now() - interval '24 hours'
                  AND r.status IN ('approved', 'merged', 'revision_requested')
            """)
        for row in rows:
            outcome = classify_outcome(row)
            counts["classified"] += 1
            try:
                rj = row.get("last_review_json") or {}
                async with learning_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO jules_review_examples
                          (project, pr_ref, diff_summary, review_json, outcome, weight)
                        VALUES ($1, $2, $3, $4::jsonb, $5, 1.0)
                    """, row["repo"], f"{row['repo']}#{row['pr_number']}",
                        (rj.get("summary", "") if isinstance(rj, dict) else ""),
                        json.dumps(rj if isinstance(rj, dict) else {}), outcome)
                    counts["examples_written"] += 1
            except Exception as e:
                logger.warning(f"[jules-batch] write failed: {e}")
    except Exception as e:
        logger.error(f"[jules-batch] batch failed: {e}")
    logger.info(f"[jules-batch] classified={counts['classified']} written={counts['examples_written']}")
    return counts
