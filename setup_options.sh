#!/usr/bin/env bash
# =============================================================================
# Pantheon — Setup Options Utility
# =============================================================================
# Configures and installs optional features for your Pantheon instance.
# Supports both an interactive CLI menu (default) and silent flags.
#
# Usage (Interactive Menu):
#   ./setup_options.sh
#
# Usage (Command-Line Flags):
#   ./setup_options.sh --with-ollama --with-searxng --with-browser --with-office
# =============================================================================

set -euo pipefail

# ── Colors & helpers ─────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
error()   { echo -e "${RED}[error]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${DIR}/.env"

# ── Defaults ─────────────────────────────────────────────────────────────────
WITH_OLLAMA=false
WITH_SEARXNG=false
WITH_BROWSER=false
WITH_OFFICE=false

MODEL_TAG="3b"
EMBEDDING_MODEL="nomic-embed-text"
SEARXNG_PORT="8888"

# ── Read current .env values (if exists) to set menu defaults ────────────────
_ensure_env_file() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${DIR}/.env.example" ]]; then
      cp "${DIR}/.env.example" "${ENV_FILE}"
      info "Created .env from template (.env.example)"
    else
      # Create a basic blank .env if no template
      touch "${ENV_FILE}"
    fi
  fi
}

_ensure_env_file

if [[ -f "${ENV_FILE}" ]]; then
  # Simple regex matches to deduce currently enabled services
  if grep -q "^LLM_BASE_URL=.*11434" "${ENV_FILE}" 2>/dev/null; then
    WITH_OLLAMA=true
  fi
  if grep -q "^SEARCH_URL=.*8888" "${ENV_FILE}" 2>/dev/null || grep -q "^SEARCH_URL=.*searxng" "${ENV_FILE}" 2>/dev/null; then
    WITH_SEARXNG=true
  fi
  if grep -q "^BROWSER_ENABLED=true" "${ENV_FILE}" 2>/dev/null; then
    WITH_BROWSER=true
  fi
  if grep -q "^INSTALL_OFFICE=true" "${ENV_FILE}" 2>/dev/null; then
    WITH_OFFICE=true
  fi
fi

# ── Parse arguments ──────────────────────────────────────────────────────────
INTERACTIVE=true
HAS_FLAGS=false

# Helper to check if flags were passed (ignores values)
for arg in "$@"; do
  if [[ "$arg" == --* ]]; then
    HAS_FLAGS=true
    INTERACTIVE=false
    break
  fi
done

if [[ "$HAS_FLAGS" == "true" ]]; then
  # Reset values before parsing flags
  WITH_OLLAMA=false
  WITH_SEARXNG=false
  WITH_BROWSER=false
  WITH_OFFICE=false
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-ollama)  WITH_OLLAMA=true;  shift ;;
    --with-searxng) WITH_SEARXNG=true; shift ;;
    --with-browser) WITH_BROWSER=true; shift ;;
    --with-office)  WITH_OFFICE=true;  shift ;;
    --tag)          MODEL_TAG="$2";    shift 2 ;;
    --embedding)    EMBEDDING_MODEL="$2"; shift 2 ;;
    --searxng-port) SEARXNG_PORT="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: setup_options.sh [options]"
      echo ""
      echo "If run without flags, launches an interactive configuration menu."
      echo ""
      echo "Options:"
      echo "  --with-ollama       Install/Configure Ollama and pull defaults"
      echo "  --with-searxng      Launch SearXNG private search (Docker)"
      echo "  --with-browser      Install Playwright Chromium + browser tools"
      echo "  --with-office       Install LibreOffice + poppler-utils + pandoc"
      echo "  --tag TAG           Ollama Qwen model tag (e.g. 1.5b, 3b, 7b) — default: 3b"
      echo "  --embedding MODEL   Ollama embedding model name — default: nomic-embed-text"
      echo "  --searxng-port PORT Host port for local SearXNG — default: 8888"
      exit 0
      ;;
    *) die "Unknown option: $1. Use --help for usage." ;;
  esac
