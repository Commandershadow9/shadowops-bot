"""Duplikat-Schutz und Rueckschreiben der Issue-URL (shadowops-bot #438).

Anlass: Am 28.08.2026 legte die Engine fuenfzehn Security-Issues in ZERODOX an,
sieben Themen doppelt. Zwei Ursachen, beide hier abgesichert:

1. Das Rueckschreiben schrieb ``status='issue_created'`` — ein Wert, den der
   CHECK-Constraint ``findings_status_check`` nicht kennt. Das UPDATE schlug
   fehl, ein ``except: pass`` verschluckte es, ``github_issue_url`` blieb NULL
   und der naechste Lauf hielt den Fund fuer unbearbeitet.
2. Der Duplikat-Check suchte per Volltext nach einem Titel voller Klammern und
   Doppelpunkte, fand nichts und schloss daraus "gibt es noch nicht".
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.security_engine.deep_scan import DeepScanMode, _DUPLIKAT_CHECK_LIMIT

_FINDING = {
    'id': 445,
    'severity': 'CRITICAL',
    'category': 'npm_audit',
    'title': '[@auth/core] Auth.js: getToken() throws an uncaught exception',
    'description': 'Package @auth/core (<=0.41.2)',
    'affected_project': 'zerodox',
    'github_issue_url': None,
}


def _proc(returncode=0, stdout=b'[]', stderr=b''):
    p = MagicMock()
    p.returncode = returncode
    p.communicate = AsyncMock(return_value=(stdout, stderr))
    return p


def _mode():
    db = AsyncMock()
    db.pool = AsyncMock()
    db.pool.execute = AsyncMock()
    return DeepScanMode(db=db)


@pytest.mark.asyncio
async def test_vorhandenes_issue_wird_am_titel_erkannt():
    """Der Vergleich laeuft lokal ueber die Titelliste, nicht ueber die Suche."""
    mode = _mode()
    vorhanden = json.dumps([
        {'title': 'irgendein anderes Issue'},
        {'title': f"[Security] {_FINDING['title']}"},
    ]).encode()
    with patch('asyncio.create_subprocess_exec',
               new=AsyncMock(return_value=_proc(stdout=vorhanden))) as spawn:
        url = await mode._create_github_issue(_FINDING)
    assert url is None
    # Genau ein Aufruf: der Check. Kein `gh issue create`.
    assert spawn.await_count == 1


@pytest.mark.asyncio
async def test_check_ohne_treffer_legt_an():
    mode = _mode()
    calls = [_proc(stdout=b'[]'), _proc(stdout=b'https://github.com/x/y/issues/1\n')]
    with patch('asyncio.create_subprocess_exec', new=AsyncMock(side_effect=calls)):
        url = await mode._create_github_issue(_FINDING)
    assert url == 'https://github.com/x/y/issues/1'


@pytest.mark.asyncio
async def test_gescheiterter_check_legt_nichts_an():
    """Fail-closed: Wer nicht nachsehen kann, legt nichts an.

    Vorher stand hier ``except: pass`` — ein Fehler im Check fuehrte also
    zuverlaessig zum Anlegen, also genau dorthin, wovor er schuetzen sollte.
    """
    mode = _mode()
    with patch('asyncio.create_subprocess_exec',
               new=AsyncMock(return_value=_proc(returncode=1, stderr=b'gh: auth required'))) as spawn:
        url = await mode._create_github_issue(_FINDING)
    assert url is None
    assert spawn.await_count == 1


@pytest.mark.asyncio
async def test_check_wirft_ausnahme_legt_nichts_an():
    mode = _mode()
    with patch('asyncio.create_subprocess_exec', new=AsyncMock(side_effect=OSError('kein gh'))):
        url = await mode._create_github_issue(_FINDING)
    assert url is None


@pytest.mark.asyncio
async def test_abgeschnittene_liste_beweist_nichts():
    """Liefert der Check das Limit, ist die Liste womoeglich unvollstaendig."""
    mode = _mode()
    voll = json.dumps([{'title': f'Issue {i}'} for i in range(_DUPLIKAT_CHECK_LIMIT)]).encode()
    with patch('asyncio.create_subprocess_exec',
               new=AsyncMock(return_value=_proc(stdout=voll))) as spawn:
        url = await mode._create_github_issue(_FINDING)
    assert url is None
    assert spawn.await_count == 1


@pytest.mark.asyncio
async def test_rueckschreiben_verletzt_den_constraint_nicht():
    """Geschrieben wird die URL — der Status bleibt 'open'.

    Ein Issue zu haben heisst nicht, dass der Fund behoben ist. Und
    'issue_created' ist kein vom CHECK-Constraint erlaubter Wert.
    """
    mode = _mode()
    mode.db.pool.fetch = AsyncMock(return_value=[dict(_FINDING)])
    with patch.object(DeepScanMode, '_create_github_issue',
                      new=AsyncMock(return_value='https://github.com/x/y/issues/1')):
        result = await mode._run_fix_phase('full_scan', {})

    assert result['issues_created'] == 1
    sql, finding_id, url = mode.db.pool.execute.await_args.args
    assert 'issue_created' not in sql
    assert 'github_issue_url' in sql
    assert (finding_id, url) == (445, 'https://github.com/x/y/issues/1')
