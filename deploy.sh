#!/usr/bin/env bash
# =============================================================================
# Pantheon — One-Command Installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/r3moteBee/pantheon/main/deploy.sh | bash
#   # Or with options:
#   curl -fsSL https://raw.githubusercontent.com/r3moteBee/pantheon/main/deploy.sh | bash -s -- --dir ~/pantheon --mode local
# =============================================================================

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/r3moteBee/pantheon.git"
INSTALL_DIR="${PANTHEON_DIR:-$HOME/pantheon}"
HTTP_PORT="${PANTHEON_PORT:-80}"
BACKEND_PORT="8000"
FRONTEND_PORT="3000"
LLM_BASE_URL="${LLM_BASE_URL:-http://localhost:11434/v1}"
LLM_API_KEY="${LLM_API_KEY:-ollama}"
LLM_MODEL="${LLM_MODEL:-qwen2.5:3b}"
BRANCH="main"
SKIP_CONFIRM=false
MODE=""   # "local" or "docker" — prompted if not set
DOMAIN=""         # domain for HTTPS via Caddy — prompted if not set
AGENT_NAME=""     # agent name written into soul.md — prompted if not set
AUTH_PASSWORD=""  # web interface password — prompted if not set
WITH_OLLAMA=true    # run demo_setup.sh --with-ollama after install
WITH_SEARXNG=""     # run demo_setup.sh --with-searxng after install (empty to detect default)
WITH_BROWSER=false  # run demo_setup.sh --with-browser after install
WITH_OFFICE=false   # install LibreOffice for Office/PDF preview rendering
OLLAMA_TAG="3b"     # Qwen model tag passed to demo_setup.sh


# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
error()   { echo -e "${RED}[error]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }
header()  { echo -e "\n${BOLD}${BLUE}$*${RESET}"; }

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)        INSTALL_DIR="$2";    shift 2 ;;
    --port)       HTTP_PORT="$2";      shift 2 ;;
    --api-key)    LLM_API_KEY="$2";    shift 2 ;;
    --model)      LLM_MODEL="$2";      shift 2 ;;
    --base-url)   LLM_BASE_URL="$2";   shift 2 ;;
    --branch)     BRANCH="$2";         shift 2 ;;
    --mode)       MODE="$2";           shift 2 ;;
    --domain)     DOMAIN="$2";         shift 2 ;;
    --agent-name)    AGENT_NAME="$2";     shift 2 ;;
    --auth-password) AUTH_PASSWORD="$2";  shift 2 ;;
    --with-ollama)  WITH_OLLAMA=true;  shift ;;
    --with-searxng) WITH_SEARXNG=true; shift ;;
    --no-searxng)   WITH_SEARXNG=false; shift ;;
    --with-browser) WITH_BROWSER=true; shift ;;
    --with-office)  WITH_OFFICE=true;  shift ;;
    --ollama-tag)   OLLAMA_TAG="$2";   shift 2 ;;
    --yes|-y)     SKIP_CONFIRM=true;   shift ;;
    --help|-h)
      echo "Usage: deploy.sh [options]"
      echo ""
      echo "Options:"
      echo "  --mode MODE      Run mode: 'local' or 'docker' (prompted if omitted)"
      echo "  --dir PATH       Installation directory (default: ~/pantheon)"
      echo "  --port PORT      HTTP port — Docker mode only (default: 80)"
      echo "  --api-key KEY    LLM API key (can also set LLM_API_KEY env var)"
      echo "  --model MODEL    LLM model name (default: gpt-4o)"
      echo "  --base-url URL   LLM provider base URL (default: OpenAI)"
      echo "  --branch NAME    Git branch to deploy (default: main)"
      echo "  --domain DOMAIN      Domain name for HTTPS via Caddy (e.g. agent.example.com)"
      echo "  --agent-name NAME        Name for the agent (default: Pan)"
      echo "  --auth-password PASS     Web interface password (prompted if omitted)"
      echo "  --with-ollama        Install Ollama + Nemotron-3-Nano-4B as the default LLM"
      echo "  --with-searxng       Run a local SearXNG container as the default search backend"
      echo "  --with-office        Install LibreOffice + poppler for Office/PDF artifact previews"
      echo "  --with-browser       Install Playwright chromium and enable agent browser tools"
      echo "  --ollama-tag TAG     Nemotron tag (4b, 4b-q8_0, 4b-bf16) — default: 4b"
      echo "  --yes, -y            Skip confirmation and model selection prompts"
      echo ""
      echo "When run interactively (without --yes), the installer will:"
      echo "  1. Ask for your LLM endpoint URL and API key"
      echo "  2. Fetch the available model list from the endpoint"
      echo "  3. Let you choose your primary, prefill, and embedding models"
      echo ""
      echo "Environment variables (alternative to flags):"
      echo "  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_PREFILL_MODEL,"
      echo "  EMBEDDING_MODEL, PANTHEON_DIR, PANTHEON_PORT"
      exit 0
      ;;
    *) warn "Unknown option: $1"; shift ;;
  esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║        Pantheon  Installer           ║"
echo "  ║  Self-hosted AI Agent Framework 2026-04-10 ║"
echo "  ╚═══════════════════════════════════════════╝"
echo -e "${RESET}"

# ── OS detection ──────────────────────────────────────────────────────────────
OS="unknown"
case "$(uname -s)" in
  Linux*)              OS="linux"   ;;
  Darwin*)             OS="macos"   ;;
  CYGWIN*|MINGW*|MSYS*) OS="windows" ;;
esac
info "Detected OS: ${OS}"

# ── Mode selection ────────────────────────────────────────────────────────────
if [[ -z "$MODE" ]]; then
  echo ""
  echo -e "  ${BOLD}How would you like to run Pantheon?${RESET}"
  echo ""
  echo -e "  ${BOLD}[1] Local${RESET} — Runs directly on your machine using Python + Node."
  echo "        • No Docker required"
  echo "        • Works on any machine, including VMs without nested virtualization"
  echo "        • ChromaDB runs in-process (no separate container)"
  echo "        • Best for development or low-overhead environments"
  echo ""
  echo -e "  ${BOLD}[2] Docker${RESET} — Runs all services in isolated containers."
  echo "        • Easiest setup — no need to install Python or Node manually"
  echo "        • Most portable and production-like"
  echo "        • Requires Docker (and nested virtualization on some VMs)"
  echo "        • Best for servers and consistent team environments"
  echo ""
  read -rp "  Enter 1 or 2 [default: 2]: " mode_choice </dev/tty
  case "${mode_choice:-2}" in
    1) MODE="local"  ;;
    2) MODE="docker" ;;
    *) warn "Invalid choice, defaulting to Docker."; MODE="docker" ;;
  esac
fi

# Normalize + validate
MODE="${MODE,,}"  # lowercase
[[ "$MODE" == "local" || "$MODE" == "docker" ]] || die "Invalid --mode '${MODE}'. Must be 'local' or 'docker'."
echo ""
success "Mode: ${MODE}"

