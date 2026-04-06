#!/usr/bin/env bash
# ============================================================
# Pantheon Demo Setup
# Installs Ollama, pulls NVIDIA Nemotron-3-Nano-4B, and
# configures Pantheon to use it as the default LLM.
#
# The HuggingFace FP8 model (nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8)
# uses safetensors format which Ollama cannot load directly for
# Mamba-2 hybrid architectures. This script pulls the official
# Ollama GGUF build (Q4_K_M, 2.8 GB) which is derived from the
# same base model. For higher fidelity, use the q8_0 (4.2 GB) or
# bf16 (8.0 GB) tags — see "Advanced options" below.
#
# Usage:
#   chmod +x demo_setup.sh
#   ./demo_setup.sh              # defaults: nemotron-3-nano:4b
#   ./demo_setup.sh --tag 4b-q8_0   # 8-bit quantisation
#   ./demo_setup.sh --tag 4b-bf16   # full bf16 precision
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
MODEL_TAG="4b"
EMBEDDING_MODEL="nomic-embed-text"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)       MODEL_TAG="$2"; shift 2 ;;
    --embedding) EMBEDDING_MODEL="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: $0 [--tag TAG] [--embedding MODEL]"
      echo ""
      echo "Options:"
      echo "  --tag TAG         Ollama model tag (default: 4b)"
      echo "                    Options: 4b, 4b-q8_0, 4b-bf16"
      echo "  --embedding MODEL Ollama embedding model (default: nomic-embed-text)"
      echo ""
      echo "Examples:"
      echo "  $0                        # Q4 quantised, 2.8 GB"
      echo "  $0 --tag 4b-q8_0          # Q8 quantised, 4.2 GB"
      echo "  $0 --tag 4b-bf16          # Full precision, 8.0 GB"
      exit 0
      ;;
    *) die "Unknown option: $1. Use --help for usage." ;;
  esac
done

OLLAMA_MODEL="nemotron-3-nano:${MODEL_TAG}"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║       Pantheon Demo Setup                    ║${RESET}"
echo -e "${BOLD}║       Model: ${CYAN}${OLLAMA_MODEL}${RESET}${BOLD}$(printf '%*s' $((18 - ${#OLLAMA_MODEL})) '')║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${RESET}"
echo ""

# ── Step 1: Install Ollama ─────────────────────────────────────
info "Step 1/5 — Checking Ollama installation..."

if command -v ollama &>/dev/null; then
  OLLAMA_VERSION=$(ollama --version 2>/dev/null || echo "unknown")
  success "Ollama is already installed (${OLLAMA_VERSION})"
else
  info "Installing Ollama..."
  if [[ "$(uname)" == "Darwin" ]]; then
    # macOS — check for Homebrew first
    if command -v brew &>/dev/null; then
      brew install ollama
    else
      warn "Homebrew not found. Installing via the official install script..."
      curl -fsSL https://ollama.com/install.sh | sh
    fi
  elif [[ "$(uname)" == "Linux" ]]; then
    curl -fsSL https://ollama.com/install.sh | sh
  else
    die "Unsupported OS: $(uname). Install Ollama manually from https://ollama.com"
  fi
  success "Ollama installed"
fi

# ── Step 2: Ensure Ollama is running ──────────────────────────
info "Step 2/5 — Ensuring Ollama server is running..."

_ollama_ready() {
  curl -sf http://localhost:11434/api/tags >/dev/null 2>&1
}

if _ollama_ready; then
  success "Ollama server is already running"
else
  info "Starting Ollama server in the background..."
  if [[ "$(uname)" == "Darwin" ]]; then
    # macOS: open the app if installed, otherwise `ollama serve`
    if [[ -d "/Applications/Ollama.app" ]]; then
      open -a Ollama
    else
      ollama serve &>/dev/null &
    fi
  else
    ollama serve &>/dev/null &
  fi

  # Wait for the server to become ready (up to 30 seconds)
  for i in $(seq 1 30); do
    if _ollama_ready; then break; fi
    sleep 1
  done

  if _ollama_ready; then
    success "Ollama server started"
  else
    die "Ollama server failed to start within 30 seconds. Check 'ollama serve' manually."
  fi