done

# ── Interactive Menu Wizard ──────────────────────────────────────────────────
if [[ "$INTERACTIVE" == "true" ]]; then
  while true; do
    echo -e "\n${BOLD}${CYAN}╔════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${CYAN}║             Pantheon Setup Options Wizard              ║${RESET}"
    echo -e "${BOLD}${CYAN}╚════════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo "Toggle the optional components you wish to install/enable:"
    echo ""
    
    # 1. Ollama
    if [[ "$WITH_OLLAMA" == "true" ]]; then
      echo -e "  [${GREEN}✓${RESET}] ${BOLD}1) Ollama Local LLM & Embeddings${RESET}"
      echo -e "        • Run free, private language models offline on your machine."
    else
      echo -e "  [ ] 1) Ollama Local LLM & Embeddings"
      echo -e "        • Run free, private language models offline on your machine."
    fi
    echo ""

    # 2. SearXNG
    if [[ "$WITH_SEARXNG" == "true" ]]; then
      echo -e "  [${GREEN}✓${RESET}] ${BOLD}2) SearXNG Private Search${RESET} (requires Docker)"
      echo -e "        • Enable private search queries without limits/API keys."
    else
      echo -e "  [ ] 2) SearXNG Private Search (requires Docker)"
      echo -e "        • Enable private search queries without limits/API keys."
    fi
    echo ""

    # 3. Playwright Browser
    if [[ "$WITH_BROWSER" == "true" ]]; then
      echo -e "  [${GREEN}✓${RESET}] ${BOLD}3) Playwright Browser tools${RESET}"
      echo -e "        • Equips the agent with tools to crawl complex, JS-heavy web pages."
    else
      echo -e "  [ ] 3) Playwright Browser tools"
      echo -e "        • Equips the agent with tools to crawl complex, JS-heavy web pages."
    fi
    echo ""

    # 4. LibreOffice Previews
    if [[ "$WITH_OFFICE" == "true" ]]; then
      echo -e "  [${GREEN}✓${RESET}] ${BOLD}4) LibreOffice Document Previews${RESET}"
      echo -e "        • Automatically renders PDF/Office docs directly in the web UI."
    else
      echo -e "  [ ] 4) LibreOffice Document Previews"
      echo -e "        • Automatically renders PDF/Office docs directly in the web UI."
    fi
    
    echo ""
    echo -e "  [${BOLD}c${RESET}] Confirm and apply changes"
    echo -e "  [${BOLD}q${RESET}] Cancel and exit"
    echo ""
    read -rp "Select an option [1-4, c, q]: " menu_choice </dev/tty
    
    case "${menu_choice}" in
      1) [[ "$WITH_OLLAMA" == "true" ]] && WITH_OLLAMA=false || WITH_OLLAMA=true ;;
      2) [[ "$WITH_SEARXNG" == "true" ]] && WITH_SEARXNG=false || WITH_SEARXNG=true ;;
      3) [[ "$WITH_BROWSER" == "true" ]] && WITH_BROWSER=false || WITH_BROWSER=true ;;
      4) [[ "$WITH_OFFICE" == "true" ]] && WITH_OFFICE=false || WITH_OFFICE=true ;;
      c|C) break ;;
      q|Q) info "Exiting setup options wizard."; exit 0 ;;
      *) warn "Invalid input. Please choose 1-4, c, or q." ;;
    esac
  done

  # Prompts for customized configurations
  if [[ "$WITH_OLLAMA" == "true" ]]; then
    echo ""
    read -rp "  Ollama Model Size Tag (1.5b, 3b, 7b) [default: 3b]: " input_tag </dev/tty
    MODEL_TAG="${input_tag:-3b}"
    read -rp "  Ollama Embedding Model [default: nomic-embed-text]: " input_embed </dev/tty
    EMBEDDING_MODEL="${input_embed:-nomic-embed-text}"
  fi

  if [[ "$WITH_SEARXNG" == "true" ]]; then
    echo ""
    read -rp "  SearXNG Host Binding Port [default: 8888]: " input_port </dev/tty
    SEARXNG_PORT="${input_port:-8888}"
  fi
