"""Plugin management handlers — list and toggle plugins via .disabled marker."""
import asyncio
import json
import logging
import os

from aiohttp import web

from handler_helpers import error_response, read_param, write_param
from .params import MAPD_PARAM_KEYS, update_mapd_settings, _read_mapd_settings, _MAPD_SETTINGS_MAP

logger = logging.getLogger("connect")

PLUGINS_DIR = '/data/plugins'
IS_C3 = os.path.exists('/TICI')
BUILD_HASH_FILE = '/tmp/plugin_build_hash'
PLUGIN_REPO_DIR = '/data/catpilot-plugins'
OPENPILOT_DIR = '/data/openpilot'
DEFAULT_PLUGIN_REPO_URL = 'https://github.com/catpilot-dev/plugins'


def _read_plugin_params(manifest):
  """Read UI-visible params (those with a 'desc' field) and their current values."""
  params_def = manifest.get('params', {})
  mapd_settings = None  # lazy-loaded

  settings = []
  for key, meta in params_def.items():
    if 'desc' not in meta:
      continue

    entry = {
      'key': key,
      'type': meta.get('type', 'string'),
      'label': meta.get('label', key),
      'desc': meta['desc'],
    }
    if 'options' in meta:
      entry['options'] = meta['options']
    if 'suffix' in meta:
      entry['suffix'] = meta['suffix']
    if 'dependsOn' in meta:
      entry['dependsOn'] = meta['dependsOn']
    if 'requiresPlugin' in meta:
      entry['requiresPlugin'] = meta['requiresPlugin']

    # Read current value
    raw = read_param(key)

    # Fallback: if individual param missing, read from MapdSettings JSON
    if not raw and key in _MAPD_SETTINGS_MAP:
      if mapd_settings is None:
        mapd_settings = _read_mapd_settings()
      json_key, conv = _MAPD_SETTINGS_MAP[key]
      json_val = mapd_settings.get(json_key)
      if json_val is not None:
        if conv == "bool":
          entry['value'] = bool(json_val)
        elif conv == "offset_pct":
          entry['value'] = round(float(json_val) * 100)
        elif conv == "lat_idx":
          lat_vals = [1.5, 2.0, 2.5, 3.0]
          try:
            entry['value'] = lat_vals.index(float(json_val))
          except ValueError:
            entry['value'] = 1
        settings.append(entry)
        continue

    # Parse value based on type
    ptype = meta.get('type', 'string')
    if ptype == 'bool':
      entry['value'] = raw == "1"
    elif ptype == 'pills':
      try:
        entry['value'] = int(raw) if raw else meta.get('default', 0)
      except ValueError:
        entry['value'] = meta.get('default', 0)
    else:
      entry['value'] = raw

    settings.append(entry)

  return settings


def _scan_plugins():
  """Scan /data/plugins/ and return list of plugin info dicts."""
  if not os.path.isdir(PLUGINS_DIR):
    return []

  plugins = []
  for name in sorted(os.listdir(PLUGINS_DIR)):
    plugin_dir = os.path.join(PLUGINS_DIR, name)
    if not os.path.isdir(plugin_dir):
      continue
    manifest_path = os.path.join(plugin_dir, 'plugin.json')
    if not os.path.exists(manifest_path):
      continue
    try:
      with open(manifest_path) as f:
        manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
      continue

    # Filter by device_filter: hide plugins not meant for this device
    device_filter = manifest.get('device_filter')
    if device_filter:
      device_type = 'tici' if IS_C3 else 'unknown'
      if device_type not in device_filter:
        continue

    # c3_compat is always enabled on C3, not toggleable
    locked = (name == 'c3_compat' and IS_C3)
    enabled = True if locked else not os.path.exists(os.path.join(plugin_dir, '.disabled'))

    plugins.append({
      'id': name,
      'name': manifest.get('name', name),
      'version': manifest.get('version', ''),
      'type': manifest.get('type', 'plugin'),
      'description': manifest.get('description', ''),
      'enabled': enabled,
      'locked': locked,
      'dependencies': manifest.get('dependencies', []),
      'panel': manifest.get('panel', False),
      'settings': _read_plugin_params(manifest),
    })

  # Sort order: model_selector first, bmw and c3_compat at bottom
  ORDER = {'model_selector': -3, 'lane_centering': -2, 'speedlimitd': -1, 'mapd': 0, 'bmw_e9x_e8x': 1, 'c3_compat': 2}
  plugins.sort(key=lambda p: (ORDER.get(p['id'], 0), p['id']))
  return plugins


