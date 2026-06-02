"""systemd-Entrypoint-Stub fuer den Security-Orchestrator (P1).

P1 triggert manuell/per Test. Der dauerhafte sec:trigger-Subscribe-Loop wird
in einer Folge-Iteration ergaenzt (analog runner._amain). Bis dahin beendet
sich der Prozess sofort, wenn das Feature-Flag aus ist.
"""
import os

if __name__ == "__main__":
    if os.environ.get("SECURITY_TEAM_ENABLED", "false").lower() not in ("1", "true", "yes", "on"):
        print("security_team disabled — orchestrator exit")
        raise SystemExit(0)
    print("orchestrator P1-Stub: sec:trigger-Loop folgt in Folge-Iteration")
