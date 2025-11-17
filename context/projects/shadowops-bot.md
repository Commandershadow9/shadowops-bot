# ShadowOps Bot (Security Automation & Discord Bot)

## Project Overview
Intelligent Discord bot for security monitoring, auto-remediation, and infrastructure management.
Self-managing security automation system with AI-powered decision making.

## Technology Stack
- **Language**: Python 3.11
- **Framework**: Discord.py 2.3.2
- **AI Models**: Ollama (llama3.1), OpenAI GPT-4o, Anthropic Claude 3.5 Sonnet
- **Async**: asyncio for concurrent operations
- **HTTP Client**: httpx 0.27.2

## Architecture

### Core Components
1. **Event Watcher** (`event_watcher.py`) - Event-driven security monitoring
2. **Self-Healing Coordinator** (`self_healing.py`) - Auto-remediation orchestration
3. **AI Service** (`ai_service.py`) - Hybrid AI analysis (Ollama + Cloud)
4. **Context Manager** (`context_manager.py`) - RAG system for project knowledge

### Security Integrations
- **Trivy** - Docker vulnerability scanning (6h intervals)
- **CrowdSec** - Threat detection and IP blocking (30s intervals)
- **Fail2ban** - Intrusion prevention (30s intervals)
- **AIDE** - File integrity monitoring (15min intervals)

## Discord Structure

### Categories
1. **Security Monitoring** - Real-time security alerts
2. **Auto-Remediation** - AI-powered fix requests and approvals
3. **System Status** - Bot health and metrics

### Channels
- `#critical` - Critical security events
- `#security` - General security alerts
- `#nexus` - System-wide notifications
- `#fail2ban` - Intrusion attempts
- `#docker` - Container security
- `#backups` - Backup status
- `#bot-status` - Bot operational status
- `#auto-remediation-alerts` - AI analysis and fix proposals
- `#auto-remediation-approvals` - Human approval requests
- `#auto-remediation-stats` - Success/failure metrics

## Event Persistence System

### Persistent Events (is_persistent=True)
These require actual fixes, not just alerts:
- **Trivy Docker Vulnerabilities** - Must update/rebuild containers
- **AIDE File Changes** - Investigate and approve/restore

### Self-Resolving Events (is_persistent=False)
These auto-expire after 24h:
- **CrowdSec Threats** - IPs already blocked
- **Fail2ban Bans** - IPs already banned

### Event Cache
- **File**: `logs/seen_events.json`
- **Format**: `{event_signature: timestamp}`
- **Expiration**: 24 hours for self-resolving events
- **Persistence**: Across bot restarts

## AI System

### Ollama (Primary - Local & Free)
- **Model**: llama3.1
- **API**: http://127.0.0.1:11434
- **Purpose**: Standard security analysis
- **Cost**: Free (local processing)

### OpenAI GPT-4o (Fallback)
- **Model**: gpt-4o
- **Purpose**: Complex analysis when Ollama needs help
- **Cost**: Pay-per-use (requires credits)

### Anthropic Claude 3.5 Sonnet (Fallback)
- **Model**: claude-3-5-sonnet-20241022
- **Purpose**: Security-focused analysis
- **Cost**: Pay-per-use (requires credits)

### Confidence Levels
- **95-100%**: Production-ready, auto-execute in AGGRESSIVE mode
- **85-95%**: Tested approach, auto-execute in BALANCED mode
- **70-85%**: Reasonable confidence, requires approval
- **<70%**: Uncertain, requires approval or skip

## Operating Modes

### PARANOID (Mode 1) - Current Default
- User must approve ALL fixes
- Maximum safety
- Learning phase
- All confidence levels → approval request

### BALANCED (Mode 2) - Future
- Auto-fix: confidence ≥85% AND low-risk operations
- Approval required: High-risk or confidence <85%
- Moderate automation

### AGGRESSIVE (Mode 3) - Future
- Auto-fix: confidence ≥75% AND not in do-not-touch list
- Minimal approvals
- Maximum automation
- Only critical systems require approval

## DO-NOT-TOUCH Rules

### NEVER Modify Without Approval
1. **Production Databases** - Customer data at risk
2. **Authentication Systems** - Could lock out users
3. **Active Docker Containers** - May disrupt services
4. **Network Firewall Rules** - Could block access
5. **System Users and Permissions** - Security implications

### Safe Auto-Fix Operations
1. **Fail2ban Rules** - Adding ban rules
2. **CrowdSec Decisions** - Blocking malicious IPs
3. **Log Rotation** - Disk space management
4. **Temporary File Cleanup** - /tmp cleaning
5. **Package Updates** - Non-critical packages with testing

## Configuration
- **Config File**: `config/config.json`
- **Secrets**: Environment variables for API keys
- **Channels**: Discord channel IDs in config
- **Scan Intervals**: Customizable per security tool

## Dependencies
- Discord API access
- Ollama service running (127.0.0.1:11434)
- Optional: OpenAI API key
- Optional: Anthropic API key
- Security tools installed: trivy, crowdsec, fail2ban, aide

## Project Location
`/home/cmdshadow/shadowops-bot`

## Logging
- **Main Log**: `logs/shadowops-bot.log`
- **Event Cache**: `logs/seen_events.json`
- **Log Rotation**: Daily rotation with 7-day retention

## Critical Features

### Live Status Updates
Real-time Discord message editing during AI analysis:
- Phase indicators (Data Collection → AI Analysis → Validation)
- Progress bars: ▰▰▰▱▱▱▱▱▱▱
- AI reasoning display
- Confidence scores

### Batch Processing
- **Trivy**: Aggregate all CVEs into single approval
- **Fail2ban**: Only alert for coordinated attacks (>50 IPs or ≥10 SSH attempts)
- Reduces notification spam

### Self-Healing Workflow
1. Event detected by watcher
2. Check if new event (persistence system)
3. Send channel alert
4. AI analyzes with live updates
5. Generate fix strategy
6. Request approval (PARANOID mode)
7. Execute fix on approval
8. Verify success
9. Report results

## Security Considerations
- Bot has elevated permissions (sudo access for security tools)
- All fixes are logged in audit trail
- Approval system prevents unauthorized changes
- Context learning prevents accidental damage
- Rollback capability for failed fixes

## Startup Process
1. Initialize Discord client
2. Load configuration
3. Create/verify Discord channels
4. Initialize AI services (Ollama primary)
5. Start Event Watchers
6. Start Self-Healing Coordinator
7. Schedule daily health checks
8. Ready for monitoring

## Common Issues

### AI Analysis Failures
- **Cause**: Ollama service down or cloud API quota exceeded
- **Fix**: Check `systemctl status ollama`, verify API keys
- **Fallback**: Uses next available AI provider

### Event Duplication After Restart
- **Cause**: Persistent event cache not loaded
- **Fix**: Verify `logs/seen_events.json` exists and readable
- **Prevention**: Automatic save on new events

### Missing Approvals
- **Cause**: Confidence too high, auto-executed in non-PARANOID mode
- **Fix**: Check mode setting, review stats channel
- **Prevention**: Start with PARANOID mode

## Monitoring
- Bot status messages in `#bot-status`
- Event watcher health checks
- AI service availability
- Daily system health report (06:00 UTC)