async def handle_plugins_get(request: web.Request) -> web.Response:
  """GET /v1/plugins — list all installed plugins with enabled state."""
  return web.json_response(_scan_plugins())


async def handle_plugin_toggle(request: web.Request) -> web.Response:
  """POST /v1/plugins/{plugin_id}/toggle — toggle enable/disable state."""
  plugin_id = request.match_info['plugin_id']
  plugin_dir = os.path.join(PLUGINS_DIR, plugin_id)

  if not os.path.isdir(plugin_dir):
    return error_response(f"Plugin '{plugin_id}' not found", 404)
  if not os.path.exists(os.path.join(plugin_dir, 'plugin.json')):
    return error_response(f"Plugin '{plugin_id}' has no plugin.json", 400)

  marker = os.path.join(plugin_dir, '.disabled')
  if os.path.exists(marker):
    os.remove(marker)
    enabled = True
  else:
    with open(marker, 'w') as f:
      f.write('')
    enabled = False

  # Force builder rebuild on next boot
  try:
    os.remove(BUILD_HASH_FILE)
  except FileNotFoundError:
    pass

  return web.json_response({
    'id': plugin_id,
    'enabled': enabled,
    'reboot_required': True,
  })


async def handle_plugin_param(request: web.Request) -> web.Response:
  """POST /v1/plugins/{plugin_id}/param — set a plugin param {key, value}."""
  plugin_id = request.match_info['plugin_id']
  plugin_dir = os.path.join(PLUGINS_DIR, plugin_id)

  if not os.path.isdir(plugin_dir):
    return error_response(f"Plugin '{plugin_id}' not found", 404)

  manifest_path = os.path.join(plugin_dir, 'plugin.json')
  try:
    with open(manifest_path) as f:
      manifest = json.load(f)
  except (json.JSONDecodeError, OSError, FileNotFoundError):
    return error_response(f"Plugin '{plugin_id}' has no valid plugin.json", 400)

  try:
    body = await request.json()
  except Exception:
    return error_response("Invalid JSON body", 400)

  key = body.get("key")
  value = body.get("value")
  params_def = manifest.get("params", {})

  if key not in params_def:
    return error_response(f"Unknown param: {key}", 400)

  meta = params_def[key]
  ptype = meta.get("type", "string")
  if ptype == "bool":
    raw = "1" if value else "0"
  elif ptype in ("int", "pills"):
    raw = str(int(value))
  else:
    raw = str(value)

  write_param(key, raw)

  # Sync MapdSettings JSON if this is a mapd param
  if key in MAPD_PARAM_KEYS:
    update_mapd_settings()

  return web.json_response({"status": "ok", "key": key, "value": value})


# ── Plugin repo management ──────────────────────────────────────────


async def handle_plugin_repo_get(request: web.Request) -> web.Response:
  """GET /v1/plugins/repo — return repo URL, installed status, last updated time."""
  url = read_param('PluginRepoUrl') or DEFAULT_PLUGIN_REPO_URL
  installed = os.path.isdir(os.path.join(PLUGIN_REPO_DIR, '.git'))
  last_updated = None
  if installed:
    try:
      git_fetch_head = os.path.join(PLUGIN_REPO_DIR, '.git', 'FETCH_HEAD')
      git_head = os.path.join(PLUGIN_REPO_DIR, '.git', 'HEAD')
      # Use FETCH_HEAD mtime (last pull), fall back to HEAD mtime
      ref = git_fetch_head if os.path.exists(git_fetch_head) else git_head
      last_updated = os.path.getmtime(ref)
    except OSError:
      pass
  return web.json_response({
    'url': url,
    'installed': installed,
    'last_updated': last_updated,
  })


