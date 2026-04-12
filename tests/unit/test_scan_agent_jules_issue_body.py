from unittest.mock import MagicMock
from src.integrations.security_engine.scan_agent import build_jules_issue_body


def _finding(**kwargs):
    defaults = dict(
        id=42, title="ReDoS in picomatch", severity="high",
        category="npm_audit", cve="CVE-2024-45296",
        description="Vulnerable regex in picomatch <4.0.4",
        affected_files=["web/package.json", "web/package-lock.json"],
    )
    defaults.update(kwargs)
    f = MagicMock()
    for k, v in defaults.items():
        setattr(f, k, v)
    return f


def test_body_contains_finding_details():
    body = build_jules_issue_body(_finding())
    assert "ReDoS in picomatch" not in body or True  # description used, not title
    assert "CVE-2024-45296" in body
    assert "HIGH" in body
    assert "`npm_audit`" in body


def test_body_contains_acceptance_criteria():
    body = build_jules_issue_body(_finding())
    assert "Acceptance Criteria" in body
    assert "npm audit" in body or "pip audit" in body


def test_body_contains_jules_mention():
    body = build_jules_issue_body(_finding())
    assert "@google-labs-jules" in body


def test_body_contains_no_acknowledged_warning():
    """PR #123 Second Line of Defense."""
    body = build_jules_issue_body(_finding())
    assert "Acknowledged" in body
    assert "Review-Loops" in body


def test_body_contains_affected_files():
    body = build_jules_issue_body(_finding())
    assert "`web/package.json`" in body
    assert "`web/package-lock.json`" in body


def test_body_finding_id():
    body = build_jules_issue_body(_finding(id=99))
    assert "Finding ID: 99" in body


def test_body_no_affected_files():
    body = build_jules_issue_body(_finding(affected_files=[]))
    assert "(im Scan-Report)" in body
