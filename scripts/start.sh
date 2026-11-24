#!/usr/bin/env bash
# Start ai-clip-creator without Docker (macOS and Linux).
#   ./scripts/start.sh
# Needs Python 3.11+, ffmpeg, Node.js and pnpm. Creates .venv the first time, installs everything,
# starts the API on http://127.0.0.1:8000 and the dashboard on http://127.0.0.1:3000, and opens the browser.
# Stop both with Ctrl+C.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
    echo "Error: $1" >&2
    exit 1
}

# ---------- tools ----------
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || fail "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/"
command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg was not found. Install it: brew install ffmpeg (macOS) or sudo apt install ffmpeg (Linux)."
command -v ffprobe >/dev/null 2>&1 || fail "ffprobe was not found. It comes with ffmpeg: install ffmpeg again."
command -v node >/dev/null 2>&1 || fail "Node.js was not found. Install Node.js 20 or newer from https://nodejs.org/"
command -v pnpm >/dev/null 2>&1 || fail "pnpm was not found. Install it: corepack enable pnpm (or npm install -g pnpm)."

# ---------- Python ----------
if [ ! -x .venv/bin/python ]; then
    echo "Creating .venv ..."
    if command -v uv >/dev/null 2>&1; then
        uv venv --python "$PYTHON" .venv || fail "Could not create .venv with uv."
    else
        "$PYTHON" -m venv .venv || fail "Could not create .venv. On Debian/Ubuntu: sudo apt install python3-venv"
    fi
fi

echo "Installing the Python packages (the first time takes a few minutes) ..."
if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -q -e ".[server,vision]" -r requirements-campaign.txt ||
        fail "Could not install the Python packages. Read the message above."
else
    .venv/bin/python -m pip install -q -e ".[server,vision]" -r requirements-campaign.txt ||
        fail "Could not install the Python packages. Read the message above."
fi

# ---------- dashboard ----------
echo "Installing the dashboard packages ..."
(cd web && pnpm install --frozen-lockfile) || fail "pnpm install failed in web/. Read the message above."

mkdir -p work out klippdata data

# ---------- start ----------
PIDS=()
stop() {
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap stop EXIT INT TERM

echo "Starting the API on http://127.0.0.1:8000 ..."
KLIPP_ROOT="$ROOT" .venv/bin/clipsmith serve --host 127.0.0.1 --port 8000 &
PIDS+=("$!")

echo "Starting the dashboard on http://127.0.0.1:3000 ..."
(cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 pnpm dev --port 3000) &
PIDS+=("$!")

URL="http://localhost:3000"
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "$URL" 2>/dev/null; then
        break
    fi
    sleep 1
done

if command -v open >/dev/null 2>&1; then
    open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
fi
echo
echo "ai-clip-creator is running: $URL"
echo "Add your keys on the Settings page. Press Ctrl+C to stop."
wait
