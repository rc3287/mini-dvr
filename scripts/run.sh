#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  Mini-DVR — Run script
# ═══════════════════════════════════════════════════════════════════
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

VENV="$ROOT_DIR/.venv/bin/activate"

if [ -f "$VENV" ]; then
  source "$VENV"
else
  echo "Virtual environment not found. Run ./scripts/install.sh first."
  exit 1
fi

cd "$ROOT_DIR"
echo "Starting Mini-DVR at http://localhost:8080 ..."
uvicorn backend.server:app \
  --host 0.0.0.0 \
  --port 8080 \
  --reload \
  --log-level info
