"""Plugin management handlers — list and toggle plugins via .disabled marker."""
import json
import os

from aiohttp import web

from handler_helpers import error_response

PLUGINS_DIR = '/data/plugins'
BUILD_HASH_FILE = '/tmp/plugin_build_hash'


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
