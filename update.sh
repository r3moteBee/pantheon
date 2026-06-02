#!/usr/bin/env bash
# Pantheon Local Auto-Updater
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$DIR/data/logs/update.log"
mkdir -p "$DIR/data/logs"

echo "=== Auto-Update started at $(date) ===" > "$LOG_FILE"

cd "$DIR"

# 1. Stash any local modifications to prevent git pull conflict
echo "[1/4] Stashing local changes..." >> "$LOG_FILE"
git stash >> "$LOG_FILE" 2>&1 || true

# 2. Pull changes
echo "[2/4] Pulling latest code..." >> "$LOG_FILE"
git pull >> "$LOG_FILE" 2>&1

# 3. Apply stashed changes
echo "Re-applying local changes..." >> "$LOG_FILE"
git stash pop >> "$LOG_FILE" 2>&1 || true

# 4. Install backend dependencies
echo "[3/4] Installing backend dependencies..." >> "$LOG_FILE"
if [[ -f "$DIR/.venv/bin/pip" ]]; then
  "$DIR/.venv/bin/pip" install -r backend/requirements.txt >> "$LOG_FILE" 2>&1
else
  pip install -r backend/requirements.txt >> "$LOG_FILE" 2>&1
fi

# 5. Rebuild frontend
echo "[4/4] Rebuilding frontend assets..." >> "$LOG_FILE"
if [[ -d "$DIR/frontend" ]]; then
  cd "$DIR/frontend"
  npm install >> "$LOG_FILE" 2>&1
  VITE_API_URL="" npm run build >> "$LOG_FILE" 2>&1
fi

# 6. Restart server
echo "Restarting server..." >> "$LOG_FILE"
cd "$DIR"
nohup sh -c "sleep 3 && ./stop.sh && ./start.sh" >> "$LOG_FILE" 2>&1 &

echo "=== Auto-Update complete! Restart scheduled ===" >> "$LOG_FILE"
