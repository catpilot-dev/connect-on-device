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

# Copy all Python modules, service file, and setup script
scp "$LOCAL_DIR"/*.py "$LOCAL_DIR"/*.service "$LOCAL_DIR"/setup_service.sh "$HOST:$REMOTE_DIR/"
ssh "$HOST" "chmod +x $REMOTE_DIR/setup_service.sh"

# Copy handlers package
scp -r "$LOCAL_DIR/handlers" "$HOST:$REMOTE_DIR/"

# Copy built frontend
scp -r "$LOCAL_DIR/static" "$HOST:$REMOTE_DIR/"

# Set up and restart via systemd user service
echo "Restarting server..."
ssh "$HOST" "$REMOTE_DIR/setup_service.sh"

sleep 2
ssh "$HOST" "systemctl --user status connect-on-device --no-pager" || true
echo ""
echo "Deployed and running at http://$HOST:8082/"
