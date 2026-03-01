import asyncio
import json
import logging
import os
import re
import shutil

from aiohttp import web

from handler_helpers import error_response, parse_json, read_param, write_param

logger = logging.getLogger("connect")


# ─── Software update management ──────────────────────────────────────

SOFTWARE_PARAMS = [
    "GitBranch", "GitCommit", "GitCommitDate", "GitRemote",
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

    # Detect stuck update: fetch available but staging never completes
    # This typically means submodule commits weren't pushed to fork repos
    if (result.get("UpdaterFetchAvailable") and
            not result.get("UpdateAvailable") and
            not result.get("UpdaterNewDescription") and
            result.get("UpdaterState") in ("", "idle")):
        result["UpdaterWarning"] = (
            "Update fetch failed — submodule commits may not be pushed to fork repos. "
            "Push panda and opendbc_repo branches, then retry."
        )

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


# ─── Plugin bootstrap for branch upgrades ────────────────────────────

STAGING_PATHS = [
    "/data/safe_staging/finalized/plugins",  # post-finalize (some updater versions)
    "/data/safe_staging/upper/plugins",       # overlay upper layer (0.10.x updater)
]
PLUGINS_DIR = "/data/plugins"


def _get_device_type() -> str:
    """Detect comma device type from devicetree."""
    try:
        with open("/sys/firmware/devicetree/base/model") as f:
            return f.read().strip('\x00').split('comma ')[-1]
    except FileNotFoundError:
        return "unknown"


async def handle_software_prepare_plugins(request: web.Request) -> web.Response:
    """POST /v1/software/prepare-plugins — copy plugins from staged update, auto-enable c3_compat."""
    staged_plugins = next((p for p in STAGING_PATHS if os.path.isdir(p)), None)
    if not staged_plugins:
        return web.json_response({"status": "no_plugins", "message": "No plugins/ in staged update"})

    # Copy staged plugins to /data/plugins/
    if os.path.exists(PLUGINS_DIR):
        shutil.rmtree(PLUGINS_DIR)
    shutil.copytree(staged_plugins, PLUGINS_DIR)

    result = {"status": "ok", "plugins_copied": True, "c3_compat_enabled": False}

    # Auto-enable c3_compat for tici (C3 on AGNOS 12.8)
    device_type = _get_device_type()
    c3_compat_dir = os.path.join(PLUGINS_DIR, "c3_compat")
    if device_type == "tici" and os.path.isdir(c3_compat_dir):
        disabled_marker = os.path.join(c3_compat_dir, ".disabled")
        if os.path.exists(disabled_marker):
            os.remove(disabled_marker)
        result["c3_compat_enabled"] = True
        logger.info("Auto-enabled c3_compat for tici device (AGNOS 12.8)")

    # Force plugin builder rebuild on next boot
    try:
        os.remove("/tmp/plugin_build_hash")
    except FileNotFoundError:
        pass

    logger.info("Plugins prepared: copied from staging, device=%s, c3_compat=%s",
                device_type, result["c3_compat_enabled"])
    return web.json_response(result)
