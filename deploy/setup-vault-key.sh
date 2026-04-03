#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Pantheon — Generate and install the vault master key
#
# Run as root (or with sudo) on first deploy.  Creates:
#   /etc/pantheon/vault.key  — raw key (read by vault.py directly)
#   /etc/pantheon/vault.env  — EnvironmentFile for systemd
#
# Both files are root:root 600, so only root and systemd can
# read them.  The running service receives the key via the
# VAULT_MASTER_KEY environment variable injected by systemd.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

KEY_DIR="/etc/pantheon"
KEY_FILE="${KEY_DIR}/vault.key"
ENV_FILE="${KEY_DIR}/vault.env"

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: this script must be run as root (or with sudo)."
    exit 1
fi

mkdir -p "$KEY_DIR"

if [ -f "$KEY_FILE" ]; then
    echo "Vault key already exists at $KEY_FILE — skipping generation."
    echo "To regenerate, delete the file first (this will invalidate existing vault data)."
else
    # Generate a 64-character hex key
    KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "$KEY" > "$KEY_FILE"
    echo "Generated new vault master key → $KEY_FILE"
fi

# Ensure correct permissions
chmod 600 "$KEY_FILE"
chown root:root "$KEY_FILE"

# Write the systemd EnvironmentFile
KEY=$(cat "$KEY_FILE")
cat > "$ENV_FILE" <<EOF
VAULT_MASTER_KEY=${KEY}
EOF
chmod 600 "$ENV_FILE"
chown root:root "$ENV_FILE"

echo "Systemd environment file → $ENV_FILE"
echo ""
echo "Done. The vault master key is secured outside user space."
echo "  Key file:  $KEY_FILE  (root:root 600)"
echo "  Env file:  $ENV_FILE  (root:root 600)"
echo ""
echo "Next steps:"
echo "  1. Copy deploy/pantheon.service → /etc/systemd/system/"
echo "  2. systemctl daemon-reload"
echo "  3. systemctl enable --now pantheon"
echo "  4. python -m secrets.setup --migrate   (to move .env secrets into vault)"
