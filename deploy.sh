#!/bin/bash
# Deploy connect_on_device to C3
# Usage: ./deploy.sh [host]
set -e

HOST="${1:-c3}"
REMOTE_DIR="/data/connect_on_device"

echo "Building frontend..."
cd "$(dirname "$0")/frontend"
npm run build

echo "Deploying to $HOST:$REMOTE_DIR..."
ssh "$HOST" "mkdir -p $REMOTE_DIR"

# Copy server + static (skip node_modules and frontend source)
scp "$(dirname "$0")/server.py" "$HOST:$REMOTE_DIR/"
scp -r "$(dirname "$0")/static" "$HOST:$REMOTE_DIR/"

echo ""
echo "Deployed. Start the server with:"
echo "  ssh $HOST \"/usr/local/venv/bin/python $REMOTE_DIR/server.py\""
echo ""
echo "Then open: http://$HOST:8082/"
