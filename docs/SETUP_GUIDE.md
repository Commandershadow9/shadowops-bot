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

## 4. Configuration

### Step 1: Create Config File

```bash
# Copy example config
cp config/config.example.yaml config/config.yaml

# Secure the file (important!)
chmod 600 config/config.yaml

# Edit config for static values
nano config/config.yaml
```

### Step 2: Set Secrets as Environment Variables (Critical!)

Sensitive data like your Bot Token and AI API keys **must not** be stored in the `config.yaml` file. They should be provided as environment variables.

A common way to manage this is to create a `.env` file in the root of your project.

```bash
# Create and edit the .env file
nano .env
```

Add your secrets to this file:

```bash
# .env file

# Discord Bot Token (Required)
DISCORD_BOT_TOKEN="YOUR_BOT_TOKEN_HERE"

# AI API Keys (Optional)
ANTHROPIC_API_KEY="sk-ant-..."
OPENAI_API_KEY="sk-..."
GITHUB_TOKEN="ghp_..."
```

**Important:** You must ensure your `systemd` service file loads this `.env` file. Edit `/etc/systemd/system/shadowops-bot.service` and add the `EnvironmentFile` line in the `[Service]` section:

```ini
[Service]
# ... other lines
EnvironmentFile=/home/user/shadowops-bot/.env
# ... other lines
```

### Step 3: Basic Configuration

Now, edit `config/config.yaml` for non-sensitive, static values.

**Minimal `config.yaml` to get started:**

```yaml
discord:
  # token: "" # This is now set via the DISCORD_BOT_TOKEN environment variable
  guild_id: 123456789 # Paste server ID you copied

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

### Step 4: Verify Configuration

```bash
# Test config loading
# Note: This test will fail if DISCORD_BOT_TOKEN is not set in your current shell
export DISCORD_BOT_TOKEN="test" # Temporarily set for validation
python3 -c "from src.utils.config import get_config; get_config()"

# Should print: "✅ Config loaded successfully" (no errors)
```

---

## 5. Optional: AI Setup

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
```

**Enable in `config.yaml`:**

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

**Setup:**

1.  Get API key from https://console.anthropic.com/
2.  Add it to your `.env` file:
    ```bash
    # .env
    ANTHROPIC_API_KEY="sk-ant-..."
    ```
3.  Enable in `config.yaml`:
    ```yaml
    ai:
      anthropic:
        enabled: true
        # api_key: "" # Set via ANTHROPIC_API_KEY environment variable
        model: claude-3-5-sonnet-20241022
    ```

### Option 3: OpenAI (Cloud, Paid)

**Setup:**

1.  Get API key from https://platform.openai.com/
2.  Add it to your `.env` file:
    ```bash
    # .env
    OPENAI_API_KEY="sk-..."
    ```
3.  Enable in `config.yaml`:
    ```yaml
    ai:
      openai:
        enabled: true
        # api_key: "" # Set via OPENAI_API_KEY environment variable
        model: gpt-4o
    ```

### Fallback Chain

If you enable multiple providers, ShadowOps uses them in this order:
1.  **Ollama** (if enabled) → Fast, free, local
2.  **Claude** (if enabled) → Fallback if Ollama fails
3.  **OpenAI** (if enabled) → Fallback if both above fail

---

## 6. Optional: GitHub Webhooks

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

1.  **Go to your GitHub repository:**
    - Settings → Webhooks → Add webhook
2.  **Configure webhook:**
    - **Payload URL**: `http://YOUR_SERVER_IP:8080/webhook`
    - **Content type**: `application/json`
    - **Secret**: (paste the secret from Step 1)
    - **Which events**: Select:
        - ✅ Pushes
        - ✅ Pull requests
        - ✅ Releases
3.  **Save webhook**.

---

## 7. Service Installation

### Step 1: Create Systemd Service File

```bash
# Copy service file
sudo cp shadowops-bot.service /etc/systemd/system/

# Edit service file to add EnvironmentFile directive
sudo nano /etc/systemd/system/shadowops-bot.service
```

**Verify paths and add `EnvironmentFile`:**

```ini
[Service]
WorkingDirectory=/home/user/shadowops-bot
ExecStart=/home/user/shadowops-bot/venv/bin/python3 /home/user/shadowops-bot/src/bot.py
User=user
# Add this line:
EnvironmentFile=/home/user/shadowops-bot/.env
```

### Step 2: Enable and Start Service

```bash
# Reload systemd to apply changes
sudo systemctl daemon-reload

# Enable service (start on boot)
sudo systemctl enable shadowops-bot

# Start service
sudo systemctl start shadowops-bot

# Check status
sudo systemctl status shadowops-bot
```

### Step 3: View Logs

```bash
# Live logs
sudo journalctl -u shadowops-bot -f
```

---

## 8. Verification
(No changes needed in this section)

---

## 9. Troubleshooting

### Bot Won't Start

**Symptom:** `systemctl status shadowops-bot` shows "failed"

**Solutions:**

1.  **Check logs:** `sudo journalctl -u shadowops-bot -n 50`
2.  **Common issues:**
    -   **Missing token:**
        ```
        Error: Missing required config fields: discord.token (or DISCORD_BOT_TOKEN env var)
        ```
        **Solution:** Ensure `DISCORD_BOT_TOKEN` is set in your `.env` file and that the systemd service loads it.
    -   **Invalid token:**
        ```
        Error: Improper token has been passed
        ```
        **Solution:** Regenerate the token in the Discord Developer Portal and update your `.env` file.

### Slash Commands Not Appearing
(No changes needed)

### AI Service Not Working

**Symptom:** `/get-ai-stats` shows API key as "Missing"

**Solution:**
1.  Ensure the API key is correctly set in your `.env` file (e.g., `ANTHROPIC_API_KEY="..."`).
2.  Reload the systemd daemon (`sudo systemctl daemon-reload`) and restart the bot (`sudo systemctl restart shadowops-bot`) after editing the `.env` file.

### Permission Denied Errors

**Symptom:** Bot can't read logs like `/var/log/fail2ban.log`.

**Solution:**
1.  Add the user that runs the bot (e.g., `user`) to the `adm` group.
    ```bash
    sudo usermod -aG adm user
    ```
2.  **Reboot the server** or fully log out and log back in for the group changes to apply to the service. A simple service restart is often not enough.

---

## Security Checklist

- [ ] Secrets (tokens, API keys) are set as environment variables, NOT in `config.yaml`.
- [ ] The `.env` file is included in `.gitignore` and is NOT committed.
- [ ] Config file has correct permissions (`chmod 600 config.yaml`).
- [ ] Webhook secret is strong (32+ characters).
- [ ] Dry-run mode tested before enabling real execution.

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
