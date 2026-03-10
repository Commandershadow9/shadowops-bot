# Unresolved Issues - 2025-11-24

This document outlines the current unresolved issues with the ShadowOps bot as of the end of the day on November 24, 2025.

## Summary of Issues

The bot is currently reporting the following services as inactive or in an error state:

- **CrowdSec:** Reported as "Inaktiv" or "Fehler".
- **Fail2ban:** Reported as "Inaktiv".
- **AIDE:** Reported as "Kein Check" or "Noch nicht durchgeführt".

## Troubleshooting Steps Taken

### CrowdSec

1.  **Initial Diagnosis:** The `log_analyzer.py` was unable to parse the CrowdSec log file at `/var/log/crowdsec.log` due to a `PermissionError`.
2.  **Attempted Fix 1:** Added the bot's user (`cmdshadow`) to the `adm` group to grant read access to log files. This did not resolve the issue, as the bot's service was not picking up the new group membership.
3.  **Attempted Fix 2:** Changed the group ownership of `/var/log/crowdsec.log` to `adm` and set group read permissions (`g+r`). This did not resolve the issue.
4.  **Attempted Fix 3:** Temporarily made `/var/log/crowdsec.log` world-readable (`a+r`). This did not resolve the issue.
5.  **Investigation:**
    - Enabled debug logging and added debug print statements to `log_analyzer.py` to trace the file access.
    - Discovered that the bot's logs were being sent to `journalctl` instead of the log files in the `logs/` directory.
    - The `journalctl` logs did not show the `PermissionError` or the debug messages, indicating that the `log_analyzer.py` was not being called for CrowdSec logs.
6.  **Current Status:** The root cause of the CrowdSec `PermissionError` is still unknown. The `log_analyzer.py` does not seem to be attempting to parse the CrowdSec log file, despite being configured to do so. The error message may be a remnant from an older log file or a different process.

### Fail2ban

1.  **Initial Diagnosis:** The `/status` command reports Fail2ban as "Inaktiv". This is because `get_jail_stats()` in `fail2ban.py` returns an empty dictionary.
2.  **Investigation:** The `get_jail_stats()` method uses `sudo fail2ban-client status` to get the list of jails. This command is likely failing due to lack of passwordless `sudo` access for the `cmdshadow` user.
3.  **Proposed Fix:** The user was instructed to add the following line to the `sudoers` file using `visudo`:
    ```
    cmdshadow ALL=(ALL) NOPASSWD: /usr/bin/fail2ban-client
    ```
4.  **Current Status:** It is unknown if this change has been successfully implemented and if it has resolved the issue. The bot was also modified to call `validate_permissions()` at startup to make this issue more visible.

### AIDE

1.  **Initial Diagnosis:** The bot reports AIDE as "Kein Check" because the `dailyaidecheck.timer` has never been triggered.
2.  **Attempted Fix 1:** Attempted to manually trigger the `dailyaidecheck.service`, but the user cancelled the command as it was taking too long.
3.  **Attempted Fix 2:** Modified `src/integrations/aide.py` to gracefully handle the "no check yet" state by returning "Pending first run".
4.  **Current Status:** It is unknown if the change to `aide.py` has resolved the "Fehler" status in the daily health check. The `/stats` command shows "Noch nicht durchgeführt", which is expected if the timer has not run.

## Next Steps for Tomorrow

1.  Verify if the `sudoers` file has been updated for Fail2ban.
2.  Get the latest output of both the `/status` and the daily health check from the bot to confirm the current status of all services.
3.  If the CrowdSec `PermissionError` persists in any log, continue investigating the cause, possibly by looking into SELinux/AppArmor or other system-level restrictions.
4.  If the AIDE issue persists, investigate why the `aide.py` changes are not being reflected in the bot's output.
