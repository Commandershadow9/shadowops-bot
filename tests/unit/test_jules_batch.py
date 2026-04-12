import pytest
from src.integrations.github_integration.jules_batch import classify_outcome


class _R:
    def __init__(self, status="merged", feedback_rating=None, human_override=False):
        self.status = status
        self.feedback_rating = feedback_rating
        self.human_override = human_override


def test_merged_positive_approved_clean():
    assert classify_outcome(_R("merged", 1)) == "approved_clean"


def test_merged_negative_false_positive():
    assert classify_outcome(_R("merged", -1)) == "false_positive"


def test_revision_negative_good_catch():
    assert classify_outcome(_R("revision_requested", -1)) == "good_catch"


def test_override_after_approval_missed_issue():
    assert classify_outcome(_R("approved", human_override=True)) == "missed_issue"


def test_merged_no_feedback_approved_clean():
    assert classify_outcome(_R("merged")) == "approved_clean"


def test_escalated_fallback():
    assert classify_outcome(_R("escalated")) == "approved_clean"