# Resolve dynamic default for SearXNG based on docker availability
if [[ -z "$WITH_SEARXNG" ]]; then
  if command -v docker &>/dev/null && docker info &>/dev/null; then
    WITH_SEARXNG=true
    info "Docker detected: defaulting SearXNG search backend to enabled"
  else
    WITH_SEARXNG=false
  fi
fi

# ── Requirement checks ────────────────────────────────────────────────────────
header "Checking requirements..."

check_cmd() {
  if command -v "$1" &>/dev/null; then
    success "$1 found"
  else
    die "$1 is required but not installed. $2"
  fi
}

check_cmd git "Install from https://git-scm.com"

# ── Detect package manager (used by local mode deps + Caddy install) ─────
PKG_MANAGER=""
if   [[ "$OS" == "macos" ]] && command -v brew &>/dev/null; then PKG_MANAGER="brew"
elif command -v apt-get  &>/dev/null; then PKG_MANAGER="apt"
elif command -v dnf      &>/dev/null; then PKG_MANAGER="dnf"
elif command -v yum      &>/dev/null; then PKG_MANAGER="yum"
elif command -v pacman   &>/dev/null; then PKG_MANAGER="pacman"
elif command -v apk      &>/dev/null; then PKG_MANAGER="apk"
fi

# Use sudo only when not already root
SUDO=""
[[ "$(id -u)" == "0" ]] || SUDO="sudo"

if [[ "$MODE" == "docker" ]]; then
  DOCKER_OK=true
  if ! command -v docker &>/dev/null; then
    warn "Docker is required for Docker mode, but it is not installed."
    DOCKER_OK=false
    if [[ "$SKIP_CONFIRM" == "false" ]]; then
      if [[ "$OS" == "linux" ]]; then
        read -rp "  Would you like to automatically install Docker now? [y/N]: " install_docker </dev/tty
        if [[ "${install_docker}" =~ ^[Yy]$ ]]; then
          info "Installing Docker via official script (curl -fsSL https://get.docker.com | sh)..."
          curl -fsSL https://get.docker.com | sh || true
          if command -v docker &>/dev/null; then
            success "Docker installed successfully"
            if [[ -n "${USER:-}" ]]; then
              info "Adding current user (${USER}) to docker group..."
              $SUDO usermod -aG docker "$USER" || true
              warn "You may need to log out and log back in (or run 'newgrp docker') for docker group permissions to take effect."
            fi
            DOCKER_OK=true
          else
            error "Docker installation failed."
          fi
        fi
      elif [[ "$OS" == "macos" ]] && command -v brew &>/dev/null; then
        read -rp "  Would you like to install Docker Desktop via Homebrew Cask? [y/N]: " install_docker </dev/tty
        if [[ "${install_docker}" =~ ^[Yy]$ ]]; then
          info "Installing Docker Cask via Homebrew..."
          brew install --cask docker
          DOCKER_OK=true
        fi
      fi
    fi
  fi

  if [[ "$DOCKER_OK" == "true" ]] && ! docker info &>/dev/null; then
    warn "Docker daemon is not running."
    DOCKER_OK=false
    if [[ "$OS" == "linux" && "$SKIP_CONFIRM" == "false" ]] && command -v systemctl &>/dev/null; then
      read -rp "  Would you like to try starting the Docker daemon? [Y/n]: " start_daemon </dev/tty
      if [[ "${start_daemon:-Y}" =~ ^[Yy]$ ]]; then
        info "Starting Docker daemon..."
        $SUDO systemctl start docker || true
        sleep 2
        if docker info &>/dev/null; then
          DOCKER_OK=true
        fi
      fi
    fi
  fi

  # Check Docker Compose (plugin or standalone)
  if [[ "$DOCKER_OK" == "true" ]]; then
    if docker compose version &>/dev/null 2>&1; then
      success "docker compose (plugin) found"
    elif command -v docker-compose &>/dev/null; then
      success "docker-compose (standalone) found"
    else
      # Try installing compose plugin if on apt system
      if [[ "$OS" == "linux" && "$PKG_MANAGER" == "apt" ]]; then
        info "Installing docker-compose-plugin or docker-compose-v2..."
        $SUDO apt-get update -qq && (
          $SUDO apt-get install -y docker-compose-plugin -qq || \
          $SUDO apt-get install -y docker-compose-v2 -qq
        ) || true
      fi
      if docker compose version &>/dev/null 2>&1 || command -v docker-compose &>/dev/null; then
        success "Docker Compose found"
      else
        warn "Docker Compose is required but it is not installed."
        DOCKER_OK=false
      fi
    fi
  fi

  if [[ "$DOCKER_OK" == "false" ]]; then
    if [[ "$SKIP_CONFIRM" == "false" ]]; then
      read -rp "  Would you like to fall back to Local mode instead? [Y/n]: " fallback_choice </dev/tty
      if [[ "${fallback_choice:-Y}" =~ ^[Yy]$ ]]; then
        MODE="local"
        info "Falling back to Local mode..."
      else
        die "Docker installation/daemon issues must be resolved to use Docker mode."
      fi
    else
      die "Docker installation/daemon issues detected. Cannot run in Docker mode in non-interactive session."
    fi
  fi
fi

if [[ "$MODE" == "docker" ]]; then
  success "Docker requirements satisfied"
