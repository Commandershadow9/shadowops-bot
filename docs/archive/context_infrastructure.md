# Server Infrastructure & Security Policies

## Server Overview
**Hostname**: (Production Server)
**OS**: Debian Linux (Kernel 6.1.0-41-cloud-amd64)
**User**: cmdshadow
**Purpose**: Multi-project hosting for production applications and security tools

## Running Projects
1. **Sicherheitstool** - Production security management system (Port 3001)
2. **ShadowOps Bot** - Security automation and Discord monitoring
3. **GuildScout** - Discord bot for guild management

## Security Infrastructure

### CrowdSec (Threat Detection)
- **Status**: Active
- **Function**: IP-based threat detection and blocking
- **Integration**: Detects brute-force, scanner, exploits
- **Auto-Action**: Automatic IP banning
- **Monitoring**: 30-second scan intervals

### Fail2ban (Intrusion Prevention)
- **Status**: Active
- **Function**: Monitors logs for malicious patterns
- **Primary Target**: SSH brute-force attempts
- **Auto-Action**: Temporary IP bans
- **Monitoring**: 30-second scan intervals

### Trivy (Container Security)
- **Status**: Active
- **Function**: Docker image vulnerability scanning
- **Scan Target**: All running containers
- **Severity Tracking**: CRITICAL, HIGH, MEDIUM, LOW
- **Monitoring**: 6-hour scan intervals

### AIDE (File Integrity Monitoring)
- **Status**: Active
- **Function**: Detects unauthorized file system changes
- **Scan Target**: System files, configs, binaries
- **Alert Trigger**: New/modified/deleted files
- **Monitoring**: 15-minute scan intervals

### UFW (Uncomplicated Firewall)
- **Status**: Active
- **Default Policy**: Deny incoming, allow outgoing
- **Allowed Ports**: SSH (22), HTTP (80), HTTPS (443), custom application ports
- **Configuration**: `/etc/ufw/`

### WireGuard (VPN)
- **Status**: Configured (optional)
- **Function**: Secure remote access
- **Interface**: wg0
- **Configuration**: `/etc/wireguard/`

## Critical System Directories

### DO-NOT-TOUCH (Automatic Changes Forbidden)
```
/etc/passwd                  # User database
/etc/shadow                  # Password hashes
/etc/ssh/                    # SSH configuration
/boot/                       # Boot files and kernel
/etc/systemd/system/         # System services
/etc/postgresql/             # Database configuration (without backup)
/home/cmdshadow/project/     # Production Sicherheitstool
```

### SAFE-TO-MODIFY (With Backups)
```
/tmp/                        # Temporary files cleanup
/var/log/                    # Log rotation and cleanup
/home/cmdshadow/shadowops-bot/logs/  # Bot logs
/home/cmdshadow/GuildScout/logs/     # GuildScout logs
```

### REQUIRES-APPROVAL
```
/etc/fail2ban/               # Fail2ban rules (test first)
/etc/crowdsec/               # CrowdSec configuration
/var/lib/docker/             # Docker volumes and containers
/etc/ufw/                    # Firewall rules
```

## Network Configuration

### Open Ports
- **22** (SSH) - Secured with Fail2ban + CrowdSec
- **80** (HTTP) - Web services
- **443** (HTTPS) - Encrypted web services
- **3001** (Sicherheitstool API) - Production API
- **11434** (Ollama) - Local AI service (localhost only)

### Services Listening
- **PostgreSQL**: localhost:5432 (Sicherheitstool database)
- **Ollama**: localhost:11434 (AI inference)
- **Node.js**: 0.0.0.0:3001 (Sicherheitstool)
- **Discord Bots**: Discord Gateway (WebSocket)

## Backup Strategy

### Automated Backups
- **Database**: Daily PostgreSQL dumps
- **Configuration**: Weekly backup of /etc/
- **Project Files**: Git-based version control
- **Logs**: 7-day retention with rotation

### Backup Locations
- **Database Dumps**: `/home/cmdshadow/backups/database/`
- **Config Backups**: `/home/cmdshadow/backups/configs/`
- **Script**: `/home/cmdshadow/backup-database.sh`
- **Schedule**: Daily via cron (02:00 UTC)

### Recovery Procedures
1. Database: Restore from latest dump with `psql`
2. Config: Copy from backup directory
3. Code: Git checkout previous commit
4. Verification: Health check after restore

## Security Policies

### Change Management
1. **PARANOID Mode** (Current):
   - All changes require human approval
   - No automatic remediation
   - Maximum safety for learning phase

2. **BALANCED Mode** (Future):
   - Low-risk changes auto-approved
   - High-risk changes require approval
   - Confidence threshold: 85%

3. **AGGRESSIVE Mode** (Future):
   - Most changes auto-approved
   - Only critical systems require approval
   - Confidence threshold: 75%

### Approval Requirements

#### Always Require Approval
1. Database schema changes
2. User/permission modifications
3. Firewall rule changes
4. Service restarts during business hours
5. Production code deployments
6. SSL/TLS certificate changes

#### Auto-Approve (Future, BALANCED/AGGRESSIVE modes)
1. Fail2ban IP bans (non-whitelisted)
2. CrowdSec threat blocking
3. Log rotation and cleanup
4. Temporary file cleanup
5. Non-critical package updates (with testing)

