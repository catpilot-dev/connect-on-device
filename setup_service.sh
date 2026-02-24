#!/bin/bash
# Set up connect-on-device as a systemd user service
# Called from /data/continue.sh on every boot
# (home dir is on read-only root, so symlink must be recreated each boot)
# Runs in background to avoid blocking openpilot startup

DIR="$(cd "$(dirname "$0")" && pwd)"

(
  # Wait for systemd user manager to be ready (up to 30s)
  for i in $(seq 1 30); do
    systemctl --user is-system-running &>/dev/null && break
    sleep 1
  done

  mkdir -p /home/comma/.config/systemd/user
  ln -sf "$DIR/connect-on-device.service" /home/comma/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now connect-on-device
) &
