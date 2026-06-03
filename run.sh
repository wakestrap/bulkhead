#!/usr/bin/env bash
# Launch Atlantic Pressure web GUI
# Usage: ./run.sh [port]

PORT="${1:-5050}"
HOST="0.0.0.0"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🌊  Atlantic Pressure"
echo "    http://localhost:${PORT}"
echo "    Press Ctrl+C to stop"
echo ""

cd "$DIR" && python3 app.py --host "$HOST" --port "$PORT"
