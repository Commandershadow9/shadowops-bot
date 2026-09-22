"""Rollback muss dieselben rsync-Ausschluesse tragen wie das Backup (ZERODOX#3515).

## Der Fehler

`_create_backup` (Backup-rsync, ohne `--delete`) und `_rollback` (Rollback-
rsync, MIT `--delete`, Ziel = der LIVE-Arbeitsbaum) verwendeten bis zu diesem
Fix zwei getrennte, von Hand gepflegte `--exclude=`-Listen. Der Kommentar ueber
dem Rollback-Aufruf versprach seit jeher "Gleiche Excludes wie beim Backup" —
das stimmte nur so lange, wie niemand eine der beiden Listen aenderte, ohne an
die andere zu denken.

ZERODOX#3447 (19.09.2026) nahm `.claude/worktrees` und `.next` in die
Backup-Liste auf (Build-Output und parallele Arbeitskopien sollen nicht ins
Backup). Die Rollback-Liste blieb unveraendert. Da diese beiden Pfade damit im
Backup NICHT existieren, das Rollback aber mit `--delete` VOM Backup IN den
Live-Baum synchronisiert, las rsync das Fehlen als "seit dem Backup neu
entstanden" — und loeschte es. Vier Vorfaelle zwischen dem 20. und 22.09.2026,
je rund 5300 getrackte Dateien in parallelen Claude-Worktrees.

## Der Test

Faengt den tatsaechlich gebauten `cmd`-Aufruf beider Methoden ab (kein echtes
rsync noetig — `asyncio.create_subprocess_exec` wird gemockt) und vergleicht
die Mengen der `--exclude=`-Muster. Eine Enumeration, die beide Listen nur
NACHBAUT, haette dieselbe Fallenanfaelligkeit wie der Code selbst: Sie kann
genauso auseinanderlaufen. Deshalb zusaetzlich ein Quelltext-Test, der
erzwingt, dass beide Aufrufe aus DERSELBEN Konstante (`DEPLOY_BACKUP_EXCLUDES`)
gebaut werden — nur das macht ein erneutes Auseinanderlaufen strukturell
unmoeglich statt nur beobachtet.

## Beleg, dass der Test vor dem Fix rot gewesen waere

Gegen den Stand vor diesem PR (Backup-Excludes minus Rollback-Excludes, per
Skript aus dem alten Quelltext extrahiert):

    Backup:   ['*.pyc', '.claude/worktrees', '.env', '.git', '.next', '.venv',
               '__pycache__', 'backups', 'logs', 'node_modules', 'uploads', 'venv']
    Rollback: ['.env', '.git', '.venv', '__pycache__', 'backups', 'logs',
               'node_modules', 'uploads']
    Fehlen im Rollback: ['*.pyc', '.claude/worktrees', '.next', 'venv']

`test_rollback_schliesst_dieselben_pfade_aus_wie_das_backup` haette diese
Differenz als Assertion-Fehler gemeldet; `test_beide_kommandos_teilen_sich_die_konstante`
haette wegen des fehlenden Symbols `DEPLOY_BACKUP_EXCLUDES` (existierte vor
diesem PR nicht) bereits beim Quelltext-Scan rot gemeldet.
"""
import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.deployment_manager import DeploymentManager, DEPLOY_BACKUP_EXCLUDES

DEPLOYMENT_MANAGER = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "integrations"
    / "deployment_manager.py"
)


class _MockProcess:
    """Mock fuer asyncio.subprocess.Process — rsync laeuft hier nie wirklich."""

    def __init__(self, returncode: int = 0):
        self.returncode = returncode

    async def communicate(self):
        return b"", b""


def _exclude_patterns(cmd: list) -> set:
    return {arg.split("=", 1)[1] for arg in cmd if arg.startswith("--exclude=")}


@pytest.fixture
def mgr(tmp_path):
    """Stub-Instance ohne __init__ (spart Bot/Config-Setup), Minimal-State manuell gesetzt."""
    instance = DeploymentManager.__new__(DeploymentManager)
    instance.backup_dir = tmp_path / "backups"
    instance.backup_dir.mkdir()
    instance.logger = logging.getLogger("test_deploy_rollback_ausschluesse")
    instance.max_backups_per_project = 5
    return instance


