#!/usr/bin/env bash
# Start the SD-CPP Video GUI web server.
# Usage: ./webui/run.sh [port]
set -e
cd "$(dirname "$0")"

PORT="${1:-8080}"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    uv venv .venv
    uv pip install -r requirements.txt
fi

exec .venv/bin/python -c "
import uvicorn
from server import app
uvicorn.run(app, host='0.0.0.0', port=$PORT)
"
