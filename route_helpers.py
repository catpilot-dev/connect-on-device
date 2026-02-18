"""Shared route utility functions for Connect on Device handlers.

Small helper functions used across multiple handler modules.
No internal imports — only uses stdlib + aiohttp.
"""

import json
from pathlib import Path

from aiohttp import web


def _clean_route(route: dict) -> dict:
    """Remove internal fields from route for API response."""
    return {k: v for k, v in route.items() if not k.startswith("_")}


def _base_url(request: web.Request) -> str:
    """Get base URL for constructing file URLs."""
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{scheme}://{host}"


def _set_route_url(route: dict, request: web.Request) -> dict:
    """Set the url field dynamically based on request host."""
    r = dict(route)
    base = _base_url(request)
    r["url"] = f"{base}/connectdata/{route['dongle_id']}/{route['fullname'].split('/')[-1]}"
    return r


def _resolve_local_id(store, request: web.Request) -> str:
    """Resolve routeName from URL to local_id. Raises 404 if not found."""
    route_name = request.match_info["routeName"].replace("|", "/")
    # Try direct lookup first
    local_id = store.get_local_id(route_name)
    if not local_id:
        # Try matching by local_id directly (for hidden routes not in _routes)
        if route_name in store._raw:
            local_id = route_name
    if not local_id:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Route {route_name} not found"}))
    return local_id


def _route_engagement(store, route: dict) -> tuple[float, float]:
    """Compute engaged_ms and total_ms for a route from cached events.json files.

    Returns (engaged_ms, total_duration_ms). Uses cached events.json files
    (generated when a route is viewed in the UI). Returns (0, 0) if no cache.
    """
    local_id = route.get("_local_id")
    if not local_id:
        return (0, 0)

    total_duration_ms = (route.get("maxqlog", 0) + 1) * 60_000
    engaged_ms = 0.0

    for seg in route.get("_segments", []):
        events_path = Path(seg["path"]) / "events.json"
        if not events_path.exists():
            continue
        try:
            events = json.loads(events_path.read_text())
            last_enabled_offset = None
            for ev in events:
                if ev.get("type") != "state":
                    continue
                enabled = ev.get("data", {}).get("enabled", False)
                offset = ev.get("route_offset_millis", 0)
                if enabled and last_enabled_offset is None:
                    last_enabled_offset = offset
                elif not enabled and last_enabled_offset is not None:
                    engaged_ms += offset - last_enabled_offset
                    last_enabled_offset = None
            # Close open engagement at segment end
            if last_enabled_offset is not None:
                seg_end_ms = (seg["number"] + 1) * 60_000
                engaged_ms += seg_end_ms - last_enabled_offset
        except Exception:
            pass

    return (engaged_ms, total_duration_ms)