else  # local mode

  if [[ -z "$PKG_MANAGER" && "$OS" == "macos" ]]; then
    warn "Homebrew is not installed."
    if [[ "$SKIP_CONFIRM" == "false" ]]; then
      read -rp "  Would you like to automatically install Homebrew now? [y/N]: " install_brew </dev/tty
      if [[ "${install_brew}" =~ ^[Yy]$ ]]; then
        info "Installing Homebrew (this may take a few minutes and require your sudo password)..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || true
        # Re-check brew path and add it to the shell environment (Apple Silicon or Intel paths)
        if [[ -x "/opt/homebrew/bin/brew" ]]; then
          eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -x "/usr/local/bin/brew" ]]; then
          eval "$(/usr/local/bin/brew shellenv)"
        fi
        if command -v brew &>/dev/null; then
          PKG_MANAGER="brew"
          success "Homebrew installed successfully"
        else
          die "Homebrew installation failed. Please install manually: https://brew.sh"
        fi
      else
        die "Homebrew is required to auto-install dependencies on macOS."
      fi
    else
      die "Homebrew is required but not installed. Cannot install in non-interactive session."
    fi
  fi

  pkg_install() {
    # $1 = display name, $2+ = package name(s)
    local label="$1"; shift
    info "Installing ${label} via ${PKG_MANAGER}..."
    if [[ "$SKIP_CONFIRM" == false ]]; then
      read -rp "  OK to install ${label} now? [Y/n] " yn </dev/tty
      [[ "${yn:-Y}" =~ ^[Yy]$ ]] || die "Cannot continue without ${label}. Install it manually and re-run."
    fi
    case "$PKG_MANAGER" in
      brew)   brew install "$@" ;;
      apt)    $SUDO apt-get update -qq && $SUDO apt-get install -y "$@" ;;
      dnf)    $SUDO dnf install -y "$@" ;;
      yum)    $SUDO yum install -y "$@" ;;
      pacman) $SUDO pacman -S --noconfirm "$@" ;;
      apk)    $SUDO apk add "$@" ;;
      *)      die "${label} is required but no supported package manager was found. Install manually: https://python.org / https://nodejs.org" ;;
    esac
  }

  # ── Check / install Python 3.11+ ──────────────────────────────────────────
  PYTHON_CMD=""
  for cmd in python3.11 python3.12 python3.13 python3; do
    if command -v "$cmd" &>/dev/null && "$cmd" -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
      PYTHON_CMD="$cmd"
      success "Python found: $cmd ($($cmd --version))"
      break
    fi
  done

  if [[ -z "$PYTHON_CMD" ]]; then
    warn "Python 3.11+ not found."
    case "$PKG_MANAGER" in
      brew)   pkg_install "Python 3.11" python@3.11 ;;
      apt)    pkg_install "Python 3.11" python3.11 python3.11-venv python3-pip ;;
      dnf)    pkg_install "Python 3.11" python3.11 ;;
      yum)    pkg_install "Python 3.11" python3.11 ;;
      pacman) pkg_install "Python"      python ;;
      apk)    pkg_install "Python 3.11" python3 py3-pip ;;
    esac
    # Re-check after install
    for cmd in python3.11 python3.12 python3.13 python3; do
      if command -v "$cmd" &>/dev/null && "$cmd" -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
        PYTHON_CMD="$cmd"; break
      fi
    done
    [[ -n "$PYTHON_CMD" ]] || die "Python 3.11+ install failed. Please install manually: https://python.org"
    success "Python installed: $PYTHON_CMD"
  fi

  # ── Check / install Node 18+ ──────────────────────────────────────────────
  NODE_OK=false
  if command -v node &>/dev/null && node -e "process.exit(parseInt(process.versions.node) < 18 ? 1 : 0)" 2>/dev/null; then
    NODE_OK=true
    success "Node.js found: $(node --version)"
  fi

  if [[ "$NODE_OK" == false ]]; then
    warn "Node.js 18+ not found."
    case "$PKG_MANAGER" in
      brew)   pkg_install "Node.js" node ;;
      apt)    pkg_install "Node.js" nodejs npm ;;
      dnf)    pkg_install "Node.js" nodejs npm ;;
      yum)    pkg_install "Node.js" nodejs npm ;;
      pacman) pkg_install "Node.js" nodejs npm ;;
      apk)    pkg_install "Node.js" nodejs npm ;;
    esac
    command -v node &>/dev/null || die "Node.js install failed. Please install manually: https://nodejs.org"
    success "Node.js installed: $(node --version)"
  fi

  command -v npm &>/dev/null || die "npm not found after Node install. Please install manually: https://nodejs.org"

  # ── Install build dependencies for native Python packages ──────────────────
  # ChromaDB's chroma-hnswlib requires C++ compilation and Python dev headers
  PYTHON_VERSION=$("$PYTHON_CMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  NEED_BUILD_DEPS=false

  # Check for Python.h
  if ! "$PYTHON_CMD" -c "import sysconfig; assert sysconfig.get_path('include')" 2>/dev/null \
     || [[ ! -f "$("$PYTHON_CMD" -c "import sysconfig; print(sysconfig.get_path('include'))")/Python.h" ]]; then
    NEED_BUILD_DEPS=true
  fi
  # Check for g++
  command -v g++ &>/dev/null || NEED_BUILD_DEPS=true

  if [[ "$NEED_BUILD_DEPS" == true ]]; then
    info "Installing build dependencies for native Python packages..."
    case "$PKG_MANAGER" in
      brew)   ;; # Xcode command line tools handle this on macOS
      apt)    $SUDO apt-get update -qq && $SUDO apt-get install -y build-essential "python${PYTHON_VERSION}-dev" ;;
      dnf)    $SUDO dnf install -y gcc-c++ "python${PYTHON_VERSION}-devel" ;;
      yum)    $SUDO yum install -y gcc-c++ "python${PYTHON_VERSION}-devel" ;;
      pacman) $SUDO pacman -S --noconfirm base-devel ;;
      apk)    $SUDO apk add build-base python3-dev ;;
    esac
    success "Build dependencies installed"
  fi

  # ── Install optional system libraries for PDF/image processing ─────────────
  # poppler-utils: enables pdf2image fallback for scanned PDF OCR
  # PyMuPDF installs from prebuilt wheels and doesn't need system deps
  info "Checking optional system libraries..."
  if ! command -v pdftoppm &>/dev/null; then
    info "Installing poppler-utils (PDF page rendering)..."
    case "$PKG_MANAGER" in
      brew)   brew install poppler ;;
      apt)    $SUDO apt-get install -y poppler-utils ;;
      dnf)    $SUDO dnf install -y poppler-utils ;;
      yum)    $SUDO yum install -y poppler-utils ;;
      pacman) $SUDO pacman -S --noconfirm poppler ;;
      apk)    $SUDO apk add poppler-utils ;;
      *)      warn "Could not install poppler-utils — scanned PDF fallback rendering will be unavailable" ;;
    esac
    success "poppler-utils installed"
  else
    success "poppler-utils already available"
  fi
fi

# ── Confirmation ──────────────────────────────────────────────────────────────
header "Installation plan"
echo -e "  Mode      : ${BOLD}${MODE}${RESET}"
echo -e "  Directory : ${BOLD}${INSTALL_DIR}${RESET}"
if [[ "$MODE" == "docker" ]]; then
  echo -e "  HTTP Port : ${BOLD}${HTTP_PORT}${RESET}"
else
  echo -e "  Backend   : ${BOLD}http://localhost:${BACKEND_PORT}${RESET}"
  echo -e "  Frontend  : ${BOLD}http://localhost:${FRONTEND_PORT}${RESET}"
fi
echo -e "  LLM Model : ${BOLD}${LLM_MODEL}${RESET}"
echo ""

