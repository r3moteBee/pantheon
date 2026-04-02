#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$DIR/.pids"
GREEN='\033[0;32m'; CYAN='\033[0;36m'; RESET='\033[0m'
info() { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
if [[ -f "$PID_FILE" ]]; then
  while IFS= read -r pid; do
    if kill -9 "$pid" 2>/dev/null; then info "Stopped PID $pid"; fi
  done < "$PID_FILE"
  rm -f "$PID_FILE"
fi
for PORT in 8000 3000; do
  STRAY=$(lsof -ti ":$PORT" 2>/dev/null || true)
  if [[ -n "$STRAY" ]]; then
    info "Force-killing stray on port $PORT (PID $STRAY)"
    kill -9 $STRAY 2>/dev/null || true
  fi
done
pkill -9 -f "uvicorn main:app" 2>/dev/null || true
for PORT in 8000 3000; do
  for _ in 1 2 3 4 5; do lsof -ti ":$PORT" &>/dev/null || break; sleep 1; done
done
success "Agent Harness stopped."
