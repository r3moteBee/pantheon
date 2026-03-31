#!/usr/bin/env bash
# =============================================================================
# Agent Harness — One-Command Installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/r3moteBee/agent-harness/main/deploy.sh | bash
#   # Or with options:
#   curl -fsSL https://raw.githubusercontent.com/r3moteBee/agent-harness/main/deploy.sh | bash -s -- --dir ~/agent-harness --mode local
# =============================================================================

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/r3moteBee/agent-harness.git"
INSTALL_DIR="${AGENT_HARNESS_DIR:-$HOME/agent-harness}"
HTTP_PORT="${AGENT_HARNESS_PORT:-80}"
BACKEND_PORT="8000"
FRONTEND_PORT="3000"
LLM_BASE_URL="${LLM_BASE_URL:-}"
LLM_API_KEY="${LLM_API_KEY:-}"
LLM_MODEL="${LLM_MODEL:-gpt-4o}"
BRANCH="main"
SKIP_CONFIRM=false
MODE=""   # "local" or "docker" — prompted if not set

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
    --yes|-y)     SKIP_CONFIRM=true;   shift ;;
    --help|-h)
      echo "Usage: deploy.sh [options]"
      echo ""
      echo "Options:"
      echo "  --mode MODE      Run mode: 'local' or 'docker' (prompted if omitted)"
      echo "  --dir PATH       Installation directory (default: ~/agent-harness)"
      echo "  --port PORT      HTTP port — Docker mode only (default: 80)"
      echo "  --api-key KEY    LLM API key (can also set LLM_API_KEY env var)"
      echo "  --model MODEL    LLM model name (default: gpt-4o)"
      echo "  --base-url URL   LLM provider base URL (default: OpenAI)"
      echo "  --branch NAME    Git branch to deploy (default: main)"
      echo "  --yes, -y        Skip confirmation prompts"
      echo ""
      echo "Environment variables (alternative to flags):"
      echo "  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, AGENT_HARNESS_DIR, AGENT_HARNESS_PORT"
      exit 0
      ;;
    *) warn "Unknown option: $1"; shift ;;
  esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║        Agent Harness  Installer           ║"
echo "  ║   Self-hosted AI Agent Framework v1.0.0   ║"
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
  echo -e "  ${BOLD}How would you like to run Agent Harness?${RESET}"
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
  read -rp "  Enter 1 or 2 [default: 2]: " mode_choice
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

  # ── Detect package manager ─────────────────────────────────────────────────
  PKG_MANAGER=""
  if   [[ "$OS" == "macos" ]] && command -v brew &>/dev/null; then PKG_MANAGER="brew"
  elif command -v apt-get  &>/dev/null; then PKG_MANAGER="apt"
  elif command -v dnf      &>/dev/null; then PKG_MANAGER="dnf"
  elif command -v yum      &>/dev/null; then PKG_MANAGER="yum"
  elif command -v pacman   &>/dev/null; then PKG_MANAGER="pacman"
  elif command -v apk      &>/dev/null; then PKG_MANAGER="apk"
  elif [[ "$OS" == "macos" ]]; then
    warn "Homebrew not found. Install it first: https://brew.sh"
    warn "Then re-run this script."
    die  "Homebrew is required to auto-install dependencies on macOS."
  fi

  pkg_install() {
    # $1 = display name, $2+ = package name(s)
    local label="$1"; shift
    info "Installing ${label} via ${PKG_MANAGER}..."
    if [[ "$SKIP_CONFIRM" == false ]]; then
      read -rp "  OK to install ${label} now? [Y/n] " yn
      [[ "${yn:-Y}" =~ ^[Yy]$ ]] || die "Cannot continue without ${label}. Install it manually and re-run."
    fi
    case "$PKG_MANAGER" in
      brew)   brew install "$@" ;;
      apt)    sudo apt-get update -qq && sudo apt-get install -y "$@" ;;
      dnf)    sudo dnf install -y "$@" ;;
      yum)    sudo yum install -y "$@" ;;
      pacman) sudo pacman -S --noconfirm "$@" ;;
      apk)    sudo apk add "$@" ;;
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
  read -rp "Proceed with installation? [Y/n] " confirm
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
  if grep -q "^${key}=" .env; then
    if [[ "$OS" == "macos" ]]; then
      sed -i '' "s|^${key}=.*|${key}=${value}|" .env
    else
      sed -i "s|^${key}=.*|${key}=${value}|" .env
    fi
  else
    echo "${key}=${value}" >> .env
  fi
}

[[ -n "$LLM_BASE_URL" ]] && update_env "LLM_BASE_URL" "$LLM_BASE_URL" && info "Set LLM_BASE_URL"
[[ -n "$LLM_API_KEY"  ]] && update_env "LLM_API_KEY"  "$LLM_API_KEY"  && info "Set LLM_API_KEY"
[[ -n "$LLM_MODEL"    ]] && update_env "LLM_MODEL"     "$LLM_MODEL"    && info "Set LLM_MODEL"

if grep -q "change-this-to-a-random" .env; then
  VAULT_KEY="$(generate_key)"
  SECRET_KEY="$(generate_key)"
  update_env "VAULT_MASTER_KEY" "$VAULT_KEY"
  update_env "SECRET_KEY"       "$SECRET_KEY"
  success "Generated secure VAULT_MASTER_KEY and SECRET_KEY"
fi