#### Never Auto-Execute
1. Database migrations (production)
2. User deletion
3. Firewall rule deletion
4. Service uninstallation
5. Data deletion

### Whitelisted IPs (Do Not Ban)
```
# Office/VPN IPs - to be configured
# Add trusted IPs that should never be blocked
# Example: 203.0.113.0/24 (Office network)
```

## Resource Limits

### CPU
- **Monitoring**: psutil
- **Limits**: None currently set
- **Alert Threshold**: >80% sustained usage

### Memory
- **Total**: (Check with `free -h`)
- **Monitoring**: psutil
- **Alert Threshold**: >90% usage

### Disk
- **Monitoring**: df, du
- **Alert Threshold**: >85% usage
- **Cleanup Targets**: /tmp, old logs, Docker unused images

### Network
- **Bandwidth**: Monitor with vnstat
- **Connection Limits**: CrowdSec manages
- **Rate Limiting**: Discord API respected by bots

## Monitoring & Alerting

### Discord Alerts (via ShadowOps Bot)
- **#critical**: CRITICAL security events
- **#security**: General security alerts
- **#docker**: Container vulnerabilities
- **#fail2ban**: Intrusion attempts
- **#nexus**: System-wide notifications

### Log Locations
- **System**: `/var/log/syslog`, `/var/log/auth.log`
- **CrowdSec**: `/var/log/crowdsec/`
- **Fail2ban**: `/var/log/fail2ban.log`
- **ShadowOps**: `/home/cmdshadow/shadowops-bot/logs/`
- **Sicherheitstool**: Application directory
- **GuildScout**: `/home/cmdshadow/GuildScout/logs/`

### Health Checks
- **Daily**: ShadowOps bot runs comprehensive health check (06:00 UTC)
- **Continuous**: Event watchers monitor security tools
- **On-Demand**: Slash commands for manual checks

## Incident Response

### Security Event Workflow
1. **Detection**: Security tool identifies threat/vulnerability
2. **Alert**: Discord notification sent to appropriate channel
3. **Analysis**: AI analyzes event with context
4. **Recommendation**: Fix strategy generated with confidence
5. **Approval**: Human approves in PARANOID mode
6. **Execution**: Fix applied with monitoring
7. **Verification**: Success/failure confirmed
8. **Documentation**: Event logged in audit trail

### Rollback Procedures
1. **Database**: Restore from backup dump
2. **Configuration**: Revert from backup directory
3. **Code**: Git revert/checkout
4. **Services**: Systemctl restart
5. **Firewall**: UFW reset to known-good state

### Emergency Contacts
- **Discord**: @admin in ShadowOps server
- **System**: SSH access for manual intervention
- **Escalation**: Check with user before destructive actions

## Compliance & Auditing

### Audit Logging
- **Sicherheitstool**: Built-in audit log service
- **ShadowOps**: All auto-remediation logged
- **System**: auditd (if configured)
- **Retention**: 90 days minimum

### GDPR Considerations
- **Sicherheitstool**: Contains customer personal data
- **Data Protection**: Encrypted at rest (PostgreSQL)
- **Access Control**: Role-based permissions
- **Data Retention**: Per customer contract

## Maintenance Windows

### Planned Maintenance
- **Preferred**: 02:00-06:00 UTC (lowest traffic)
- **Notification**: Announce in Discord before changes
- **Rollback Plan**: Always prepared before changes

### Emergency Maintenance
- **Immediate**: Critical security vulnerabilities
- **Fast Response**: Auto-remediation in AGGRESSIVE mode
- **Post-Action**: Notify and document

## Docker Environment

### Running Containers
- **Check**: `docker ps`
- **Images**: Scanned by Trivy every 6 hours
- **Volumes**: Persistent data storage
- **Networks**: Isolated per project

### Container Security
- **Vulnerability Scanning**: Trivy
- **Image Updates**: Notify on CRITICAL/HIGH CVEs
- **Rebuild**: Required for vulnerability fixes
- **Testing**: Always test rebuilt images

## User Permissions

### cmdshadow User
- **Sudo Access**: Yes (required for security tools)
- **Home Directory**: `/home/cmdshadow`
- **Groups**: ollama, docker, sudo
- **Shell**: bash

### ShadowOps Bot Permissions
- **Sudo**: Via cmdshadow user
- **Security Tools**: Full access (cscli, fail2ban-client, trivy, aide)
- **System**: Limited to security operations
- **Logs**: Read access to /var/log/

## Service Dependencies

### Sicherheitstool Dependencies
- PostgreSQL running
- Node.js installed
- Network access on port 3001
- Environment variables configured

### ShadowOps Bot Dependencies
- Ollama service running
- Security tools installed
- Discord API access
- Log file read permissions

### GuildScout Dependencies
- Discord API access
- SQLite database
- Python 3.11+ environment
- Minimal system resources

## Recovery Priority

### Priority 1 (Restore Immediately)
1. Sicherheitstool (customer-facing)
2. PostgreSQL database
3. Network/firewall

### Priority 2 (Restore Soon)
1. ShadowOps bot (monitoring)
2. CrowdSec/Fail2ban
3. Ollama AI service

### Priority 3 (Restore When Possible)
1. GuildScout bot
2. Backup systems
3. Monitoring tools

## Notes
- All automated fixes must respect these policies
- Context learning system uses this document for safety
- Unknown operations default to REQUIRE-APPROVAL
- Customer-facing systems always have highest priority
