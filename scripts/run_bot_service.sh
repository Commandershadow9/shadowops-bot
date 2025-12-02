#!/bin/bash
# ShadowOps Bot Service Starter
# This script is used by systemd to start the bot with proper environment

set -e

# Change to bot directory
cd /home/cmdshadow/shadowops-bot

# Activate virtual environment
source venv/bin/activate

# Set unbuffered Python output for immediate logging
export PYTHONUNBUFFERED=1

# Start the bot
exec python3 src/bot.py