if [[ "$SKIP_CONFIRM" == false ]]; then
  read -rp "Proceed with installation? [Y/n] " confirm </dev/tty
  confirm="${confirm:-Y}"
  [[ "$confirm" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }
fi

# ── Clone or update ───────────────────────────────────────────────────────────
header "Fetching code..."

if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Existing installation found at ${INSTALL_DIR}. Updating..."
  git -C "$INSTALL_DIR" fetch origin
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull origin "$BRANCH"
  success "Updated to latest ${BRANCH}"
else
  info "Cloning to ${INSTALL_DIR}..."
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
  success "Cloned successfully"
fi

cd "$INSTALL_DIR"

# ── Environment setup ─────────────────────────────────────────────────────────
header "Configuring environment..."

if [[ ! -f .env ]]; then
  cp .env.example .env
  success "Created .env from template"
else
  info ".env already exists — skipping (won't overwrite)"
fi

generate_key() {
  python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null \
    || openssl rand -hex 32 2>/dev/null \
    || cat /dev/urandom | tr -dc 'a-f0-9' | head -c 64
}

update_env() {
  local key="$1" value="$2"
  # Escape sed special chars in value (|, &, \, /)
  local escaped_value
  escaped_value=$(printf '%s' "$value" | sed 's/[|&/\]/\\&/g')
  if grep -q "^${key}=" .env; then
    if [[ "$OS" == "macos" ]]; then
      sed -i '' "s|^${key}=.*|${key}=${escaped_value}|" .env
    else
      sed -i "s|^${key}=.*|${key}=${escaped_value}|" .env
    fi
  else
    echo "${key}=${value}" >> .env
  fi
}

# Auto-generate secure keys if still at defaults
if grep -q "change-this-to-a-random" .env; then
  VAULT_KEY="$(generate_key)"
  SECRET_KEY="$(generate_key)"
  update_env "VAULT_MASTER_KEY" "$VAULT_KEY"
  update_env "SECRET_KEY"       "$SECRET_KEY"
  success "Generated secure VAULT_MASTER_KEY and SECRET_KEY"
fi

# Configure SearXNG SEARCH_URL in .env if enabled
if [[ "$WITH_SEARXNG" == "true" ]]; then
  if [[ "$MODE" == "docker" ]]; then
    update_env "SEARCH_URL" "http://searxng:8080"
  else
    update_env "SEARCH_URL" "http://localhost:8888"
  fi
  success "Configured SEARCH_URL in .env"
fi

# Configure build-args for optional Docker packages in .env
if [[ "$WITH_OFFICE" == "true" ]]; then
  update_env "INSTALL_OFFICE" "true"
else
  update_env "INSTALL_OFFICE" "false"
fi

if [[ "$WITH_BROWSER" == "true" ]]; then
  update_env "INSTALL_BROWSER" "true"
  update_env "BROWSER_ENABLED" "true"
  update_env "BROWSER_HEADLESS" "true"
else
  update_env "INSTALL_BROWSER" "false"
fi


# ── Interactive LLM configuration ────────────────────────────────────────────
# Fetch models from an OpenAI-compatible /v1/models endpoint and display a
# numbered picker. Works with OpenAI, Ollama, Groq, LiteLLM, vLLM, etc.

fetch_models() {
  # $1 = base_url, $2 = api_key — returns newline-separated model IDs
  local url="${1%/}/models"
  local key="$2"
  local response
  response=$(curl -sf -H "Authorization: Bearer ${key}" "$url" 2>/dev/null) || return 1
  echo "$response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    models = data.get('data') or data.get('models') or []
    for m in sorted(models, key=lambda x: x.get('id','')):
        print(m.get('id',''))
except: pass
" 2>/dev/null
}

pick_model() {
  # $1 = prompt text, $2 = model list (newline-separated), $3 = default value
  # IMPORTANT: All display output goes to /dev/tty so that $() only captures
  # the final model name echoed to stdout.
  local prompt_text="$1"
  local model_list="$2"
  local default_val="$3"
  local count i choice

  # Convert to array
  local -a models
  while IFS= read -r line; do
    [[ -n "$line" ]] && models+=("$line")
  done <<< "$model_list"
  count=${#models[@]}

  if [[ "$count" -eq 0 ]]; then
    warn "No models found." >/dev/tty
    read -rp "  Enter model name manually [${default_val}]: " choice </dev/tty
    echo "${choice:-$default_val}"
    return
  fi

  {
    echo ""
    echo -e "  ${BOLD}${prompt_text}${RESET}"
    echo ""
    for i in "${!models[@]}"; do
      local marker=""
      [[ "${models[$i]}" == "$default_val" ]] && marker=" ${YELLOW}(current)${RESET}"
      echo -e "    $((i+1))) ${models[$i]}${marker}"
    done
    echo ""
  } >/dev/tty

  read -rp "  Enter number or type a model name [${default_val}]: " choice </dev/tty

  if [[ -z "$choice" ]]; then
    echo "$default_val"
  elif [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= count )); then
    echo "${models[$((choice-1))]}"
  else
    echo "$choice"  # treat as a manually typed model name
  fi
}

# Apply any values passed via flags first
[[ -n "$LLM_BASE_URL" ]] && update_env "LLM_BASE_URL" "$LLM_BASE_URL" && info "Set LLM_BASE_URL"
[[ -n "$LLM_API_KEY"  ]] && update_env "LLM_API_KEY"  "$LLM_API_KEY"  && info "Set LLM_API_KEY"
[[ -n "$LLM_MODEL"    ]] && update_env "LLM_MODEL"     "$LLM_MODEL"    && info "Set LLM_MODEL"

# Read current .env values (|| true needed — set -eo pipefail kills on grep no-match)
CURRENT_BASE_URL=$(grep "^LLM_BASE_URL=" .env 2>/dev/null | cut -d= -f2- || true)
CURRENT_API_KEY=$(grep "^LLM_API_KEY=" .env 2>/dev/null | cut -d= -f2- || true)
CURRENT_MODEL=$(grep "^LLM_MODEL=" .env 2>/dev/null | cut -d= -f2- || true)
CURRENT_EMBEDDING=$(grep "^EMBEDDING_MODEL=" .env 2>/dev/null | cut -d= -f2- || true)

if [[ "$WITH_OLLAMA" == "true" ]]; then
  info "--with-ollama flag set — skipping LLM provider prompts (demo_setup.sh will configure)"
elif [[ "$SKIP_CONFIRM" == false ]]; then
  header "LLM Provider Configuration"
  echo ""
  echo "  Pantheon works with any OpenAI-compatible API endpoint."
  echo "  Common providers: Ollama (local), OpenAI, Groq, Together.ai, vLLM, LiteLLM"
  echo -e "  ${YELLOW}Note: You can easily switch models and providers at any time after install${RESET}"
  echo -e "        ${YELLOW}directly inside the Web UI (Settings → LLM Endpoints).${RESET}"
  echo ""

  # ── Provider Selection ────────────────────────────────────────────────────
  echo -e "  ${BOLD}Select your LLM Provider:${RESET}"
  echo "    1) Ollama (local) [default]"
  echo "    2) OpenAI"
  echo "    3) Groq"
  echo "    4) OpenRouter"
  echo "    5) Custom (OpenAI-compatible)"
  echo ""
  read -rp "  Enter choice [1-5]: " provider_choice </dev/tty
  provider_choice="${provider_choice:-1}"

  case "${provider_choice}" in
    1)
      LLM_BASE_URL="http://localhost:11434/v1"
      WITH_OLLAMA=true
      ;;
    2)
      LLM_BASE_URL="https://api.openai.com/v1"
      WITH_OLLAMA=false
      ;;
    3)
      LLM_BASE_URL="https://api.groq.com/openai/v1"
      WITH_OLLAMA=false
      ;;
    4)
      LLM_BASE_URL="https://openrouter.ai/api/v1"
      WITH_OLLAMA=false
      ;;
    5)
      read -rp "  LLM Base URL [${CURRENT_BASE_URL:-http://localhost:11434/v1}]: " input_url </dev/tty
      LLM_BASE_URL="${input_url:-${CURRENT_BASE_URL:-http://localhost:11434/v1}}"
      if [[ "$LLM_BASE_URL" == *"ollama"* || "$LLM_BASE_URL" == *"11434"* ]]; then
        WITH_OLLAMA=true
      else
        WITH_OLLAMA=false
      fi
      ;;
    *)
      LLM_BASE_URL="http://localhost:11434/v1"
      WITH_OLLAMA=true
      ;;
  esac
  update_env "LLM_BASE_URL" "$LLM_BASE_URL"



  # ── API Key ───────────────────────────────────────────────────────────────
  if [[ "$LLM_BASE_URL" == *"ollama"* || "$LLM_BASE_URL" == *"localhost:11434"* ]]; then
    LLM_API_KEY="${CURRENT_API_KEY:-ollama}"
    info "Ollama detected — using placeholder API key"
  else
    # Mask the current key for display
    if [[ -n "$CURRENT_API_KEY" && "$CURRENT_API_KEY" != "sk-your"* ]]; then
      MASKED_KEY="${CURRENT_API_KEY:0:8}...${CURRENT_API_KEY: -4}"
      read -rp "  API Key [${MASKED_KEY}]: " input_key </dev/tty
      LLM_API_KEY="${input_key:-$CURRENT_API_KEY}"
    else
      read -rp "  API Key: " input_key </dev/tty
      LLM_API_KEY="${input_key:-$CURRENT_API_KEY}"
    fi
  fi
  update_env "LLM_API_KEY" "$LLM_API_KEY"

  # ── Fetch available models ────────────────────────────────────────────────
  info "Fetching available models from ${LLM_BASE_URL}..."
  MODEL_LIST=$(fetch_models "$LLM_BASE_URL" "$LLM_API_KEY" 2>/dev/null) || MODEL_LIST=""

  if [[ -n "$MODEL_LIST" ]]; then
    MODEL_COUNT=$(echo "$MODEL_LIST" | wc -l | tr -d ' ')
    success "Found ${MODEL_COUNT} models"

    # ── Primary chat model ────────────────────────────────────────────────
    CHOSEN_MODEL=$(pick_model "Select primary chat model:" "$MODEL_LIST" "${CURRENT_MODEL:-gpt-4o}")
    update_env "LLM_MODEL" "$CHOSEN_MODEL"
    success "Primary model: ${CHOSEN_MODEL}"

    # ── Prefill / fast model (optional) ───────────────────────────────────
    echo ""
    echo -e "  ${CYAN}A prefill model is a faster/cheaper model used for tasks like${RESET}"
    echo -e "  ${CYAN}summarization, memory consolidation, and background processing.${RESET}"
    echo -e "  ${CYAN}Leave blank to use the primary model for everything.${RESET}"
    CHOSEN_PREFILL=$(pick_model "Select prefill / fast model (optional):" "$MODEL_LIST" "${CURRENT_MODEL:-}")
    if [[ -n "$CHOSEN_PREFILL" && "$CHOSEN_PREFILL" != "$CHOSEN_MODEL" ]]; then
      update_env "LLM_PREFILL_MODEL" "$CHOSEN_PREFILL"
      success "Prefill model: ${CHOSEN_PREFILL}"
    else
      info "Prefill model: same as primary (${CHOSEN_MODEL})"
    fi

    # ── Embedding model ───────────────────────────────────────────────────
    # Filter model list for likely embedding models, but show all as fallback
    EMBED_MODELS=$(echo "$MODEL_LIST" | grep -iE 'embed|e5|bge|gte|mxbai|nomic' 2>/dev/null) || EMBED_MODELS=""
    if [[ -z "$EMBED_MODELS" ]]; then
      EMBED_MODELS="$MODEL_LIST"
    fi
    CHOSEN_EMBED=$(pick_model "Select embedding model:" "$EMBED_MODELS" "${CURRENT_EMBEDDING:-text-embedding-3-small}")
    update_env "EMBEDDING_MODEL" "$CHOSEN_EMBED"
    success "Embedding model: ${CHOSEN_EMBED}"

  else
    warn "Could not fetch model list from ${LLM_BASE_URL}"
    warn "This can happen if the endpoint is not yet running or the API key is invalid."
    echo ""

    read -rp "  Primary chat model [${CURRENT_MODEL:-gpt-4o}]: " input_model </dev/tty
    update_env "LLM_MODEL" "${input_model:-${CURRENT_MODEL:-gpt-4o}}"

    read -rp "  Prefill / fast model (optional, Enter to skip): " input_prefill </dev/tty
    [[ -n "$input_prefill" ]] && update_env "LLM_PREFILL_MODEL" "$input_prefill"

    read -rp "  Embedding model [${CURRENT_EMBEDDING:-text-embedding-3-small}]: " input_embed </dev/tty
    update_env "EMBEDDING_MODEL" "${input_embed:-${CURRENT_EMBEDDING:-text-embedding-3-small}}"
  fi
