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
