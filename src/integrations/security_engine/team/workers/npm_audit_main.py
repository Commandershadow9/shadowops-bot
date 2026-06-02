"""systemd-Entrypoint: startet den npm-audit-Worker."""
from .npm_audit_worker import NpmAuditWorker
from ..runner import run_worker

if __name__ == "__main__":
    run_worker(NpmAuditWorker)
