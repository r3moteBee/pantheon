#!/usr/bin/env bash
# ============================================================
# Pantheon Demo Setup
# Sets up local services and configures Pantheon to use them.
#
# Components (enable independently or together):
#   --with-ollama    Install Ollama + pull NVIDIA Nemotron-3-Nano-4B
#                    and configure Pantheon to use it as the default LLM
#   --with-searxng   Run a local SearXNG container (no API key, no
#                    rate limits) and configure Pantheon to use it
#                    as the search backend
#
# When run with no flags, both --with-ollama and --with-searxng
# are enabled (the standard demo experience).
#
# About the model:
#   The HuggingFace FP8 model (nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8)
#   uses safetensors format which Ollama cannot load directly for
#   Mamba-2 hybrid architectures. This script pulls the official
#   Ollama GGUF build (Q4_K_M, 2.8 GB) which is derived from the
#   same base model.
#
# Usage:
#   chmod +x demo_setup.sh
#   ./demo_setup.sh                       # both ollama + searxng (default)
#   ./demo_setup.sh --with-ollama         # ollama only
#   ./demo_setup.sh --with-searxng        # searxng only
#   ./demo_setup.sh --with-ollama --with-searxng   # explicit both
#   ./demo_setup.sh --with-ollama --tag 4b-q8_0    # higher precision
# ============================================================
set -euo pipefail

# ── Colours & helpers ──────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
die()     { echo -e "${RED}[error]${RESET} $*" >&2; exit 1; }

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse arguments ────────────────────────────────────────────
WITH_OLLAMA=false
WITH_SEARXNG=false
WITH_BROWSER=false
MODEL_TAG="4b"
EMBEDDING_MODEL="nomic-embed-text"
SEARXNG_PORT="8888"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-ollama)  WITH_OLLAMA=true;  shift ;;
    --with-searxng) WITH_SEARXNG=true; shift ;;
    --with-browser) WITH_BROWSER=true; shift ;;
    --tag)          MODEL_TAG="$2";    shift 2 ;;
    --embedding)    EMBEDDING_MODEL="$2"; shift 2 ;;
    --searxng-port) SEARXNG_PORT="$2"; shift 2 ;;
    --help|-h)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "Unknown option: $1. Use --help for usage." ;;
  esac
done

# Default: enable ollama+searxng if no component flag was specified
if [[ "$WITH_OLLAMA" == "false" && "$WITH_SEARXNG" == "false" && "$WITH_BROWSER" == "false" ]]; then
  WITH_OLLAMA=true
  WITH_SEARXNG=true
fi

OLLAMA_MODEL="nemotron-3-nano:${MODEL_TAG}"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║       Pantheon Demo Setup                    ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Components to install:"
[[ "$WITH_OLLAMA" == "true" ]]  && echo -e "    ${GREEN}✓${RESET} Ollama + ${OLLAMA_MODEL}"
[[ "$WITH_SEARXNG" == "true" ]] && echo -e "    ${GREEN}✓${RESET} SearXNG (port ${SEARXNG_PORT})"
[[ "$WITH_BROWSER" == "true" ]] && echo -e "    ${GREEN}✓${RESET} Playwright browser (headless chromium)"
echo ""

# ── Helper: edit .env in place ─────────────────────────────────
ENV_FILE="${DIR}/.env"

_ensure_env_file() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${DIR}/.env.example" ]]; then
      cp "${DIR}/.env.example" "${ENV_FILE}"
      info "Created .env from .env.example"
    else
      die ".env.example not found — cannot create config"
    fi
  fi
}