else
  # --yes mode: just apply flag values or keep existing
  info "Skipping interactive model selection (--yes mode)"
fi

# ── Create data directories ───────────────────────────────────────────────────
header "Preparing data directories..."
mkdir -p data/db data/chroma data/personality data/projects data/workspace
success "Data directories ready"

# ── Agent name ────────────────────────────────────────────────────────────────
SOUL_SRC="$INSTALL_DIR/backend/data/personality/soul.md"
SOUL_DEST="$INSTALL_DIR/data/personality/soul.md"

# Detect the current name baked into soul.md (default: Pan)
CURRENT_AGENT_NAME=$(grep -o "^You are [A-Za-z0-9_-]*" "$SOUL_SRC" 2>/dev/null | awk '{print $3}' || echo "Pan")

if [[ "$SKIP_CONFIRM" == false ]]; then
  echo ""
  header "Agent Identity"
  echo ""
  echo "  Your agent introduces itself by name. You can choose any name you like."
  echo "  Leave blank to keep the default."
  echo ""
  read -rp "  Agent name [${CURRENT_AGENT_NAME}]: " input_agent_name </dev/tty
  AGENT_NAME="${input_agent_name:-$CURRENT_AGENT_NAME}"
else
  AGENT_NAME="${AGENT_NAME:-$CURRENT_AGENT_NAME}"
fi