fi

# ── Step 3: Pull the LLM model ────────────────────────────────
info "Step 3/5 — Pulling ${OLLAMA_MODEL}..."

# Check if already pulled
if ollama list 2>/dev/null | grep -q "nemotron-3-nano.*${MODEL_TAG}"; then
  success "${OLLAMA_MODEL} is already downloaded"
else
  ollama pull "${OLLAMA_MODEL}"
  success "${OLLAMA_MODEL} downloaded"
fi

# ── Step 4: Pull the embedding model ──────────────────────────
info "Step 4/5 — Pulling embedding model (${EMBEDDING_MODEL})..."

if ollama list 2>/dev/null | grep -q "${EMBEDDING_MODEL}"; then
  success "${EMBEDDING_MODEL} is already downloaded"
else
  ollama pull "${EMBEDDING_MODEL}"
  success "${EMBEDDING_MODEL} downloaded"
fi

# ── Step 5: Configure Pantheon .env ───────────────────────────
info "Step 5/5 — Configuring Pantheon to use Ollama..."

ENV_FILE="${DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ -f "${DIR}/.env.example" ]]; then
    cp "${DIR}/.env.example" "${ENV_FILE}"
    info "Created .env from .env.example"
  else
    die ".env.example not found — cannot create config"
  fi
fi

# Back up existing .env
cp "${ENV_FILE}" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
info "Backed up existing .env"

# Helper: set a key in the .env file (update if exists, append if not)
_env_set() {
  local key="$1" value="$2" file="${ENV_FILE}"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    # Use a different delimiter for sed in case the value contains /
    sed -i.tmp "s|^${key}=.*|${key}=${value}|" "$file" && rm -f "${file}.tmp"
  elif grep -q "^# *${key}=" "$file" 2>/dev/null; then
    # Uncomment and set
    sed -i.tmp "s|^# *${key}=.*|${key}=${value}|" "$file" && rm -f "${file}.tmp"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

_env_set "LLM_BASE_URL"    "http://localhost:11434/v1"
_env_set "LLM_API_KEY"     "ollama"
_env_set "LLM_MODEL"       "${OLLAMA_MODEL}"

# Embedding — use Ollama's embedding endpoint
_env_set "EMBEDDING_BASE_URL" "http://localhost:11434/v1"
_env_set "EMBEDDING_API_KEY"  "ollama"
_env_set "EMBEDDING_MODEL"    "${EMBEDDING_MODEL}"

# Use the same model for prefill tasks (summarisation, memory consolidation)
_env_set "LLM_PREFILL_MODEL" "${OLLAMA_MODEL}"

# Security keys — generate if still placeholder
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

success "Pantheon configured for Ollama"

# ── Summary ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}Demo setup complete!${RESET}"
echo ""
echo -e "  LLM Model     → ${CYAN}${OLLAMA_MODEL}${RESET}"
echo -e "  Embeddings    → ${CYAN}${EMBEDDING_MODEL}${RESET}"
echo -e "  Ollama API    → http://localhost:11434"
echo -e "  Pantheon .env → ${ENV_FILE}"
echo ""
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "    1. Start Pantheon:  ${CYAN}./start.sh${RESET}"
echo -e "    2. Open the UI:     ${CYAN}http://localhost:8000${RESET}"
echo ""
echo -e "  ${BOLD}Model info:${RESET}"
echo -e "    NVIDIA Nemotron-3-Nano-4B is a Mamba-2 hybrid model"
echo -e "    (4 attention + Mamba-2/MLP layers) with 262K context."
echo -e "    The Ollama build uses Q4_K_M GGUF quantisation (2.8 GB)."
echo -e "    For the original FP8 weights, see:"
echo -e "    ${CYAN}https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8${RESET}"
echo ""
echo -e "  ${BOLD}Alternative tags:${RESET}"
echo -e "    ./demo_setup.sh --tag 4b-q8_0   # 8-bit (4.2 GB)"
echo -e "    ./demo_setup.sh --tag 4b-bf16   # full precision (8.0 GB)"
echo ""
