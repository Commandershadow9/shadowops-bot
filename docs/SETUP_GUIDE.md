# 🚀 ShadowOps Setup Guide v3.1

Complete step-by-step setup guide for ShadowOps Security Guardian.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Discord Bot Setup](#discord-bot-setup)
3. [Server Installation](#server-installation)
4. [Configuration](#configuration)
5. [Optional: AI Setup](#optional-ai-setup)
6. [Optional: GitHub Webhooks](#optional-github-webhooks)
7. [Service Installation](#service-installation)
8. [Verification](#verification)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements

**Minimum:**
- OS: Ubuntu 20.04+ / Debian 11+ / CentOS 8+
- CPU: 2 cores
- RAM: 2 GB
- Disk: 10 GB free space
- Python: 3.9+

**Recommended:**
- OS: Ubuntu 22.04 LTS
- CPU: 4 cores
- RAM: 4 GB
- Disk: 20 GB free space
- Python: 3.11+

### Required Permissions

- `sudo` access for:
  - Reading security logs (`/var/log/fail2ban/`, `/var/log/crowdsec/`)
  - Managing systemd services
  - Installing system packages
  - Deploying projects (v3.1)

### Required Software

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3 python3-pip python3-venv git systemd rsync

# Verify Python version
python3 --version  # Should be 3.9 or higher
```

---

## Discord Bot Setup

### Step 1: Create Discord Application

1. **Go to Discord Developer Portal:**
   - Navigate to https://discord.com/developers/applications
   - Login with your Discord account

2. **Create New Application:**
   - Click "New Application" (top right)
   - Name: `ShadowOps`
   - Click "Create"

### Step 2: Configure Bot

1. **Navigate to Bot tab:**
   - Click "Bot" in left sidebar
   - Click "Add Bot" → "Yes, do it!"

2. **Configure Bot Settings:**
   - **Username**: ShadowOps (or your choice)
   - **Icon**: Upload an icon (optional)
   - **Public Bot**: OFF (keep private)
   - **Requires OAuth2 Code Grant**: OFF

3. **Get Bot Token:**
   - Click "Reset Token" → "Yes, do it!"
   - **Copy the token** (you'll need this later)
   - ⚠️ **IMPORTANT**: Token is shown only once! Save it securely.

4. **Configure Privileged Gateway Intents:**
   - Scroll down to "Privileged Gateway Intents"
   - Enable (optional):
     - ✅ **Presence Intent** (if you want online status)
     - ✅ **Server Members Intent** (for member tracking)
     - ✅ **Message Content Intent** (not required for slash commands)

### Step 3: Generate Invite Link

1. **Navigate to OAuth2 → URL Generator:**
   - Click "OAuth2" in left sidebar
   - Click "URL Generator"

2. **Select Scopes:**
   - ✅ `bot`
   - ✅ `applications.commands`

3. **Select Bot Permissions:**
   - Text Permissions:
     - ✅ Send Messages
     - ✅ Send Messages in Threads
     - ✅ Embed Links
     - ✅ Attach Files
     - ✅ Read Message History
     - ✅ Add Reactions
   - Thread Permissions (v3.1):
     - ✅ Create Public Threads
     - ✅ Send Messages in Threads
     - ✅ Manage Threads

4. **Invite Bot to Server:**
   - Copy the generated URL at bottom
   - Paste into browser
   - Select your Discord server
   - Click "Authorize"
   - Complete captcha

### Step 4: Get Server and Channel IDs

1. **Enable Developer Mode in Discord:**
   - Discord → Settings → Advanced
   - Toggle "Developer Mode" ON

2. **Get Guild (Server) ID:**
   - Right-click on your server icon
   - Click "Copy Server ID"
   - Save this number (you'll need it in config)

3. **Channel IDs (Optional):**
   - The bot auto-creates all channels
   - But you can manually create and get IDs if preferred:
     - Right-click on channel → "Copy Channel ID"

---

## Server Installation

### Step 1: Clone Repository

```bash
# Create directory
cd /home/user/
git clone https://github.com/Commandershadow9/shadowops-bot.git
cd shadowops-bot
```

Or if using SSH:

```bash
git clone git@github.com:Commandershadow9/shadowops-bot.git
cd shadowops-bot
```

### Step 2: Create Virtual Environment

```bash
# Create venv
python3 -m venv venv

# Activate venv
source venv/bin/activate

# Verify activation (should show venv path)
which python3
```

### Step 3: Install Dependencies

```bash
# Install production dependencies
pip install -r requirements.txt

# Install development dependencies (optional, for testing)
pip install -r requirements-dev.txt

# Verify installation
pip list | grep discord.py
pip list | grep openai
pip list | grep anthropic
```

### Step 4: Create Required Directories

```bash
# Create all required directories
mkdir -p logs
mkdir -p data
mkdir -p backups
mkdir -p context/git_history
mkdir -p context/logs
mkdir -p config

# Set permissions
chmod 755 logs data backups context config
```

---

## Configuration

### Step 1: Create Config File

```bash
# Copy example config
cp config/config.example.yaml config/config.yaml

# Secure the file (important!)
chmod 600 config/config.yaml

# Edit config
nano config/config.yaml
```

### Step 2: Basic Configuration

**Minimal config to get started:**

```yaml
discord:
  token: "YOUR_BOT_TOKEN_HERE"  # Paste token from Discord Developer Portal
  guild_id: 123456789            # Paste server ID you copied

ai:
  ollama:
    enabled: false  # Start with disabled, enable after testing bot works

auto_remediation:
  enabled: true
  dry_run: true    # IMPORTANT: Start in dry-run mode!
  approval_mode: paranoid

projects:
  shadowops-bot:
    enabled: true
    path: /home/user/shadowops-bot  # Update with your actual path
```

**Save and exit:** `Ctrl+X`, then `Y`, then `Enter`

### Step 3: Verify Configuration

```bash
# Test config loading
python3 -c "from src.utils.config import get_config; get_config()"

# Should print: "✅ Config loaded successfully" (no errors)
```

---

## Optional: AI Setup

ShadowOps supports 3 AI providers. You need **at least one** enabled.

### Option 1: Ollama (Local, Free, Recommended)

**Advantages:**
- Free and private
- Fast (local)
- No API keys needed
- No rate limits

**Installation:**

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull recommended models
ollama pull phi3:mini      # Fast, lightweight (2.7GB)
ollama pull llama3.1       # Better for critical issues (4.7GB)

# Verify installation
ollama list

# Test model
ollama run phi3:mini "Hello, how are you?"
```

**Enable in config:**

```yaml
ai:
  ollama:
    enabled: true
    url: http://localhost:11434
    model: phi3:mini
    model_critical: llama3.1
    hybrid_models: true
```

### Option 2: Anthropic Claude (Cloud, Paid)

**Advantages:**
- Excellent reasoning
- Great for complex security analysis
- Good context window

**Setup:**

1. Get API key:
   - Go to https://console.anthropic.com/
   - Create account / Login
   - Navigate to API Keys
   - Create new API key
   - Copy key

2. Enable in config:
   ```yaml
   ai:
     anthropic:
       enabled: true
       api_key: "sk-ant-..."  # Paste your API key
       model: claude-3-5-sonnet-20241022
   ```

### Option 3: OpenAI (Cloud, Paid)

**Advantages:**
- Fast responses
- Good general knowledge
- Familiar API

**Setup:**

1. Get API key:
   - Go to https://platform.openai.com/
   - Create account / Login
   - Navigate to API Keys
   - Create new secret key
   - Copy key

2. Enable in config:
   ```yaml
   ai:
     openai:
       enabled: true
       api_key: "sk-..."  # Paste your API key
       model: gpt-4o
   ```

### Fallback Chain

If you enable multiple providers, ShadowOps uses them in this order:
1. **Ollama** (if enabled) → Fast, free, local
2. **Claude** (if enabled) → Fallback if Ollama fails
3. **OpenAI** (if enabled) → Fallback if both above fail

**Recommended setup for production:**
```yaml
ai:
  ollama:
    enabled: true  # Primary (free, fast)
  anthropic:
    enabled: true  # Fallback for critical events
  openai:
    enabled: false  # Optional second fallback
```

---

## Optional: GitHub Webhooks

Enable auto-deployment when you push code to GitHub.

### Step 1: Generate Webhook Secret

```bash
# Generate strong random secret
openssl rand -hex 32
```

Copy the output (64-character hex string).

### Step 2: Configure ShadowOps

Edit `config/config.yaml`:

```yaml
github:
  enabled: true
  webhook_secret: "YOUR_GENERATED_SECRET_HERE"
  webhook_port: 8080
  auto_deploy: true
  deploy_branches:
    - main
    - master
```

### Step 3: Configure Firewall

```bash
# Allow webhook port through firewall
sudo ufw allow 8080/tcp

# Verify
sudo ufw status
```

### Step 4: Configure Repository Webhook

1. **Go to your GitHub repository:**
   - Settings → Webhooks → Add webhook

2. **Configure webhook:**
   - **Payload URL**: `http://YOUR_SERVER_IP:8080/webhook`
   - **Content type**: `application/json`
   - **Secret**: (paste the secret from Step 1)
   - **Which events**: Select:
     - ✅ Pushes
     - ✅ Pull requests
     - ✅ Releases

3. **Save webhook:**
   - Click "Add webhook"
   - GitHub will send a test ping
   - Check "Recent Deliveries" tab for green checkmark

### Step 5: Configure Project Deployment

Edit `config/config.yaml`:

```yaml
projects:
  shadowops-bot:
    enabled: true
    path: /home/user/shadowops-bot
    branch: main

    # Deployment config (v3.1)
    deploy:
      run_tests: true
      test_command: pytest tests/
      post_deploy_command: pip install -r requirements.txt
      service_name: shadowops-bot
```

### Step 6: Configure Sudoers (for deployment)

```bash
# Edit sudoers
sudo visudo

# Add this line (replace 'username' with your user):
username ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart shadowops-bot
username ALL=(ALL) NOPASSWD: /usr/bin/systemctl status shadowops-bot
```

---

## Service Installation

### Step 1: Create Systemd Service File

```bash
# Copy service file
sudo cp shadowops-bot.service /etc/systemd/system/

# Edit if paths are different
sudo nano /etc/systemd/system/shadowops-bot.service
```

**Verify these paths match your installation:**

```ini
[Service]
WorkingDirectory=/home/user/shadowops-bot
ExecStart=/home/user/shadowops-bot/venv/bin/python3 /home/user/shadowops-bot/src/bot.py
User=user
```

### Step 2: Enable and Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service (start on boot)
sudo systemctl enable shadowops-bot

# Start service
sudo systemctl start shadowops-bot

# Check status
sudo systemctl status shadowops-bot
```

**Expected output:**
```
● shadowops-bot.service - ShadowOps Security Bot
   Loaded: loaded (/etc/systemd/system/shadowops-bot.service; enabled)
   Active: active (running) since ...
```

### Step 3: View Logs

```bash
# Live logs
sudo journalctl -u shadowops-bot -f

# Last 100 lines
sudo journalctl -u shadowops-bot -n 100

# Logs since today
sudo journalctl -u shadowops-bot --since today
```

### Service Management Commands

```bash
# Start
sudo systemctl start shadowops-bot

# Stop
sudo systemctl stop shadowops-bot

# Restart
sudo systemctl restart shadowops-bot

# Status
sudo systemctl status shadowops-bot

# Disable (don't start on boot)
sudo systemctl disable shadowops-bot

# Enable (start on boot)
sudo systemctl enable shadowops-bot
```

---

## Verification

### Step 1: Check Bot is Online

1. Open Discord
2. Look for ShadowOps bot in member list
3. Should show as "Online" with green dot

### Step 2: Check Channels Were Created

The bot auto-creates these channels:

**🤖 Auto-Remediation** category:
- 🚨-security-alerts
- ✅-approval-requests
- ⚙️-execution-logs
- 📊-stats
- 🧠-ai-learning
- 🔧-code-fixes
- ⚡-orchestrator

**🌐 Multi-Project** category (v3.1):
- 👥-customer-alerts
- 📊-customer-status
- 🚀-deployment-log

### Step 3: Test Slash Commands

In any Discord channel, type `/` and you should see ShadowOps commands:

```
/status               - Should show system status
/get-ai-stats        - Should show AI provider status
/alle-projekte       - Should show monitored projects
```

### Step 4: Verify Logs

```bash
# Check application log
tail -f logs/shadowops.log

# Should see lines like:
# [INFO] Bot started successfully
# [INFO] Connected to Discord
# [INFO] Slash commands synced
```

### Step 5: Run Tests (Optional)

```bash
# Activate venv if not already active
source venv/bin/activate

# Run test suite
pytest tests/ -v

# Should see 150+ tests passing
```

---

## Troubleshooting

### Bot Won't Start

**Symptom:** `systemctl status shadowops-bot` shows "failed"

**Solutions:**

1. **Check logs:**
   ```bash
   sudo journalctl -u shadowops-bot -n 50
   ```

2. **Common issues:**

   **Missing token:**
   ```
   Error: discord.token is required
   ```
   Solution: Add token to `config/config.yaml`

   **Invalid token:**
   ```
   Error: Improper token has been passed
   ```
   Solution: Regenerate token in Discord Developer Portal

   **Python not found:**
   ```
   Failed to execute command
   ```
   Solution: Check path in service file matches your installation

3. **Test manually:**
   ```bash
   cd /home/user/shadowops-bot
   source venv/bin/activate
   python3 src/bot.py
   ```

   Should show startup logs. Press `Ctrl+C` to stop.

### Slash Commands Not Appearing

**Symptom:** Can't see `/status` or other commands in Discord

**Solutions:**

1. **Wait up to 1 hour:**
   - Discord caches commands
   - Can take up to 1 hour to appear

2. **Force re-invite bot:**
   - Go to OAuth2 → URL Generator (Discord Developer Portal)
   - Generate new invite URL with `applications.commands`
   - Re-invite bot to server

3. **Check bot permissions:**
   - Bot needs "Use Application Commands" permission

### AI Service Not Working

**Symptom:** `/get-ai-stats` shows all providers disabled

**Solutions:**

1. **Enable at least one provider:**
   ```yaml
   ai:
     ollama:
       enabled: true  # Set to true
   ```

2. **For Ollama specifically:**
   ```bash
   # Check Ollama is running
   curl http://localhost:11434/api/tags

   # Should return JSON with model list
   # If not, start Ollama:
   systemctl start ollama
   ```

3. **Restart bot:**
   ```bash
   sudo systemctl restart shadowops-bot
   ```

### Deployment Failing

**Symptom:** GitHub webhook triggers but deployment fails

**Solutions:**

1. **Check permissions:**
   ```bash
   # Test sudo access
   sudo systemctl status shadowops-bot

   # Should work without password
   # If asks for password, add to sudoers
   ```

2. **Check paths:**
   ```yaml
   projects:
     shadowops-bot:
       path: /home/user/shadowops-bot  # Verify this is correct
   ```

3. **Check deployment logs:**
   ```bash
   tail -f logs/shadowops.log | grep deployment
   ```

4. **Test webhook manually:**
   ```bash
   curl -X POST http://localhost:8080/health

   # Should return:
   # {"status":"healthy","service":"github-webhook","timestamp":"..."}
   ```

### Permission Denied Errors

**Symptom:** Bot can't read logs or execute commands

**Solutions:**

1. **Add user to required groups:**
   ```bash
   # For fail2ban
   sudo usermod -a -G adm $USER

   # For systemd logs
   sudo usermod -a -G systemd-journal $USER

   # Log out and back in for changes to take effect
   ```

2. **Configure sudoers:**
   ```bash
   sudo visudo

   # Add (replace 'user' with your username):
   user ALL=(ALL) NOPASSWD: /usr/bin/fail2ban-client
   user ALL=(ALL) NOPASSWD: /usr/bin/systemctl
   ```

3. **Check file permissions:**
   ```bash
   ls -la /var/log/fail2ban/fail2ban.log
   ls -la /var/log/crowdsec/crowdsec.log

   # Should be readable by your user or group
   ```

### High Memory Usage

**Symptom:** Bot using excessive RAM

**Solutions:**

1. **Disable unused AI providers:**
   ```yaml
   ai:
     ollama:
       enabled: true  # Keep
     anthropic:
       enabled: false  # Disable if not using
     openai:
       enabled: false  # Disable if not using
   ```

2. **Use smaller Ollama model:**
   ```yaml
   ai:
     ollama:
       model: phi3:mini  # ~2GB RAM
       # Instead of llama3.1 (~4GB RAM)
   ```

3. **Reduce batch size:**
   ```yaml
   auto_remediation:
     max_batch_size: 5  # Reduce from 10
   ```

### Webhook Not Receiving Events

**Symptom:** GitHub shows webhook deliveries failing

**Solutions:**

1. **Check firewall:**
   ```bash
   sudo ufw status

   # If port 8080 not allowed:
   sudo ufw allow 8080/tcp
   ```

2. **Check bot is listening:**
   ```bash
   sudo netstat -tlnp | grep 8080

   # Should show Python process listening on port 8080
   ```

3. **Test from outside:**
   ```bash
   # From another machine/server:
   curl http://YOUR_SERVER_IP:8080/health

   # Should return JSON health status
   ```

4. **Check webhook secret:**
   - Verify secret in `config/config.yaml` matches GitHub
   - Regenerate if unsure

---

## Next Steps

### 1. Test in Dry-Run Mode

Keep `dry_run: true` for at least 24 hours:

```yaml
auto_remediation:
  dry_run: true
```

Monitor logs to ensure fixes are detected and planned correctly.

### 2. Review Logs

Check Discord channels for AI learning logs:
- 🧠-ai-learning
- 🔧-code-fixes
- ⚡-orchestrator

### 3. Configure Projects

Add all your projects to monitoring:

```yaml
projects:
  project1:
    enabled: true
    path: /home/user/project1
    monitor:
      enabled: true
      url: http://localhost:3000/health
```

### 4. Enable Auto-Remediation

After testing, enable real execution:

```yaml
auto_remediation:
  dry_run: false
  approval_mode: paranoid  # Still require manual approval
```

### 5. Gradually Increase Autonomy

After 1-2 weeks of successful operation:

```yaml
auto_remediation:
  approval_mode: auto  # Auto-fix non-critical issues
```

---

## Security Checklist

- [ ] Config file has correct permissions (`chmod 600 config/config.yaml`)
- [ ] Bot token is not committed to git
- [ ] Webhook secret is strong (32+ characters)
- [ ] Sudoers is configured with NOPASSWD only for specific commands
- [ ] Firewall only allows necessary ports (8080 for webhooks)
- [ ] DO-NOT-TOUCH.md is configured for your environment
- [ ] Dry-run mode tested before enabling real execution
- [ ] Backup directory exists and has sufficient space
- [ ] Logs are monitored regularly
- [ ] Test suite passes (`pytest tests/`)

---

## Additional Resources

- [README.md](../README.md) - Project overview
- [API.md](./API.md) - Complete API reference
- [CHANGELOG.md](../CHANGELOG.md) - Version history
- [GitHub Repository](https://github.com/Commandershadow9/shadowops-bot)

---

## Getting Help

If you encounter issues not covered in this guide:

1. **Check logs:**
   ```bash
   sudo journalctl -u shadowops-bot -f
   tail -f logs/shadowops.log
   ```

2. **Run tests:**
   ```bash
   pytest tests/ -v
   ```

3. **Ask in Discord:**
   - Use the 🚨-security-alerts channel
   - Tag an admin

4. **Create GitHub Issue:**
   - https://github.com/Commandershadow9/shadowops-bot/issues
   - Include logs and config (remove sensitive data!)

---

**Setup Guide v3.1** | Last Updated: 2025-11-21

**You're all set!** 🎉 ShadowOps is now protecting your infrastructure.
