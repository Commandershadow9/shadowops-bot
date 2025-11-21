# 🔧 ShadowOps API Documentation v3.1

Complete API reference for ShadowOps Security Guardian.

## Table of Contents

- [Discord Commands](#discord-commands)
- [Configuration Reference](#configuration-reference)
- [GitHub Webhook API](#github-webhook-api)
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
- Ollama status (enabled/disabled, model, URL)
- Claude status (enabled/disabled, model)
- OpenAI status (enabled/disabled, model)
- Active fallback chain
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
  ollama:
    enabled: true                           # Enable Ollama (local AI)
    url: http://localhost:11434             # Ollama API URL
    model: phi3:mini                        # Model for regular analysis
    model_critical: llama3.1                # Model for CRITICAL events
    hybrid_models: true                     # Use different models by severity
    request_delay_seconds: 4.0              # Rate limiting (seconds between requests)

  anthropic:
    enabled: false                          # Enable Claude
    api_key: null                           # Anthropic API key
    model: claude-3-5-sonnet-20241022       # Claude model

  openai:
    enabled: false                          # Enable OpenAI
    api_key: null                           # OpenAI API key
    model: gpt-4o                           # OpenAI model

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
  auto_deploy: true                         # Auto-deploy on push to deploy branches
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
     auto_deploy: true
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

## Python API

### Knowledge Base API

```python
from src.integrations.knowledge_base import KnowledgeBase

# Initialize
kb = KnowledgeBase(db_path="data/knowledge_base.db")

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

### Knowledge Base (SQLite)

#### fixes table
```sql
CREATE TABLE fixes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_signature TEXT NOT NULL,  -- Unique event identifier
    event_type TEXT NOT NULL,  -- vulnerability, intrusion, etc.
    severity TEXT,
    event_details TEXT,  -- JSON
    strategy TEXT NOT NULL,  -- JSON: Full fix strategy
    result TEXT NOT NULL,  -- 'success' or 'failed'
    duration_seconds REAL,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0
);

CREATE INDEX idx_event_signature ON fixes(event_signature);
CREATE INDEX idx_event_type ON fixes(event_type);
CREATE INDEX idx_result ON fixes(result);
CREATE INDEX idx_timestamp ON fixes(timestamp);
```

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
- `No AI providers enabled` - All AI services disabled
- `Ollama connection failed` - Cannot reach Ollama server
- `AI request timeout` - AI provider took too long

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
- **Ollama**: Configurable delay (`ai.ollama.request_delay_seconds`, default: 4.0s)
- **Anthropic**: Built-in retry with exponential backoff (1s, 2s, 4s)
- **OpenAI**: Built-in retry with exponential backoff (1s, 2s, 4s)

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

**Knowledge Base locked:**
```bash
# Check if database is locked
sqlite3 data/knowledge_base.db "PRAGMA busy_timeout=5000;"

# If still locked, restart bot
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

**API Documentation v3.1** | Last Updated: 2025-11-21