async def handle_plugin_repo_set(request: web.Request) -> web.Response:
  """POST /v1/plugins/repo — set the plugin repo URL param."""
  try:
    body = await request.json()
  except Exception:
    return error_response("Invalid JSON body", 400)

  url = body.get('url', '').strip()
  if not url:
    return error_response("Missing 'url' field", 400)

  write_param('PluginRepoUrl', url)
  return web.json_response({'status': 'ok', 'url': url})


async def handle_plugin_repo_install(request: web.Request) -> web.Response:
  """POST /v1/plugins/repo/install — git clone/pull + run install.sh."""
  url = read_param('PluginRepoUrl') or DEFAULT_PLUGIN_REPO_URL

  try:
    # Clone or fetch+reset
    is_clone = False
    if os.path.isdir(os.path.join(PLUGIN_REPO_DIR, '.git')):
      # Get current HEAD before fetch
      proc = await asyncio.create_subprocess_exec(
        'git', '-C', PLUGIN_REPO_DIR, 'rev-parse', 'HEAD',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
      )
      stdout, _ = await proc.communicate()
      head_before = stdout.decode().strip()

      logger.info("Plugin repo: fetching %s", url)
      proc = await asyncio.create_subprocess_exec(
        'git', '-C', PLUGIN_REPO_DIR, 'fetch', 'origin',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
      )
      stdout, _ = await proc.communicate()
      git_output = stdout.decode(errors='replace')
      if proc.returncode != 0:
        logger.error("Plugin repo git fetch failed: %s", git_output)
        return web.json_response({
          'status': 'error',
          'output': git_output,
          'reboot_required': False,
        }, status=500)

      # Reset to match remote (local changes are from boot_patch.sh, safe to discard)
      proc = await asyncio.create_subprocess_exec(
        'git', '-C', PLUGIN_REPO_DIR, 'reset', '--hard', 'origin/main',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
      )
    else:
      is_clone = True
      head_before = None
      logger.info("Plugin repo: cloning %s", url)
      proc = await asyncio.create_subprocess_exec(
        'git', 'clone', '--depth=1', url, PLUGIN_REPO_DIR,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
      )
    stdout, _ = await proc.communicate()
    git_output = stdout.decode(errors='replace')

    if proc.returncode != 0:
      logger.error("Plugin repo git failed: %s", git_output)
      return web.json_response({
        'status': 'error',
        'output': git_output,
        'reboot_required': False,
      }, status=500)

    # Run install.sh
    install_script = os.path.join(PLUGIN_REPO_DIR, 'install.sh')
    if not os.path.isfile(install_script):
      return web.json_response({
        'status': 'error',
        'output': 'install.sh not found in plugin repo',
        'reboot_required': False,
      }, status=500)

    logger.info("Plugin repo: running install.sh --target %s", OPENPILOT_DIR)
    proc = await asyncio.create_subprocess_exec(
      'bash', install_script, '--target', OPENPILOT_DIR,
      stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    install_output = stdout.decode(errors='replace')

    if proc.returncode != 0:
      logger.error("Plugin install.sh failed: %s", install_output)
      return web.json_response({
        'status': 'error',
        'output': install_output,
        'reboot_required': False,
      }, status=500)

    # Check if HEAD actually changed
    proc = await asyncio.create_subprocess_exec(
      'git', '-C', PLUGIN_REPO_DIR, 'rev-parse', 'HEAD',
      stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    head_after = stdout.decode().strip()
    changed = is_clone or head_before != head_after

    if changed:
      # Force plugin rebuild on next boot
      try:
        os.remove(BUILD_HASH_FILE)
      except FileNotFoundError:
        pass

    combined = git_output + '\n' + install_output
    logger.info("Plugin repo install complete (changed=%s)", changed)
    return web.json_response({
      'status': 'ok',
      'output': combined.strip() if changed else 'Already up to date.',
      'reboot_required': changed,
    })

  except Exception as e:
    logger.exception("Plugin repo install error")
    return web.json_response({
      'status': 'error',
      'output': str(e),
      'reboot_required': False,
    }, status=500)
