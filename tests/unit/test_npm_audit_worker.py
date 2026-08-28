import json
import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.security_engine.team.workers.npm_audit_worker import NpmAuditWorker
from src.integrations.security_engine.team.contracts import SecurityJob, JobStatus

_AUDIT_JSON = json.dumps({
    "vulnerabilities": {
        "lodash": {"severity": "high", "range": "<4.17.21",
                   "via": [{"title": "Prototype Pollution", "url": "https://x/1"}]},
        "minimist": {"severity": "critical", "range": "<1.2.6", "via": ["lodash"]},
    }
})


def _db():
    db = AsyncMock()
    db.pool = AsyncMock()
    db.pool.execute = AsyncMock()
    db.pool.fetchval = AsyncMock(return_value=None)
    # fetch bedient den Abgleich veralteter Funde (UPDATE … RETURNING id).
    # Default: nichts zu schliessen.
    db.pool.fetch = AsyncMock(return_value=[])
    db.store_finding = AsyncMock(side_effect=[101, 102])
    return db


def test_parse_extracts_findings():
    out = NpmAuditWorker._parse(_AUDIT_JSON)
    assert len(out) == 2
    assert any(f["severity"] == "HIGH" for f in out)
    assert all(f["category"] == "npm_audit" for f in out)


def test_parse_broken_json_returns_empty():
    assert NpmAuditWorker._parse("not-json") == []


@pytest.mark.asyncio
async def test_process_missing_path_is_partial():
    w = NpmAuditWorker(db=_db())
    job = SecurityJob(worker_type="npm_audit", project="guildscout", payload={})
    res = await w.process(job)
    assert res.status == JobStatus.PARTIAL


@pytest.mark.asyncio
async def test_process_stores_new_findings():
    db = _db()
    w = NpmAuditWorker(db=db)
    job = SecurityJob(worker_type="npm_audit", project="guildscout",
                      payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit",
                      new=AsyncMock(return_value=_AUDIT_JSON)), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.status == JobStatus.OK
    assert res.findings_added == 2
    assert db.store_finding.await_count == 2


@pytest.mark.asyncio
async def test_process_skips_deduped_findings():
    db = _db()
    db.pool.fetchval = AsyncMock(return_value=1)
    w = NpmAuditWorker(db=db)
    job = SecurityJob(worker_type="npm_audit", project="guildscout",
                      payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit",
                      new=AsyncMock(return_value=_AUDIT_JSON)), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.findings_added == 0
    assert db.store_finding.await_count == 0


@pytest.mark.asyncio
async def test_process_enolock_is_partial():
    w = NpmAuditWorker(db=_db())
    job = SecurityJob(worker_type="npm_audit", project="guildscout", payload={"path": "/tmp"})
    err_json = json.dumps({"error": {"code": "ENOLOCK", "summary": "requires lockfile"}})
    with patch.object(NpmAuditWorker, "_run_npm_audit", new=AsyncMock(return_value=err_json)), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.status == JobStatus.PARTIAL
    assert any("ENOLOCK" in e or "lockfile" in e for e in res.errors)


@pytest.mark.asyncio
async def test_process_npm_not_found_is_partial():
    w = NpmAuditWorker(db=_db())
    job = SecurityJob(worker_type="npm_audit", project="guildscout", payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit", new=AsyncMock(side_effect=FileNotFoundError())), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.status == JobStatus.PARTIAL


@pytest.mark.asyncio
async def test_process_timeout_is_partial():
    import asyncio
    w = NpmAuditWorker(db=_db())
    job = SecurityJob(worker_type="npm_audit", project="guildscout", payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit", new=AsyncMock(side_effect=asyncio.TimeoutError())), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.status == JobStatus.PARTIAL


# ── Abgleich veralteter Funde ────────────────────────────────────────────
# Ohne ihn altert der Bestand zu Unwahrheiten: Am 28.08.2026 entstanden aus
# Juli-Funden, die es laengst nicht mehr gab, dreizehn GitHub-Issues.

async def _lauf(db, raw=_AUDIT_JSON, project="guildscout"):
    w = NpmAuditWorker(db=db)
    job = SecurityJob(worker_type="npm_audit", project=project, payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit", new=AsyncMock(return_value=raw)), \
         patch("os.path.isdir", return_value=True):
        return await w.process(job)


def _abgleich_aufrufe(db):
    """Die fetch-Aufrufe, die veraltete Funde schliessen."""
    return [c for c in db.pool.fetch.await_args_list
            if "UPDATE findings" in c.args[0] and "'fixed'" in c.args[0]]


@pytest.mark.asyncio
async def test_verschwundene_funde_werden_geschlossen():
    db = _db()
    db.pool.fetch = AsyncMock(return_value=[{"id": 445}, {"id": 448}])
    res = await _lauf(db)
    assert res.status == JobStatus.OK
    assert res.metadata["findings_closed"] == 2

    aufrufe = _abgleich_aufrufe(db)
    assert len(aufrufe) == 1
    sql, project, fingerprints = aufrufe[0].args
    # Auf Kategorie UND Projekt begrenzt: ein npm-Lauf fuer ein Projekt darf
    # nichts ueber ein anderes oder ueber Trivy-Funde aussagen.
    assert "category = 'npm_audit'" in sql
    assert project == "guildscout"
    # Die Fingerprints der AKTUELLEN Ausgabe bleiben verschont.
    assert len(fingerprints) == 2


@pytest.mark.asyncio
async def test_leere_ausgabe_schliesst_alles():
    """Kein Fund mehr heisst: alles behoben — das ist ein gueltiges Ergebnis."""
    db = _db()
    db.pool.fetch = AsyncMock(return_value=[{"id": 1}])
    res = await _lauf(db, raw=json.dumps({"vulnerabilities": {}}))
    assert res.status == JobStatus.OK
    aufrufe = _abgleich_aufrufe(db)
    assert len(aufrufe) == 1
    assert aufrufe[0].args[2] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,kwargs", [
    (json.dumps({"error": {"code": "ENOLOCK", "summary": "requires lockfile"}}), {}),
    ("not-json", {}),
])
async def test_partial_schliesst_nichts(raw, kwargs):
    """Der gefaehrlichste Fall: Ein kaputter Scanner darf den Bestand nicht abraeumen.

    Bei Timeout, fehlendem npm oder ENOLOCK ist die Fundmenge leer — nicht weil
    nichts gefunden wurde, sondern weil nichts gemessen wurde. Wer beides
    verwechselt, setzt den gesamten offenen Bestand auf 'fixed'.
    """
    db = _db()
    res = await _lauf(db, raw=raw, **kwargs)
    assert res.status == JobStatus.PARTIAL
    assert _abgleich_aufrufe(db) == []


@pytest.mark.asyncio
async def test_timeout_schliesst_nichts():
    import asyncio
    db = _db()
    w = NpmAuditWorker(db=db)
    job = SecurityJob(worker_type="npm_audit", project="guildscout", payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit",
                      new=AsyncMock(side_effect=asyncio.TimeoutError())), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.status == JobStatus.PARTIAL
    assert _abgleich_aufrufe(db) == []


@pytest.mark.asyncio
async def test_fehlgeschlagener_abgleich_kippt_den_lauf_nicht():
    db = _db()
    db.pool.fetch = AsyncMock(side_effect=RuntimeError("DB weg"))
    res = await _lauf(db)
    assert res.status == JobStatus.OK
    assert res.metadata["findings_closed"] == 0
