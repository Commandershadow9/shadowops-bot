# ShadowOps Bot - Changelog

## [2.0.0] - 2025-11-14

### 🎯 Major Feature: Event-Driven Auto-Remediation System

#### ✨ Added

**AI-Powered Security Analysis:**
- Integrated OpenAI GPT-4o and Anthropic Claude 3.5 Sonnet for deep security analysis
- Live status updates during AI analysis with progress bars and reasoning display
- Confidence-based fix validation (85% threshold for execution safety)
- Detailed AI analysis with CVE research, package investigation, and risk assessment

**Event-Driven Architecture:**
- Complete rewrite from polling-based to event-driven monitoring
- Unified Event Watcher system replacing individual monitor tasks
- Persistent event tracking with 24h cache to prevent duplicate alerts
- Intelligent event classification: Persistent (Docker, AIDE) vs. Self-Resolving (Fail2ban, CrowdSec)

**Smart Event Deduplication:**
- Persistent events (Docker vulnerabilities, AIDE violations) always trigger actions until fixed
- Self-resolving events (Fail2ban bans, CrowdSec blocks) use 24h expiration cache
- Event signatures stored in `logs/seen_events.json` for restart persistence
- Automatic cleanup of expired events (>24h)

**Batch Processing:**
- Trivy: Consolidates 270 individual vulnerabilities → 1 batch approval request
- Fail2ban: Aggregates multiple bans → 1 summary with statistics
- Intelligent Fail2ban analysis: Only requests approval for coordinated attacks (>50 IPs or >=10 SSH attempts)

**Enhanced Approval Workflow:**
1. Security event detected → Immediate alert sent to relevant Discord channels
2. AI analyzes threat in background → Live status updates shown to user
3. Fix strategy generated with confidence score → Detailed approval request created
4. User sees complete context before decision → Approve/Deny with full information

**Live Status Updates:**
- Real-time Discord message updates during AI analysis
- Progress indicators (visual bars: ▰▰▰▱▱▱▱▱▱▱)
- AI reasoning display showing thought process
- Phase-based updates: Data Collection → Context Building → AI Analysis → Strategy Validation

**Channel Integration:**
- Event Watcher automatically sends alerts to appropriate channels
- Docker vulnerabilities → #docker + #critical
- Fail2ban bans → #fail2ban
- CrowdSec threats → #critical
- AIDE violations → #critical
- Auto-remediation requests → #auto-remediation-approvals

#### 🔧 Changed

**Replaced Old System:**
- Disabled legacy `monitor_security` task (30s polling)
- Event Watcher now handles all security monitoring
- Single unified system for alerts + auto-remediation

**Scan Intervals (Optimized):**
- Trivy: 21,600s (6 hours) - Docker scans are resource-intensive
- CrowdSec: 30s - Active threats require fast response
- Fail2ban: 30s - Real-time intrusion detection
- AIDE: 900s (15 minutes) - File integrity monitoring

**Confidence Requirements:**
- <85%: Warning displayed, execution blocked, manual review required
- 85-95%: Safe for manual approval
- ≥95%: Safe for automation
- Execution automatically blocked for fixes with <85% confidence

**Event Model:**
- Added `is_persistent` flag to SecurityEvent dataclass
- Enhanced event signatures for better deduplication
- Improved event serialization for cache persistence

#### 📁 New Files

- `src/integrations/ai_service.py` - AI analysis service (OpenAI + Anthropic)
- `CHANGELOG.md` - Version history and feature documentation
- `logs/seen_events.json` - Persistent event cache (gitignored)

#### 📦 Dependencies

**Added:**
- `openai==1.54.0` - OpenAI GPT-4o integration
- `anthropic==0.39.0` - Claude 3.5 Sonnet integration

#### 🐛 Known Issues

**HTTP Client Conflict:**
- OpenAI/Anthropic libraries conflict with discord.py's httpx dependency
- Error: `AsyncClient.__init__() got an unexpected keyword argument 'proxies'`
- **Workaround:** System falls back to predefined strategies (70% confidence)
- **Impact:** Live updates work, but AI analysis currently fails
- **Status:** Investigating httpx version compatibility

**Temporary Behavior:**
- Event detection: ✅ Working
- Channel alerts: ✅ Working
- Live status updates: ✅ Working
- AI analysis: ❌ Fails (HTTP conflict)
- Approval requests: ✅ Working (with fallback strategy)

#### 🔐 Security Improvements

**Intelligent Threat Detection:**
- Fail2ban: Only alerts on coordinated attacks (>50 IPs) or targeted SSH bruteforce (>=10 attempts)
- Normal bans (already handled by Fail2ban) don't create approval spam
- Reduces approval fatigue while maintaining security visibility

**Confidence-Based Safety:**
- Unsafe fixes (<85%) cannot execute even if approved
- Detailed warnings shown to users before approval
- Risk assessment included in all fix strategies

#### 📊 Technical Details

**Event Persistence:**
```json
{
  "trivy_batch_270c_0h_0m_5i": 1763078456.126,
  "fail2ban_batch_23bans": 1763078396.147
}
```
- Events stored with Unix timestamps
- Automatic expiration for self-resolving events
- Persistent events always treated as new

**Batch Event Format:**
```python
SecurityEvent(
    source='trivy',
    event_type='docker_vulnerabilities_batch',
    severity='CRITICAL',
    details={
        'Stats': {
            'critical': 270,
            'high': 0,
            'medium': 0,
            'images': 5
        }
    },
    is_persistent=True
)
```

#### 🚀 Next Steps

1. **Resolve HTTP Client Conflict:**
   - Investigate httpx version pinning
   - Consider using separate HTTP client for AI services
   - Test with isolated virtual environment

2. **Enhanced AI Features:**
   - Once HTTP conflict resolved, enable full AI analysis
   - Add more detailed CVE research
   - Implement fix success prediction

3. **Monitoring & Metrics:**
   - Track fix success rates
   - Measure AI confidence accuracy
   - Monitor false positive rates

---

## [1.0.0] - 2024-11-12

### Initial Release

- Basic security monitoring (Fail2ban, CrowdSec, Docker, AIDE)
- Discord integration with channel-based alerts
- Manual approval workflow
- Polling-based monitoring (30s intervals)
