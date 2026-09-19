"""Das Deploy-Backup sichert Code, nicht Build-Output und Arbeitskopien (ZERODOX#3447).

Gemessen am 19.09.2026 an einem echten Backup (zerodox_20260918_145640, 22 GB):

    18   GB  .claude/worktrees/  Arbeitskopien paralleler Claude-Sessions
     4,1 GB  web/.next/          Build-Output, den der Deploy neu erzeugt
    ~0,2 GB                      alles Uebrige — der Code, also der einzige
                                 Grund, aus dem dieses Backup existiert

Mehr als 99 % des Backups waren damit reproduzierbar oder fluechtig. Die Kosten
trug jeder Deploy: 5m28s von 10m46s Gesamtzeit entfielen auf das Backup, fuenf
Staende je Projekt belegten 112 GB, und bei rund 21 Merges am Tag schrieb der
Bot etwa 460 GB taeglich auf die NVMe.

Dazu kam ein Ausfall: Am 18.09.2026 um 12:56 brach ein ZERODOX-Deploy ab, weil
eine parallele Session ihren Worktree waehrend des Backups entfernte
("directory has vanished"). rsync meldet dafuer Exit 24, und der galt als
Fehler.

Geprueft wird der QUELLTEXT. Ein Laufzeittest muesste ein echtes rsync gegen
einen echten Baum fahren, um dasselbe auszusagen — und der teure Teil (dass die
Ausschluesse auch WIRKLICH in der Kommandozeile landen) waere dabei genau der,
den eine Attrappe verdeckt.
"""
import re
from pathlib import Path

DEPLOYMENT_MANAGER = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "integrations"
    / "deployment_manager.py"
)


def _quelle() -> str:
    return DEPLOYMENT_MANAGER.read_text(encoding="utf-8")


def _ohne_kommentare(text: str) -> str:
    """Kommentare zu Leerzeichen, damit Zeilennummern erhalten bleiben.

    Ohne das faende dieser Test seine eigene Begruendung: Die Kommentare im
    Backup-Block nennen jeden Ausschluss beim Namen, und eine Pruefung, die das
    mitzaehlt, bliebe gruen, nachdem jemand die Option geloescht und die
    Erklaerung stehen gelassen hat.
    """
    return re.sub(r"(^|\s)#.*$", lambda m: m.group(0).replace("#", " "), text, flags=re.M)


def test_worktrees_sind_ausgeschlossen():
    code = _ohne_kommentare(_quelle())
    assert "'--exclude=.claude/worktrees'" in code, (
        "Der rsync-Aufruf schliesst .claude/worktrees nicht aus. Diese "
        "Arbeitskopien machten 18 der 22 GB eines ZERODOX-Backups aus und "
        "haben keinen Sicherungswert — sie liegen als Branch auf GitHub."
    )


def test_ausschluss_traegt_den_pfad_nicht_nur_den_namen():
    """`--exclude=worktrees` allein waere zu breit.

    Im ZERODOX-Arbeitsbaum liegt neben `.claude/worktrees/` ein
    unversioniertes `worktrees/` mit anderem Inhalt. Ein blosser Basisname
    schluesse beide aus — und das zweite gehoert moeglicherweise gesichert.
    """
    code = _ohne_kommentare(_quelle())
    assert "'--exclude=worktrees'" not in code, (
        "Ausschluss per Basisname gefunden. rsync wendet den auf JEDES "
        "Verzeichnis dieses Namens an, auch auf das unversionierte "
        "worktrees/ im Repo-Wurzelverzeichnis."
    )


def test_build_output_ist_ausgeschlossen():
    code = _ohne_kommentare(_quelle())
    assert "'--exclude=.next'" in code, (
        "Der rsync-Aufruf schliesst web/.next nicht aus — 4,1 GB Build-Output, "
        "den jeder Deploy ohnehin neu erzeugt."
    )


def test_python_fallback_kennt_den_build_output_auch():
    """Der Zweig ohne rsync darf nicht stillschweigend das Alte tun."""
    code = _ohne_kommentare(_quelle())
    treffer = re.search(r"ignore\s*=\s*shutil\.ignore_patterns\((.*?)\)", code, re.S)
    assert treffer, "shutil.ignore_patterns nicht gefunden — Aufbau geaendert?"
    assert '".next"' in treffer.group(1), (
        "Der Python-Fallback kennt .next nicht. Er laeuft zwar nur ohne rsync, "
        "aber ein Backup, das je nach Host anderes sichert, ist schwerer zu "
        "beurteilen als eines, das immer dasselbe tut."
    )


def test_verschwundene_dateien_brechen_den_deploy_nicht_ab():
    """rsync 24 ist kein Fehlschlag.

    Eine Datei, die es beim Kopieren nicht mehr gibt, ist per Definition keine,
    die gesichert werden musste. Am 18.09.2026 kostete die Gegenauffassung
    einen kompletten Deploy.
    """
    code = _ohne_kommentare(_quelle())
    treffer = re.search(r"returncode not in \(([^)]*)\)", code)
    assert treffer, "Die Pruefung des rsync-Rueckgabewerts wurde umgebaut."
    toleriert = {t.strip() for t in treffer.group(1).split(",") if t.strip()}
    assert "24" in toleriert, (
        f"rsync-Exit 24 gilt weiterhin als Fehler (toleriert: {sorted(toleriert)}). "
        "Ein Deploy bricht dann ab, sobald waehrend des mehrminuetigen Backups "
        "irgendeine Datei verschwindet — etwa ein Worktree oder eine Logdatei."
    )
    assert "23" in toleriert, (
        "Exit 23 (partial transfer) war vorher toleriert und muss es bleiben — "
        "Docker-Container-Dateien sind fuer den Bot nicht lesbar."
    )


def test_echter_fehler_bleibt_ein_fehler():
    """Gegenprobe: Die Liste darf nicht zur Blankovollmacht werden."""
    code = _ohne_kommentare(_quelle())
    treffer = re.search(r"returncode not in \(([^)]*)\)", code)
    toleriert = {t.strip() for t in treffer.group(1).split(",") if t.strip()}
    # 11 = Fehler beim Schreiben, 12 = Protokollfehler, 30 = Timeout.
    # Wer die hier aufnaehme, machte aus dem Backup eine Behauptung.
    for schwer in ("11", "12", "30"):
        assert schwer not in toleriert, (
            f"rsync-Exit {schwer} wird toleriert — das sind echte "
            "Uebertragungsfehler, nach denen kein Backup existiert."
        )
    assert "DeploymentError" in code, "Der Fehlerpfad wurde entfernt."
