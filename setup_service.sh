#!/bin/bash
# Start connect-on-device as a systemd user service
# Called from /data/continue.sh on every boot
# Uses systemd-run to create a transient service — no symlink into /home needed

DIR="$(cd "$(dirname "$0")" && pwd)"

(
  # Wait for openpilot manager — guarantees systemd user instance is ready
  while ! pgrep -f 'manager.py' &>/dev/null; do
    sleep 2
  done

  # Kill any leftover server processes
  pkill -9 -f 'python.*server\.py' 2>/dev/null || true
  sleep 1

  systemd-run --user \
    --unit=connect-on-device \
    --description="Connect on Device" \
    --working-directory="$DIR" \
    --property=Restart=always \
    --property=RestartSec=3 \
    /usr/local/venv/bin/python -u server.py
) &
