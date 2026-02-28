"""Plugin management handlers — list and toggle plugins via .disabled marker."""
import json
import os

from aiohttp import web

from handler_helpers import error_response, read_param, write_param
from .params import MAPD_PARAM_KEYS, update_mapd_settings, _read_mapd_settings, _MAPD_SETTINGS_MAP

PLUGINS_DIR = '/data/plugins'
BUILD_HASH_FILE = '/tmp/plugin_build_hash'


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

    enabled = not os.path.exists(os.path.join(plugin_dir, '.disabled'))
    plugins.append({
      'id': name,
      'name': manifest.get('name', name),
      'version': manifest.get('version', ''),
      'type': manifest.get('type', 'plugin'),
      'description': manifest.get('description', ''),
      'enabled': enabled,
      'dependencies': manifest.get('dependencies', []),
      'panel': manifest.get('panel', False),
      'settings': _read_plugin_params(manifest),
    })
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
