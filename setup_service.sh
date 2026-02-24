#!/bin/bash
# Start connect-on-device web server with auto-restart
# Called from /data/continue.sh on every boot

DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill any leftover server processes
pkill -9 -f 'python.*server\.py' 2>/dev/null || true

# Start server with auto-restart loop (nohup survives exec)
nohup bash -c "
  cd $DIR
  while true; do
    /usr/local/venv/bin/python -u server.py >> /tmp/connect.log 2>&1
    sleep 3
  done
" &>/dev/null &
