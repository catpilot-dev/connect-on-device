"""SPA fallback handler — serves static files or index.html."""

import mimetypes
from pathlib import Path

from aiohttp import web

# Content types for pre-compressed assets
_MIME = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".html": "text/html",
    ".json": "application/json",
}


def _serve_with_gzip(file_path: Path, request: web.Request) -> web.Response:
    """Serve pre-compressed .gz file if available and client accepts gzip."""
    accept = request.headers.get("Accept-Encoding", "")
    gz_path = file_path.with_name(file_path.name + ".gz")
    if "gzip" in accept and gz_path.is_file():
        content_type = _MIME.get(file_path.suffix) or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        return web.Response(
            body=gz_path.read_bytes(),
            content_type=content_type,
            headers={
                "Content-Encoding": "gzip",
                "Cache-Control": "public, max-age=31536000, immutable",
                "Vary": "Accept-Encoding",
            },
        )
    resp = web.FileResponse(file_path)
    # Hashed filenames are immutable
    if "/assets/" in str(file_path):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


async def handle_spa(request: web.Request) -> web.Response:
    """Serve static files if they exist, otherwise the SPA index.html."""
    static_dir: Path = request.app["static_dir"]
    req_path = request.match_info.get("path", "")

    # Try to serve the actual static file first
    if req_path:
        # Security: resolve and ensure it's within static_dir
        file_path = (static_dir / req_path).resolve()
        if file_path.is_relative_to(static_dir) and file_path.is_file():
            return _serve_with_gzip(file_path, request)

    # Fall back to index.html for SPA client-side routing — no-cache so updates are immediate
    index = static_dir / "index.html"
    if index.exists():
        resp = web.FileResponse(index)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp
    return web.Response(
        text="<html><body><h1>Connect on Device</h1>"
             "<p>Frontend not built yet.</p>"
             "<p>API: <a href='/v1/me/'>/v1/me/</a></p>"
             "</body></html>",
        content_type="text/html",
    )
