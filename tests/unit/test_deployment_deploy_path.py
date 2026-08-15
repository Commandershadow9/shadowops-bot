"""
Tests fuer den getrennten Deploy-Pfad und das harte Zuruecksetzen im Git-Schritt.

Hintergrund (ZERODOX #2344, Vorfaelle in der Nacht zum 15.08.2026):

Der Bot deployt aus demselben Verzeichnis, in dem Menschen und parallele
Sessions arbeiten. Sein `git pull` scheitert dort an allem, was der Baum
gerade ist:

  1. ~03:00 — vier uncommittete Dateien:
     "Your local changes to the following files would be overwritten by
      merge ... Aborting"
  2. ~03:40 — ein nicht gepushter lokaler Commit auf main:
     "Not possible to fast-forward, aborting"

Sechs Merges blieben dadurch ueber Stunden undeployt. Es heilt nicht von
selbst: Nach fehlgeschlagenem deploy.sh gibt es keinen Retry, und der
Reconcile-Versuch scheitert an derselben Stelle. In Discord erscheint dabei
"Deployment fehlgeschlagen" — dieselbe Meldung wie bei rotem CI-Gate,
weshalb man in der CI sucht und dort nichts findet.

Zwei Konsequenzen, die diese Tests absichern:

**deploy_path getrennt von path.** `project['path']` steuert nicht nur den
Deploy, sondern auch Backup-Monitoring (`Path(path)/'backups'/'daily'`),
Disk-Schwellwerte, Kontext, Verifikation und GitHub-Polling — acht Dateien
insgesamt. Wer einfach `path` umbiegt, lenkt fuenf Funktionen um, um eine zu
reparieren, und macht das Backup-Monitoring stillschweigend blind.

**reset --hard statt pull.** Ein `pull` hat Merge-Semantik und kann deshalb
scheitern. `fetch` + `reset --hard` ist idempotent und immun gegen dirty,
divergiert und abgebrochenen Rebase gleichermassen. Fuer einen Baum, in dem
niemand arbeitet, ist das genau richtig — man kann nicht fuer jeden Zustand
einen eigenen Guard bauen, man muss die Abhaengigkeit aufloesen.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.deployment_manager import DeploymentManager, DeploymentError


class _MockProcess:
    """Mock fuer asyncio.subprocess.Process."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.fixture
def mgr():
    """Stub-Instance ohne __init__ — die geprueften Methoden nutzen nur das
    project-Argument, keinen Instanz-State."""
    return DeploymentManager.__new__(DeploymentManager)


def test_load_projects_reicht_deploy_path_durch():
    """
    `_load_projects` baut das project-Dict aus einer expliziten Feldliste.
    Fehlt `deploy_path` dort, kommt der Config-Eintrag nie beim Leser an —
    `_deploy_path` naehme still den Fallback und der Bot deployte weiterhin
    aus dem Arbeitsbaum. Der Defekt waere unsichtbar: kein Fehler, keine
    Warnung, nur die alte Blockade bleibt bestehen.
    """
    class _Config:
        projects = {
            'zerodox': {
                'enabled': True,
                'path': '/home/cmdshadow/ZERODOX',
                'deploy_path': '/home/cmdshadow/ZERODOX-deploy',
                'deploy': {'enabled': True},
            }
        }

    mgr = DeploymentManager.__new__(DeploymentManager)
    mgr.config = _Config()
    mgr.logger = __import__('logging').getLogger('test')

    geladen = mgr._load_projects()

    assert 'deploy_path' in geladen['zerodox'], (
        '_load_projects reicht deploy_path nicht durch — der Config-Eintrag '
        'bleibt wirkungslos und der Bot deployt weiter aus dem Arbeitsbaum'
    )
    assert geladen['zerodox']['deploy_path'] == '/home/cmdshadow/ZERODOX-deploy'