# Write soul.md to the data directory, replacing the name throughout
if [[ "$AGENT_NAME" != "$CURRENT_AGENT_NAME" ]]; then
  # Escape special chars for sed
  OLD_ESC=$(printf '%s\n' "$CURRENT_AGENT_NAME" | sed 's/[\/&]/\\&/g')
  NEW_ESC=$(printf '%s\n' "$AGENT_NAME"          | sed 's/[\/&]/\\&/g')
  sed "s/${OLD_ESC}/${NEW_ESC}/g" "$SOUL_SRC" > "$SOUL_DEST"
  success "Agent named \"${AGENT_NAME}\" (soul.md written to data/personality/)"
else
  # Just copy as-is if name unchanged
  cp "$SOUL_SRC" "$SOUL_DEST"
  success "Agent personality copied (name: ${AGENT_NAME})"
fi

# Always copy agent.md template to data dir (safe to overwrite — it's config, not user data)
AGENT_SRC="$INSTALL_DIR/backend/data/personality/agent.md"
AGENT_DEST="$INSTALL_DIR/data/personality/agent.md"
if [[ -f "$AGENT_SRC" ]]; then
  cp "$AGENT_SRC" "$AGENT_DEST"
  success "Agent behavior config copied (agent.md)"
fi

# ── Web interface password ────────────────────────────────────────────────────
CURRENT_AUTH_PW=$(grep "^AUTH_PASSWORD=" .env 2>/dev/null | cut -d= -f2- || true)

if [[ "$SKIP_CONFIRM" == false ]]; then
  echo ""
  header "Web Interface Security"
  echo ""
  echo "  Set a password to protect the web interface from unauthorized access."
  echo "  This is strongly recommended if the server is publicly reachable."
  echo "  Leave blank to disable authentication."
  echo ""
  if [[ -n "$CURRENT_AUTH_PW" ]]; then
    read -rsp "  Password [leave blank to keep existing]: " input_auth_pw </dev/tty
    echo ""
  else
    read -rsp "  Password (Enter to skip): " input_auth_pw </dev/tty
    echo ""
  fi
  if [[ -n "$input_auth_pw" ]]; then
    AUTH_PASSWORD="$input_auth_pw"
  elif [[ -z "$input_auth_pw" && -n "$CURRENT_AUTH_PW" ]]; then
    AUTH_PASSWORD="$CURRENT_AUTH_PW"
  fi
fi

if [[ -n "$AUTH_PASSWORD" ]]; then
  update_env "AUTH_PASSWORD" "$AUTH_PASSWORD"
  success "Web interface password set"
else
  update_env "AUTH_PASSWORD" ""
  warn "No password set — web interface is open to anyone who can reach this server"
fi

# =============================================================================
# ── DOCKER MODE ───────────────────────────────────────────────────────────────
# =============================================================================
if [[ "$MODE" == "docker" ]]; then

  # Adjust nginx port if non-default
  if [[ "$HTTP_PORT" != "80" ]]; then
    if [[ "$OS" == "macos" ]]; then
      sed -i '' "s|\"80:80\"|\"${HTTP_PORT}:80\"|g" docker-compose.yml
    else
      sed -i "s|\"80:80\"|\"${HTTP_PORT}:80\"|g" docker-compose.yml
    fi
    info "Configured nginx to serve on port ${HTTP_PORT}"
  fi

  # Generate SearXNG config in docker mode if enabled
  if [[ "$WITH_SEARXNG" == "true" ]]; then
    mkdir -p data/searxng
    if [[ ! -f data/searxng/settings.yml ]]; then
      info "Writing SearXNG settings.yml..."
      local sx_secret
      sx_secret=$(generate_key)
      cat > data/searxng/settings.yml <<EOF
# Pantheon SearXNG settings — generated by deploy.sh
use_default_settings: true
server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "${sx_secret}"
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
      success "SearXNG config written to data/searxng/settings.yml"
    fi
  fi

  header "Building Docker images (this may take a few minutes on first run)..."
  docker compose pull chromadb 2>/dev/null || true
  docker compose build --parallel
  success "Images built"

  header "Starting services..."
  docker compose up -d
  success "All services started"

  header "Waiting for backend to be healthy..."
  MAX_TRIES=30; WAIT=2
  for i in $(seq 1 $MAX_TRIES); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${HTTP_PORT}/health" 2>/dev/null || echo "000")
    if [[ "$STATUS" == "200" ]]; then
      success "Backend is healthy (HTTP 200)"; break
    fi
    if [[ $i -eq $MAX_TRIES ]]; then
      warn "Health check timed out. Run: docker compose logs -f"; break
    fi
    echo -ne "\r  Attempt ${i}/${MAX_TRIES} (HTTP ${STATUS})... "
    sleep $WAIT
  done

  if grep -q "^LLM_API_KEY=$\|^LLM_API_KEY=sk-your" .env 2>/dev/null; then
    echo ""
    warn "┌─────────────────────────────────────────────────────┐"
    warn "│  ACTION REQUIRED: Set your LLM API key              │"
    warn "│  Edit ${INSTALL_DIR}/.env                           │"
    warn "│  Set LLM_API_KEY=<your key>                         │"
    warn "│  Then: docker compose restart backend               │"
    warn "└─────────────────────────────────────────────────────┘"
  fi

  RUNNING_MSG="Docker"
  RUNNING_URL="http://localhost:${HTTP_PORT}"

