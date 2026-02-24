#!/bin/bash
# Set up connect-on-device as a systemd user service
# Called from /data/continue.sh on every boot
# (home dir is on read-only root, so symlink must be recreated each boot)
# Runs in background; waits for manager.py (openpilot ready) before starting

DIR="$(cd "$(dirname "$0")" && pwd)"

(
  # Wait for openpilot manager — guarantees systemd user instance is ready
  while ! pgrep -f 'manager.py' &>/dev/null; do
    sleep 2
  done

  mkdir -p /home/comma/.config/systemd/user
  ln -sf "$DIR/connect-on-device.service" /home/comma/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now connect-on-device
) &
