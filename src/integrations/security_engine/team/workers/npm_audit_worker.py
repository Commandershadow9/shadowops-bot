"""npm-audit-Worker — Dependency-CVE-Scan via `npm audit --json`.

Kleinster Worker-Scope (der #1069-Fall). Dedup im Worker via
compute_finding_fingerprint, schreibt Findings über db.store_finding().
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from ..base_worker import BaseSecurityWorker
from ..contracts import SecurityJob, JobResult, JobStatus
from ...fingerprint import compute_finding_fingerprint

logger = logging.getLogger("security.worker.npm_audit")

_NPM_TIMEOUT_S = 180


class NpmAuditWorker(BaseSecurityWorker):
    worker_type = "npm_audit"

    async def process(self, job: SecurityJob) -> JobResult:
        """Scannt ein Projekt mit `npm audit --json`, dedupt und speichert Findings.

        Returns PARTIAL bei ungültigem Pfad, fehlendem npm, Timeout oder
        strukturiertem npm-Fehler (z.B. ENOLOCK ohne package-lock.json); sonst OK.
        """
        path = job.payload.get("path")
        if not path or not os.path.isdir(path):
            return self._partial(job, f"Pfad fehlt/ungültig: {path!r}")
        try:
            raw = await self._run_npm_audit(path)
        except FileNotFoundError:
            return self._partial(job, "npm nicht im PATH")
        except asyncio.TimeoutError:
            return self._partial(job, f"npm audit Timeout (>{_NPM_TIMEOUT_S}s)")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._partial(job, "npm-Output nicht als JSON parsebar")
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            summary = err.get("summary") if isinstance(err, dict) else str(err)
            return self._partial(job, f"npm audit Fehler: {summary}")

        findings = self._extract(data)
        added = 0
        aktuelle_fps: set[str] = set()
        for f in findings:
            fp = compute_finding_fingerprint("npm_audit", job.project, None, f["title"])
            aktuelle_fps.add(fp)
            exists = await self.db.pool.fetchval(
                "SELECT 1 FROM findings WHERE finding_fingerprint=$1 AND status='open' LIMIT 1",
                fp,
            )
            if exists:
                continue
            fid = await self.db.store_finding(
                severity=f["severity"], category=f["category"],
                title=f["title"], description=f["description"],
                affected_project=job.project, finding_fingerprint=fp,
            )
            if fid:
                added += 1

        # Nur an dieser Stelle erreichbar, also nach einem VOLLSTAENDIGEN Lauf:
        # jeder PARTIAL-Ausgang ist oben schon zurueckgekehrt. Das ist die
        # Bedingung fuer den Abgleich — bei Timeout, fehlendem npm oder ENOLOCK
        # waere `aktuelle_fps` leer und wuerde den gesamten Bestand abraeumen.
        geschlossen = await self._veraltete_schliessen(job.project, aktuelle_fps)

        return JobResult(job_id=job.job_id, worker=self.worker_type,
                         project=job.project, status=JobStatus.OK,
                         findings_added=added,
                         metadata={"scanned_path": path, "raw_count": len(findings),
                                   "findings_closed": geschlossen})

    async def _veraltete_schliessen(self, project: str, aktuelle_fps: set[str]) -> int:
        """Schliesst offene Funde, die in der aktuellen Ausgabe nicht mehr stehen.

        Ohne diesen Abgleich altert der Bestand zu Unwahrheiten: Ein durch ein
        Update behobener Fund bliebe dauerhaft `open` und wuerde Wochen spaeter
        zum GitHub-Issue. Genau so entstanden am 28.08.2026 dreizehn Issues fuer
        Funde aus Juli, die es laengst nicht mehr gab (ZERODOX #2685 ff.).

        Begrenzt auf Kategorie UND Projekt: Ein npm-Lauf fuer ZERODOX darf
        nichts ueber GuildScout oder ueber Trivy-Funde aussagen.
        """
        try:
            rows = await self.db.pool.fetch(
                """
                UPDATE findings
                   SET status = 'fixed', fixed_at = NOW()
                 WHERE status = 'open'
                   AND category = 'npm_audit'
                   AND affected_project = $1
                   AND finding_fingerprint IS NOT NULL
                   AND NOT (finding_fingerprint = ANY($2::text[]))
                RETURNING id
                """,
                project, list(aktuelle_fps),
            )
        except Exception:
            logger.warning("Abgleich veralteter Funde fehlgeschlagen (project=%r)",
                           project, exc_info=True)
            return 0

        if rows:
            logger.info("%d veraltete npm-Funde geschlossen (project=%s)", len(rows), project)
        return len(rows)

    def _partial(self, job: SecurityJob, msg: str) -> JobResult:
        return JobResult(job_id=job.job_id, worker=self.worker_type,
                         project=job.project, status=JobStatus.PARTIAL,
                         errors=[msg])

    async def _run_npm_audit(self, path: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "npm", "audit", "--json", cwd=path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CLAUDECODE": ""},  # nested-session-Schutz
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_NPM_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        # npm exit-code != 0 ist NORMAL wenn Vulns existieren → stdout trotzdem parsen
        return stdout.decode("utf-8", errors="replace")

    @staticmethod
    def _parse(raw: str) -> list[dict]:
        """Dünner Wrapper für Unit-Tests: parst JSON und extrahiert Findings."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return NpmAuditWorker._extract(data)

    @staticmethod
    def _extract(data: dict) -> list[dict]:
        out: list[dict] = []
        for name, info in (data.get("vulnerabilities") or {}).items():
            severity = str(info.get("severity", "unknown")).upper()
            advisories = [v for v in info.get("via", []) if isinstance(v, dict)]
            title = advisories[0].get("title") if advisories else None
            title = title or f"Vulnerable package: {name}"  # Minor-Fix: kein "None"
            url = advisories[0].get("url", "") if advisories else ""
            out.append({
                "severity": severity,
                "category": "npm_audit",
                "title": f"[{name}] {title}"[:300],
                "description": f"Package {name} ({info.get('range', '?')}) — {url}",
            })
        return out
