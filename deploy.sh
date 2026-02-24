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

# Copy all Python modules and setup script
scp "$LOCAL_DIR"/*.py "$LOCAL_DIR"/setup_service.sh "$HOST:$REMOTE_DIR/"
ssh "$HOST" "chmod +x $REMOTE_DIR/setup_service.sh"

# Copy handlers package
scp -r "$LOCAL_DIR/handlers" "$HOST:$REMOTE_DIR/"

# Copy built frontend
scp -r "$LOCAL_DIR/static" "$HOST:$REMOTE_DIR/"

# Restart via transient systemd user service
echo "Restarting server..."
ssh "$HOST" "systemctl --user stop connect-on-device 2>/dev/null; systemctl --user reset-failed connect-on-device 2>/dev/null; pkill -9 -f 'python.*server.py' 2>/dev/null; sleep 1; systemd-run --user --unit=connect-on-device --description='Connect on Device' --working-directory=$REMOTE_DIR --property=Restart=always --property=RestartSec=3 /usr/local/venv/bin/python -u server.py"

sleep 2
ssh "$HOST" "systemctl --user status connect-on-device --no-pager" || true
echo ""
echo "Deployed and running at http://$HOST:8082/"