fi

# ── Setup Environment Helper functions ────────────────────────────────────────
_env_set() {
  local key="$1" value="$2"
  # Escape special chars for sed
  local escaped_value
  escaped_value=$(printf '%s' "$value" | sed 's/[|&/\]/\\&/g')
  if grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
    # OS compatible in-place edit
    if [[ "$(uname -s)" == "Darwin" ]]; then
      sed -i '' "s|^${key}=.*|${key}=${escaped_value}|" "${ENV_FILE}"
    else
      sed -i "s|^${key}=.*|${key}=${escaped_value}|" "${ENV_FILE}"
    fi
  else
    echo "${key}=${value}" >> "${ENV_FILE}"
  fi
}

# Backup env
cp "${ENV_FILE}" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
info "Backed up configuration to ${ENV_FILE}.bak.*"

# ── 1. Ollama Local LLM Configuration ────────────────────────────────────────
if [[ "$WITH_OLLAMA" == "true" ]]; then
  echo -e "\n${BOLD}── Configuring Ollama ──${RESET}"
  
  info "Checking Ollama..."
  if command -v ollama &>/dev/null; then
    success "Ollama already installed ($(ollama --version 2>/dev/null || echo unknown))"
  else
    info "Installing Ollama..."
    if [[ "$(uname -s)" == "macos" || "$(uname -s)" == "Darwin" ]]; then
      if command -v brew &>/dev/null; then
        brew install ollama
      else
        curl -fsSL https://ollama.com/install.sh | sh
      fi
    elif [[ "$(uname -s)" == "linux" || "$(uname -s)" == "Linux" ]]; then
      curl -fsSL https://ollama.com/install.sh | sh
    else
      die "Unsupported OS for automatic Ollama install. Please install manually: https://ollama.com"
    fi
    success "Ollama installed successfully"
  fi

  # Start Ollama daemon if not running
  _ollama_ready() { curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; }
  if _ollama_ready; then
    success "Ollama daemon is running"
  else
    info "Starting Ollama daemon in background..."
    if [[ "$(uname -s)" == "Darwin" && -d "/Applications/Ollama.app" ]]; then
      open -a Ollama
    else
      ollama serve &>/dev/null &
    fi
    for ((i=1; i<=30; i++)); do
      _ollama_ready && break
      sleep 1
    done
    _ollama_ready || die "Ollama daemon failed to start. Start it manually and run setup again."
    success "Ollama daemon started"
  fi

  OLLAMA_MODEL="qwen2.5:${MODEL_TAG}"
  info "Pulling chat model: ${OLLAMA_MODEL}..."
  if ollama list 2>/dev/null | grep -q "qwen2.5.*${MODEL_TAG}"; then
    success "${OLLAMA_MODEL} already downloaded"
  else
    ollama pull "${OLLAMA_MODEL}"
    success "${OLLAMA_MODEL} downloaded"
  fi

  info "Pulling embedding model: ${EMBEDDING_MODEL}..."
  if ollama list 2>/dev/null | grep -q "${EMBEDDING_MODEL}"; then
    success "${EMBEDDING_MODEL} already downloaded"
  else
    ollama pull "${EMBEDDING_MODEL}"
    success "${EMBEDDING_MODEL} downloaded"
  fi

  _env_set "LLM_BASE_URL"       "http://localhost:11434/v1"
  _env_set "LLM_API_KEY"        "ollama"
  _env_set "LLM_MODEL"          "${OLLAMA_MODEL}"
  _env_set "LLM_PREFILL_MODEL"  "${OLLAMA_MODEL}"
  _env_set "EMBEDDING_BASE_URL" "http://localhost:11434/v1"
  _env_set "EMBEDDING_API_KEY"  "ollama"
  _env_set "EMBEDDING_MODEL"    "${EMBEDDING_MODEL}"
  success "Pantheon configured to use Ollama"
