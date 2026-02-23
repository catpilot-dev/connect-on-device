import asyncio
import json
import logging
import re

from aiohttp import web

from handler_helpers import error_response, parse_json, read_param, write_param

logger = logging.getLogger("connect")


# ─── Software update management ──────────────────────────────────────

SOFTWARE_PARAMS = [
    "GitBranch", "GitCommit", "GitCommitDate",
    "UpdaterState", "UpdaterTargetBranch",
    "UpdaterCurrentDescription", "UpdaterNewDescription",
    "UpdaterCurrentReleaseNotes", "UpdaterNewReleaseNotes",
    "UpdaterAvailableBranches",
    "UpdateAvailable", "UpdaterFetchAvailable",
    "LastUpdateTime", "UpdateFailedCount",
    "IsTestedBranch",
]

_SOFTWARE_BOOL_PARAMS = {"UpdateAvailable", "UpdaterFetchAvailable", "IsTestedBranch"}
_SOFTWARE_INT_PARAMS = {"UpdateFailedCount"}


async def handle_software_get(request: web.Request) -> web.Response:
    """GET /v1/software — read all software-related params."""
    result = {}
    for key in SOFTWARE_PARAMS:
        raw = read_param(key)

        if key in _SOFTWARE_BOOL_PARAMS:
            result[key] = raw == "1"
        elif key in _SOFTWARE_INT_PARAMS:
            try:
                result[key] = int(raw) if raw else 0
            except ValueError:
                result[key] = 0
        elif key == "UpdaterAvailableBranches":
            result[key] = [b for b in raw.split(",") if b] if raw else []
        elif key == "GitCommitDate":
            # Raw: "'1770870385 2026-02-12 12:26:25 +0800'" → "2026-02-12 12:26:25"
            m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', raw)
            result[key] = m.group(1) if m else raw.strip("'\" ")
        else:
            result[key] = raw
    return web.json_response(result)


async def handle_software_check(request: web.Request) -> web.Response:
    """POST /v1/software/check — send SIGUSR1 to updater to trigger check."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill", "-SIGUSR1", "-f", "system.updated.updated",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception as e:
        logger.warning("Failed to signal updater for check: %s", e)
    return web.json_response({"status": "checking"})


async def handle_software_download(request: web.Request) -> web.Response:
    """POST /v1/software/download — send SIGHUP to updater to trigger download."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill", "-SIGHUP", "-f", "system.updated.updated",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception as e:
        logger.warning("Failed to signal updater for download: %s", e)
    return web.json_response({"status": "downloading"})


async def handle_software_install(request: web.Request) -> web.Response:
    """POST /v1/software/install — write DoReboot param to trigger reboot."""
    try:
        write_param("DoReboot", "1")
    except Exception as e:
        logger.error("Failed to set DoReboot: %s", e)
        return error_response(str(e), 500)
    return web.json_response({"status": "rebooting"})


async def handle_software_branch(request: web.Request) -> web.Response:
    """POST /v1/software/branch — set UpdaterTargetBranch and trigger check."""
    body = await parse_json(request)

    branch = body.get("branch", "").strip()
    if not branch:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Missing branch name"}))

    try:
        write_param("UpdaterTargetBranch", branch)
    except Exception as e:
        return error_response(str(e), 500)

    # Trigger a check for the new branch
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill", "-SIGUSR1", "-f", "system.updated.updated",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception:
        pass

    return web.json_response({"status": "ok", "branch": branch})


async def handle_software_uninstall(request: web.Request) -> web.Response:
    """POST /v1/software/uninstall — write DoUninstall param."""
    try:
        write_param("DoUninstall", "1")
    except Exception as e:
        logger.error("Failed to set DoUninstall: %s", e)
        return error_response(str(e), 500)
    return web.json_response({"status": "uninstalling"})
