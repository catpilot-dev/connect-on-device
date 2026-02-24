"""handlers package — re-exports all handler functions for server.py compatibility."""

from .middleware import cors_middleware

from .auth import (
    handle_me,
    handle_auth,
    handle_devices,
    handle_device_get,
    handle_device_stats,
    handle_device_location,
    handle_storage,
    handle_device_info,
    handle_device_reboot,
    handle_device_poweroff,
    handle_device_language,
)

from .routes import (
    handle_routes_list,
    handle_routes_segments,
    handle_preserved_routes,
    handle_route_get,
    handle_route_enrich,
    handle_route_files,
    handle_route_manifest,
    handle_share_signature,
    handle_route_delete,
    handle_route_note,
    handle_route_preserve,
    handle_route_unpreserve,
    handle_route_download,
    handle_connectdata,
)

from .media import (
    handle_screenshot,
    handle_frame,
)

from .hud import (
    handle_hud_prerender,
    handle_hud_progress,
    handle_hud_cancel,
    handle_hud_video,
    handle_hud_stream_start,
    handle_hud_stream_stop,
    handle_hud_stream_status,
    handle_hud_stream_serve,
    handle_hud_ws,
)

from .software import (
    handle_software_get,
    handle_software_check,
    handle_software_download,
    handle_software_install,
    handle_software_branch,
    handle_software_uninstall,
)

from .params import (
    handle_lateral_delay,
    handle_toggles_get,
    handle_toggles_set,
    handle_params_get,
    handle_params_set,
)

from .mapd import (
    handle_tile_list,
    handle_tile_download,
    handle_tile_progress,
    handle_tile_cancel,
    handle_tile_delete,
    handle_mapd_check_update,
    handle_mapd_update,
)

from .models import (
    handle_models_list,
    handle_models_swap,
    handle_models_check_updates,
    handle_models_download,
)

from .ssh_keys import (
    handle_ssh_keys_get,
    handle_ssh_keys_set,
    handle_ssh_keys_delete,
    handle_webrtc,
)

from .dashboard import (
    handle_dashboard_telemetry,
    handle_dashboard_ws,
)

from .signals import (
    handle_signal_catalog,
    handle_signal_data,
    handle_signal_all,
)

from .stubs import (
    handle_stub_empty_array,
    handle_stub_error,
)

from .spa import handle_spa
