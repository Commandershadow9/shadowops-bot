---
title: ShadowOps API Documentation
status: active
last_reviewed: 2026-04-28
owner: CommanderShadow9
---

# ShadowOps API Documentation

Complete API reference for ShadowOps Security Guardian.

## Table of Contents

- [Discord Commands](#discord-commands)
- [Configuration Reference](#configuration-reference)
- [GitHub Webhook API](#github-webhook-api)
- [Jules SecOps Workflow API](#jules-secops-workflow-api)
- [Python API](#python-api)
- [Event System](#event-system)
- [Knowledge Base API](#knowledge-base-api)
- [Project Monitor API](#project-monitor-api)

---

## Discord Commands

All commands are implemented as Discord Slash Commands (`/command`).

### Security & Monitoring Commands

#### `/status`
Shows overall security status across all systems.

**Permissions:** None
**Parameters:** None
**Returns:** Embed with:
- Fail2ban status
- CrowdSec status
- AIDE status
- Docker scan status
- Active threats count

**Example:**
```
/status
```

#### `/scan`
Triggers a manual Docker security scan using Trivy.

**Permissions:** None
**Parameters:** None
**Returns:** Scan results with vulnerabilities by severity

**Example:**
```
/scan
```

#### `/threats [hours]`
Shows detected threats from the last N hours.

**Permissions:** None
**Parameters:**
- `hours` (optional): Number of hours to look back (default: 24)

**Returns:** List of threats with timestamps and sources

**Example:**
```
/threats 48
```

#### `/bans [limit]`
Shows currently banned IPs from Fail2ban and CrowdSec.

**Permissions:** None
**Parameters:**
- `limit` (optional): Number of bans to show (default: 10, max: 50)

**Returns:** List of banned IPs with reasons and timestamps

**Example:**
```
/bans 20
```

#### `/aide`
Shows AIDE File Integrity Check status.

**Permissions:** None
**Parameters:** None
**Returns:** Last check time and file changes detected

**Example:**
```
/aide
```

---

### Auto-Remediation Commands

#### `/remediation-stats`
Shows auto-remediation statistics and performance metrics.

**Permissions:** None
**Parameters:** None
**Returns:** Embed with:
- Total fixes executed
- Success/failure rates
- Average fix duration
- Events by severity
- Top fix strategies

**Example:**
```
/remediation-stats
```

#### `/stop-all-fixes`
🛑 EMERGENCY: Stops all running auto-fix processes immediately.

**Permissions:** Administrator
**Parameters:** None
**Returns:** Confirmation of stopped processes

**Example:**
```
/stop-all-fixes
```

**⚠️ Warning:** Only use in emergency situations. May leave fixes in incomplete state.

#### `/set-approval-mode [mode]`
Changes the auto-remediation approval mode.

**Permissions:** Administrator
**Parameters:**
- `mode` (required): One of:
  - `paranoid` - Ask for approval on EVERY event
  - `auto` - Only ask for CRITICAL events, auto-fix others
  - `dry-run` - Log everything, execute nothing

**Returns:** Confirmation of mode change

**Example:**
```
/set-approval-mode auto
```

**Modes Explained:**
- **Paranoid**: Maximum safety, approve every fix manually
- **Auto**: Balanced, only critical fixes need approval
- **Dry-Run**: Test mode, no actual execution

---

### AI & Learning System Commands

#### `/get-ai-stats`
Shows AI provider status, fallback chain, and usage statistics.

**Permissions:** None
**Parameters:** None
**Returns:** Embed with:
- Codex CLI status (primary engine, active model, quota)
- Claude CLI status (fallback engine, active model)
- Active routing rules (CRITICAL/HIGH/LOW → engine + model)
- Request counts per provider

**Example:**
```
/get-ai-stats
```

#### `/reload-context`
Reloads all project context files (DO-NOT-TOUCH, INFRASTRUCTURE, PROJECT_*.md).

**Permissions:** Administrator
**Parameters:** None
**Returns:** Confirmation with loaded file counts

**Example:**
```
/reload-context
```

**Use Cases:**
- After updating DO-NOT-TOUCH.md
- After modifying project documentation
- After adding new infrastructure knowledge

---

### Multi-Project Management Commands

#### `/projekt-status [name]`
Shows detailed status for a specific monitored project.

**Permissions:** None
**Parameters:**
- `name` (required): Project name (e.g., shadowops-bot, guildscout)

**Returns:** Embed with:
- Online/Offline status
- Uptime percentage
- Average response time
- Total health checks (successful/failed)
- Last check time
- Current downtime (if offline)
- Last error (if offline)

**Example:**
```
/projekt-status shadowops-bot
```

#### `/alle-projekte`
Shows overview of all monitored projects.

**Permissions:** None
**Parameters:** None
**Returns:** Embed with:
- Total projects online/offline
- Status per project (online/offline, uptime %, response time)
- Sorted by status (online first)

**Example:**
```
/alle-projekte
```

---

## Configuration Reference

### Complete `config/config.yaml` Structure

```yaml
# ========================================
# DISCORD CONFIGURATION
# ========================================
discord:
  token: "YOUR_BOT_TOKEN_HERE"  # Discord bot token (REQUIRED)
  guild_id: 123456789            # Discord server ID (REQUIRED)

# ========================================
# CHANNEL CONFIGURATION
# ========================================
channels:
  # Auto-Remediation Channels (Auto-created)
  auto_remediation_security_alerts: 0
  auto_remediation_approval_requests: 0
  auto_remediation_execution_logs: 0
  auto_remediation_stats: 0
  auto_remediation_ai_learning: 0
  auto_remediation_code_fixes: 0
  auto_remediation_orchestrator: 0

  # Multi-Project Channels (Auto-created v3.1)
  customer_alerts: 0
  customer_status: 0
  deployment_log: 0

# ========================================
# AI CONFIGURATION
# ========================================
ai:
  enabled: true

  primary:
    engine: codex
    models:
      fast: gpt-4o
      standard: gpt-5.3-codex
      thinking: o3
    timeout: 300

  fallback:
    engine: claude
    cli_path: /home/user/.local/bin/claude
    models:
      fast: claude-sonnet-4-6
      standard: claude-sonnet-4-6
      thinking: claude-opus-4-6
    timeout: 300

  routing:
    critical_analysis: { engine: codex, model: thinking }
    high_analysis:     { engine: codex, model: standard }
    low_analysis:      { engine: codex, model: fast }
    critical_verify:   { engine: claude, model: thinking }

# ========================================
# AUTO-REMEDIATION CONFIGURATION
# ========================================
auto_remediation:
  enabled: true                             # Enable auto-fix system
  dry_run: false                            # Dry-run mode (log only, no execution)
  approval_mode: paranoid                   # paranoid | auto | dry-run
  max_batch_size: 10                        # Max events per batch
  collection_window_seconds: 300            # Wait time before processing batch (5min)

  # Scan intervals (seconds)
  scan_intervals:
    trivy: 21600     # Docker scans every 6 hours
    crowdsec: 30     # CrowdSec check every 30 seconds
    fail2ban: 30     # Fail2ban check every 30 seconds
    aide: 900        # AIDE check every 15 minutes

# ========================================
# PROJECT CONFIGURATION
# ========================================
projects:
  shadowops-bot:
    enabled: true                           # Enable this project
    path: /home/user/shadowops-bot          # Absolute path to project
    branch: main                            # Git branch for deployments

    # Health monitoring (v3.1)
    monitor:
      enabled: true                         # Enable health checks
      url: http://localhost:5000/health     # Health check endpoint
      expected_status: 200                  # Expected HTTP status
      check_interval: 60                    # Check every N seconds
      timeout: 10                           # Request timeout (seconds)

    # Deployment configuration (v3.1)
    deploy:
      run_tests: true                       # Run tests before deploy
      test_command: pytest tests/           # Test command
      post_deploy_command: pip install -r requirements.txt  # Post-deploy command
      service_name: shadowops-bot           # Systemd service name

  guildscout:
    enabled: true
    path: /home/user/guildscout
    branch: main
    monitor:
      enabled: true
      url: http://localhost:3000/health
      check_interval: 60
    deploy:
      run_tests: false
      service_name: guildscout

# ========================================
# GITHUB INTEGRATION (v3.1)
# ========================================
github:
  enabled: false                            # Enable GitHub webhooks
  webhook_secret: "your_webhook_secret_here"  # HMAC secret for verification
  webhook_port: 8080                        # Webhook server port
  auto_deploy: false                        # Auto-deploy on push (RECOMENDED: false for security)
  deploy_branches:                          # Branches that trigger deployments
    - main
    - master

# ========================================
# DEPLOYMENT CONFIGURATION (v3.1)
# ========================================
deployment:
  backup_dir: backups                       # Backup directory
  max_backups: 5                            # Max backups per project
  health_check_timeout: 30                  # Post-deploy health check timeout (s)
  test_timeout: 300                         # Test execution timeout (s)

# ========================================
# INCIDENT MANAGEMENT (v3.1)
# ========================================
incidents:
  auto_close_hours: 24                      # Auto-close resolved incidents after N hours

# ========================================
# CUSTOMER NOTIFICATIONS (v3.1)
# ========================================
customer_notifications:
  min_severity: HIGH                        # Minimum severity for customer alerts (LOW|MEDIUM|HIGH|CRITICAL)

# ========================================
# LOG PATHS
# ========================================
log_paths:
  fail2ban: /var/log/fail2ban/fail2ban.log
  crowdsec: /var/log/crowdsec/crowdsec.log
  docker: /var/log/docker.log
  shadowops: logs/shadowops.log
```

---

## GitHub Webhook API

ShadowOps can receive GitHub webhook events for auto-deployment.

### Setup

1. **Configure webhook in GitHub:**
   - Repository → Settings → Webhooks → Add webhook
   - Payload URL: `http://your-server:8080/webhook`
   - Content type: `application/json`
   - Secret: (from `github.webhook_secret` in config.yaml)
   - Events: `Push`, `Pull request`, `Release`

2. **Configure ShadowOps:**
   ```yaml
   github:
     enabled: true
     webhook_secret: "your_secret_here"
     webhook_port: 8080
     auto_deploy: false
     deploy_branches: [main, master]
   ```

### Supported Events

#### Push Events
Triggers when code is pushed to repository.

**Auto-Deploy Trigger:** If push is to a deploy branch (e.g., `main`)

**Discord Notification:** Always sent with:
- Repository name
- Branch
- Commit count
- Latest commit message and author

#### Pull Request Events
Triggers when PRs are opened, closed, or updated.

**Auto-Deploy Trigger:** If PR is merged to a deploy branch

**Discord Notification:** Always sent with:
- PR number and title
- Author
- Source → Target branch
- Action (opened/closed/merged)

#### Release Events
Triggers when releases are published.

**Auto-Deploy Trigger:** Never (releases don't trigger deployments)

**Discord Notification:** Always sent with:
- Tag name
- Release name
- Author
- Type (stable/prerelease)

### Webhook Endpoints

#### `POST /webhook`
Receives GitHub webhook events.

**Headers:**
- `X-GitHub-Event`: Event type (push, pull_request, release)
- `X-Hub-Signature-256`: HMAC signature for verification

**Response:**
- `200 OK`: Event processed successfully
- `401 Unauthorized`: Invalid signature
- `500 Internal Server Error`: Processing error

#### `GET /health`
Health check endpoint for webhook server.

**Response:**
```json
{
  "status": "healthy",
  "service": "github-webhook",
  "timestamp": "2025-11-21T12:00:00Z"
}
```

---

## Jules SecOps Workflow API

### Health-Endpoint

**GET** `/health/jules`

Gibt den aktuellen Status des Jules-Workflows zurueck.

**Response (enabled):**
```json
{
  "enabled": true,
  "status": "healthy",
  "active_reviews": 0,
  "pending_prs": 2,
  "escalated_24h": 0,
  "stats_24h": {
    "total_reviews": 5,
    "approved": 4,
    "revisions": 1,
    "merged": 3,
    "tokens_consumed": 12500
  },
  "last_review_at": "2026-04-12T13:42:18Z"
}
```

**Response (disabled):**
```json
{
  "enabled": false,
  "status": "disabled"
}
```

### Webhook-Verhalten

Der Jules-Workflow reagiert auf folgende GitHub-Webhook-Events:

| Event | Action | Reaktion |
|-------|--------|----------|
| `pull_request` | `opened` | Jules-PR erkannt → Claude-Review starten |
| `pull_request` | `synchronize` | Neuer Commit → Re-Review (wenn neuer SHA) |
| `pull_request` | `ready_for_review` | Draft→Ready → Review starten |
| `pull_request` | `closed` | Merged → Finding resolved / Abandoned → Terminal |
| `issue_comment` | `created` | Nur `/review` Command vom Repo-Owner |

**Blockierte Events (Loop-Schutz):**
Alle `issue_comment`, `pull_request_review`, `pull_request_review_comment` Events werden fuer Auto-Reviews blockiert (PR #123 Vorfall).

### Konfiguration

Siehe `config/config.example.yaml` → `jules_workflow:` Block.

| Parameter | Default | Beschreibung |
|-----------|---------|-------------|
| `enabled` | `false` | Master-Switch |
| `dry_run` | `false` | Loggt statt auszufuehren |
| `max_iterations` | `5` | Max Review-Runden pro PR |
| `cooldown_seconds` | `300` | Mindestabstand zwischen Reviews |
| `max_hours_per_pr` | `2` | Timeout pro PR |
| `circuit_breaker.max_reviews_per_hour` | `20` | Globaler Breaker pro Repo |
| `token_cap_per_pr` | `50000` | Max Token-Kosten pro PR |
| `api_key` | `""` | Jules API-Key fuer programmatischen Zugriff (optional) |

### Jules REST API Integration

**Base-URL:** `https://jules.googleapis.com/v1alpha`
**Auth:** Header `X-Goog-Api-Key: <key>`
**API-Key erstellen:** https://jules.google.com/settings

**Endpoints:**
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`  | `/sources` | Liste verbundener GitHub-Repos |
| `POST` | `/sessions` | Neue Task-Session erstellen |
| `GET`  | `/sessions?pageSize=N` | Sessions auflisten |
| `GET`  | `/sessions/{id}` | Session-Details + State |
| `POST` | `/sessions/{id}:approvePlan` | Plan genehmigen (wenn requirePlanApproval=true) |
| `POST` | `/sessions/{id}:sendMessage` | Nachricht an laufende Session |

**States:** `IN_PROGRESS`, `COMPLETED`, `FAILED`

### Intelligente Modell-Wahl

Der Bot waehlt automatisch Opus oder Sonnet basierend auf PR-Charakteristik:

| Kriterium | Modell | Timeout |
|-----------|--------|---------|
| Security-Keywords (xss/cve/injection/dos/auth/csrf) | **Opus (thinking)** | 180s |
| Diff > 3000 Zeichen | **Opus (thinking)** | 180s |
| Alles andere | **Sonnet (standard)** | 120s |

Fallback auf das jeweils andere Modell bei Timeout oder leerer Response.

### Datenbank-Tabellen

| Tabelle | DB | Zweck |
|---------|-----|-------|
| `jules_pr_reviews` | security_analyst | PR-State, Lock-Claim, Iteration-Counter |
| `jules_review_examples` | agent_learning | Few-Shot Learning aus vergangenen Reviews |
| `jules_daily_stats` (View) | security_analyst | Taegliche Metriken |

---

## Python API

### Knowledge Base API

```python
from src.integrations.knowledge_base import KnowledgeBase

# Initialize (DSN from config or SECURITY_ANALYST_DB_URL env var)
kb = KnowledgeBase(dsn="postgresql://user:pass@localhost/security_analyst")

# Record a fix
kb.record_fix(
    event={'source': 'trivy', 'severity': 'HIGH', 'details': {...}},
    strategy={'description': 'Update package', 'confidence': 0.9, 'steps': [...]},
    result='success',  # or 'failed'
    duration_seconds=15.2,
    error_message=None,  # if failed
    retry_count=0
)

# Get best strategies for similar events
strategies = kb.get_best_strategies(
    event_type='vulnerability',
    severity='HIGH',
    limit=5
)
# Returns: [{'strategy': {...}, 'success_rate': 0.95, 'avg_confidence': 0.88, ...}, ...]

# Get success rate for specific event
stats = kb.get_success_rate(event_signature='CVE-2024-1234')
# Returns: {'total_attempts': 5, 'successful': 4, 'failed': 1, 'success_rate': 0.8}

# Get learning insights
insights = kb.get_learning_insights(days=30)
# Returns: {'total_fixes': 150, 'success_rate': 0.87, 'avg_duration': 12.5, ...}
```

### Project Monitor API

```python
from src.integrations.project_monitor import ProjectMonitor

# Initialize
monitor = ProjectMonitor(bot, config)

# Start monitoring all projects
await monitor.start_monitoring()

# Get status for specific project
status = monitor.get_project_status('shadowops-bot')
# Returns: {'name': 'shadowops-bot', 'is_online': True, 'uptime_percentage': 99.8, ...}

# Get all project statuses
all_statuses = monitor.get_all_projects_status()
# Returns: [{'name': 'project1', 'is_online': True, ...}, ...]

# Stop monitoring
await monitor.stop_monitoring()
```

### Incident Manager API

```python
from src.integrations.incident_manager import IncidentManager, IncidentSeverity

# Initialize
incident_mgr = IncidentManager(bot, config)

# Create incident
incident = await incident_mgr.create_incident(
    title='Service Down',
    description='Project is not responding to health checks',
    severity=IncidentSeverity.HIGH,
    affected_projects=['shadowops-bot'],
    event_type='downtime'
)

# Update incident
await incident_mgr.update_incident(
    incident_id=incident.id,
    status=IncidentStatus.IN_PROGRESS,
    update_message='Investigation started',
    author='admin'
)

# Resolve incident
await incident_mgr.resolve_incident(
    incident_id=incident.id,
    resolution_notes='Service restarted, health checks passing',
    author='admin'
)

# Get active incidents
active = incident_mgr.get_active_incidents()
```

### Deployment Manager API

```python
from src.integrations.deployment_manager import DeploymentManager

# Initialize
deploy_mgr = DeploymentManager(bot, config)

# Deploy project
result = await deploy_mgr.deploy_project(
    project_name='shadowops-bot',
    branch='main'  # optional, defaults to project config
)

# Result structure:
# {
#     'success': True/False,
#     'project': 'shadowops-bot',
#     'branch': 'main',
#     'duration_seconds': 15.2,
#     'tests_passed': True,
#     'backup_created': True,
#     'deployed': True,
#     'rolled_back': False,
#     'error': None  # or error message if failed
# }
```

---

## Event System

### SecurityEvent Structure

```python
from src.integrations.event_watcher import SecurityEvent

event = SecurityEvent(
    source='trivy',  # Source: trivy, fail2ban, crowdsec, aide
    event_type='vulnerability',  # Type: vulnerability, intrusion, integrity
    severity='HIGH',  # Severity: LOW, MEDIUM, HIGH, CRITICAL
    details={  # Event-specific details
        'VulnerabilityID': 'CVE-2024-1234',
        'PkgName': 'openssl',
        'InstalledVersion': '1.0.0',
        'FixedVersion': '1.1.0',
        'Title': 'Critical vulnerability',
        'Description': 'A security issue...'
    },
    is_persistent=True  # Whether this needs remediation
)
```

### Event Sources

#### Trivy (Docker Scans)
```python
{
    'source': 'trivy',
    'event_type': 'vulnerability',
    'severity': 'CRITICAL',
    'details': {
        'VulnerabilityID': 'CVE-2024-1234',
        'PkgName': 'package-name',
        'InstalledVersion': '1.0.0',
        'FixedVersion': '2.0.0',
        'total_critical': 5,
        'total_high': 10
    }
}
```

#### Fail2ban
```python
{
    'source': 'fail2ban',
    'event_type': 'intrusion',
    'severity': 'HIGH',
    'details': {
        'ip': '192.168.1.100',
        'jail': 'sshd',
        'action': 'banned',
        'timestamp': '2025-11-21T12:00:00'
    }
}
```

#### CrowdSec
```python
{
    'source': 'crowdsec',
    'event_type': 'intrusion',
    'severity': 'CRITICAL',
    'details': {
        'ip': '192.168.1.100',
        'scenario': 'crowdsecurity/ssh-bf',
        'decision': 'ban',
        'duration': '4h'
    }
}
```

#### AIDE
```python
{
    'source': 'aide',
    'event_type': 'integrity',
    'severity': 'MEDIUM',
    'details': {
        'file': '/etc/passwd',
        'change_type': 'modified',
        'timestamp': '2025-11-21T12:00:00'
    }
}
```

---

## Database Schema

### Security Analyst DB (PostgreSQL — `security_analyst`)

The bot uses asyncpg with a connection pool (min 2, max 5).
DSN from `config.security_analyst_dsn` or env `SECURITY_ANALYST_DB_URL`.

Key tables: `fix_attempts_v2`, `remediation_status`, `findings`,
`jules_pr_reviews`, `jules_daily_stats` (view).

### Agent Learning DB (PostgreSQL — `agent_learning`)

DSN from `config.agent_learning_dsn` or env `AGENT_LEARNING_DB_URL`.

Key tables: `agent_feedback`, `agent_quality_scores`, `agent_knowledge`,
`pn_generations`, `pn_variants`, `pn_examples`, `jules_review_examples`.

### Project Monitor State (JSON)

```json
{
  "project-name": {
    "total_checks": 1000,
    "successful_checks": 980,
    "failed_checks": 20
  }
}
```

### Incident Tracking (JSON)

```json
[
  {
    "id": "abc123",
    "title": "Service Down",
    "description": "Project not responding",
    "severity": "high",
    "affected_projects": ["shadowops-bot"],
    "event_type": "downtime",
    "status": "resolved",
    "created_at": "2025-11-21T10:00:00Z",
    "updated_at": "2025-11-21T10:30:00Z",
    "resolved_at": "2025-11-21T10:30:00Z",
    "thread_id": 123456789,
    "original_message_id": 987654321,
    "timeline": [
      {
        "timestamp": "2025-11-21T10:00:00Z",
        "event": "Incident created",
        "author": "system"
      },
      {
        "timestamp": "2025-11-21T10:15:00Z",
        "event": "Status changed: open → in_progress",
        "author": "admin"
      }
    ],
    "resolution_notes": "Service restarted successfully"
  }
]
```

---

## Error Codes & Handling

### Common Error Messages

#### Configuration Errors
- `Config file not found` - `config/config.yaml` missing
- `Invalid config structure` - YAML syntax error
- `Missing required field: discord.token` - Required config missing

#### AI Service Errors
- `No AI providers enabled` - All AI services disabled (`ai.enabled: false`)
- `Codex CLI not found` - `codex` binary missing from PATH
- `AI request timeout` - AI provider exceeded configured timeout

#### Deployment Errors
- `Project not found in deployment config` - Unknown project
- `Deployment already in progress` - Concurrent deployment blocked
- `Tests failed` - Pre-deployment tests didn't pass
- `Health check failed after deployment` - Post-deploy validation failed
- `Rollback failed` - Automatic rollback encountered error

#### Permission Errors
- `No sudo permissions for fail2ban-client` - Missing sudo access
- `Cannot access log file` - Insufficient file permissions
- `Service not found` - Systemd service doesn't exist

---

## Rate Limiting

### AI Requests
- **Codex CLI (primary)**: Timeout configurable via `ai.primary.timeout` (default: 300s)
- **Claude CLI (fallback)**: Timeout configurable via `ai.fallback.timeout` (default: 300s); automatic fallback on quota or timeout

### Discord Commands
- **Global**: No built-in rate limiting (Discord handles this)
- **Per-Command**: Some commands may have internal cooldowns

### GitHub Webhooks
- **No rate limiting** - All webhooks processed immediately

---

## Best Practices

### Configuration
1. **Always use version bounds** in requirements.txt
2. **Set approval_mode appropriately** for your risk tolerance
3. **Enable dry_run** for initial testing
4. **Configure backup_dir** on separate disk if possible

### Deployment
1. **Run tests** before enabling auto-deploy
2. **Configure health checks** for all projects
3. **Set reasonable check_interval** to avoid overload
4. **Monitor deployment_log** channel regularly

### AI Learning
1. **Let the system learn** - Don't intervene too quickly
2. **Review learning insights** periodically (`/get-ai-stats`)
3. **Keep Knowledge Base** for at least 90 days
4. **Analyze failed fixes** to improve prompts

### Security
1. **Rotate webhook_secret** periodically
2. **Use paranoid mode** for production initially
3. **Review DO-NOT-TOUCH.md** before first deployment
4. **Monitor customer_alerts** for incidents

---

## Troubleshooting API

### Debug Mode

Enable debug logging in config:
```yaml
debug_mode: true
```

### Log Locations
- **Application logs**: `logs/shadowops.log`
- **Systemd logs**: `sudo journalctl -u shadowops-bot -f`
- **Discord channel logs**: See auto-created log channels

### Common API Issues

**Knowledge Base connection issues:**
```bash
# Check PostgreSQL connectivity
psql "$SECURITY_ANALYST_DB_URL" -c "SELECT 1;"

# Restart bot (pool will reconnect)
sudo systemctl restart shadowops-bot
```

**Project monitor not starting:**
```bash
# Verify project configs
python3 -c "from src.utils.config import get_config; print(get_config().projects)"

# Check for URL accessibility
curl -I http://localhost:5000/health
```

**Webhook not receiving events:**
```bash
# Test webhook server
curl http://localhost:8080/health

# Check firewall
sudo ufw status

# Verify GitHub webhook configuration
# Check "Recent Deliveries" in GitHub webhook settings
```

---

**API Documentation v5.1** | Last Updated: 2026-04-12
