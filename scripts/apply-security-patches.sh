#!/bin/bash
# apply-security-patches.sh - Priority Security Patching for ShadowOps Host
#
# This script performs the following:
# 1. Updates package lists
# 2. Prioritizes security updates for critical packages (Chrome, GDK-Pixbuf)
# 3. Installs all remaining security updates from noble-security
# 4. Performs a full system upgrade
# 5. Verifies the status of critical services

set -e

# Ensure non-interactive mode for apt to prevent hanging on prompts
export DEBIAN_FRONTEND=noninteractive

echo "🗡️ Starting ShadowOps Host Security Patching..."

# 1. Update package lists
echo "--- Updating package lists ---"
sudo -E apt-get update

# 2. Prioritize critical security updates
echo "--- Installing prioritized security updates ---"
# google-chrome-stable provides the Chromium-based browser requested
# libgdk-pixbuf-2.0-0 was specifically identified as having a backlog
# Note: apt-get install will upgrade these if already installed
sudo -E apt-get install --only-upgrade -y \
    google-chrome-stable \
    libgdk-pixbuf-2.0-0 \
    libgdk-pixbuf-2.0-common || echo "⚠️ One or more prioritized packages not found, continuing..."

# 3. Install remaining security updates
echo "--- Identifying remaining security updates ---"
# We use a subshell to capture the packages and handle potential grep exit codes safely
# grep returns 1 if no matches are found, which would trigger set -e without || true
SECURITY_PACKAGES=$(apt-get -s upgrade | grep "^Inst" | grep "security" | cut -d' ' -f2 | tr '\n' ' ') || true

if [ ! -z "$SECURITY_PACKAGES" ]; then
    echo "Installing: $SECURITY_PACKAGES"
    sudo -E apt-get install --only-upgrade -y $SECURITY_PACKAGES
else
    echo "No further security updates pending."
fi

# 4. Perform full system upgrade
echo "--- Performing full system upgrade ---"
sudo -E apt-get upgrade -y

# 5. Post-patch verification
echo "--- Post-patch verification ---"
if [ -f /var/run/reboot-required ]; then
    echo "⚠️ WARNING: A reboot is required to finish applying updates."
else
    echo "✅ No reboot required."
fi

echo "--- Service Status ---"
# Check services based on ShadowOps ServiceManager definitions
SERVICES=("shadowops-bot" "postgresql" "nexus")
for service in "${SERVICES[@]}"; do
    STATUS=$(systemctl is-active "$service" 2>/dev/null || echo "not-found")
    echo "$service: $STATUS"
done

echo "✅ Security patching complete."
