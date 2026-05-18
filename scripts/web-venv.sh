#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_tty() { [[ -t 2 ]]; }

log_info() {
  if _tty; then
    printf '\033[0;32m[INFO]\033[0m  %s\n' "$*" >&2
  else
    printf '[INFO]  %s\n' "$*" >&2
  fi
}

log_warn() {
  if _tty; then
    printf '\033[0;33m[WARN]\033[0m  %s\n' "$*" >&2
  else
    printf '[WARN]  %s\n' "$*" >&2
  fi
}

log_error() {
  if _tty; then
    printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2
  else
    printf '[ERROR] %s\n' "$*" >&2
  fi
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") <subcommand>

Manage the web/frontend Node environment (Python venv style).

Subcommands:
  setup     Enable Corepack, verify Node version, run npm ci  (alias: install)
  check     Verify setup status: node_modules, Node version, vite.config
  build     Run npm run build — outputs to web/static/
  dev       Start Vite dev server in foreground (Ctrl-C to stop)
  reset     Remove node_modules/.vite/dist, then re-run setup
  clean     Remove node_modules/.vite/dist/web/static (no reinstall)

Examples:
  $(basename "$0") setup
  $(basename "$0") check
  $(basename "$0") build
  $(basename "$0") dev
  $(basename "$0") reset
  $(basename "$0") clean
EOF
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
FRONTEND_DIR="$REPO_ROOT/web/frontend"
NVMRC_FILE="$FRONTEND_DIR/.nvmrc"

_check_nvmrc() {
  if [[ ! -f "$NVMRC_FILE" ]]; then
    log_error "web/frontend/.nvmrc not found — cannot verify Node version."
    log_error "Create it first:  echo '20.18.0' > web/frontend/.nvmrc"
    exit 1
  fi
}

_check_node_version() {
  local required_major
  required_major="$(sed 's/[^0-9]//g' "$NVMRC_FILE" | cut -c1-2)"
  # handle lts/* aliases like "lts/iron" → extract number if possible
  if [[ -z "$required_major" ]]; then
    log_warn "Could not parse a numeric Node major from .nvmrc ('$(cat "$NVMRC_FILE")')."
    log_warn "Skipping Node version check."
    return
  fi

  if ! command -v node >/dev/null 2>&1; then
    log_warn "node not found in PATH — skipping version check."
    return
  fi

  local current_version current_major
  current_version="$(node --version)"          # e.g. v20.18.0
  current_major="${current_version#v}"          # strip leading 'v'
  current_major="${current_major%%.*}"          # keep major only

  if [[ "$current_major" != "$required_major" ]]; then
    log_warn "Node version mismatch: active=$current_version, .nvmrc requires major $required_major."
    log_warn "Switch with one of:"
    log_warn "  nvm:   nvm use"
    log_warn "  fnm:   fnm use"
    log_warn "  volta: volta pin node@$required_major"
  else
    log_info "Node version OK: $current_version (major $current_major matches .nvmrc)"
  fi
}

_corepack_enable() {
  if command -v corepack >/dev/null 2>&1; then
    log_info "Running: corepack enable"
    corepack enable || log_warn "corepack enable failed — continuing without it."
  else
    log_warn "corepack not found — skipping. Install Node 16.9+ to get Corepack."
  fi
}

_npm_install() {
  [[ -n "$REPO_ROOT" ]] || { log_error "REPO_ROOT is empty — aborting."; exit 1; }

  cd "$FRONTEND_DIR"

  if [[ -f "package-lock.json" ]]; then
    log_info "Running: npm ci"
    npm ci
  else
    log_warn "package-lock.json not found — falling back to npm install (results may differ)."
    npm install
  fi

  cd "$REPO_ROOT"
}

_remove_artifacts() {
  [[ -n "$REPO_ROOT" ]] || { log_error "REPO_ROOT is empty — aborting."; exit 1; }

  local targets=(
    "$REPO_ROOT/web/frontend/node_modules"
    "$REPO_ROOT/web/frontend/.vite"
    "$REPO_ROOT/web/frontend/dist"
  )

  for t in "${targets[@]}"; do
    if [[ -e "$t" ]]; then
      log_info "Removing: $t"
      rm -rf "$t"
    fi
  done
}

_require_setup() {
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    log_error "node_modules not found — run '$(basename "$0") setup' first."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
cmd_setup() {
  log_info "=== web-venv setup ==="
  _check_nvmrc
  _check_node_version
  _corepack_enable
  _npm_install
  log_info "Setup complete."
}

cmd_check() {
  log_info "=== web-venv check ==="
  local all_ok=true

  if [[ -d "$FRONTEND_DIR/node_modules" ]]; then
    log_info "node_modules: present"
  else
    log_error "node_modules: missing — run '$(basename "$0") setup'"
    all_ok=false
  fi

  if [[ -f "$NVMRC_FILE" ]]; then
    log_info ".nvmrc: found"
    _check_node_version
  else
    log_warn ".nvmrc: not found in web/frontend/"
  fi

  if [[ -f "$FRONTEND_DIR/vite.config.ts" || -f "$FRONTEND_DIR/vite.config.js" ]]; then
    log_info "vite.config: found"
  else
    log_warn "vite.config.ts/js: not found in web/frontend/"
  fi

  if [[ "$all_ok" == "false" ]]; then
    exit 1
  fi

  log_info "Check complete — environment looks healthy."
}

cmd_build() {
  log_info "=== web-venv build ==="
  _require_setup
  log_info "Running: npm run build  (output → web/static/)"
  cd "$FRONTEND_DIR"
  npm run build
  cd "$REPO_ROOT"
  log_info "Build complete."
}

cmd_dev() {
  log_info "=== web-venv dev ==="
  _require_setup
  log_info "Starting Vite dev server — press Ctrl-C to stop."
  cd "$FRONTEND_DIR"
  npm run dev
}

cmd_reset() {
  log_info "=== web-venv reset ==="
  [[ -n "$REPO_ROOT" ]] || { log_error "REPO_ROOT is empty — aborting."; exit 1; }
  _remove_artifacts
  log_info "Artifacts removed. Re-running setup..."
  cmd_setup
}

cmd_clean() {
  log_info "=== web-venv clean ==="
  [[ -n "$REPO_ROOT" ]] || { log_error "REPO_ROOT is empty — aborting."; exit 1; }
  _remove_artifacts

  local web_static="$REPO_ROOT/web/static"
  if [[ -e "$web_static" ]]; then
    log_info "Removing: $web_static"
    rm -rf "$web_static"
  fi

  log_info "Clean complete (no reinstall)."
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
cd "$REPO_ROOT"

SUBCOMMAND="${1:-}"

case "$SUBCOMMAND" in
  setup|install)
    cmd_setup
    ;;
  check)
    cmd_check
    ;;
  build)
    cmd_build
    ;;
  dev)
    cmd_dev
    ;;
  reset)
    cmd_reset
    ;;
  clean)
    cmd_clean
    ;;
  --help|-h|help)
    usage
    exit 0
    ;;
  "")
    log_error "No subcommand given."
    usage
    exit 2
    ;;
  *)
    log_error "Unknown subcommand: $SUBCOMMAND"
    usage
    exit 2
    ;;
esac