def test_load_projects_ohne_deploy_path_bleibt_unveraendert():
    """Projekte ohne das Feld duerfen sich nicht anders verhalten als bisher."""
    class _Config:
        projects = {
            'guildscout': {
                'enabled': True,
                'path': '/home/cmdshadow/GuildScout',
                'deploy': {'enabled': True},
            }
        }

    mgr = DeploymentManager.__new__(DeploymentManager)
    mgr.config = _Config()
    mgr.logger = __import__('logging').getLogger('test')

    geladen = mgr._load_projects()
    assert not geladen['guildscout'].get('deploy_path')
    assert str(mgr._deploy_path(geladen['guildscout'])) == '/home/cmdshadow/GuildScout'


def test_deploy_path_bevorzugt_eigenes_feld(mgr):
    projekt = {
        'path': Path('/home/cmdshadow/ZERODOX'),
        'deploy_path': '/home/cmdshadow/ZERODOX-deploy',
    }
    assert str(mgr._deploy_path(projekt)) == '/home/cmdshadow/ZERODOX-deploy'


def test_deploy_path_faellt_auf_path_zurueck(mgr):
    """Ohne deploy_path bleibt alles wie bisher — die Umstellung ist damit
    fuer jedes andere Projekt ein No-op."""
    projekt = {'path': Path('/home/cmdshadow/GuildScout')}
    assert str(mgr._deploy_path(projekt)) == '/home/cmdshadow/GuildScout'


def test_deploy_path_ignoriert_leeren_wert(mgr):
    """Ein leerer String in der Config darf nicht zu cwd='' fuehren."""
    projekt = {'path': Path('/home/cmdshadow/ZERODOX'), 'deploy_path': ''}
    assert str(mgr._deploy_path(projekt)) == '/home/cmdshadow/ZERODOX'


def test_git_pull_arbeitet_im_deploy_pfad(mgr):
    """Alle Git-Aufrufe muessen im Deploy-Baum laufen, nicht im Arbeitsbaum."""
    projekt = {
        'path': Path('/home/cmdshadow/ZERODOX'),
        'deploy_path': '/home/cmdshadow/ZERODOX-deploy',
    }
    aufrufe = []

    async def _fake_exec(*cmd, **kwargs):
        aufrufe.append((cmd, kwargs.get('cwd')))
        return _MockProcess(0)

    with patch('asyncio.create_subprocess_exec', new=AsyncMock(side_effect=_fake_exec)):
        asyncio.run(mgr._git_pull(projekt, 'main'))

    assert aufrufe, 'es wurde gar kein Git-Kommando ausgefuehrt'
    for cmd, cwd in aufrufe:
        assert cwd == '/home/cmdshadow/ZERODOX-deploy', (
            f'Git-Kommando {cmd[:3]} lief in {cwd} statt im Deploy-Baum'
        )


def test_git_pull_setzt_hart_zurueck_statt_zu_pullen(mgr):
    """
    `git pull` scheitert an dirty oder divergiert — genau die beiden Faelle,
    die den Bot blockiert haben. `reset --hard origin/<branch>` kann das nicht.
    """
    projekt = {'path': Path('/x'), 'deploy_path': '/y'}
    kommandos = []

    async def _fake_exec(*cmd, **kwargs):
        kommandos.append(list(cmd))
        return _MockProcess(0)

    with patch('asyncio.create_subprocess_exec', new=AsyncMock(side_effect=_fake_exec)):
        asyncio.run(mgr._git_pull(projekt, 'main'))

    flach = [' '.join(k) for k in kommandos]

    assert any('reset --hard origin/main' in k for k in flach), (
        f'kein hartes Zuruecksetzen gefunden: {flach}'
    )
    assert not any(k.startswith('git pull') for k in flach), (
        f'`git pull` ist noch vorhanden und kann weiterhin an einem dirty oder '
        f'divergierten Baum scheitern: {flach}'
    )


def test_git_pull_meldet_fehler_weiterhin(mgr):
    """Die Fehlerbehandlung darf durch die Umstellung nicht verlorengehen."""
    projekt = {'path': Path('/x'), 'deploy_path': '/y'}

    async def _fake_exec(*cmd, **kwargs):
        return _MockProcess(1, stderr=b'fatal: irgendwas')

    with patch('asyncio.create_subprocess_exec', new=AsyncMock(side_effect=_fake_exec)):
        with pytest.raises(DeploymentError):
            asyncio.run(mgr._git_pull(projekt, 'main'))
