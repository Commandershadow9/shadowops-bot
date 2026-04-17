import json
import pathlib
import pytest
import jsonschema

SCHEMA_PATH = pathlib.Path("src/schemas/jules_review.json")

@pytest.fixture
def schema():
    return json.loads(SCHEMA_PATH.read_text())

def test_schema_loads(schema):
    jsonschema.Draft7Validator.check_schema(schema)

def test_valid_minimal_review_passes(schema):
    review = {
        "verdict": "approved",
        "summary": "Clean dependency bump.",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "Matches finding"},
    }
    jsonschema.validate(review, schema)

def test_valid_revision_with_blocker_passes(schema):
    review = {
        "verdict": "revision_requested",
        "summary": "Scope violation detected.",
        "blockers": [{
            "title": "defu removal out of scope",
            "reason": "Finding was picomatch only.",
            "file": "web/package.json",
            "line": 23,
            "severity": "high",
            "suggested_fix": "Revert defu removal",
        }],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": False, "explanation": "Extra removal"},
    }
    jsonschema.validate(review, schema)

def test_missing_scope_check_fails(schema):
    review = {
        "verdict": "approved",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(review, schema)

def test_invalid_verdict_fails(schema):
    review = {
        "verdict": "maybe",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "x"},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(review, schema)
