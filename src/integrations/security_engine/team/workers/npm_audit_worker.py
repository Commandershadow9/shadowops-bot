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
        path = job.payload.get("path")
        if not path or not os.path.isdir(path):
            return JobResult(job_id=job.job_id, worker=self.worker_type,
                             project=job.project, status=JobStatus.PARTIAL,
                             errors=[f"Pfad fehlt/ungültig: {path!r}"])
        try:
            raw = await self._run_npm_audit(path)
        except FileNotFoundError:
            return JobResult(job_id=job.job_id, worker=self.worker_type,
                             project=job.project, status=JobStatus.PARTIAL,
                             errors=["npm nicht im PATH"])
        except asyncio.TimeoutError:
            return JobResult(job_id=job.job_id, worker=self.worker_type,
                             project=job.project, status=JobStatus.PARTIAL,
                             errors=[f"npm audit Timeout (>{_NPM_TIMEOUT_S}s)"])

        findings = self._parse(raw, job.project)
        added = 0
        for f in findings:
            fp = compute_finding_fingerprint("npm_audit", job.project, None, f["title"])
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

        return JobResult(job_id=job.job_id, worker=self.worker_type,
                         project=job.project, status=JobStatus.OK,
                         findings_added=added,
                         metadata={"scanned_path": path, "raw_count": len(findings)})

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
    def _parse(raw: str, project: str) -> list[dict]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        out: list[dict] = []
        for name, info in (data.get("vulnerabilities") or {}).items():
            severity = str(info.get("severity", "unknown")).upper()
            advisories = [v for v in info.get("via", []) if isinstance(v, dict)]
            title = advisories[0].get("title") if advisories else f"Vulnerable package: {name}"
            url = advisories[0].get("url", "") if advisories else ""
            out.append({
                "severity": severity,
                "category": "npm_audit",
                "title": f"[{name}] {title}"[:300],
                "description": f"Package {name} ({info.get('range', '?')}) — {url}",
            })
        return out
