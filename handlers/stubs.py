"""Stub handlers for unimplemented comma.ai endpoints."""

from aiohttp import web

from handler_helpers import error_response


async def handle_stub_empty_array(request: web.Request) -> web.Response:
    return web.json_response([])


async def handle_stub_error(request: web.Request) -> web.Response:
    return error_response("Not available on local device", 501)
