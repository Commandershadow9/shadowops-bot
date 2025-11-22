"""
Auto-Fix Manager

Steuert den Reaction-basierten Flow für Code-Fix-Vorschläge, Testläufe und (zukünftig) Draft-PRs.
Aktuell: postet Vorschläge, verarbeitet Reaktionen (✅ umsetzen, 🧪 nur Tests/Analyse, ❌ verwerfen),
führt definierte Tests/Lint-Befehle aus und berichtet die Ergebnisse in Discord.

Safety:
- Keine Commits/Merges/Deploys.
- Pfad-Whitelist: nutzt Projektpfade aus Config.
- Zeitlimit pro Command.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import discord
import httpx


logger = logging.getLogger("shadowops.auto_fix")


PROPOSAL_FILE = Path(__file__).parent.parent / "data" / "auto_fix_proposals.json"


@dataclass
class FixProposal:
    project: str
    summary: str
    actions: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    suggested_tests: List[str] = field(default_factory=list)
    severity: str = "medium"
    message_id: Optional[int] = None
    channel_id: Optional[int] = None
    author_id: Optional[int] = None


class AutoFixManager:
    def __init__(self, config=None, ai_service=None):
        self.config = config
        self.ai_service = ai_service
        self.proposals: Dict[int, FixProposal] = {}  # message_id -> proposal
        self.channel_id = None
        self._load_state()
        self.max_patch_size = 12000  # chars safeguard
        # Per-Project Profile (Tests/Lint)
        self.project_profiles = {
            "shadowops-bot": {
                "tests": ["pytest"],
                "lint": []
            },
            "guildscout": {
                "tests": ["pytest"],
                "lint": []
            },
            "sicherheitsdiensttool": {
                "tests": ["npm test -- --runInBand"],
                "lint": ["npm run lint"]
            },
            "nexus-api": {
                "tests": ["npm test -- --runInBand"],
                "lint": ["npm run lint"]
            }
        }

    async def ensure_channels(self, bot):
        """
        Sicherstellen, dass es einen Channel für Code-Scans gibt.
        Nutzt auto_create_channels Mechanik aus bot.setup; falls nicht angelegt, versucht hier eine minimale Prüfung.
        """
        # Wenn Channel schon in Config, übernehmen
        cid = self.config.channels.get("ai_code_scans")
        if cid:
            self.channel_id = cid
            return

        guild = bot.get_guild(self.config.guild_id)
        if not guild:
            return

        # Versuche bestehenden Channel per Name zu finden
        channel = discord.utils.get(guild.text_channels, name="🔎-ai-code-scans")
        if channel:
            self.channel_id = channel.id
            return

        # Minimaler Fallback: erstelle Channel in Auto-Remediation Kategorie, falls erlaubt
        try:
            category = discord.utils.get(guild.categories, name="🤖 Auto-Remediation")
            if not category:
                category = await guild.create_category("🤖 Auto-Remediation", reason="Auto-Fix Manager Setup")

            new_channel = await guild.create_text_channel(
                name="🔎-ai-code-scans",
                topic="Auto-Fix Vorschläge & Status (Reaction-basiert)",
                category=category,
                reason="Auto-Fix Manager Setup"
            )
            self.channel_id = new_channel.id
        except Exception as e:
            logger.warning(f"Konnte ai_code_scans Channel nicht erstellen: {e}")

    def _load_state(self):
        try:
            if PROPOSAL_FILE.exists():
                data = json.loads(PROPOSAL_FILE.read_text(encoding="utf-8"))
                for item in data:
                    p = FixProposal(**item)
                    if p.message_id:
                        self.proposals[p.message_id] = p
        except Exception as e:
            logger.debug(f"Could not load proposals: {e}")

    def _save_state(self):
        try:
            PROPOSAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = [vars(p) for p in self.proposals.values()]
            PROPOSAL_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Could not save proposals: {e}")

    def _get_project_path(self, project_name: str) -> Optional[Path]:
        # Default: shadowops-bot root
        if project_name == "shadowops-bot":
            return Path(__file__).parent.parent.parent
        # Config projects
        if self.config and hasattr(self.config, "projects"):
            proj = self.config.projects.get(project_name)
            if proj and proj.get("path"):
                return Path(proj["path"])
        return None

    def _default_tests_for_project(self, project_name: str, project_path: Path) -> List[str]:
        profile = self.project_profiles.get(project_name, {})
        cmds = profile.get("tests", []).copy()
        lint_cmds = profile.get("lint", [])
        # If package.json exists but no test command in profile, fallback to npm test
        if not cmds and (project_path / "package.json").exists():
            cmds.append("npm test -- --runInBand")
        # pytest fallback if tests folder exists
        if not cmds and (project_path / "tests").exists():
            cmds.append("pytest")
        # add lint if script exists and not already in profile
        if (project_path / "package.json").exists():
            lint_script = self._npm_has_script(project_path, "lint")
            if lint_script and lint_cmds:
                cmds.extend(lint_cmds)
            elif lint_script and not lint_cmds:
                cmds.append("npm run lint")
        # If profile defines lint for non-npm projects, include
        if lint_cmds and not (project_path / "package.json").exists():
            cmds.extend(lint_cmds)
        return cmds or ["echo 'No tests configured'"]

    def _npm_has_script(self, project_path: Path, script: str) -> bool:
        pkg = project_path / "package.json"
        if not pkg.exists():
            return False
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
            return script in scripts
        except Exception:
            return False

    async def post_proposal(self, bot, proposal: FixProposal):
        """Postet einen Vorschlag in den Code-Scan-Channel mit Reaktionen."""
        if not self.channel_id:
            await self.ensure_channels(bot)
        channel = bot.get_channel(self.channel_id) if self.channel_id else None
        if not channel:
            logger.warning("Kein ai_code_scans Channel gefunden")
            return

        # Ergänze Standard-Tests, falls keine angegeben
        project_path = self._get_project_path(proposal.project)
        default_tests = self._default_tests_for_project(proposal.project, project_path) if project_path else []
        tests_list = proposal.tests or []
        if not tests_list:
            tests_list = default_tests
        all_tests = tests_list.copy()
        if proposal.suggested_tests:
            all_tests += [f"(KI empfohlen) {t}" for t in proposal.suggested_tests]
        if not all_tests:
            all_tests = ["Keine Tests definiert/gefunden"]

        embed = discord.Embed(
            title=f"🔎 Fix-Vorschlag: {proposal.project}",
            description=proposal.summary,
            color=0x3498DB
        )
        if proposal.actions:
            embed.add_field(
                name="Geplante Actions",
                value="\n".join([f"• {a}" for a in proposal.actions])[:1024],
                inline=False
            )
        else:
            embed.add_field(name="Geplante Actions", value="Keine spezifischen Actions erkannt", inline=False)
        if all_tests:
            embed.add_field(
                name="Geplante Tests",
                value="\n".join([f"• {t}" for t in all_tests])[:1024],
                inline=False
            )
        embed.set_footer(text=f"Reagiere mit ✅ (umsetzen), 🧪 (nur Tests), ❌ (verwerfen)")

        msg = await channel.send(embed=embed)
        for emoji in ["✅", "🧪", "❌"]:
            try:
                await msg.add_reaction(emoji)
            except Exception:
                pass

        proposal.message_id = msg.id
        proposal.channel_id = msg.channel.id
        self.proposals[msg.id] = proposal
        self._save_state()

    async def handle_reaction(self, bot, payload: discord.RawReactionActionEvent):
        """Verarbeitet Reaktionen auf Vorschlags-Messages."""
        if payload.message_id not in self.proposals:
            return

        # Nur Admins dürfen approvals auslösen
        admins = self.config.permissions.get("admins", []) if self.config else []
        if payload.user_id not in admins:
            if channel:
                await channel.send("⛔ Keine Berechtigung für Auto-Fix Approvals.")
            return

        proposal = self.proposals[payload.message_id]
        emoji = str(payload.emoji)
        channel = bot.get_channel(payload.channel_id)
        if not channel:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        if emoji == "❌":
            await channel.send(f"❌ Vorschlag verworfen: {proposal.project}")
            return

        run_tests_only = emoji == "🧪"
        await channel.send(f"🧪 Starte {'Tests' if run_tests_only else 'Umsetzungs-'}Pipeline für {proposal.project} ... (ausgelöst von <@{payload.user_id}>)")

        await self._execute_pipeline(bot, proposal, channel, run_tests_only=run_tests_only)

    async def _execute_pipeline(self, bot, proposal: FixProposal, channel, run_tests_only: bool = False):
        """
        Führt Tests/Lint auf Heuristik-Basis aus.
        Umsetzungspfad: Branch → Patch (LLM) → Tests/Lint → optional Commit/Push/Draft-PR (nur wenn grün).
        """
        project_path = self._get_project_path(proposal.project)
        if not project_path or not project_path.exists():
            await channel.send(f"❌ Projektpfad nicht gefunden: {proposal.project}")
            return

        tests_to_run = proposal.tests or self._default_tests_for_project(proposal.project, project_path)
        if proposal.suggested_tests:
            tests_to_run.extend(proposal.suggested_tests)

        branch_name = None
        branch_created = False
        patch_applied = False
        pr_link = None
        patch_output = ""
        commit_hash = None
        diff_stat = ""

        if not run_tests_only:
            clean = await self._check_git_clean(project_path)
            if not clean:
                await channel.send("❌ Git-Working-Tree ist nicht clean. Abbruch (sicherheit).")
                return
            branch_name = self._make_branch_name(proposal)
            created = await self._create_branch(project_path, branch_name)
            if not created:
                await channel.send("❌ Konnte Branch nicht erstellen.")
                return
            branch_created = True
            # Patch-Generierung via KI
            patch_text = await self._generate_patch(bot, proposal, project_path)
            if patch_text:
                applied, patch_output = await self._apply_patch(project_path, patch_text)
                patch_applied = applied
                if not applied:
                    await channel.send(f"❌ Patch konnte nicht angewendet werden:\n{patch_output[:800]}")
                    return
            else:
                await channel.send("⚠️ Keine Patch-Änderung generiert; führe nur Tests auf aktuellem Stand aus.")
            diff_stat = await self._get_diff_stat(project_path)

        results = []
        for cmd in tests_to_run:
            result = await self._run_command(cmd, cwd=project_path)
            results.append((cmd, result))
            # If a command fails, stop further commands
            if result["returncode"] != 0:
                break

        all_passed = all(r["returncode"] == 0 for _, r in results) if results else True

        # Commit/Push/PR nur wenn Patch angewandt und Tests grün und Umsetzungspfad
        if not run_tests_only and patch_applied and all_passed:
            commit_hash = await self._commit_changes(project_path, proposal)
            pushed = False
            if commit_hash:
                pushed = await self._push_branch(project_path, branch_name)
                if pushed:
                    pr_link = await self._create_draft_pr(project_path, branch_name, proposal, diff_stat, results)
        elif not all_passed:
            await channel.send("⚠️ Tests fehlgeschlagen, keine Commits/PR.")

        summary_lines = []
        for cmd, res in results:
            status = "✅" if res["returncode"] == 0 else "❌"
            summary_lines.append(f"{status} `{cmd}` ({res['duration']:.1f}s)")
            if res["returncode"] != 0:
                summary_lines.append(f"Ausgabe:\n{res['stdout'][:500]}\n{res['stderr'][:500]}")

        if not results:
            summary_lines.append("⚠️ Keine Tests definiert/gefunden; nichts ausgeführt.")

        color = 0x2ECC71 if all_passed else 0xE74C3C
        embed = discord.Embed(
            title=f"🧪 Pipeline-Ergebnis: {proposal.project}",
            description="\n".join(summary_lines)[:1900],
            color=color
        )
        embed.add_field(name="Modus", value="Nur Tests" if run_tests_only else "Umsetzung (Branch, Patch, Tests)", inline=False)
        if branch_created and branch_name:
            embed.add_field(name="Branch", value=branch_name, inline=False)
        if patch_applied:
            embed.add_field(name="Patch", value="Angewendet", inline=False)
        else:
            embed.add_field(name="Patch", value="Nicht angewendet oder nicht generiert", inline=False)
        if diff_stat:
            embed.add_field(name="Diff", value=diff_stat[:1024], inline=False)
        if commit_hash:
            embed.add_field(name="Commit", value=commit_hash, inline=False)
        if pr_link:
            embed.add_field(name="Draft PR", value=pr_link, inline=False)
        embed.set_footer(text="Auto-Fix Pipeline (sicherer Modus)")
        await channel.send(embed=embed)

    async def _run_command(self, cmd: str, cwd: Path, timeout: int = 300) -> Dict[str, Any]:
        """Führt einen Shell-Befehl aus und gibt Resultat zurück."""
        start = asyncio.get_event_loop().time()
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"returncode": -1, "stdout": "", "stderr": "Timeout", "duration": timeout}

        duration = asyncio.get_event_loop().time() - start
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="ignore"),
            "stderr": stderr.decode(errors="ignore"),
            "duration": duration
        }

    async def _check_git_clean(self, project_path: Path) -> bool:
        """Prüft, ob Working Tree clean ist."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0 and result.stdout.strip() == ""
        except Exception as e:
            logger.debug(f"git status failed: {e}")
            return False

    def _make_branch_name(self, proposal: FixProposal) -> str:
        slug = proposal.summary.lower().replace(" ", "-")[:30]
        return f"ai-fix/{slug or 'proposal'}"

    async def _create_branch(self, project_path: Path, branch_name: str) -> bool:
        """Erstellt neuen Branch und wechselt hinein."""
        try:
            # fetch not needed in safe mode
            subprocess.run(["git", "checkout", "-B", branch_name], cwd=project_path, check=True, timeout=20)
            return True
        except Exception as e:
            logger.debug(f"create branch failed: {e}")
            return False

    async def _get_diff_stat(self, project_path: Path) -> str:
        try:
            res = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            return res.stdout.strip()[:1000]
        except Exception as e:
            logger.debug(f"diff stat failed: {e}")
            return ""

    async def _commit_changes(self, project_path: Path, proposal: FixProposal) -> Optional[str]:
        try:
            # Add all
            subprocess.run(["git", "add", "-A"], cwd=project_path, check=True, timeout=20)
            # Check if there is anything to commit
            status = subprocess.run(["git", "diff", "--cached", "--stat"], cwd=project_path, capture_output=True, text=True, timeout=10)
            if not status.stdout.strip():
                return None
            msg = f"chore: apply ai-fix {proposal.summary[:40]}"
            subprocess.run(["git", "commit", "-m", msg], cwd=project_path, check=True, timeout=20)
            commit_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_path, capture_output=True, text=True, timeout=10)
            return commit_res.stdout.strip()
        except Exception as e:
            logger.debug(f"commit failed: {e}")
            return None

    async def _push_branch(self, project_path: Path, branch_name: str) -> bool:
        try:
            res = subprocess.run(["git", "push", "-u", "origin", branch_name], cwd=project_path, capture_output=True, text=True, timeout=30)
            if res.returncode != 0:
                logger.debug(f"push failed: {res.stderr}")
                return False
            return True
        except Exception as e:
            logger.debug(f"push branch failed: {e}")
            return False

    def _parse_remote_slug(self, project_path: Path) -> Optional[str]:
        """Parse owner/repo from git remote origin."""
        try:
            res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=project_path, capture_output=True, text=True, timeout=10)
            url = res.stdout.strip()
            if "github.com" not in url:
                return None
            url = url.replace("git@github.com:", "https://github.com/")
            url = url.replace(".git", "")
            parts = url.split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                return f"{parts[0]}/{parts[1]}"
        except Exception:
            return None
        return None

    async def _create_draft_pr(self, project_path: Path, branch_name: str, proposal: FixProposal, diff_stat: str, results: List[Tuple[str, Dict[str, Any]]]) -> Optional[str]:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return None
        slug = self._parse_remote_slug(project_path)
        if not slug:
            return None
        api_url = f"https://api.github.com/repos/{slug}/pulls"

        body_lines = [
            f"Auto-Fix Proposal: {proposal.summary}",
            "",
            "Tests:",
        ]
        for cmd, res in results:
            status = "✅" if res["returncode"] == 0 else "❌"
            body_lines.append(f"- {status} `{cmd}` ({res['duration']:.1f}s)")
        if diff_stat:
            body_lines.append("")
            body_lines.append("Diff-Stat:")
            body_lines.append(f"`{diff_stat}`")

        payload = {
            "title": f"AI Fix: {proposal.summary[:60]}",
            "head": branch_name,
            "base": "main",
            "body": "\n".join(body_lines)[:4000],
            "draft": True,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(api_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, json=payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    return data.get("html_url")
                else:
                    logger.debug(f"PR creation failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.debug(f"PR request error: {e}")
        return None

    async def _generate_patch(self, bot, proposal: FixProposal, project_path: Path) -> Optional[str]:
        """Erzeugt einen Patch (Unified Diff) via AI, falls ai_service verfügbar."""
        if not self.ai_service:
            return None

        try:
            status_cmd = subprocess.run(
                ["git", "status", "-sb"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            status_text = status_cmd.stdout.strip()
        except Exception:
            status_text = ""

        tests_hint = ", ".join(proposal.tests or self._default_tests_for_project(proposal.project, project_path)) or "keine Tests erkannt"
        prompt = (
            "Du bist ein Code-Fix-Assistent. Erzeuge einen Unified Diff (git apply kompatibel) für das folgende Projekt.\n"
            f"Projekt: {proposal.project}\n"
            f"Status:\n{status_text}\n\n"
            f"Problem/Ziel: {proposal.summary}\n"
            f"Actions: {', '.join(proposal.actions) if proposal.actions else 'keine'}\n"
            f"Geplante Tests: {tests_hint}\n"
            "Beschränke Änderungen auf relevante Code-Dateien. Keine Secrets/Config/CI-Dateien ändern.\n"
            "Output ausschließlich als Unified Diff. Keine Erklärungen, kein Markdown.\n"
        )

        try:
            diff = await self.ai_service.get_ai_analysis(
                prompt=prompt,
                context="",
                use_critical_model=False
            )
            if diff and "diff --git" in diff and len(diff) < self.max_patch_size:
                return diff
        except Exception as e:
            logger.debug(f"Patch generation failed: {e}")
        return None

    async def _apply_patch(self, project_path: Path, patch_text: str) -> (bool, str):
        """Wendet Patch an (git apply)."""
        try:
            with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tmp:
                tmp.write(patch_text)
                tmp_path = tmp.name
            proc = subprocess.run(
                ["git", "apply", tmp_path],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=20
            )
            output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            Path(tmp_path).unlink(missing_ok=True)
            return proc.returncode == 0, output.strip()
        except Exception as e:
            return False, str(e)
