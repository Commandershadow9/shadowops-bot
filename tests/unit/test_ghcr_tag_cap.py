"""scripts/lib/ghcr-tag-cap.sh — alte GHCR-Deploy-Tags begrenzen (ZERODOX#3858).

Ein Ersatz-`docker` im PATH protokolliert jeden Aufruf; echte Images werden nie
angefasst. Geprueft wird die Invariante aus #1186: geloescht wird nur per
`docker rmi <repo>:<tag>`, nie per Image-ID und nie per `prune -a`.
"""

import os
import stat
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "ghcr-tag-cap.sh"
REPO = "ghcr.io/commandershadow9/zerodox-web"


def _fake_docker(tmp_path: Path, images: list[tuple[str, str]], in_nutzung: set[str] = frozenset()) -> Path:
    """images: (CreatedAt, Tag) in beliebiger Reihenfolge."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    liste = "\n".join(f"{c}\t{t}" for c, t in images)
    blockiert = " ".join(in_nutzung)
    script = f"""#!/usr/bin/env bash
echo "$@" >> "{tmp_path}/aufrufe.log"
case "$1" in
  images) printf '%s\\n' "{liste}" ;;
  rmi)
    tag="${{2##*:}}"
    for b in {blockiert}; do [ "$tag" = "$b" ] && exit 1; done
    exit 0 ;;
  *) exit 0 ;;
esac
"""
    docker = bindir / "docker"
    docker.write_text(script)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    return bindir


def _lauf(tmp_path: Path, bindir: Path, behalten: str = "10", trocken: str = "0") -> tuple[list[str], list[str]]:
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    out = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; ghcr_tag_cap "{REPO}" "{behalten}" "{trocken}"'],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    log = tmp_path / "aufrufe.log"
    aufrufe = log.read_text().splitlines() if log.exists() else []
    return out, aufrufe


def _images(n: int) -> list[tuple[str, str]]:
    # Tag tNN, groessere Zahl = neuer. Absichtlich ungeordnet geliefert.
    liste = [(f"2026-09-{i:02d} 10:00:00 +0200 CEST", f"t{i:02d}") for i in range(1, n + 1)]
    return liste[::2] + liste[1::2]


def test_behaelt_die_neuesten_und_entfernt_den_rest(tmp_path):
    bindir = _fake_docker(tmp_path, _images(14))
    out, aufrufe = _lauf(tmp_path, bindir, behalten="10")
    entfernt = sorted(z.split()[1] for z in out if z.startswith("entfernt "))
    assert entfernt == ["t01", "t02", "t03", "t04"]
    rmi = [a for a in aufrufe if a.startswith("rmi ")]
    assert rmi and all(a.startswith(f"rmi {REPO}:t") for a in rmi)


def test_loescht_nur_per_tag_nie_per_id_oder_prune(tmp_path):
    bindir = _fake_docker(tmp_path, _images(20))
    _, aufrufe = _lauf(tmp_path, bindir, behalten="3")
    assert not any("prune" in a for a in aufrufe)
    assert all(a.startswith(f"rmi {REPO}:") for a in aufrufe if a.startswith("rmi"))


def test_in_nutzung_bleibt_stehen_und_wird_gemeldet(tmp_path):
    bindir = _fake_docker(tmp_path, _images(12), in_nutzung={"t01"})
    out, _ = _lauf(tmp_path, bindir, behalten="10")
    assert "behalten t01 (in Nutzung)" in out
    assert "entfernt t02" in out


def test_trockenlauf_loescht_nichts(tmp_path):
    bindir = _fake_docker(tmp_path, _images(13))
    out, aufrufe = _lauf(tmp_path, bindir, behalten="10", trocken="1")
    assert sorted(out) == ["wuerde_entfernen t01", "wuerde_entfernen t02", "wuerde_entfernen t03"]
    assert not any(a.startswith("rmi") for a in aufrufe)


def test_none_tags_und_wenige_images_werden_nie_angefasst(tmp_path):
    bindir = _fake_docker(tmp_path, [("2026-09-01 10:00:00 +0200 CEST", "<none>")] + _images(5))
    out, aufrufe = _lauf(tmp_path, bindir, behalten="10")
    assert out == []
    assert not any(a.startswith("rmi") for a in aufrufe)


def test_null_behalten_wird_auf_minimum_zwei_angehoben(tmp_path):
    # Nie „alles loeschen": laufender Stand + ein Vorgaenger bleiben immer.
    bindir = _fake_docker(tmp_path, _images(12))
    out, _ = _lauf(tmp_path, bindir, behalten="0")
    assert len([z for z in out if z.startswith("entfernt ")]) == 10


def test_kein_zahlenwert_faellt_auf_standard_zehn(tmp_path):
    bindir = _fake_docker(tmp_path, _images(12))
    out, _ = _lauf(tmp_path, bindir, behalten="abc")
    assert len([z for z in out if z.startswith("entfernt ")]) == 2
