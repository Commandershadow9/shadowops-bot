"""
GitHub-Rueckmeldung fuer Deploys (ZERODOX#3638).

Bisher meldete der Bot ein Deploy-Ergebnis nur nach Discord. Sobald ein
Partner selbst in ZERODOX mergt, hat dessen KI keinen Zugriff auf den
Discord-Kanal — sie liest den PR nur ueber `gh pr view`/`gh api`. Dieses
Modul spiegelt deshalb JEDES Deploy-Ergebnis zusaetzlich nach GitHub:

- Commit-Status `zerodox/deploy` (pending/success/failure) auf dem
  ausgelieferten Commit.
- EIN sich selbst aktualisierender PR-Kommentar je Deploy (Start → Erfolg
  bzw. Abbruch ueberschreibt denselben Kommentar per PATCH statt neue
  Kommentare anzuhaeufen).

⚠️ **Fail-soft ist Pflicht.** Ein fehlendes Token, ein 4xx/5xx von GitHub
oder ein Timeout duerfen NIE den Deploy oder die Discord-Meldung stoppen —
nur `logger.warning`. Der Aufrufer (`deployment_manager.py`) faengt
zusaetzlich alles ab, was dieses Modul selbst nicht abfaengt.

⚠️ **Default-AN nur fuer zerodox.** Andere Projekte (mayday-sim,
GuildScout, ai-agent-framework) bekommen diese Rueckmeldung nur, wenn ihr
Projekt-Eintrag `github_deploy_feedback: true` explizit setzt — das ist eine
bewusste Code-Entscheidung (siehe `ist_aktiviert()`), keine Config-Vorgabe.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Dict, Optional, Tuple

import aiohttp

try:  # pragma: no cover - Python < 3.9 hat kein zoneinfo; hier immer >= 3.12
    from zoneinfo import ZoneInfo
    _BERLIN_TZ = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover
    _BERLIN_TZ = None

logger = logging.getLogger('shadowops.deployment')

# Timeout je HTTP-Aufruf. Bewusst kurz — eine haengende GitHub-API darf den
# Deploy-Abschluss nicht verzoegern (Punkt 4 der Aufgabe).
TIMEOUT_SECONDS = 10

# GitHub-Commit-Status-Context. Eigener Namensraum, damit er neben CI-Checks
# ("Web Quality" etc.) klar als eigene Quelle erkennbar ist.
STATUS_CONTEXT = "zerodox/deploy"

# EIN Kommentar je laufendem Deploy: (repo_slug, sha) -> comment_id.
# Modul-globaler State (Prozess-Speicher), weil `deploy_project()` bei jedem
# Aufruf (Start/Erfolg/Abbruch) eine neue, kurzlebige Anfrage an dieses Modul
# stellt — es gibt keine gemeinsame Instanz, an der die ID sonst haengen
# koennte. Ueberlebt keinen Bot-Neustart; ein neuer Kommentar bei einem
# Nachhol-Deploy nach Neustart ist ein akzeptabler Nebeneffekt (kein
# Datenverlust, nur ein zweiter Kommentar statt eines aktualisierten).
_COMMENT_IDS: Dict[Tuple[str, str], int] = {}

_MARKER_PREFIX = "<!-- zerodox-deploy-status sha="


def _marker(sha: str) -> str:
    return f"{_MARKER_PREFIX}{sha} -->"


def ist_aktiviert(project: Optional[Dict]) -> bool:
    """Ist die GitHub-Rueckmeldung fuer dieses Projekt an?

    Default AN nur fuer 'zerodox' (Projekt-Config-Key, nicht Anzeigename) —
    bewusst im Code verankert, nicht in `config.yaml`. Ein expliziter
    `github_deploy_feedback`-Schluessel im Projekt-Eintrag ueberstimmt den
    Default in beide Richtungen.
    """
    if not project:
        return False
    flag = project.get('github_deploy_feedback')
    if flag is not None:
        return bool(flag)
    return str(project.get('name', '')).strip().lower() == 'zerodox'


def _parse_repo_slug(repo_url: Optional[str]) -> Optional[str]:
    """Extrahiert 'owner/repo' aus einer GitHub-Repo-URL (https:// oder git@)."""
    if not repo_url:
        return None
    url = repo_url.strip().rstrip('/')
    if url.endswith('.git'):
        url = url[:-4]
    match = re.search(r'github\.com[/:]([^/]+/[^/]+)$', url)
    return match.group(1) if match else None


def _kurze_beschreibung(text: str, limit: int = 140) -> str:
    """GitHub-Status-`description` ist auf 140 Zeichen begrenzt."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _jetzt_berlin() -> datetime:
    if _BERLIN_TZ is not None:
        return datetime.now(_BERLIN_TZ)
    return datetime.now()


def _rollback_zeile(result: Optional[Dict], is_exception: bool) -> Tuple[str, bool]:
    """Bestimmt den Rollback-Text und ob '@Commandershadow9' noetig ist.

    Rueckgabe: (Zeile, mention_noetig).

    Ableitung ausschliesslich aus `result['backup_created']` /
    `result['rolled_back']` — keine Fehlertext-Heuristik:
    - kein Backup erstellt  → nichts wurde ausgeliefert, alte Version laeuft
      weiter (Abbruch kam VOR dem Deploy-Schritt).
    - Backup + Rollback ok  → alte Version laeuft wieder.
    - Backup + KEIN Rollback → Rollback wurde versucht und ist gescheitert
      (der einzige Zweig, der `rolled_back` nicht setzt, obwohl ein Backup
      existiert — siehe `deploy_project()`s `except DeploymentError`-Ast).
    """
    if is_exception or result is None:
        # Kein `result`-Dict verfuegbar (z.B. `_send_deployment_exception`,
        # aktuell toter Code ohne Aufrufer) — konservativ als unklar behandeln
        # und den Betreiber informieren, statt Erfolg vorzutaeuschen.
        return ("⚠️ nichts ausgeliefert, alte Version läuft weiter", True)

    if result.get('rolled_back'):
        return ("✅ alte Version läuft weiter", False)
    if not result.get('backup_created'):
        return ("⚠️ nichts ausgeliefert, alte Version läuft weiter", False)
    return ("❌ Rollback fehlgeschlagen — Betreiber informieren", True)


async def _http_call(coro_factory):
    """Fuehrt einen HTTP-Aufruf fail-soft aus: Fehler landen nur im Log."""
    try:
        return await coro_factory()
    except Exception as exc:
        logger.warning(f"⚠️ GitHub-Deploy-Rueckmeldung: HTTP-Aufruf fehlgeschlagen: {exc}")
        return None


async def _set_commit_status(
    session: aiohttp.ClientSession,
    repo_slug: str,
    sha: str,
    state: str,
    description: str,
    target_url: Optional[str] = None,
) -> None:
    payload = {
        "state": state,
        "context": STATUS_CONTEXT,
        "description": _kurze_beschreibung(description),
    }
    if target_url:
        payload["target_url"] = target_url
    url = f"https://api.github.com/repos/{repo_slug}/statuses/{sha}"

    async def _call():
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        ) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                logger.warning(
                    f"⚠️ Commit-Status ({state}) fuer {repo_slug}@{sha[:7]} "
                    f"fehlgeschlagen ({resp.status}): {body[:300]}"
                )
        return None

    await _http_call(_call)


async def _upsert_pr_comment(
    session: aiohttp.ClientSession,
    repo_slug: str,
    pr_number: int,
    sha: str,
    body: str,
) -> None:
    """Legt den Deploy-Kommentar an oder aktualisiert den bestehenden.

    EIN Kommentar je (repo_slug, sha): Die ID wird nach dem ersten
    erfolgreichen POST im Prozess-Speicher gehalten. Schlaegt ein PATCH auf
    eine gemerkte ID fehl (z.B. Kommentar zwischenzeitlich geloescht), wird
    die ID verworfen und ein neuer Kommentar angelegt — kein Deploy-Ergebnis
    geht dadurch verloren.
    """
    key = (repo_slug, sha)
    full_body = f"{_marker(sha)}\n{body}"
    comment_id = _COMMENT_IDS.get(key)

    if comment_id is not None:
        patch_url = f"https://api.github.com/repos/{repo_slug}/issues/comments/{comment_id}"

        async def _patch():
            async with session.patch(
                patch_url,
                json={"body": full_body},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            ) as resp:
                if resp.status == 200:
                    return True
                response_body = await resp.text()
                logger.warning(
                    f"⚠️ PR-Kommentar-Update fuer {repo_slug}#{pr_number} "
                    f"fehlgeschlagen ({resp.status}): {response_body[:300]}"
                )
                return False

        patched = await _http_call(_patch)
        if patched:
            return
        # PATCH ist gescheitert (falsche/veraltete ID oder HTTP-Fehler) —
        # verwerfen und unten neu anlegen.
        _COMMENT_IDS.pop(key, None)

    post_url = f"https://api.github.com/repos/{repo_slug}/issues/{pr_number}/comments"

    async def _post():
        async with session.post(
            post_url,
            json={"body": full_body},
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
        ) as resp:
            if resp.status not in (200, 201):
                response_body = await resp.text()
                logger.warning(
                    f"⚠️ PR-Kommentar fuer {repo_slug}#{pr_number} "
                    f"fehlgeschlagen ({resp.status}): {response_body[:300]}"
                )
                return None
            data = await resp.json()
            return data.get("id")

    new_id = await _http_call(_post)
    if new_id:
        _COMMENT_IDS[key] = int(new_id)


def _text_started(sha7: str) -> str:
    return (
        f"🚀 **Deploy läuft** — Stand `{sha7}` wird gerade geprüft und "
        "ausgeliefert. Dauer meist 7–25 Minuten. Nichts tun, dieser "
        "Kommentar wird aktualisiert."
    )


def _text_success(sha7: str, duration_min: float, health_check_url: Optional[str]) -> str:
    now = _jetzt_berlin().strftime("%H:%M")
    zeile = (
        f"✅ **Live** — Stand `{sha7}` ist seit {now} Uhr ausgeliefert "
        f"(Dauer {duration_min:.0f} min)."
    )
    if health_check_url:
        zeile += f"\nNachprüfen: {health_check_url} → `buildSha`."
    return zeile


def _text_failure(
    *,
    sha7: str,
    phase: str,
    ursache: str,
    rollback_zeile: str,
    mention_noetig: bool,
    technische_details: Optional[str],
) -> str:
    zeilen = [
        f"❌ **Deploy abgebrochen** — Phase: {phase} · Ursache: {ursache} · "
        f"Rollback: {rollback_zeile}",
        "",
        "**Was jetzt?**",
    ]
    if "alte Version läuft weiter" in rollback_zeile or "wieder" in rollback_zeile:
        zeilen.append(
            "Die Seite läuft mit dem vorherigen Stand weiter — für Kundinnen "
            "und Kunden hat sich nichts verändert."
        )
    zeilen.append(f"Ursache in einem neuen PR beheben (betroffener Stand: `{sha7}`).")
    if mention_noetig:
        zeilen.append("@Commandershadow9 bitte informieren — Rollback-Zustand unklar bzw. fehlgeschlagen.")
    if technische_details:
        gekuerzt = technische_details.strip()
        if len(gekuerzt) > 1500:
            gekuerzt = gekuerzt[:1497] + "…"
        zeilen.append("")
        zeilen.append("<details><summary>Technische Details</summary>\n\n```\n" + gekuerzt + "\n```\n</details>")
    return "\n".join(zeilen)


async def report(
    bot,
    project: Optional[Dict],
    *,
    phase: str,
    deploy_context: Optional[Dict] = None,
    result: Optional[Dict] = None,
    duration: Optional[float] = None,
    is_exception: bool = False,
) -> None:
    """Einstiegspunkt: meldet eine Deploy-Phase zusaetzlich nach GitHub.

    Args:
        bot: Discord-Bot-Instanz (liefert `bot.config.github_token`).
        project: Aufgeloester Projekt-Eintrag aus
            `DeploymentManager.projects` (traegt `name`, `repo_url`,
            `health_check_url`, `github_deploy_feedback`).
        phase: "started" | "success" | "failure".
        deploy_context: Deploy-Kontext mit `commit_sha`, `pr_number`,
            `repo_url` (siehe `ci_mixin._trigger_deployment`).
        result: `deploy_project()`-Ergebnis-Dict (fuer success/failure).
        duration: Dauer in Sekunden (fuer success/failure).
        is_exception: True fuer den unerwarteten-Fehler-Pfad
            (`except Exception` in `deploy_project`) — steuert die
            `@Commandershadow9`-Erwaehnung zusaetzlich zum Rollback-Zustand.
    """
    if not ist_aktiviert(project):
        return

    deploy_context = deploy_context or {}
    if result is not None:
        # `result['deploy_context']` ist die vollstaendigere Quelle (traegt
        # denselben Kontext, der beim Start gesetzt wurde) — deploy_context
        # als expliziter Parameter bleibt fuer den Start-Aufruf nuetzlich,
        # der noch kein `result` hat.
        deploy_context = {**(result.get('deploy_context') or {}), **deploy_context}

    sha = str(deploy_context.get('commit_sha') or '').strip()
    if not sha:
        # Ohne SHA gibt es nichts, wogegen ein Commit-Status oder ein PR-
        # Kommentar sich verankern liesse — kein Fehler, nur nichts zu tun
        # (z.B. manuell ausgeloester Deploy ohne Merge-Kontext).
        return

    repo_url = str(deploy_context.get('repo_url') or (project or {}).get('repo_url') or '').strip()
    repo_slug = _parse_repo_slug(repo_url)
    if not repo_slug:
        logger.warning(
            f"⚠️ GitHub-Deploy-Rueckmeldung: repo_url fehlt/ungueltig fuer "
            f"'{(project or {}).get('name')}' — uebersprungen."
        )
        return

    token = _get_token(bot)
    if not token:
        logger.warning(
            "⚠️ GitHub-Deploy-Rueckmeldung: kein GitHub-Token konfiguriert — uebersprungen."
        )
        return

    sha7 = sha[:7]
    pr_number = deploy_context.get('pr_number')
    health_check_url = (project or {}).get('health_check_url') or None

    if phase == 'started':
        status_state, status_desc, comment_body = (
            "pending",
            "Deploy läuft — Prüfung und Auslieferung.",
            _text_started(sha7),
        )
    elif phase == 'success':
        duration_min = (duration or 0.0) / 60.0
        now = _jetzt_berlin().strftime("%H:%M")
        status_state, status_desc, comment_body = (
            "success",
            f"Live seit {now} · {duration_min:.0f} min",
            _text_success(sha7, duration_min, health_check_url),
        )
    elif phase == 'failure':
        result = result or {}
        phase_label = str(result.get('failed_stage') or 'Deploy').strip()
        ursache_lang = str(result.get('error') or 'Kein Fehlergrund gemeldet.')
        ursache_kurz = _kurze_beschreibung(ursache_lang.splitlines()[-1] if ursache_lang else '', limit=100)
        rollback_zeile, mention_noetig = _rollback_zeile(result, is_exception)
        status_state, status_desc, comment_body = (
            "failure",
            f"Abgebrochen: {phase_label} · Rollback: {rollback_zeile.split(' ', 1)[0]}",
            _text_failure(
                sha7=sha7,
                phase=phase_label,
                ursache=ursache_kurz or 'Kein Fehlergrund gemeldet.',
                rollback_zeile=rollback_zeile,
                mention_noetig=mention_noetig,
                technische_details=ursache_lang,
            ),
        )
    else:  # pragma: no cover - defensiv, sollte nicht erreichbar sein
        logger.warning(f"⚠️ GitHub-Deploy-Rueckmeldung: unbekannte Phase '{phase}'")
        return

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
    }

    async def _run():
        async with aiohttp.ClientSession(headers=headers) as session:
            await _set_commit_status(session, repo_slug, sha, status_state, status_desc)
            if pr_number:
                await _upsert_pr_comment(session, repo_slug, int(pr_number), sha, comment_body)

    await _http_call(_run)


def _get_token(bot) -> Optional[str]:
    """Liest das GitHub-Token wie `webhook_mixin._get_github_token`.

    Eigene, schlanke Kopie statt Import aus `WebhookMixin`: Dieses Modul
    braucht ausschliesslich die Token-Aufloesung, nicht die restliche Mixin-
    Funktionalitaet (Webhook-Anlage etc.) — ein Import wuerde nur unnoetig
    an die `GitHubIntegration`-Instanz koppeln, die zum Zeitpunkt eines
    Deploys nicht zwingend dieselbe ist wie die des Config-Objekts.
    """
    import os

    env_token = os.getenv('GITHUB_TOKEN') or os.getenv('GH_TOKEN')
    if env_token:
        return env_token
    config = getattr(bot, 'config', None)
    if config is None:
        return None
    try:
        token = getattr(config, 'github_token', None)
        if token:
            return token
    except Exception:
        pass
    if isinstance(config, dict):
        return config.get('github', {}).get('token')
    return None