_env_set() {
  local key="$1" value="$2" file="${ENV_FILE}"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i.tmp "s|^${key}=.*|${key}=${value}|" "$file" && rm -f "${file}.tmp"
  elif grep -q "^# *${key}=" "$file" 2>/dev/null; then
    sed -i.tmp "s|^# *${key}=.*|${key}=${value}|" "$file" && rm -f "${file}.tmp"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

_ensure_env_file
cp "${ENV_FILE}" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
info "Backed up existing .env"

# ============================================================
# OLLAMA SETUP
# ============================================================
if [[ "$WITH_OLLAMA" == "true" ]]; then

  echo ""
  echo -e "${BOLD}── Ollama Setup ──${RESET}"

  # Step 1: Install Ollama
  info "Checking Ollama installation..."
  if command -v ollama &>/dev/null; then
    success "Ollama is already installed ($(ollama --version 2>/dev/null || echo unknown))"
  else
    info "Installing Ollama..."
    if [[ "$(uname)" == "Darwin" ]]; then
      if command -v brew &>/dev/null; then
        brew install ollama
      else
        curl -fsSL https://ollama.com/install.sh | sh
      fi
    elif [[ "$(uname)" == "Linux" ]]; then
      curl -fsSL https://ollama.com/install.sh | sh
    else
      die "Unsupported OS: $(uname). Install Ollama manually from https://ollama.com"
    fi
    success "Ollama installed"
  fi

  # Step 2: Ensure server is running
  _ollama_ready() { curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; }

  if _ollama_ready; then
    success "Ollama server is already running"
  else
    info "Starting Ollama server..."
    if [[ "$(uname)" == "Darwin" && -d "/Applications/Ollama.app" ]]; then
      open -a Ollama
    else
      ollama serve &>/dev/null &
    fi
    for i in $(seq 1 30); do
      _ollama_ready && break
      sleep 1
    done
    _ollama_ready || die "Ollama server failed to start within 30 seconds"
    success "Ollama server started"
  fi

  # Step 3: Pull LLM
  info "Pulling ${OLLAMA_MODEL}..."
  if ollama list 2>/dev/null | grep -q "nemotron-3-nano.*${MODEL_TAG}"; then
    success "${OLLAMA_MODEL} is already downloaded"
  else
    ollama pull "${OLLAMA_MODEL}"
    success "${OLLAMA_MODEL} downloaded"
  fi

  # Step 4: Pull embedding model
  info "Pulling embedding model (${EMBEDDING_MODEL})..."
  if ollama list 2>/dev/null | grep -q "${EMBEDDING_MODEL}"; then
    success "${EMBEDDING_MODEL} is already downloaded"
  else
    ollama pull "${EMBEDDING_MODEL}"
    success "${EMBEDDING_MODEL} downloaded"
  fi

  # Step 5: Configure .env
  info "Configuring Pantheon to use Ollama..."
  _env_set "LLM_BASE_URL"       "http://localhost:11434/v1"
  _env_set "LLM_API_KEY"        "ollama"
  _env_set "LLM_MODEL"          "${OLLAMA_MODEL}"
  _env_set "LLM_PREFILL_MODEL"  "${OLLAMA_MODEL}"
  _env_set "EMBEDDING_BASE_URL" "http://localhost:11434/v1"
  _env_set "EMBEDDING_API_KEY"  "ollama"
  _env_set "EMBEDDING_MODEL"    "${EMBEDDING_MODEL}"
  success "Pantheon configured for Ollama"
fi

# ============================================================
# SEARXNG SETUP
# ============================================================
if [[ "$WITH_SEARXNG" == "true" ]]; then

  echo ""
  echo -e "${BOLD}── SearXNG Setup ──${RESET}"

  # Step 1: Ensure Docker is available
  info "Checking Docker..."
  if ! command -v docker &>/dev/null; then
    die "Docker is required for SearXNG. Install Docker Desktop (macOS) or 'curl -fsSL https://get.docker.com | sh' (Linux), then rerun."
  fi
  if ! docker info &>/dev/null; then
    die "Docker is installed but not running. Start Docker and rerun this script."
  fi
  success "Docker is available"

  # Step 2: Create SearXNG config directory with JSON output enabled
  SEARXNG_DIR="${DIR}/data/searxng"
  mkdir -p "${SEARXNG_DIR}"

  if [[ ! -f "${SEARXNG_DIR}/settings.yml" ]]; then
    info "Writing SearXNG settings.yml (JSON format enabled)..."
    cat > "${SEARXNG_DIR}/settings.yml" <<EOF
# Pantheon SearXNG settings — generated by demo_setup.sh
use_default_settings: true
server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')"
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
  else
    success "SearXNG config already exists"
  fi

  # Step 3: Start SearXNG container
  CONTAINER_NAME="pantheon-searxng"

  if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    info "SearXNG container already exists — restarting..."
    docker start "${CONTAINER_NAME}" >/dev/null
  else
    info "Starting SearXNG container on port ${SEARXNG_PORT}..."
    docker run -d \
      --name "${CONTAINER_NAME}" \
      --restart unless-stopped \
      -p "${SEARXNG_PORT}:8080" \
      -v "${SEARXNG_DIR}:/etc/searxng:rw" \
      -e BASE_URL="http://localhost:${SEARXNG_PORT}/" \
      -e INSTANCE_NAME="pantheon-searxng" \
      docker.io/searxng/searxng:latest >/dev/null
  fi

  # Wait for SearXNG to be ready
  info "Waiting for SearXNG to become ready..."
  _searxng_ready() {
    curl -sf "http://localhost:${SEARXNG_PORT}/search?q=test&format=json" >/dev/null 2>&1
  }
  for i in $(seq 1 30); do
    _searxng_ready && break
    sleep 1
  done
  if _searxng_ready; then
    success "SearXNG is ready at http://localhost:${SEARXNG_PORT}"
  else
    warn "SearXNG container started but didn't respond within 30s — check 'docker logs ${CONTAINER_NAME}'"
  fi

  # Step 4: Configure .env
  info "Configuring Pantheon to use SearXNG..."
  _env_set "SEARCH_URL" "http://localhost:${SEARXNG_PORT}"
  _env_set "SEARCH_API_KEY" ""
  success "Pantheon configured for SearXNG"
fi

# ============================================================
# BROWSER SETUP (Playwright)
# ============================================================
if [[ "$WITH_BROWSER" == "true" ]]; then

  echo ""
  echo -e "${BOLD}── Browser Setup ──${RESET}"

  PY_BIN=""
  if [[ -x "${DIR}/.venv/bin/python" ]]; then
    PY_BIN="${DIR}/.venv/bin/python"
    info "Using venv python: ${PY_BIN}"
  elif command -v python3 &>/dev/null; then
    PY_BIN="$(command -v python3)"
    info "Using system python3 (no venv found): ${PY_BIN}"
  else
    die "python3 not found — cannot install Playwright"
  fi

  info "Installing playwright package..."
  "${PY_BIN}" -m pip install --quiet playwright || die "pip install playwright failed"

  info "Installing chromium browser (this may take a minute)..."
  "${PY_BIN}" -m playwright install chromium || die "playwright install chromium failed"

  # On Linux, install system deps for chromium if we have sudo
  if [[ "$(uname)" == "Linux" ]] && command -v sudo &>/dev/null; then
    info "Installing chromium system dependencies (sudo)..."
    sudo "${PY_BIN}" -m playwright install-deps chromium || warn "install-deps failed — headless chromium may still work"
  fi

  info "Enabling browser tools in .env..."
  _env_set "BROWSER_ENABLED" "true"
  _env_set "BROWSER_HEADLESS" "true"
  success "Browser tools enabled. Agent now has browser_open/read/click/type/screenshot."
fi

# ============================================================
# SECURITY KEYS — generate if still placeholder
# ============================================================
if grep -q "change-this" "${ENV_FILE}" 2>/dev/null; then
  info "Generating fresh security keys..."
  if command -v python3 &>/dev/null; then
    VAULT_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    _env_set "VAULT_MASTER_KEY" "${VAULT_KEY}"
    _env_set "SECRET_KEY"       "${SECRET_KEY}"
    success "Security keys generated"
  else
    warn "python3 not found — update VAULT_MASTER_KEY and SECRET_KEY in .env manually"
  fi
fi

# ============================================================
# SUMMARY
# ============================================================
echo ""
echo -e "${BOLD}${GREEN}Demo setup complete!${RESET}"
echo ""

if [[ "$WITH_OLLAMA" == "true" ]]; then
  echo -e "  ${BOLD}LLM:${RESET}"
  echo -e "    Model       → ${CYAN}${OLLAMA_MODEL}${RESET}"
  echo -e "    Embeddings  → ${CYAN}${EMBEDDING_MODEL}${RESET}"
  echo -e "    Endpoint    → http://localhost:11434"
  echo ""
fi

if [[ "$WITH_SEARXNG" == "true" ]]; then
  echo -e "  ${BOLD}Search:${RESET}"
  echo -e "    Backend     → ${CYAN}SearXNG${RESET} (self-hosted, no key, no rate limits)"
  echo -e "    Endpoint    → http://localhost:${SEARXNG_PORT}"
  echo -e "    Container   → docker logs pantheon-searxng"
  echo ""
fi

echo -e "  ${BOLD}Config:${RESET}      ${ENV_FILE}"
echo ""
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "    1. Start Pantheon:  ${CYAN}./start.sh${RESET}"
echo -e "    2. Open the UI:     ${CYAN}http://localhost:8000${RESET}"
echo ""

if [[ "$WITH_OLLAMA" == "true" ]]; then
  echo -e "  ${BOLD}Model info:${RESET}"
  echo -e "    NVIDIA Nemotron-3-Nano-4B is a Mamba-2 hybrid model"
  echo -e "    (4 attention + Mamba-2/MLP layers) with 262K context."
  echo -e "    The Ollama build uses Q4_K_M GGUF quantisation (2.8 GB)."
  echo -e "    Original FP8 weights:"
  echo -e "    ${CYAN}https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8${RESET}"
  echo ""
  echo -e "  ${BOLD}Alternative Ollama tags:${RESET}"
  echo -e "    ./demo_setup.sh --with-ollama --tag 4b-q8_0   # 8-bit (4.2 GB)"
  echo -e "    ./demo_setup.sh --with-ollama --tag 4b-bf16   # full precision (8.0 GB)"
  echo ""
fi
