#!/usr/bin/env bash
# ChimeraChess — Linux/macOS launcher for chess GUIs (CuteChess, En Croissant, etc.)
# Register this script as the engine executable in your GUI.
# All arguments passed by the GUI are forwarded as-is.

set -euo pipefail

# Resolve the directory this script lives in, then find the engine relative to it
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$SCRIPT_DIR/../src/hybrid_engine.py"

# Check Python is available (prefer python3)
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "ERROR: python3 / python not found on PATH. Install Python 3.10+." >&2
    exit 1
fi

# Verify Python version is 3.10+
PY_VER=$("$PYTHON" -c "import sys; print(sys.version_info >= (3,10))")
if [ "$PY_VER" != "True" ]; then
    echo "ERROR: Python 3.10 or newer is required." >&2
    exit 1
fi

exec "$PYTHON" "$ENGINE" "$@"