fi

# ── 2. SearXNG Setup ──────────────────────────────────────────────────────────
if [[ "$WITH_SEARXNG" == "true" ]]; then
  echo -e "\n${BOLD}── Configuring SearXNG ──${RESET}"
  
  info "Validating Docker environment..."
  if ! command -v docker &>/dev/null; then
    die "Docker is required for SearXNG. Please install Docker and retry."
  fi
  if ! docker info &>/dev/null; then
    die "Docker daemon is not running. Start Docker and try again."
  fi
  success "Docker is available"

  SEARXNG_DIR="${DIR}/data/searxng"
  mkdir -p "${SEARXNG_DIR}"

  if [[ ! -f "${SEARXNG_DIR}/settings.yml" ]]; then
    info "Generating SearXNG settings.yml..."
    # Generate secret keys
    local secret
    secret=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32 2>/dev/null || echo "fallbacksearxngsecret")
    cat > "${SEARXNG_DIR}/settings.yml" <<EOF
# Pantheon SearXNG settings — generated by setup_options.sh
use_default_settings: true
server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "${secret}"
  limiter: false
  image_proxy: false
search:
  safe_search: 0
  autocomplete: ""
  default_lang: "en"
  formats:
    - html
    - json
EOF
    success "SearXNG config written to ${SEARXNG_DIR}/settings.yml"
  fi

  CONTAINER_NAME="pantheon-searxng"
  if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    info "Restarting existing SearXNG container..."
    docker restart "${CONTAINER_NAME}" >/dev/null
  else
    info "Launching SearXNG container on port ${SEARXNG_PORT}..."
    docker run -d \
      --name "${CONTAINER_NAME}" \
      --restart unless-stopped \
      -p "${SEARXNG_PORT}:8080" \
      -v "${SEARXNG_DIR}:/etc/searxng:rw" \
      -e BASE_URL="http://localhost:${SEARXNG_PORT}/" \
      -e INSTANCE_NAME="pantheon-searxng" \
      docker.io/searxng/searxng:latest >/dev/null
  fi

  info "Waiting for SearXNG service to respond..."
  _sear_ready() { curl -sf "http://localhost:${SEARXNG_PORT}/search?q=test&format=json" >/dev/null 2>&1; }
  for ((i=1; i<=30; i++)); do
    _sear_ready && break
    sleep 1
  done
  if _sear_ready; then
    success "SearXNG is active at http://localhost:${SEARXNG_PORT}"
  else
    warn "SearXNG is taking longer than expected to start. Check: docker logs ${CONTAINER_NAME}"
  fi

  _env_set "SEARCH_URL" "http://localhost:${SEARXNG_PORT}"
  _env_set "SEARCH_API_KEY" ""
  success "Pantheon configured to use SearXNG"
else
  # If explicitly disabled in non-interactive flag execution or previously enabled but deselected
  # Only clear SEARCH_URL if we are intentionally not setting it
  if [[ "$HAS_FLAGS" == "true" ]]; then
    _env_set "SEARCH_URL" ""
  fi
fi

