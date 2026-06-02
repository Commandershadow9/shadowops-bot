import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.security_engine.deep_scan import DeepScanMode


@pytest.mark.asyncio
async def test_deep_scan_store_finding_delegates_to_helper():
    db = MagicMock()
    db.store_finding = AsyncMock(return_value=11)
    scanner = DeepScanMode.__new__(DeepScanMode)  # ohne __init__-Seiteneffekte
    scanner.db = db
    fid = await scanner._store_finding({
        "severity": "HIGH", "category": "general",
        "title": "T", "description": "D", "affected_project": "server",
    })
    assert fid == 11
    db.store_finding.assert_awaited_once()
    kwargs = db.store_finding.await_args.kwargs
    assert kwargs["severity"] == "HIGH"
    assert kwargs["affected_project"] == "server"