# =============================================================================
# ── LOCAL MODE ────────────────────────────────────────────────────────────────
# =============================================================================
else

  # Point ChromaDB to in-process (PersistentClient) by clearing the host
  update_env "CHROMA_HOST" ""
  update_env "DATA_DIR" "${INSTALL_DIR}/data"
  info "ChromaDB set to in-process mode (no container needed)"
  info "DATA_DIR set to ${INSTALL_DIR}/data"

  # ── Python virtual environment ──────────────────────────────────────────────
  header "Setting up Python environment..."

  VENV_DIR="$INSTALL_DIR/.venv"
  if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
    rm -rf "$VENV_DIR"
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    success "Created virtual environment at .venv"
  else
    info "Virtual environment already exists"
  fi

  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip -q
  pip install --no-cache-dir -r backend/requirements.txt -q
  success "Python dependencies installed"

  # ── Optional: LibreOffice for Office artifact previews ─────────────────────
  if [[ "$WITH_OFFICE" == "true" ]]; then
    header "Installing LibreOffice for Office/PDF previews..."
    if [[ -z "${PKG_MANAGER:-}" ]]; then
      warn "No package manager detected — skipping LibreOffice install."
    else
      case "$PKG_MANAGER" in
        apt)    $SUDO apt-get update -qq && $SUDO apt-get install -y libreoffice poppler-utils ;;
        dnf)    $SUDO dnf install -y libreoffice poppler-utils ;;
        yum)    $SUDO yum install -y libreoffice poppler-utils ;;
        pacman) $SUDO pacman -S --noconfirm libreoffice-fresh poppler ;;
        brew)   brew install --cask libreoffice && brew install poppler ;;
        apk)    $SUDO apk add libreoffice poppler-utils ;;
        *)      warn "Package manager $PKG_MANAGER unsupported — install libreoffice + poppler-utils manually." ;;
      esac
      success "LibreOffice installed (artifact previews for .docx/.xlsx/.pptx/.pdf will work)"
    fi
  fi

  # ── Frontend build ──────────────────────────────────────────────────────────
  header "Building frontend..."

  cd frontend
  npm install --silent
  VITE_API_URL="" npm run build
  cd "$INSTALL_DIR"
  success "Frontend built"

  # ── Ensure helper scripts are executable ───────────────────────────────────
  chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/stop.sh"
  success "start.sh and stop.sh are ready"

  # ── Start services ──────────────────────────────────────────────────────────
  header "Starting Pantheon..."
  BACKEND_PORT="$BACKEND_PORT" FRONTEND_PORT="$FRONTEND_PORT" "$INSTALL_DIR/start.sh"

  # ── Health check ────────────────────────────────────────────────────────────
  header "Waiting for backend to be healthy..."
  MAX_TRIES=20; WAIT=2
  for i in $(seq 1 $MAX_TRIES); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${BACKEND_PORT}/health" 2>/dev/null || echo "000")
    if [[ "$STATUS" == "200" ]]; then
      success "Backend is healthy (HTTP 200)"; break
    fi
    if [[ $i -eq $MAX_TRIES ]]; then
      warn "Health check timed out. Check logs with: tail -f ${INSTALL_DIR}/backend.log"; break
    fi
    echo -ne "\r  Attempt ${i}/${MAX_TRIES} (HTTP ${STATUS})... "
    sleep $WAIT
  done

  if grep -q "^LLM_API_KEY=$\|^LLM_API_KEY=sk-your" .env 2>/dev/null; then
    echo ""
    warn "┌─────────────────────────────────────────────────────┐"
    warn "│  ACTION REQUIRED: Set your LLM API key              │"
    warn "│  Edit ${INSTALL_DIR}/.env                           │"
    warn "│  Set LLM_API_KEY=<your key>                         │"
    warn "│  Then restart: ${INSTALL_DIR}/stop.sh               │"
    warn "│               ${INSTALL_DIR}/start.sh               │"
    warn "└─────────────────────────────────────────────────────┘"
  fi

  RUNNING_MSG="Local"
  RUNNING_URL="http://localhost:${BACKEND_PORT}"

fi

# =============================================================================
# ── HTTPS / CADDY SETUP (both modes) ─────────────────────────────────────────
# =============================================================================

setup_caddy() {
  local domain="$1"

  # ── Install Caddy if not present ────────────────────────────────────────
  if ! command -v caddy &>/dev/null; then
    info "Installing Caddy..."
    case "$PKG_MANAGER" in
      brew)
        brew install caddy
        ;;
      apt)
        $SUDO apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl -qq
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | $SUDO gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | $SUDO tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
        $SUDO apt-get update -qq
        $SUDO apt-get install -y caddy
        ;;
      dnf)
        $SUDO dnf install -y 'dnf-command(copr)'
        $SUDO dnf copr enable -y @caddy/caddy
        $SUDO dnf install -y caddy
        ;;
      yum)
        $SUDO yum install -y yum-plugin-copr
        $SUDO yum copr enable -y @caddy/caddy
        $SUDO yum install -y caddy
        ;;
      pacman)
        $SUDO pacman -S --noconfirm caddy
        ;;
      apk)
        $SUDO apk add caddy
        ;;
      *)
        warn "Could not auto-install Caddy. Install manually: https://caddyserver.com/docs/install"
        return 1
        ;;
    esac
    success "Caddy installed"
  else
    success "Caddy already installed ($(caddy version 2>/dev/null | head -1))"
  fi

  # ── Write the Caddyfile ─────────────────────────────────────────────────
  local caddy_file="$INSTALL_DIR/Caddyfile"
  info "Configuring Caddy for ${domain}..."

  # Determine backend ports based on mode
  local be_port="${BACKEND_PORT:-8000}"
  if [[ "$MODE" == "docker" ]]; then
    # In Docker mode, nginx is on HTTP_PORT, proxy everything through it
    be_port="${HTTP_PORT:-80}"
  fi

  # Build the frontend handle block:
  #   Docker: nginx serves the SPA (handles its own routing)
  #   Local:  Caddy serves static files directly with SPA fallback
  local frontend_handle
  if [[ "$MODE" == "docker" ]]; then
    local fe_port="${HTTP_PORT:-80}"
    frontend_handle="	handle {
		reverse_proxy localhost:${fe_port}
	}"
  else
    frontend_handle="	# Serve the pre-built React SPA.
	# try_files makes React Router routes (e.g. /chat) work on page refresh.
	handle {
		root * ${INSTALL_DIR}/frontend/dist
		try_files {path} /index.html
		file_server
	}"
  fi

  cat > "$caddy_file" <<CADDYEOF
# Pantheon — Caddy reverse proxy with automatic HTTPS
# Auto-generated by deploy.sh for domain: ${domain}