@pytest.mark.asyncio
async def test_rollback_schliesst_dieselben_pfade_aus_wie_das_backup(mgr, tmp_path):
    """Kernbefund #3515: Backup- und Rollback-Excludes muessen exakt uebereinstimmen.

    Jeder Pfad, den das Backup auslaesst, existiert im Backup nicht — ein
    Rollback OHNE denselben Ausschluss loescht ihn per `--delete` aus dem
    Live-Baum, weil rsync das Fehlen als "seit dem Backup entfernt" liest.
    """
    quell_projekt = {'name': 'zerodox', 'path': tmp_path / "projekt"}
    quell_projekt['path'].mkdir()
    (quell_projekt['path'] / "code.txt").write_text("x")

    erfasste_cmds = []

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        erfasste_cmds.append(list(cmd))
        # Echtes rsync laeuft hier nie — das Zielverzeichnis muss der Mock
        # trotzdem anlegen, sonst scheitern os.utime()/iterdir() danach.
        Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        return _MockProcess(returncode=0)

    with patch(
        'asyncio.create_subprocess_exec',
        AsyncMock(side_effect=_fake_create_subprocess_exec),
    ):
        backup_path = await mgr._create_backup(quell_projekt)
        rollback_projekt = {'name': 'zerodox', 'path': quell_projekt['path']}
        await mgr._rollback(rollback_projekt, backup_path)

    assert len(erfasste_cmds) == 2, (
        f"Erwartet genau einen Backup- und einen Rollback-rsync-Aufruf, "
        f"erfasst wurden {len(erfasste_cmds)}."
    )
    backup_cmd, rollback_cmd = erfasste_cmds

    backup_excludes = _exclude_patterns(backup_cmd)
    rollback_excludes = _exclude_patterns(rollback_cmd)

    fehlend_im_rollback = backup_excludes - rollback_excludes
    assert not fehlend_im_rollback, (
        f"Diese Pfade schliesst das Backup aus, der Rollback aber nicht: "
        f"{sorted(fehlend_im_rollback)}. Sie existieren im Backup nicht — ein "
        f"Rollback mit --delete loescht sie deshalb aus dem LIVE-Baum, sobald "
        f"sie dort noch vorhanden sind (Vorfall ZERODOX#3515)."
    )
    assert backup_excludes == rollback_excludes, (
        f"Backup- und Rollback-Excludes weichen voneinander ab. "
        f"Backup: {sorted(backup_excludes)} / Rollback: {sorted(rollback_excludes)}"
    )
    # Gegenprobe: der Test selbst muss die bekannten #3447-Pfade wirklich pruefen.
    assert {".claude/worktrees", ".next"} <= backup_excludes


def test_beide_kommandos_teilen_sich_die_konstante():
    """Strukturelle Absicherung: Aufzaehlung allein kann erneut auseinanderlaufen.

    Der Laufzeittest oben belegt den heutigen Stand — er warnt aber erst,
    NACHDEM jemand die Listen wieder auseinanderlaufen liess. Dieser Test
    erzwingt, dass beide rsync-Aufrufe ihre `--exclude`-Optionen aus derselben
    Quelltext-Konstante `DEPLOY_BACKUP_EXCLUDES` bauen, statt sie erneut
    einzeln aufzuzaehlen.
    """
    code = DEPLOYMENT_MANAGER.read_text(encoding="utf-8")

    backup_start = code.index('if shutil.which("rsync"):')
    backup_end = code.index("process = await asyncio.create_subprocess_exec", backup_start)
    backup_block = code[backup_start:backup_end]

    rollback_start = code.index("async def _rollback")
    rollback_end = code.index("process = await asyncio.create_subprocess_exec", rollback_start)
    rollback_block = code[rollback_start:rollback_end]

    for name, block in (("Backup", backup_block), ("Rollback", rollback_block)):
        assert re.search(r"DEPLOY_BACKUP_EXCLUDES", block), (
            f"{name}-rsync-Aufruf baut seine --exclude-Optionen nicht aus "
            f"DEPLOY_BACKUP_EXCLUDES. Zwei getrennt aufgezaehlte Listen sind "
            f"genau der Zustand, der ZERODOX#3515 verursacht hat."
        )


def test_konstante_enthaelt_die_bekannten_3447_pfade():
    """Gegenprobe zur gemeinsamen Konstante selbst."""
    assert ".claude/worktrees" in DEPLOY_BACKUP_EXCLUDES
    assert ".next" in DEPLOY_BACKUP_EXCLUDES
