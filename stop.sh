#!/usr/bin/env bash
# Stop Agent Harness (local mode)
# For Docker mode, use: docker compose down

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$DIR/.pids"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RESET='\033[0m'
info() { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }

if [[ ! -f "$PID_FILE" ]]; then
  echo "No running processes found (.pids file missing — already stopped?)."
  exit 0
fi

while IFS= read -r pid; do
  if kill "$pid" 2>/dev/null; then
    info "Stopped PID $pid"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
success "Agent Harness stopped."
