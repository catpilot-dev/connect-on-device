#!/bin/bash
# Set up connect-on-device as a systemd user service
# Called from /data/continue.sh on every boot
# (home dir is on read-only root, so symlink must be recreated each boot)

DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p /home/comma/.config/systemd/user
ln -sf "$DIR/connect-on-device.service" /home/comma/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now connect-on-device