# ── Create data directories ───────────────────────────────────────────────────
header "Preparing data directories..."
mkdir -p data/db data/chroma data/personality data/projects data/workspace
success "Data directories ready"

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

  echo ""
  echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════╗${RESET}"
  echo -e "${BOLD}${GREEN}║   Agent Harness is running!  (Docker)             ║${RESET}"
  echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════╝${RESET}"
  echo ""
  echo -e "  ${BOLD}Web UI${RESET}       →  http://localhost:${HTTP_PORT}"
  echo -e "  ${BOLD}API Docs${RESET}     →  http://localhost:${HTTP_PORT}/docs"
  echo -e "  ${BOLD}Logs${RESET}         →  docker compose -C ${INSTALL_DIR} logs -f backend"
  echo -e "  ${BOLD}Stop${RESET}         →  docker compose -C ${INSTALL_DIR} down"
  echo ""
  echo -e "  ${YELLOW}Next step:${RESET} Open Settings in the UI and configure your LLM provider."
  echo ""

# =============================================================================
# ── LOCAL MODE ────────────────────────────────────────────────────────────────
# =============================================================================
else

  # Point ChromaDB to in-process (PersistentClient) by clearing the host
  update_env "CHROMA_HOST" ""
  info "ChromaDB set to in-process mode (no container needed)"

  # ── Python virtual environment ──────────────────────────────────────────────
  header "Setting up Python environment..."

  VENV_DIR="$INSTALL_DIR/.venv"
  if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    success "Created virtual environment at .venv"
  else
    info "Virtual environment already exists"
  fi

  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip -q
  pip install -r backend/requirements.txt -q
  success "Python dependencies installed"

  # ── Frontend build ──────────────────────────────────────────────────────────
  header "Building frontend..."

  cd frontend
  npm install --silent
  VITE_API_URL="http://localhost:${BACKEND_PORT}" npm run build
  cd "$INSTALL_DIR"
  success "Frontend built"

  # ── Write start.sh ──────────────────────────────────────────────────────────
  header "Writing helper scripts..."

  cat > "$INSTALL_DIR/start.sh" <<'STARTSCRIPT'
#!/usr/bin/env bash
# Start Agent Harness (local mode)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
PID_FILE="$DIR/.pids"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
die()     { echo -e "${RED}[error]${RESET} $*" >&2; exit 1; }

[[ -f "$DIR/.venv/bin/activate" ]] || die "Virtual environment not found. Run deploy.sh first."
[[ -d "$DIR/frontend/dist" ]]      || die "Frontend not built. Run deploy.sh first."

# Kill any already-running processes
if [[ -f "$PID_FILE" ]]; then
  while IFS= read -r pid; do
    kill "$pid" 2>/dev/null || true
  done < "$PID_FILE"
  rm -f "$PID_FILE"
fi

source "$DIR/.venv/bin/activate"

info "Starting backend on port ${BACKEND_PORT}..."
cd "$DIR/backend"
uvicorn main:app --host 0.0.0.0 --port "$BACKEND_PORT" --log-level info &
BACKEND_PID=$!
echo "$BACKEND_PID" >> "$DIR/.pids"
success "Backend started (PID ${BACKEND_PID})"

info "Starting frontend on port ${FRONTEND_PORT}..."
python3 -m http.server "$FRONTEND_PORT" --directory "$DIR/frontend/dist" &>/dev/null &
FRONTEND_PID=$!
echo "$FRONTEND_PID" >> "$DIR/.pids"
success "Frontend started (PID ${FRONTEND_PID})"

echo ""
echo -e "${BOLD}${GREEN}Agent Harness is running!${RESET}"
echo -e "  Web UI   → http://localhost:${FRONTEND_PORT}"
echo -e "  API      → http://localhost:${BACKEND_PORT}"
echo -e "  API Docs → http://localhost:${BACKEND_PORT}/docs"
echo -e "  Stop     → $DIR/stop.sh"
echo ""
STARTSCRIPT
  chmod +x "$INSTALL_DIR/start.sh"
  success "Created start.sh"

  # ── Write stop.sh ───────────────────────────────────────────────────────────
  cat > "$INSTALL_DIR/stop.sh" <<'STOPSCRIPT'
#!/usr/bin/env bash
# Stop Agent Harness (local mode)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$DIR/.pids"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No running processes found (.pids file missing)."
  exit 0
fi

while IFS= read -r pid; do
  if kill "$pid" 2>/dev/null; then
    echo "Stopped PID $pid"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
echo "Agent Harness stopped."
STOPSCRIPT
  chmod +x "$INSTALL_DIR/stop.sh"
  success "Created stop.sh"

  # ── Start services ──────────────────────────────────────────────────────────
  header "Starting Agent Harness..."
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

  echo ""
  echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════╗${RESET}"
  echo -e "${BOLD}${GREEN}║   Agent Harness is running!  (Local)              ║${RESET}"
  echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════╝${RESET}"
  echo ""
  echo -e "  ${BOLD}Web UI${RESET}       →  http://localhost:${FRONTEND_PORT}"
  echo -e "  ${BOLD}API${RESET}          →  http://localhost:${BACKEND_PORT}"
  echo -e "  ${BOLD}API Docs${RESET}     →  http://localhost:${BACKEND_PORT}/docs"
  echo -e "  ${BOLD}Stop${RESET}         →  ${INSTALL_DIR}/stop.sh"
  echo -e "  ${BOLD}Restart${RESET}      →  ${INSTALL_DIR}/start.sh"
  echo ""
  echo -e "  ${YELLOW}Next step:${RESET} Open Settings in the UI and configure your LLM provider."
  echo ""

fi
