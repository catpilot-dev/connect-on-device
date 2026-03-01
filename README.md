# COD — Connect On Device

A self-hosted web app that runs directly on your [comma 3](https://comma.ai/) device, giving you the full [connect.comma.ai](https://connect.comma.ai/) experience without any cloud dependency. Browse routes, watch videos, tune settings, manage software updates — all from a browser on your local network.

## Why COD?

**connect.comma.ai** requires internet access and routes your data through comma's servers. COD runs entirely on-device:

- **Offline-first** — works on your local network, no internet needed
- **Zero latency** — route data served straight from the device's storage
- **Full control** — manage software, models, maps, SSH keys, and device settings from one UI
- **Privacy** — your driving data never leaves your network
- **Extended features** — HUD video rendering, signal browser, live dashboard, and more that the cloud version doesn't offer

## Features

### Route Management
Browse all driving routes with distance, duration, engagement stats, and GPS location. Soft-delete routes you don't need, star ones you want to preserve, and add notes for documentation. On-demand enrichment computes GPS tracks and engagement stats lazily when you first view a route.

### Video Playback
Stream dashcam footage (front, wide, driver, quick) directly in the browser. Extract individual frames at any timestamp with full EXIF metadata (GPS, speed, bearing, camera intrinsics). Download screenshots with auto-generated filenames.

### HUD Video Rendering
Render openpilot's HUD overlay onto dashcam footage as a downloadable MP4. Choose quality presets, select time ranges, and monitor render progress. Also supports live HLS streaming of the HUD at 0.2x replay speed.

### Live Dashboard
Real-time telemetry dashboard via WebSocket — speed, steering angle, temperature, engagement state — viewable while driving or replaying a stored route.

### Signal Browser
Deep data analysis tool: discover all CAN message types in a route, extract signal data across segments, and export as JSON. Useful for debugging vehicle integration and analyzing driving behavior.

### Settings & Configuration
Comprehensive device management:
- **Driving** — personality, DCC calibration, curve comfort, lane centering, speed limit offsets
- **Software** — check/download/install updates, switch branches
- **Models** — swap driving and driver monitoring models, check for updates, download new ones
- **Maps** — download/manage offline OSM tiles for mapd
- **SSH keys** — add/remove authorized keys
- **System toggles** — experimental mode, recording, driver monitoring, units, developer options

### Software Updates
Full update lifecycle: check for new commits, download to staging, install with automatic reboot. Switch between openpilot branches without SSH.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python aiohttp (async) |
| Frontend | Svelte 5 + Vite 7 |
| Styling | Tailwind CSS 3 |
| Maps | Leaflet |
| Video | HLS.js |
| UI components | Bits UI |
| Log parsing | cereal (openpilot native) |

## Project Structure

```
connect_on_device/
├── server.py              # aiohttp entry point (port 8082)
├── route_store.py         # Route discovery, caching, enrichment
├── log_parser.py          # Cereal message parsing & signal extraction (qlog preferred)
├── route_helpers.py       # Metadata, URLs, engagement stats
├── hud_stream.py          # HLS streaming pipeline
├── render_clip_drm.py     # HUD-to-MP4 rendering
├── model_swapper.py       # Background model swap
├── model_download.py      # Background model download
├── tile_manager.py        # OSM tile management
├── storage_management.py  # Route download builder
├── handler_helpers.py     # Shared handler utilities
├── handlers/
│   ├── routes.py          # Route CRUD & enrichment
│   ├── media.py           # Video/frame extraction
│   ├── hud.py             # HUD render & stream
│   ├── dashboard.py       # Live telemetry WebSocket
│   ├── signals.py         # Signal browser data
│   ├── software.py        # Update lifecycle
│   ├── models.py          # Model management
│   ├── mapd.py            # Offline map tiles
│   ├── params.py          # Device params & toggles
│   ├── ssh_keys.py        # SSH key management
│   ├── auth.py            # Auth stubs
│   └── stubs.py           # connect.comma.ai compat stubs
├── frontend/
│   ├── src/lib/pages/     # Svelte page components
│   ├── src/lib/components/# Reusable UI components
│   └── public/            # Static assets (favicon, etc.)
└── static/                # Built frontend (served by aiohttp)
```

## Setup

### On the Comma 3

```bash
# Copy the project to the device
scp -r connect_on_device/ c3:/data/connect_on_device/

# Start the server
ssh c3 "cd /data/connect_on_device && nohup /usr/local/venv/bin/python server.py > /tmp/connect.log 2>&1 &"
```

Then open `http://<device-ip>:8082` in your browser.

### Local Development

```bash
# Backend (serves API + built frontend)
python server.py --data-dir ~/driving_data/data --port 8082

# Frontend dev server (hot reload, proxies API to :8082)
cd frontend && npm install && npm run dev
```

### Build & Deploy

```bash
# Build frontend
cd frontend && npm run build    # outputs to ../static/

# Deploy to C3
ssh c3 "rm -rf /data/connect_on_device/static"
scp -r static c3:/data/connect_on_device/

# Restart server
ssh c3 "pkill -f 'python.*server.py'"
ssh c3 "cd /data/connect_on_device && nohup /usr/local/venv/bin/python server.py > /tmp/connect.log 2>&1 &"
```

## API Compatibility

COD implements the same REST API as connect.comma.ai, so existing tools that talk to the comma API can point at your device instead. Stub endpoints return empty responses for features that don't apply locally (bootlogs, crashlogs, athena queue, etc.).

## Credits

Built for BMW E8x/E9x integration with [openpilot](https://github.com/commaai/openpilot) on the [comma 3](https://comma.ai/) platform.

## License

Private project — not affiliated with or endorsed by comma.ai.