# ── 3. Playwright Browser Setup ──────────────────────────────────────────────
if [[ "$WITH_BROWSER" == "true" ]]; then
  echo -e "\n${BOLD}── Configuring Playwright Browser ──${RESET}"
  
  PY_BIN=""
  if [[ -x "${DIR}/.venv/bin/python" ]]; then
    PY_BIN="${DIR}/.venv/bin/python"
  elif command -v python3 &>/dev/null; then
    PY_BIN="$(command -v python3)"
  else
    die "Python3 executable not found. Unable to setup Playwright."
  fi
  
  info "Using Python at: ${PY_BIN}"
  info "Installing playwright python package..."
  "${PY_BIN}" -m pip install --quiet playwright || die "Failed to install playwright package via pip."

  info "Installing Chromium binary..."
  "${PY_BIN}" -m playwright install chromium || die "Failed to install Chromium binaries."

  if [[ "$(uname -s)" == "Linux" || "$(uname -s)" == "linux" ]]; then
    if command -v sudo &>/dev/null; then
      info "Installing system dependencies for Chromium (sudo)..."
      sudo "${PY_BIN}" -m playwright install-deps chromium || warn "install-deps returned non-zero. Chromium may still run headless."
    else
      warn "Sudo not available. Skipping chromium system dependencies installation."
    fi
  fi

  _env_set "BROWSER_ENABLED" "true"
  _env_set "BROWSER_HEADLESS" "true"
  _env_set "INSTALL_BROWSER" "true"
  success "Playwright browser tools successfully configured"
else
  if [[ "$HAS_FLAGS" == "true" ]]; then
    _env_set "BROWSER_ENABLED" "false"
    _env_set "INSTALL_BROWSER" "false"
  fi
fi

# ── 4. LibreOffice Setup ─────────────────────────────────────────────────────
if [[ "$WITH_OFFICE" == "true" ]]; then
  echo -e "\n${BOLD}── Configuring LibreOffice Previews ──${RESET}"
  
  # Detect package manager
  PKG=""
  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew &>/dev/null; then PKG="brew"
  elif command -v apt-get &>/dev/null; then PKG="apt"
  elif command -v dnf     &>/dev/null; then PKG="dnf"
  elif command -v yum     &>/dev/null; then PKG="yum"
  elif command -v pacman  &>/dev/null; then PKG="pacman"
  elif command -v apk     &>/dev/null; then PKG="apk"
  fi

  SUDO=""
  [[ "$(id -u)" == "0" ]] || SUDO="sudo"

  if [[ -z "$PKG" ]]; then
    warn "Unsupported system or no package manager found. Please install LibreOffice, poppler-utils, and pandoc manually."
  else
    info "Installing LibreOffice, poppler-utils, and pandoc via ${PKG}..."
    case "$PKG" in
      brew)
        brew install --cask libreoffice
        brew install poppler pandoc
        ;;
      apt)
        $SUDO apt-get update -qq
        $SUDO apt-get install -y -qq libreoffice poppler-utils pandoc
        ;;
      dnf)
        $SUDO dnf install -y -q libreoffice poppler-utils pandoc
        ;;
      yum)
        $SUDO yum install -y -q libreoffice poppler-utils pandoc
        ;;
      pacman)
        $SUDO pacman -S --noconfirm -q libreoffice-fresh poppler pandoc-cli || $SUDO pacman -S --noconfirm -q libreoffice-fresh poppler pandoc
        ;;
      apk)
        $SUDO apk add -q libreoffice poppler-utils pandoc
        ;;
    esac
    success "Document preview software package(s) installed"
  fi

  _env_set "INSTALL_OFFICE" "true"
else
  if [[ "$HAS_FLAGS" == "true" ]]; then
    _env_set "INSTALL_OFFICE" "false"
  fi
fi

# ── Security Keys Check ──────────────────────────────────────────────────────
if grep -q "change-this-to-a-random" "${ENV_FILE}" 2>/dev/null || grep -q "change-this" "${ENV_FILE}" 2>/dev/null; then
  info "Generating fresh secret keys in .env..."
  local vkey skey
  vkey=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32 2>/dev/null || echo "fallbackvaultkey32byteslengthval")
  skey=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32 2>/dev/null || echo "fallbacksecretkey32byteslengthval")
  _env_set "VAULT_MASTER_KEY" "${vkey}"
  _env_set "SECRET_KEY"       "${skey}"
  success "Cryptographic keys rotated successfully"
fi

echo -e "\n${BOLD}${GREEN}Setup Options Update Completed!${RESET}"
echo ""