${domain} {
	# API and backend routes
	handle /api/* {
		reverse_proxy localhost:${be_port}
	}

	handle /docs {
		reverse_proxy localhost:${be_port}
	}

	handle /openapi.json {
		reverse_proxy localhost:${be_port}
	}

	handle /health {
		reverse_proxy localhost:${be_port}
	}

	# WebSocket
	handle /ws/* {
		reverse_proxy localhost:${be_port}
	}

	# Frontend
${frontend_handle}

	log {
		output file /var/log/caddy/pantheon.log {
			roll_size 10mb
			roll_keep 5
		}
	}
}
CADDYEOF
  success "Caddyfile written to ${caddy_file}"

  # ── Create log directory ────────────────────────────────────────────────
  $SUDO mkdir -p /var/log/caddy
  $SUDO chown caddy:caddy /var/log/caddy 2>/dev/null || true

  # ── Validate config ─────────────────────────────────────────────────────
  if caddy validate --config "$caddy_file" --adapter caddyfile &>/dev/null; then
    success "Caddyfile validated"
  else
    warn "Caddyfile validation failed — check ${caddy_file}"
    return 1
  fi

  # ── Install as systemd service (Linux) or run directly ──────────────────
  if [[ "$OS" == "linux" ]] && command -v systemctl &>/dev/null; then
    # Copy Caddyfile to standard location
    $SUDO cp "$caddy_file" /etc/caddy/Caddyfile

    # Enable and start/restart Caddy
    $SUDO systemctl enable caddy 2>/dev/null || true
    if systemctl is-active caddy &>/dev/null; then
      $SUDO systemctl reload caddy
      success "Caddy reloaded with new config"
    else
      $SUDO systemctl start caddy
      success "Caddy started"
    fi

    # Verify HTTPS is working (give Caddy a moment to get the cert)
    info "Waiting for HTTPS certificate provisioning..."
    sleep 3
    local https_status
    https_status=$(curl -sf -o /dev/null -w "%{http_code}" "https://${domain}/health" 2>/dev/null || echo "000")
    if [[ "$https_status" == "200" || "$https_status" == "502" ]]; then
      success "HTTPS is live at https://${domain}"
    else
      info "Certificate may still be provisioning. Check: sudo caddy status"
      info "Ensure ports 80 and 443 are open in your firewall/security group."
    fi
  else
    # macOS or non-systemd: just print instructions
    info "To start Caddy manually:"
    echo "  caddy run --config ${caddy_file} --adapter caddyfile"
  fi
}

# ── Prompt for HTTPS setup ───────────────────────────────────────────────────
if [[ -z "$DOMAIN" && "$SKIP_CONFIRM" == false ]]; then
  echo ""
  header "HTTPS Setup (optional)"
  echo ""
  echo "  Caddy provides automatic HTTPS with Let's Encrypt certificates."
  echo "  If you have a domain pointed at this server, enter it below."
  echo "  Leave blank to skip (you can set it up later)."
  echo ""
  read -rp "  Domain name (e.g. agent.example.com): " DOMAIN </dev/tty
fi

if [[ -n "$DOMAIN" ]]; then
  # Add the domain to CORS_ORIGINS so the backend accepts requests from it
  CURRENT_CORS=$(grep "^CORS_ORIGINS=" .env 2>/dev/null | cut -d= -f2- || true)
  if [[ -n "$CURRENT_CORS" && "$CURRENT_CORS" != *"${DOMAIN}"* ]]; then
    update_env "CORS_ORIGINS" "${CURRENT_CORS},https://${DOMAIN}"
    info "Added https://${DOMAIN} to CORS_ORIGINS"
  elif [[ -z "$CURRENT_CORS" ]]; then
    update_env "CORS_ORIGINS" "http://localhost:3000,http://localhost:5173,https://${DOMAIN}"
    info "Set CORS_ORIGINS with https://${DOMAIN}"
  fi

  setup_caddy "$DOMAIN"
  RUNNING_URL="https://${DOMAIN}"

  # Rebuild the frontend without a hardcoded API URL so that all requests
  # are relative and flow through Caddy. Without this, VITE_API_URL points
  # to http://localhost:8000, which the user's browser can't reach.
  header "Rebuilding frontend for HTTPS..."
  if [[ -d "$INSTALL_DIR/frontend" ]]; then
    cd "$INSTALL_DIR/frontend"
    VITE_API_URL="" npm run build
    cd "$INSTALL_DIR"
    success "Frontend rebuilt for Caddy (relative API URLs)"

    # Restart services to pick up new frontend build and updated .env
    if [[ -f "$INSTALL_DIR/stop.sh" ]]; then
      "$INSTALL_DIR/stop.sh" 2>/dev/null || true
      sleep 1
      "$INSTALL_DIR/start.sh"
    fi
  fi
fi

# =============================================================================
# ── Optional demo extras (Ollama / SearXNG) ──────────────────────────────────
# =============================================================================
if [[ "$WITH_OLLAMA" == "true" || "$WITH_SEARXNG" == "true" || "$WITH_BROWSER" == "true" ]]; then
  header "Running demo_setup.sh for optional extras"
  DEMO_ARGS=()
  [[ "$WITH_OLLAMA" == "true" ]]  && DEMO_ARGS+=(--with-ollama --tag "$OLLAMA_TAG")
  [[ "$WITH_SEARXNG" == "true" && "$MODE" == "local" ]] && DEMO_ARGS+=(--with-searxng)
  [[ "$WITH_BROWSER" == "true" && "$MODE" == "local" ]] && DEMO_ARGS+=(--with-browser)

  if [[ -x "${INSTALL_DIR}/demo_setup.sh" ]]; then
    ( cd "$INSTALL_DIR" && ./demo_setup.sh "${DEMO_ARGS[@]}" ) || warn "demo_setup.sh exited non-zero — continuing"

    # Restart Pantheon so it picks up the new .env configuration
    if [[ "$MODE" == "local" ]]; then
      info "Restarting Pantheon to pick up new configuration..."
      "$INSTALL_DIR/stop.sh" 2>/dev/null || true
      sleep 1
      "$INSTALL_DIR/start.sh"
    elif [[ "$MODE" == "docker" ]]; then
      info "Restarting Docker stack to pick up new configuration..."
      ( cd "$INSTALL_DIR" && docker compose restart backend ) || warn "Docker restart failed"
    fi
  else
    warn "demo_setup.sh not found at ${INSTALL_DIR}/demo_setup.sh — skipping extras"
  fi
fi

# =============================================================================
# ── DONE ─────────────────────────────────────────────────────────────────────
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   Pantheon is running!  (${RUNNING_MSG})${RESET}"
echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}Web UI${RESET}       →  ${RUNNING_URL}"
if [[ -n "$DOMAIN" ]]; then
  echo -e "  ${BOLD}API Docs${RESET}     →  https://${DOMAIN}/docs"
  echo -e "  ${BOLD}Caddy logs${RESET}   →  /var/log/caddy/pantheon.log"
  echo -e "  ${BOLD}Caddy config${RESET} →  /etc/caddy/Caddyfile"
elif [[ "$MODE" == "docker" ]]; then
  echo -e "  ${BOLD}API Docs${RESET}     →  http://localhost:${HTTP_PORT}/docs"
  echo -e "  ${BOLD}Logs${RESET}         →  docker compose -C ${INSTALL_DIR} logs -f backend"
  echo -e "  ${BOLD}Stop${RESET}         →  docker compose -C ${INSTALL_DIR} down"
else
  echo -e "  ${BOLD}API Docs${RESET}     →  http://localhost:${BACKEND_PORT}/docs"
  echo -e "  ${BOLD}Stop${RESET}         →  ${INSTALL_DIR}/stop.sh"
  echo -e "  ${BOLD}Restart${RESET}      →  ${INSTALL_DIR}/start.sh"
fi
echo ""
echo -e "  ${YELLOW}Next step:${RESET} Open the Web UI and start chatting. You can customize models"
echo -e "             and providers at any time in Settings → LLM Endpoints."
echo ""
echo -e "  ${CYAN}Optional extras:${RESET}"
echo -e "    ${BOLD}Browser tools${RESET}  →  ./demo_setup.sh --with-browser  (Playwright + headless Chromium)"
echo -e "    ${BOLD}Local LLM${RESET}      →  ./demo_setup.sh --with-ollama   (Ollama + Qwen 2.5)"
echo -e "    ${BOLD}Private search${RESET} →  ./demo_setup.sh --with-searxng  (SearXNG search engine)"
echo ""
