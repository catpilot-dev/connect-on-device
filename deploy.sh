#!/bin/bash
# Deploy connect_on_device to C3
# Usage: ./deploy.sh [host]
set -e

HOST="${1:-c3}"
REMOTE_DIR="/data/connect_on_device"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Building frontend..."
cd "$LOCAL_DIR/frontend"
npm run build

echo "Deploying to $HOST:$REMOTE_DIR..."
ssh "$HOST" "mkdir -p $REMOTE_DIR"

# Clean old layout (monolithic handlers.py replaced by handlers/ package)
ssh "$HOST" "rm -f $REMOTE_DIR/handlers.py; rm -rf $REMOTE_DIR/handlers $REMOTE_DIR/static"

# Copy all Python modules
scp "$LOCAL_DIR"/*.py "$HOST:$REMOTE_DIR/"

# Copy handlers package
scp -r "$LOCAL_DIR/handlers" "$HOST:$REMOTE_DIR/"

# Copy built frontend
scp -r "$LOCAL_DIR/static" "$HOST:$REMOTE_DIR/"

# Restart server — kill ALL existing server.py processes, then start fresh
echo "Restarting server..."
ssh "$HOST" "pkill -9 -f 'python.*server.py'" 2>/dev/null || true
sleep 1
# Use -f to force pseudo-tty allocation off; redirect all fds so ssh doesn't hang
ssh -f "$HOST" "cd $REMOTE_DIR && nohup /usr/local/venv/bin/python -u server.py > /tmp/connect.log 2>&1 &"

sleep 2
echo ""
echo "Deployed and running at http://$HOST:8082/"
