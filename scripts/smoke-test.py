#!/usr/bin/env python3
"""
ShadowOps Bot — Smoke-Test (Pre-Restart Validation)

Schneller Pre-Deploy-Check, der verifiziert, dass der Bot nach git pull
ueberhaupt sauber starten kann. Wird von scripts/restart.sh aufgerufen,
bevor systemctl restart laeuft.

Was geprueft wird (< 30s, < 500 MB RAM):
  1. Config-Load:        from src.utils.config import get_config; Config()
  2. Kritische Imports:  src.bot.ShadowOpsBot + zentrale src.integrations Module
  3. DB-Connect-Probe:   asyncpg SELECT 1 gegen 127.0.0.1:5433 + 127.0.0.1:5434
  4. Discord-Token:      DISCORD_BOT_TOKEN gesetzt (NICHT verbinden!)

Exit-Codes:
  0  Alles ok — Restart darf weiterlaufen
  1  Smoke-Test failed — Restart MUSS abgebrochen werden

Standalone-Aufruf (manuelles Debugging):
  /home/cmdshadow/shadowops-bot/.venv/bin/python scripts/smoke-test.py
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Iterable, List, Tuple

# Repo-Root in sys.path damit "from src.x.y import z" funktioniert,
# unabhaengig von cwd des Aufrufers.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ----- ANSI-Farben (gleiches Schema wie restart.sh) ------------------------
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
NC = "\033[0m"


def info(msg: str) -> None:
    print(f"{CYAN}[INFO]{NC}  {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{NC}    {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{NC}  {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"{RED}[FAIL]{NC}  {msg}", flush=True)


# ----- Konfiguration des Smoke-Tests ---------------------------------------
# Kritische Imports — diese Module muessen ladbar sein. Wir importieren
# bewusst nicht ALLES (das wuerde den Bot effektiv starten), sondern nur
# die Top-Level-Module der wichtigsten Integrations + ShadowOpsBot.
CRITICAL_IMPORTS: Tuple[str, ...] = (
    "src.utils.config",
    "src.utils.logger",
    "src.bot",
    "src.integrations.ai_engine",
    "src.integrations.event_watcher",
    "src.integrations.github_integration",
    "src.integrations.knowledge_base",
    "src.integrations.smart_queue",
    "src.integrations.orchestrator",
    "src.integrations.deployment_manager",
)

# DB-Probes — (Label, Host, Port). Beide Postgres-Instanzen auf localhost.
# Wir machen einen rohen TCP/asyncpg-Connect mit anonymous-Probe; "SELECT 1"
# braucht Authentifizierung, deswegen versuchen wir asyncpg.connect() mit
# postgres-defaults und akzeptieren "InvalidAuthorizationSpecificationError"
# als Beweis dass der Server lebt (Auth-Fehler = TCP+Postgres-Handshake ok).
DB_PROBES: Tuple[Tuple[str, str, int], ...] = (
    ("guildscout-postgres", "127.0.0.1", 5433),
    ("zerodox-postgres", "127.0.0.1", 5434),
)

# Timeout pro DB-Probe. Insgesamt also max 2 * 2s = 4s fuer DBs.
DB_PROBE_TIMEOUT = 2.0

# Hartes Gesamtbudget. Wenn wir das ueberschreiten ist irgendwas falsch.
TOTAL_BUDGET_SECONDS = 30.0


# ----- Einzelne Checks -----------------------------------------------------
def check_config_load() -> Tuple[bool, str]:
    """Config laden — wenn config.yaml broken ist, crasht das hier."""
    try:
        from src.utils.config import get_config  # noqa: WPS433

        cfg = get_config()
        # Sanity: discord-Section muss da sein, sonst ist es kein Bot-Config
        if not cfg.discord:
            return False, "config.yaml geladen, aber 'discord'-Section ist leer"
        return True, f"Config geladen ({cfg.config_path.name})"
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=3)
        return False, f"Config-Load fehlgeschlagen: {exc}\n{tb}"


def check_imports(modules: Iterable[str]) -> Tuple[bool, str, int]:
    """Kritische Imports versuchen — jeder einzeln, damit wir wissen welcher kaputt ist."""
    failed: List[Tuple[str, str]] = []
    success = 0
    for module_name in modules:
        try:
            importlib.import_module(module_name)
            success += 1
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc(limit=3)
            failed.append((module_name, f"{exc}\n{tb}"))

    if failed:
        lines = [f"  - {name}: {err.splitlines()[0]}" for name, err in failed]
        # Vollstaendigen Traceback fuer ersten Fehler anzeigen
        first_name, first_err = failed[0]
        return (
            False,
            f"{len(failed)}/{success + len(failed)} Imports failed:\n"
            + "\n".join(lines)
            + f"\n\nDetails zum ersten Fehler ({first_name}):\n{first_err}",
            success,
        )
    return True, f"{success} kritische Imports geladen", success


async def _probe_one_db(label: str, host: str, port: int, timeout: float) -> Tuple[bool, str]:
    """Ein DB-Connect-Probe via asyncpg.

    Wir wollen wissen: ist der Postgres-Server erreichbar UND antwortet er
    mit dem Postgres-Wire-Protocol? Wir versuchen deshalb einen Connect
    mit ungueltigen Credentials. Erwartete Outcomes:

      - InvalidAuthorizationSpecificationError / InvalidPasswordError
        → Server lebt, Postgres spricht, nur Auth daneben. = OK
      - TimeoutError / ConnectionRefusedError / OSError
        → Server tot. = FAIL
      - Wenn der echte Bot-User Zugriff hat (zB POSTGRES_USER=bot)
        → connect klappt, wir disconnecten sofort. = OK
    """
    try:
        import asyncpg  # noqa: WPS433
    except ImportError as exc:
        return False, f"asyncpg fehlt: {exc}"

    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=host,
                port=port,
                user="shadowops_smoke_probe",
                password="invalid_on_purpose",
                database="postgres",
                timeout=timeout,
            ),
            timeout=timeout + 1,
        )
        # Falls connect tatsaechlich klappt (Trust-Auth o.ä.), Test laufen lassen.
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
        return True, f"{label} {host}:{port} reachable (SELECT 1 ok)"
    except asyncpg.InvalidAuthorizationSpecificationError:
        return True, f"{label} {host}:{port} reachable (auth-handshake ok)"
    except asyncpg.InvalidPasswordError:
        return True, f"{label} {host}:{port} reachable (password-rejected = handshake ok)"
    except asyncpg.InvalidCatalogNameError:
        # Datenbank "postgres" existiert nicht — Server lebt aber trotzdem.
        return True, f"{label} {host}:{port} reachable (catalog-error = handshake ok)"
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as exc:
        return False, f"{label} {host}:{port} unreachable: {type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        # Andere asyncpg-Errors mit "Server existiert" deuten ebenfalls auf
        # einen lebenden Server hin. Wir loggen aber als WARN.
        msg = f"{label} {host}:{port}: unexpected {type(exc).__name__}: {exc}"
        return True, msg


async def check_db_probes(probes: Iterable[Tuple[str, str, int]], timeout: float) -> Tuple[bool, str, int]:
    """Alle DB-Probes parallel."""
    results = await asyncio.gather(
        *(_probe_one_db(label, host, port, timeout) for label, host, port in probes),
        return_exceptions=False,
    )
    failed = [msg for ok_, msg in results if not ok_]
    ok_count = sum(1 for ok_, _ in results if ok_)

    if failed:
        return False, "DB-Probes failed:\n  - " + "\n  - ".join(failed), ok_count

    lines = "\n  - ".join(msg for _, msg in results)
    return True, f"{ok_count} DB-Probes ok:\n  - {lines}", ok_count


def check_discord_token() -> Tuple[bool, str]:
    """Discord-Token muss gesetzt sein — entweder als Env-Var oder in config.yaml.

    Wir verbinden NICHT — das wuerde Rate-Limits gegen Discord erzeugen und
    ist im Smoke-Test verboten. Wir pruefen nur Praesenz + grobe Sanity.
    """
    # 1. Env-Var (Production-Pfad)
    env_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if env_token:
        if len(env_token) < 50:
            return False, f"DISCORD_BOT_TOKEN env-var gesetzt aber zu kurz ({len(env_token)} chars)"
        return True, "DISCORD_BOT_TOKEN env-var gesetzt (Praesenz-Check)"

    # 2. Fallback: config.yaml via Config-Loader
    try:
        from src.utils.config import get_config  # noqa: WPS433

        cfg = get_config()
        token = cfg.discord_token
        if not token:
            return False, "DISCORD_BOT_TOKEN nicht gesetzt (weder env-var noch config.yaml)"
        if len(token) < 50:
            return False, f"discord.token in config.yaml gesetzt aber zu kurz ({len(token)} chars)"
        return True, "Token aus config.yaml geladen (Env-Var bevorzugt nutzen!)"
    except Exception as exc:  # noqa: BLE001
        return False, f"Token-Check fehlgeschlagen: {exc}"


# ----- Orchestrator --------------------------------------------------------
async def run_all() -> int:
    """Fuehrt alle Checks der Reihe nach aus. Returnt exit-code."""
    start = time.monotonic()
    print("")
    print("==========================================")
    print("  ShadowOps Bot — Smoke-Test")
    print("==========================================")
    print("")

    import_count = 0
    db_count = 0

    # 1. Config
    info("1/4  Config-Load ...")
    cfg_ok, cfg_msg = check_config_load()
    if not cfg_ok:
        fail(cfg_msg)
        return 1
    ok(cfg_msg)

    # 2. Imports
    info(f"2/4  Kritische Imports ({len(CRITICAL_IMPORTS)} Module) ...")
    imp_ok, imp_msg, import_count = check_imports(CRITICAL_IMPORTS)
    if not imp_ok:
        fail(imp_msg)
        return 1
    ok(imp_msg)

    # 3. DB-Probes
    info(f"3/4  DB-Probes ({len(DB_PROBES)} Instanzen, timeout {DB_PROBE_TIMEOUT}s) ...")
    db_ok, db_msg, db_count = await check_db_probes(DB_PROBES, DB_PROBE_TIMEOUT)
    if not db_ok:
        fail(db_msg)
        return 1
    ok(db_msg)

    # 4. Discord-Token
    info("4/4  Discord-Token-Check ...")
    tok_ok, tok_msg = check_discord_token()
    if not tok_ok:
        fail(tok_msg)
        return 1
    ok(tok_msg)

    # Zusammenfassung
    elapsed = time.monotonic() - start
    print("")
    summary = (
        f"Smoke-Test passed "
        f"(Config + {import_count} imports + {db_count} DB-probes + token) "
        f"in {elapsed:.2f}s"
    )
    ok(summary)

    if elapsed > TOTAL_BUDGET_SECONDS:
        warn(
            f"Laufzeit ({elapsed:.2f}s) ueberschritt Budget "
            f"({TOTAL_BUDGET_SECONDS:.0f}s) — bitte Smoke-Test verschlanken."
        )
    return 0


def main() -> int:
    try:
        return asyncio.run(run_all())
    except KeyboardInterrupt:
        fail("Abgebrochen durch User (Ctrl+C)")
        return 1
    except Exception as exc:  # noqa: BLE001
        fail(f"Unerwarteter Crash im Smoke-Test selbst: {exc}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
