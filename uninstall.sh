#!/usr/bin/env bash
# Pantheon uninstaller — removes services, containers, and (optionally) data.
#
# Usage:
#   ./uninstall.sh                  # interactive
#   ./uninstall.sh --yes            # non-interactive, keeps data dir
#   ./uninstall.sh --yes --purge    # also removes data dir + .env (DESTRUCTIVE)
#   ./uninstall.sh --dir /path      # custom install dir
#
set -euo pipefail

INSTALL_DIR="${PANTHEON_DIR:-$HOME/pantheon}"
ASSUME_YES=false
PURGE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)    INSTALL_DIR="$2"; shift 2 ;;
    --yes|-y) ASSUME_YES=true; shift ;;
    --purge)  PURGE=true; shift ;;
    -h|--help)
      sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; RST=$'\033[0m'
info() { echo "${GRN}==>${RST} $*"; }
warn() { echo "${YLW}!! ${RST} $*"; }
err()  { echo "${RED}xx ${RST} $*" >&2; }

confirm() {
  $ASSUME_YES && return 0
  read -r -p "$1 [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

echo "${BOLD}Pantheon uninstaller${RST}"
echo "  Install dir : $INSTALL_DIR"
echo "  Purge data  : $PURGE"
echo

if ! confirm "Proceed with uninstall?"; then
  info "Aborted."; exit 0
fi

# 1. Stop local processes
if [[ -x "$INSTALL_DIR/stop.sh" ]]; then
  info "Stopping Pantheon (stop.sh)..."
  "$INSTALL_DIR/stop.sh" 2>/dev/null || warn "stop.sh exited non-zero"
fi

# 2. Stop docker compose stack if present
if [[ -f "$INSTALL_DIR/docker-compose.yml" ]] && command -v docker >/dev/null 2>&1; then
  info "Stopping docker compose stack..."
  ( cd "$INSTALL_DIR" && docker compose down --remove-orphans ) || warn "docker compose down failed"
fi

# 3. Remove SearXNG demo container
if command -v docker >/dev/null 2>&1; then
  if docker ps -a --format '{{.Names}}' | grep -q '^pantheon-searxng$'; then
    info "Removing pantheon-searxng container..."
    docker rm -f pantheon-searxng >/dev/null || warn "could not remove pantheon-searxng"
  fi
fi

# 4. Remove systemd unit (Linux)
if command -v systemctl >/dev/null 2>&1; then
  for unit in pantheon.service pantheon-backend.service; do
    if systemctl list-unit-files | grep -q "^${unit}"; then
      info "Disabling ${unit}..."
      sudo systemctl stop "$unit" 2>/dev/null || true
      sudo systemctl disable "$unit" 2>/dev/null || true
      sudo rm -f "/etc/systemd/system/${unit}"
      sudo systemctl daemon-reload || true
    fi
  done
fi

# 5. Optionally stop Ollama (don't uninstall — user may use it elsewhere)
if command -v ollama >/dev/null 2>&1; then
  warn "Ollama is installed system-wide and was left in place."
  warn "  To remove: pkill ollama && (brew uninstall ollama || sudo rm /usr/local/bin/ollama)"
fi

# 6. Remove install dir
if [[ "$PURGE" == "true" ]]; then
  if confirm "PURGE: delete entire ${INSTALL_DIR} including data/ and .env?"; then
    info "Removing ${INSTALL_DIR}..."
    rm -rf "$INSTALL_DIR"
  else
    warn "Skipped purge."
  fi
else
  if confirm "Remove install dir ${INSTALL_DIR} but KEEP data/ and .env?"; then
    info "Preserving data/ and .env, removing the rest..."
    TMP_KEEP="$(mktemp -d)"
    [[ -d "$INSTALL_DIR/data" ]] && mv "$INSTALL_DIR/data" "$TMP_KEEP/data"
    [[ -f "$INSTALL_DIR/.env" ]] && mv "$INSTALL_DIR/.env" "$TMP_KEEP/.env"
    rm -rf "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    [[ -d "$TMP_KEEP/data" ]] && mv "$TMP_KEEP/data" "$INSTALL_DIR/data"
    [[ -f "$TMP_KEEP/.env" ]] && mv "$TMP_KEEP/.env" "$INSTALL_DIR/.env"
    rmdir "$TMP_KEEP" 2>/dev/null || true
    info "Kept: ${INSTALL_DIR}/data and ${INSTALL_DIR}/.env"
  fi
fi

info "${BOLD}Uninstall complete.${RST}"
