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
LLM_BASE_URL="${LLM_BASE_URL:-}"
LLM_API_KEY="${LLM_API_KEY:-}"
LLM_MODEL="${LLM_MODEL:-gpt-4o}"
BRANCH="main"
SKIP_CONFIRM=false
MODE=""   # "local" or "docker" — prompted if not set
DOMAIN=""         # domain for HTTPS via Caddy — prompted if not set
AGENT_NAME=""     # agent name written into soul.md — prompted if not set
AUTH_PASSWORD=""  # web interface password — prompted if not set
WITH_OLLAMA=false   # run demo_setup.sh --with-ollama after install
WITH_SEARXNG=false  # run demo_setup.sh --with-searxng after install
WITH_BROWSER=false  # run demo_setup.sh --with-browser after install
OLLAMA_TAG="4b"     # Nemotron model tag passed to demo_setup.sh

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
    --with-browser) WITH_BROWSER=true; shift ;;
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
echo "  ║  Self-hosted AI Agent Framework 2026-04-01 ║"
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
  check_cmd docker "Install from https://docs.docker.com/get-docker/"

  if docker compose version &>/dev/null 2>&1; then
    success "docker compose (plugin) found"
  elif command -v docker-compose &>/dev/null; then
    success "docker-compose (standalone) found"
  else
    die "Docker Compose is required. Install from https://docs.docker.com/compose/install/"
  fi

  if ! docker info &>/dev/null; then
    die "Docker daemon is not running. Start Docker and try again."
  fi
  success "Docker daemon is running"

else  # local mode

  if [[ -z "$PKG_MANAGER" && "$OS" == "macos" ]]; then
    warn "Homebrew not found. Install it first: https://brew.sh"
    die  "Homebrew is required to auto-install dependencies on macOS."
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
  echo "  Common providers: OpenAI, Ollama (local), Groq, Together.ai, vLLM, LiteLLM"
  echo ""

  # ── Endpoint ──────────────────────────────────────────────────────────────
  read -rp "  LLM Base URL [${CURRENT_BASE_URL:-https://api.openai.com/v1}]: " input_url </dev/tty
  LLM_BASE_URL="${input_url:-${CURRENT_BASE_URL:-https://api.openai.com/v1}}"
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
  [[ "$WITH_SEARXNG" == "true" ]] && DEMO_ARGS+=(--with-searxng)
  [[ "$WITH_BROWSER" == "true" ]] && DEMO_ARGS+=(--with-browser)

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
echo -e "  ${YELLOW}Next step:${RESET} Open Settings in the UI and configure your LLM provider."
echo ""
