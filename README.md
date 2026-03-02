# Connect on Device

A self-hosted web companion for [openpilot](https://github.com/commaai/openpilot). Runs directly on your comma device — browse routes, watch dashcam footage, manage plugins, and tune settings from any browser on your local network. No cloud, no account, no internet required.

Integrated into catpilot releases starting from `v0.10.3` — automatically installed on first boot.

## Access

Open `http://<comma_device_ip>:8082` in any browser on your local network.

## Features

- **Route browser** — distance, duration, engagement stats, GPS map, soft-delete, star, and notes
- **Video playback** — stream front/wide/driver cameras, extract frames with EXIF metadata
- **HUD video** — render openpilot's HUD overlay onto dashcam footage as downloadable MP4 or live HLS stream
- **Live dashboard** — real-time telemetry via WebSocket (speed, steering, temperature, engagement)
- **Signal browser** — explore all CAN messages in a route, extract and export signal data
- **Note taking** — add notes to any route for documentation, debugging, or personal reference
- **Plugin management** — enable/disable plugins without SSH
- **Settings** — driving personality, speed limit offsets, experimental mode, SSH keys, and more
- **Model management** — swap driving models, check for updates, download new ones
- **Map tiles** — download/manage offline OSM tiles for mapd
- **Software updates** — check, download, install openpilot updates and switch branches

## Project Structure

```
connect/
├── server.py              # aiohttp entry point (port 8082)
├── route_store.py         # Route discovery, caching, enrichment
├── handlers/              # REST API handlers
│   ├── routes.py          # Route CRUD & enrichment
│   ├── media.py           # Video/frame extraction
│   ├── hud.py             # HUD render & stream
│   ├── dashboard.py       # Live telemetry WebSocket
│   ├── signals.py         # Signal browser
│   ├── software.py        # Update lifecycle
│   ├── updates.py         # COD + plugin update checks
│   ├── models.py          # Model management
│   ├── mapd.py            # Offline map tiles
│   ├── params.py          # Device params & toggles
│   └── ssh_keys.py        # SSH key management
├── frontend/              # Svelte 5 + Vite + Tailwind CSS
└── static/                # Built frontend (served by aiohttp)
```

## Setup

### catpilot users

No setup needed — catpilot installs COD automatically on first boot.

### Upstream openpilot or other forks

[How to connect to your comma device](https://docs.comma.ai/how-to/connect-to-comma/)

```bash
ssh comma@<device_ip>

# Clone and start
cd /data
git clone https://github.com/catpilot-dev/connect.git connect_on_device
cd connect_on_device && bash setup_service.sh
```

### Local development

```bash
# Backend
python server.py --data-dir ~/driving_data/data --port 8082

# Frontend (hot reload, proxies API to :8082)
cd frontend && npm install && npm run dev
```

## License

MIT
