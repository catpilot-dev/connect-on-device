#!/bin/bash
# Start connect-on-device web server with auto-restart
# Called from /data/continue.sh on every boot

DIR="$(cd "$(dirname "$0")" && pwd)"

# Advertise only A (IPv4) records on IPv4 networks so catpilot.local resolves to
# the IPv4 address.  This prevents iOS from resolving to IPv6 link-local, which
# causes WebRTC ICE candidate family mismatch and ~20s connection delays.
_avahi_conf=/etc/avahi/avahi-daemon.conf
if grep -q 'publish-aaaa-on-ipv4=yes\|#publish-aaaa-on-ipv4\|use-ipv6=no' "$_avahi_conf" 2>/dev/null; then
  sudo mount -o remount,rw / 2>/dev/null || true
  sudo sed -i 's/#\?publish-aaaa-on-ipv4=.*/publish-aaaa-on-ipv4=no/; s/use-ipv6=no/use-ipv6=yes/' "$_avahi_conf"
  sudo systemctl restart avahi-daemon 2>/dev/null || true
fi

# Set mDNS hostname from config.py so the device is reachable as http://catpilot.local
# Done here (from /data) so it survives OTA updates that reset /etc/hostname.
# avahi-daemon doesn't always pick up hostnamectl changes via DBus, so restart
# it (and the SSH announce service) when we change the hostname.
_hostname=$(cd "$DIR" && python3 -c "from config import DEVICE_HOSTNAME; print(DEVICE_HOSTNAME)" 2>/dev/null || echo "catpilot")
if [ "$(hostname)" != "$_hostname" ]; then
  sudo hostnamectl set-hostname "$_hostname" 2>/dev/null || true
  sudo systemctl restart avahi-daemon avahi-ssh-publish 2>/dev/null || true
fi

# Install COD's Python deps into the openpilot venv (aiohttp is no longer an
# openpilot dependency since v0.11.2; see requirements.txt). The check is
# local, so pip -- network plus a rootfs remount -- runs only when something is
# unmet: first boot, a COD update that bumps a pin, or an AGNOS update that
# replaced the venv. Short timeouts: offline at boot must not hold COD up, and
# a failed install is retried next boot.
_venv_py=/usr/local/venv/bin/python
if ! _unmet=$(cd "$DIR" && "$_venv_py" requirements_check.py 2>&1); then
  echo "[setup_service] unmet requirements: $(echo $_unmet)" >> /tmp/connect.log
  _was_ro=$(findmnt -no OPTIONS / | grep -qw ro && echo 1)
  [ -n "$_was_ro" ] && sudo mount -o remount,rw /
  sudo "$_venv_py" -m pip install --disable-pip-version-check --root-user-action=ignore \
      --no-cache-dir --timeout 10 --retries 1 -r "$DIR/requirements.txt" >> /tmp/connect.log 2>&1 \
    || echo "[setup_service] pip install failed; will retry next boot" >> /tmp/connect.log
  [ -n "$_was_ro" ] && sudo mount -o remount,ro / 2>/dev/null
fi

# Grant Python the capability to bind port 80 directly (works for both IPv4 and
# IPv6, no iptables NAT needed).  The root filesystem may be read-only until
# remounted; retry once after remounting.
_py=$(readlink -f /usr/local/venv/bin/python)
sudo setcap cap_net_bind_service=+ep "$_py" 2>/dev/null || {
  sudo mount -o remount,rw / 2>/dev/null
  sudo setcap cap_net_bind_service=+ep "$_py"
}

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
