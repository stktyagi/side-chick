#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

# Colors
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${BOLD}${GREEN}==>${NC} ${BOLD}$1${NC}"; }
warn()  { echo -e "${BOLD}${YELLOW}==>${NC} $1"; }
error() { echo -e "${BOLD}${RED}==>${NC} $1"; }

# --- Python check ---
info "Checking Python version..."
PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
    error "Python is not installed. Install Python >= 3.12 and re-run this script."
    exit 1
fi

PY_VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
    error "Python >= 3.12 required (found $PY_VER). Please upgrade and re-run."
    exit 1
fi
info "Found Python ${PY_VER}"

# --- uv check / install ---
if ! command -v uv &>/dev/null; then
    warn "uv not found — installing..."
    if command -v curl &>/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget &>/dev/null; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        error "Need curl or wget to install uv. Install one of them and re-run."
        exit 1
    fi
    # Source the updated PATH
    if [ -f "$HOME/.cargo/env" ]; then
        . "$HOME/.cargo/env"
    fi
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        error "uv installation succeeded but uv is not on PATH. Add ~/.local/bin or ~/.cargo/bin to PATH and re-run."
        exit 1
    fi
    info "uv installed successfully"
else
    info "uv is already installed ($(uv --version))"
fi

# --- Install the package ---
info "Installing fastcontext..."
uv tool install "$REPO_DIR"

# --- Create .env.example ---
if [ ! -f "$REPO_DIR/.env" ]; then
    if [ ! -f "$REPO_DIR/.env.example" ]; then
        cat > "$REPO_DIR/.env.example" << 'EOF'
BASE_URL=https://your-endpoint/v1
MODEL=your-model-name
API_KEY=your-api-key
MAX_TURNS=6
EOF
    fi
    echo ""
    warn "No .env file found. Copy .env.example to .env and fill in your credentials:"
    echo "  cp .env.example .env"
    echo "  # then edit .env with your endpoint details"
fi

echo ""
info "Installation complete! Run 'fastcontext --help' to get started."
echo ""
echo "Next steps:"
echo "  1. Edit .env with your model endpoint details."
echo "  2. Run fastcontext in any repository:"
echo "     fastcontext --query \"your exploration query\" --max-turns 6"
echo ""
echo "  3. To use FastContext as an MCP tool in opencode, save the"
echo "     following to ~/.config/opencode/opencode.json:"
echo ""
echo '{'
echo '  "$schema": "https://opencode.ai/config.json",'
echo '  "mcp": {'
echo '    "fastcontext": {'
echo '      "type": "local",'
echo '      "command": ["uv", "tool", "run", "fastcontext", "mcp"],'
echo '      "enabled": true'
echo '    }'
echo '  },'
echo '  "experimental": {'
echo '    "mcp_timeout": 600000'
echo '  }'
echo '}'
echo ""
echo "     Then set the env vars in the file, or let fastcontext load"
echo '     them from ~/.config/fastcontext/env.'
