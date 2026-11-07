#!/usr/bin/env bash
# ==============================================================================
# Automated Local Synchronization Script for Economic Data Monitoring System (macOS / Linux)
#
# Usage:
#   ./scripts/sync_local.sh
#   ./scripts/sync_local.sh --no-pull
#   ./scripts/sync_local.sh --dry-run-notify
# ==============================================================================

set -euo pipefail

# Ensure script runs from project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Ensure log directory exists
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/sync_local.log"

log() {
    local msg="[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

NO_PULL=false
EXTRA_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --no-pull)
            NO_PULL=true
            ;;
        *)
            EXTRA_ARGS+=("$arg")
            ;;
    esac
done

log "=========================================================="
log "Starting Local Synchronization Pipeline"
log "Working Directory: $PROJECT_ROOT"

# 1. Pull latest commits if not disabled
if [ "$NO_PULL" = false ]; then
    log "Running 'git pull --ff-only' to pull remote Parquet snapshots..."
    if ! git pull --ff-only 2>&1 | while read -r line; do log "  $line"; done; then
        log "ERROR: Git pull failed."
        exit 1
    fi
else
    log "Skipping git pull (--no-pull flag active)."
fi

# 2. Execute sync command
CMD=("python" "-m" "econ_monitor.cli" "sync" "${EXTRA_ARGS[@]}")
log "Executing: ${CMD[*]}"

if "${CMD[@]}" 2>&1 | while IFS= read -r line; do log "  $line"; done; then
    log "Local Synchronization Pipeline completed successfully."
    exit 0
else
    log "ERROR: Pipeline failed."
    exit 1
fi
