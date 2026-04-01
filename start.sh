#!/usr/bin/env bash
# Start Agent Harness (local mode)
# For Docker mode, use: docker compose up -d
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
PID_FILE="$DIR/.pids"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
die()     { echo -e "${RED}[error]${RESET} $*" >&2; exit 1; }

[[ -f "$DIR/.venv/bin/activate" ]] || die "Virtual environment not found. Run deploy.sh first to complete installation."
[[ -d "$DIR/frontend/dist" ]]      || die "Frontend not built. Run deploy.sh first to complete installation."
[[ -f "$DIR/.env" ]]               || die ".env not found. Copy .env.example to .env and configure it."

# Stop any already-running processes from a previous start
if [[ -f "$PID_FILE" ]]; then
  while IFS= read -r pid; do
    kill "$pid" 2>/dev/null || true
  done < "$PID_FILE"
  rm -f "$PID_FILE"
fi

source "$DIR/.venv/bin/activate"

# Load .env into the environment for the backend
set -a; source "$DIR/.env"; set +a

info "Starting backend on port ${BACKEND_PORT}..."
cd "$DIR/backend"
uvicorn main:app --host 0.0.0.0 --port "$BACKEND_PORT" --log-level info >> "$DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" >> "$DIR/.pids"
success "Backend started (PID ${BACKEND_PID}) — logs: $DIR/backend.log"

info "Starting frontend on port ${FRONTEND_PORT}..."
python3 -m http.server "$FRONTEND_PORT" --directory "$DIR/frontend/dist" >> "$DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" >> "$DIR/.pids"
success "Frontend started (PID ${FRONTEND_PID}) — logs: $DIR/frontend.log"

echo ""
echo -e "${BOLD}${GREEN}Agent Harness is running!${RESET}"
echo -e "  Web UI   → http://localhost:${FRONTEND_PORT}"
echo -e "  API      → http://localhost:${BACKEND_PORT}"
echo -e "  API Docs → http://localhost:${BACKEND_PORT}/docs"
echo -e "  Stop     → $DIR/stop.sh"
echo ""
