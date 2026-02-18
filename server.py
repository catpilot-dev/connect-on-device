#!/usr/bin/env python3
"""
Connect on Device - comma-compatible API server for local route browsing.

Implements the same REST API as api.comma.ai so the asiusai/connect React
frontend works unchanged. Serves route data from /data/media/0/realdata/.

Usage:
  On C3:  /usr/local/venv/bin/python /data/connect_on_device/server.py
  Local:  python server.py --data-dir ~/driving_data/data
"""

import argparse
import asyncio
import logging
from pathlib import Path

from aiohttp import web

from handlers import (
    cors_middleware,
    handle_auth,
    handle_connectdata,
    handle_device_get,
    handle_device_location,
    handle_device_stats,
    handle_devices,
    handle_hud_ws,
    handle_me,
    handle_preserved_routes,
    handle_route_delete,
    handle_route_download,
    handle_route_enrich,
    handle_route_files,
    handle_route_get,
    handle_route_preserve,
    handle_route_unpreserve,
    handle_routes_list,
    handle_routes_segments,
    handle_share_signature,
    handle_spa,
    handle_storage,
    handle_stub_empty_array,
    handle_stub_error,
    handle_webrtc,
)
from route_store import DEFAULT_DATA_DIR, DEFAULT_PORT, RouteStore

logger = logging.getLogger("connect")


# ─── Lifecycle hooks ──────────────────────────────────────────────────

async def _start_enrichment(app: web.Application):
    """Start the persistent background enrichment loop on server startup."""
    store: RouteStore = app["store"]
    store.scan(force=True)
    app["enrichment_task"] = asyncio.create_task(store._enrichment_loop())
    logger.info("Background enrichment task started")


async def _stop_enrichment(app: web.Application):
    """Cancel the background enrichment loop on server shutdown."""
    task = app.get("enrichment_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("Background enrichment task stopped")


# ─── App factory ──────────────────────────────────────────────────────

def create_app(data_dir: str, static_dir: str) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])

    store = RouteStore(data_dir)
    app["store"] = store
    app["static_dir"] = Path(static_dir)

    # Background enrichment lifecycle
    app.on_startup.append(_start_enrichment)
    app.on_cleanup.append(_stop_enrichment)

    # ── comma-compatible API ──
    # Auth
    app.router.add_get("/v1/me/", handle_me)
    app.router.add_post("/v2/auth/", handle_auth)

    # Devices
    app.router.add_get("/v1/me/devices/", handle_devices)
    app.router.add_get("/v1.1/devices/{dongleId}/", handle_device_get)
    app.router.add_get("/v1.1/devices/{dongleId}/stats", handle_device_stats)
    app.router.add_get("/v1/devices/{dongleId}/location", handle_device_location)
    app.router.add_get("/v1/devices/{dongleId}/routes/preserved", handle_preserved_routes)

    # Routes
    app.router.add_get("/v1/devices/{dongleId}/routes", handle_routes_list)
    app.router.add_get("/v1/devices/{dongleId}/routes_segments", handle_routes_segments)

    # Route detail
    app.router.add_get("/v1/route/{routeName}/", handle_route_get)
    app.router.add_delete("/v1/route/{routeName}/", handle_route_delete)
    app.router.add_get("/v1/route/{routeName}/files", handle_route_files)
    app.router.add_get("/v1/route/{routeName}/share_signature", handle_share_signature)
    app.router.add_post("/v1/route/{routeName}/preserve", handle_route_preserve)
    app.router.add_delete("/v1/route/{routeName}/preserve", handle_route_unpreserve)
    app.router.add_get("/v1/route/{routeName}/download", handle_route_download)
    app.router.add_post("/v1/route/{routeName}/enrich", handle_route_enrich)

    # Stubs for endpoints the frontend may query
    app.router.add_get("/v1/devices/{dongleId}/athena_offline_queue", handle_stub_empty_array)
    app.router.add_get("/v1/devices/{dongleId}/bootlogs", handle_stub_empty_array)
    app.router.add_get("/v1/devices/{dongleId}/crashlogs", handle_stub_empty_array)
    app.router.add_get("/v1/devices/{dongleId}/users", handle_stub_empty_array)
    app.router.add_get("/v1/devices/{dongleId}/firehose_stats", handle_stub_error)
    app.router.add_post("/v1/devices/{dongleId}/unpair", handle_stub_error)
    app.router.add_get("/v1/devices/{dongleId}/", handle_device_get)
    app.router.add_get("/v1/prime/subscription", handle_stub_error)
    app.router.add_get("/v1/prime/subscribe_info", handle_stub_error)
    app.router.add_get("/v1/storage", handle_storage)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))

    # WebRTC signaling proxy (to local webrtcd on port 5001)
    app.router.add_post("/api/webrtc", handle_webrtc)

    # HUD overlay WebSocket (server-side rendered overlay at 20Hz)
    app.router.add_get("/ws/hud", handle_hud_ws)

    # Media file serving
    app.router.add_get("/connectdata/{path:.*}", handle_connectdata)

    # SPA fallback — serves static files if they exist, otherwise index.html
    app.router.add_get("/{path:.*}", handle_spa)

    return app


def main():
    parser = argparse.ArgumentParser(description="Connect on Device - comma-compatible local server")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help=f"Route data directory (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Server port (default: {DEFAULT_PORT})")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--static-dir", default=None,
                        help="Static files directory (default: ./static next to server.py)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    static_dir = args.static_dir or str(Path(__file__).parent / "static")

    logger.info("Starting Connect on Device (comma-compatible API)")
    logger.info("  Data dir:   %s", args.data_dir)
    logger.info("  Static dir: %s", static_dir)
    logger.info("  Listening:  http://%s:%d", args.host, args.port)

    app = create_app(args.data_dir, static_dir)
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
