"""HUD live streaming pipeline manager for C3.

Supports two modes:

1. **DRM mode** (preferred, production):
   replay(0.2x) -> UI(RECORD=1, RECORD_HLS=1, DRM) -> ffmpeg(HLS) -> /tmp/hud_live/

2. **Weston mode** (legacy fallback):
   patched_weston -> replay(1x) + UI(wayland-egl) -> stream_capture(5fps) -> ffmpeg -> HLS

Only one stream can be active at a time since both modes require exclusive display access.
DRM mode is auto-selected when raylib + ui.py are available; otherwise falls back to Weston.
"""

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger("hud_stream")

# C3 binary paths
OPENPILOT_DIR = "/data/openpilot"
BIN_DIR = "/data/connect_on_device/bin"
UI_BIN = os.path.join(OPENPILOT_DIR, "selfdrive/ui/ui")
REPLAY_BIN = os.path.join(OPENPILOT_DIR, "tools/replay/replay")
WESTON_PATCHED = os.path.join(BIN_DIR, "weston_patched")
STREAM_CAPTURE = os.path.join(BIN_DIR, "stream_capture")
WESTON_STOCK = "/usr/bin/weston"
WESTON_CONFIG = "/usr/comma/weston.ini"

# Weston capture settings — C3 display is 1080x2160 portrait
CAPTURE_WIDTH = 1080
CAPTURE_HEIGHT = 2160
CAPTURE_FPS = 5       # Achievable rate with GPU readback
WARMUP_SEC = 5
XDG_RUNTIME_DIR = "/var/tmp/weston"
WAYLAND_DISPLAY = "wayland-0"

# DRM mode constants (reuse proven values from render_clip_drm.py)
DRM_RAYLIB_PATH = "/data/pip_packages"
PYTHON_BIN = "/usr/local/venv/bin/python"
UI_SCRIPT = "selfdrive/ui/ui.py"
DRM_RECORD_FPS = 2       # GPU readback rate at 2160x1080
DRM_REPLAY_SPEED = 0.2   # 0.2x -> 10 unique frames/route-second

# HLS output
HLS_DIR = Path("/tmp/hud_live")
HLS_TIME = 2          # seconds per segment
HLS_LIST_SIZE = 5     # segments in playlist


def _is_drm_available() -> bool:
    """Check if DRM streaming mode can be used."""
    return (
        Path(DRM_RAYLIB_PATH, "raylib").is_dir() and
        Path(OPENPILOT_DIR, UI_SCRIPT).is_file()
    )


def _is_weston_available() -> bool:
    """Check if Weston streaming mode can be used."""
    return all(os.path.isfile(p) for p in [
        WESTON_PATCHED, STREAM_CAPTURE, UI_BIN, REPLAY_BIN,
    ])


def is_available() -> bool:
    """Check if any streaming mode is available."""
    return _is_drm_available() or _is_weston_available()


def _start_selfdrive_publisher(stop_event: threading.Event):
    """Publish fake selfdriveState to prevent 'System Unresponsive' alert.

    On C3 (non-PC), the UI checks for stale selfdriveState and shows a
    full-screen red alert after 5s. Publishing at 20Hz keeps it fresh.
    """
    if OPENPILOT_DIR not in sys.path:
        sys.path.insert(0, OPENPILOT_DIR)
    import cereal.messaging as messaging
    pm = messaging.PubMaster(['selfdriveState'])
    while not stop_event.is_set():
        msg = messaging.new_message('selfdriveState')
        msg.valid = True
        ss = msg.selfdriveState
        ss.enabled = False
        ss.active = False
        ss.alertSize = 0  # NONE - no alert overlay
        pm.send('selfdriveState', msg)
        stop_event.wait(0.05)  # 20Hz


def _stop_production_ui():
    """Stop the production openpilot UI to free up DRM master."""
    logger.info("Stopping production UI...")
    subprocess.run(["pkill", "-f", "selfdrive.ui.ui"], capture_output=True)
    time.sleep(1)


def _restart_production_ui():
    """Restart the production UI after HUD streaming.

    Manager uses multiprocessing.Process for UI - externally killed processes
    leave a stale self.proc object, so manager can't detect the death and won't
    restart it (UI has no watchdog_max_dt). We start the UI ourselves.
    """
    logger.info("Restarting production UI...")
    subprocess.run(["pkill", "-f", "selfdrive.ui.ui"], capture_output=True)
    time.sleep(1)

    ui_env = os.environ.copy()
    ui_env["PYTHONPATH"] = f"{OPENPILOT_DIR}:{DRM_RAYLIB_PATH}"
    ui_env["PATH"] = "/usr/local/venv/bin:/usr/local/bin:/usr/bin:/bin"
    ui_env["HOME"] = os.environ.get("HOME", "/root")

    try:
        subprocess.Popen(
            [PYTHON_BIN, "-m", "selfdrive.ui.ui"],
            cwd=OPENPILOT_DIR,
            env=ui_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Production UI started")
    except Exception as e:
        logger.error("Failed to start production UI: %s", e)


def _find_rlog(data_dir: str, local_id: str) -> str | None:
    """Find rlog in segment 0."""
    seg0 = os.path.join(data_dir, f"{local_id}--0")
    for fname in ["rlog.zst", "rlog"]:
        path = os.path.join(seg0, fname)
        if os.path.exists(path):
            return path
    return None


class HudStreamManager:
    """Manages the HUD live streaming pipeline. Only one stream at a time."""

    def __init__(self):
        self._procs: list = []
        self._weston_proc = None
        self._status = "idle"   # idle | starting | streaming | error | stopping
        self._error = None
        self._route_name = None
        self._symlink_dir = None
        self._prefix = None
        self._lock = asyncio.Lock()
        self._mode = "drm" if _is_drm_available() else "weston"
        self._sd_stop = None  # selfdriveState publisher stop event (DRM mode)

    @property
    def status(self) -> dict:
        result = {"status": self._status, "mode": self._mode}
        if self._route_name:
            result["route"] = self._route_name
        if self._error:
            result["error"] = self._error
        # Check pipeline health when streaming
        if self._status == "streaming" and not self._pipeline_alive():
            self._status = "error"
            self._error = "Pipeline process died"
            result["status"] = "error"
            result["error"] = self._error
        return result

    @property
    def is_active(self) -> bool:
        return self._status in ("starting", "streaming")

    def _pipeline_alive(self) -> bool:
        """Check if key processes are still running."""
        if not self._procs:
            return False
        if self._mode == "drm":
            # In DRM mode, the UI process is the pipeline (it runs ffmpeg internally)
            # Check the last proc (UI)
            return self._procs[-1].poll() is None
        else:
            # Weston mode: last two procs are capture and ffmpeg
            return all(p.poll() is None for p in self._procs[-2:] if p is not None)

    async def start(self, route_name: str, local_id: str, dongle_id: str,
                    data_dir: str, start_sec: float = 0, max_seg: int = -1):
        """Start the HUD streaming pipeline. Returns immediately; frontend polls status."""
        async with self._lock:
            if self.is_active:
                if self._route_name == route_name:
                    return  # Already streaming this route
                await self._stop_internal()

            self._route_name = route_name
            self._status = "starting"
            self._error = None

        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None, self._start_sync, route_name, local_id, dongle_id,
            data_dir, start_sec, max_seg,
        )

    def _start_sync(self, route_name, local_id, dongle_id, data_dir, start_sec, max_seg):
        """Route to the appropriate mode's startup."""
        if self._mode == "drm":
            self._start_sync_drm(route_name, local_id, dongle_id, data_dir, start_sec, max_seg)
        else:
            self._start_sync_weston(route_name, local_id, dongle_id, data_dir, start_sec, max_seg)

    def _start_sync_drm(self, route_name, local_id, dongle_id, data_dir, start_sec, max_seg):
        """DRM mode: replay + UI(RECORD_HLS) -> HLS output."""
        try:
            self._prefix = f"stream_{os.getpid()}_{int(time.time())}"

            # Prepare HLS output directory
            if HLS_DIR.exists():
                shutil.rmtree(HLS_DIR)
            HLS_DIR.mkdir(parents=True)

            # Find segments
            if max_seg < 0:
                max_seg = _find_max_segment(data_dir, local_id)
            if max_seg < 0:
                self._status = "error"
                self._error = "No segments found"
                return

            # Create symlinks for replay
            self._symlink_dir = _create_symlink_dir(
                data_dir, local_id, dongle_id, max_seg + 1)

            # Populate CarParams from rlog so UI knows the car platform
            rlog_path = _find_rlog(data_dir, local_id)
            if not rlog_path:
                self._status = "error"
                self._error = "No rlog found in segment 0"
                return

            if OPENPILOT_DIR not in sys.path:
                sys.path.insert(0, OPENPILOT_DIR)
            from common.prefix import OpenpilotPrefix

            # Create isolated prefix and populate CarParams
            self._op_prefix = OpenpilotPrefix(self._prefix, shared_download_cache=True)
            self._op_prefix.__enter__()

            from tools.lib.logreader import LogReader
            from tools.clip.run import populate_car_params
            lr = LogReader(rlog_path)
            populate_car_params(lr)

            # Copy user params (IsMetric etc.) into isolated prefix
            _copy_user_params(self._prefix)

            # Start selfdriveState publisher to prevent "System Unresponsive"
            self._sd_stop = threading.Event()
            sd_thread = threading.Thread(
                target=_start_selfdrive_publisher,
                args=(self._sd_stop,), daemon=True)
            sd_thread.start()

            # Stop production UI to free DRM master
            _stop_production_ui()

            # Start replay at 0.2x speed (loops by default for continuous streaming)
            canonical = f"{dongle_id}|{local_id}"
            replay_env = os.environ.copy()
            replay_env["TERM"] = "xterm"
            replay_env["OPENPILOT_PREFIX"] = self._prefix

            replay_proc = subprocess.Popen(
                [REPLAY_BIN, "-c", "1",
                 "-s", str(int(max(start_sec, 0))),
                 "-x", str(DRM_REPLAY_SPEED),
                 "--data_dir", self._symlink_dir,
                 "--prefix", self._prefix,
                 canonical],
                env=replay_env,
                stdout=subprocess.DEVNULL,
                stderr=open("/tmp/hud_replay.log", "w"),
            )
            self._procs.append(replay_proc)

            # Wait for replay to create cereal sockets
            shm_dir = f"/dev/shm/{self._prefix}"
            deadline = time.monotonic() + 15
            replay_ready = False
            while time.monotonic() < deadline:
                if replay_proc.poll() is not None:
                    break
                if (os.path.isdir(shm_dir) and
                        os.path.exists(os.path.join(shm_dir, "modelV2"))):
                    replay_ready = True
                    break
                time.sleep(0.3)

            if not replay_ready:
                self._status = "error"
                self._error = "Replay failed to start"
                self._cleanup_sync()
                return

            logger.info("Replay ready for %s (DRM mode, %.1fx speed)", route_name, DRM_REPLAY_SPEED)

            # Start UI with DRM RECORD + HLS env vars
            hls_output = str(HLS_DIR / "stream.m3u8")
            ui_env = {
                "RECORD": "1",
                "RECORD_HLS": "1",
                "RECORD_OUTPUT": hls_output,
                "RECORD_HLS_TIME": str(HLS_TIME),
                "RECORD_HLS_LIST_SIZE": str(HLS_LIST_SIZE),
                "FPS": str(DRM_RECORD_FPS),
                "BIG": "1",  # Force 2160x1080 layout
                "PYTHONPATH": f"{OPENPILOT_DIR}:{DRM_RAYLIB_PATH}",
                "OPENPILOT_PREFIX": self._prefix,
                "PATH": "/usr/local/venv/bin:/usr/local/bin:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/root"),
                "TERM": "xterm",
            }

            # Skip config_realtime_process (SCHED_FIFO requires root)
            ui_cmd = [
                PYTHON_BIN, "-c",
                "import os; os.sched_setscheduler = lambda *a, **kw: None; "
                "exec(open('selfdrive/ui/ui.py').read())",
            ]

            ui_proc = subprocess.Popen(
                ui_cmd,
                cwd=OPENPILOT_DIR,
                env=ui_env,
                stdout=open("/tmp/hud_ui_drm.log", "w"),
                stderr=subprocess.STDOUT,
            )
            self._procs.append(ui_proc)

            # Wait for first HLS segment to appear
            m3u8_path = HLS_DIR / "stream.m3u8"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if m3u8_path.exists() and m3u8_path.stat().st_size > 20:
                    break
                if ui_proc.poll() is not None:
                    break
                time.sleep(0.5)

            if m3u8_path.exists() and m3u8_path.stat().st_size > 20:
                self._status = "streaming"
                logger.info("HLS streaming active for %s (DRM mode)", route_name)
            else:
                self._status = "error"
                self._error = "HLS output not generated"
                # Check UI log for hints
                try:
                    log = Path("/tmp/hud_ui_drm.log").read_text()[-500:]
                    if log.strip():
                        self._error += f" (UI: {log.strip()[-200:]})"
                except Exception:
                    pass
                self._cleanup_sync()

        except Exception as e:
            logger.exception("DRM stream start failed")
            self._status = "error"
            self._error = str(e)
            self._cleanup_sync()

    def _start_sync_weston(self, route_name, local_id, dongle_id, data_dir, start_sec, max_seg):
        """Weston mode: patched_weston + stream_capture -> ffmpeg -> HLS."""
        try:
            self._prefix = f"stream_{os.getpid()}_{int(time.time())}"

            # Prepare HLS output directory
            if HLS_DIR.exists():
                shutil.rmtree(HLS_DIR)
            HLS_DIR.mkdir(parents=True)

            # Find segments
            if max_seg < 0:
                max_seg = _find_max_segment(data_dir, local_id)
            if max_seg < 0:
                self._status = "error"
                self._error = "No segments found"
                return

            # Create symlinks for replay
            self._symlink_dir = _create_symlink_dir(
                data_dir, local_id, dongle_id, max_seg + 1)

            # Switch to patched weston
            self._weston_proc = _switch_to_patched_weston()
            if self._weston_proc is None:
                self._status = "error"
                self._error = "Compositor failed to start"
                _restore_stock_weston()
                return

            # Copy user params (IsMetric etc.) into isolated prefix
            _copy_user_params(self._prefix)

            # Environment for child processes
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = XDG_RUNTIME_DIR
            env["WAYLAND_DISPLAY"] = WAYLAND_DISPLAY
            env["OPENPILOT_PREFIX"] = self._prefix
            env["TERM"] = "xterm"

            # Start replay (loops by default for continuous streaming)
            canonical = f"{dongle_id}|{local_id}"
            replay_proc = subprocess.Popen(
                [REPLAY_BIN, "--qcam", "-c", "1",
                 "-s", str(int(max(start_sec, 0))),
                 "--data_dir", self._symlink_dir,
                 "--prefix", self._prefix,
                 canonical],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=open("/tmp/hud_replay.log", "w"),
            )
            self._procs.append(replay_proc)

            # Wait for replay to create cereal sockets
            shm_dir = f"/dev/shm/{self._prefix}"
            deadline = time.monotonic() + 15
            replay_ready = False
            while time.monotonic() < deadline:
                if replay_proc.poll() is not None:
                    break
                if (os.path.isdir(shm_dir) and
                        os.path.exists(os.path.join(shm_dir, "modelV2"))):
                    replay_ready = True
                    break
                time.sleep(0.3)

            if not replay_ready:
                self._status = "error"
                self._error = "Replay failed to start"
                self._cleanup_sync()
                return

            logger.info("Replay ready for %s", route_name)

            # Start openpilot UI as wayland client
            ui_proc = subprocess.Popen(
                [UI_BIN, "-platform", "wayland-egl"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=open("/tmp/hud_ui.log", "w"),
            )
            self._procs.append(ui_proc)
            time.sleep(WARMUP_SEC)

            if ui_proc.poll() is not None:
                self._status = "error"
                self._error = f"UI exited ({ui_proc.returncode})"
                self._cleanup_sync()
                return

            logger.info("UI running, starting HLS capture")

            # stream_capture -> ffmpeg -> HLS pipeline
            cap_env = os.environ.copy()
            cap_env["XDG_RUNTIME_DIR"] = XDG_RUNTIME_DIR
            cap_env["WAYLAND_DISPLAY"] = WAYLAND_DISPLAY

            capture_proc = subprocess.Popen(
                ["sudo", "-E", STREAM_CAPTURE,
                 "-w", str(CAPTURE_WIDTH), "-h", str(CAPTURE_HEIGHT),
                 "-r", str(CAPTURE_FPS), "-v"],
                env=cap_env,
                stdout=subprocess.PIPE,
                stderr=open("/tmp/hud_capture.log", "w"),
            )
            self._procs.append(capture_proc)

            # GOP size = fps * hls_time -> keyframe every segment boundary
            gop = CAPTURE_FPS * HLS_TIME

            ffmpeg_proc = subprocess.Popen(
                ["ffmpeg", "-y",
                 "-f", "rawvideo", "-pixel_format", "bgra",
                 "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
                 "-framerate", str(CAPTURE_FPS),
                 "-i", "pipe:0",
                 "-vf", "transpose=2",
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-tune", "zerolatency",
                 "-g", str(gop), "-keyint_min", str(gop),
                 "-crf", "23",
                 "-pix_fmt", "yuv420p",
                 "-f", "hls",
                 "-hls_time", str(HLS_TIME),
                 "-hls_list_size", str(HLS_LIST_SIZE),
                 "-hls_flags", "delete_segments",
                 "-hls_segment_filename", str(HLS_DIR / "seg_%03d.ts"),
                 str(HLS_DIR / "stream.m3u8")],
                stdin=capture_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=open("/tmp/hud_ffmpeg.log", "w"),
            )
            self._procs.append(ffmpeg_proc)
            capture_proc.stdout.close()  # Allow SIGPIPE propagation

            # Wait for first HLS segment to appear
            m3u8_path = HLS_DIR / "stream.m3u8"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if m3u8_path.exists() and m3u8_path.stat().st_size > 20:
                    break
                if capture_proc.poll() is not None or ffmpeg_proc.poll() is not None:
                    break
                time.sleep(0.5)

            if m3u8_path.exists() and m3u8_path.stat().st_size > 20:
                self._status = "streaming"
                logger.info("HLS streaming active for %s", route_name)
            else:
                self._status = "error"
                self._error = "HLS output not generated"
                self._cleanup_sync()

        except Exception as e:
            logger.exception("Stream start failed")
            self._status = "error"
            self._error = str(e)
            self._cleanup_sync()

    async def stop(self):
        """Stop the streaming pipeline and restore display."""
        async with self._lock:
            await self._stop_internal()

    async def _stop_internal(self):
        if self._status == "idle":
            return
        self._status = "stopping"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._cleanup_sync)
        self._status = "idle"
        self._route_name = None
        self._error = None

    def _cleanup_sync(self):
        """Kill all child processes, restore display, clean up temp files."""
        # Stop selfdriveState publisher (DRM mode)
        if self._sd_stop:
            self._sd_stop.set()
            self._sd_stop = None

        # Terminate child processes
        for p in self._procs:
            try:
                if p.poll() is None:
                    p.send_signal(signal.SIGTERM)
            except Exception:
                pass
        time.sleep(1)
        for p in self._procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass
        self._procs.clear()

        # Name-based fallback for orphan replay processes
        subprocess.run(["pkill", "-KILL", "-f", "tools/replay/replay"],
                       capture_output=True)

        if self._mode == "drm":
            # Exit OpenpilotPrefix context if active
            if hasattr(self, '_op_prefix') and self._op_prefix:
                try:
                    self._op_prefix.__exit__(None, None, None)
                except Exception:
                    pass
                self._op_prefix = None

            # Restart production UI (same approach as render_clip_drm.py)
            _restart_production_ui()
        else:
            # Weston mode: restore stock compositor
            _restore_stock_weston()

        # Clean up symlink directory
        if self._symlink_dir:
            shutil.rmtree(self._symlink_dir, ignore_errors=True)
            self._symlink_dir = None

        # Clean up shared memory and params
        if self._prefix:
            shutil.rmtree(f"/dev/shm/{self._prefix}", ignore_errors=True)
            shutil.rmtree(f"/data/params/{self._prefix}", ignore_errors=True)
            self._prefix = None


# --- Module-level helpers (shared with render_clip.py logic) ----------

def _find_max_segment(data_dir: str, local_id: str) -> int:
    max_seg = -1
    data_path = Path(data_dir)
    for entry in data_path.iterdir():
        name = entry.name
        if name.startswith(f"{local_id}--") and entry.is_dir():
            try:
                seg = int(name.split("--")[-1])
                max_seg = max(max_seg, seg)
            except ValueError:
                pass
    return max_seg


def _create_symlink_dir(data_dir: str, local_id: str, dongle_id: str,
                        num_segments: int) -> str:
    tmpdir = tempfile.mkdtemp(prefix="hud_stream_")
    for seg in range(num_segments):
        src = os.path.join(data_dir, f"{local_id}--{seg}")
        if not os.path.isdir(src):
            continue
        dst = os.path.join(tmpdir, f"{dongle_id}|{local_id}--{seg}")
        os.symlink(src, dst)
    return tmpdir


def _copy_user_params(prefix: str):
    """Copy key user params (IsMetric, etc.) into the isolated prefix's params dir.

    Openpilot Params path: /data/params/{OPENPILOT_PREFIX}/
    Default prefix is 'd', so user params live at /data/params/d/.
    """
    src_params = Path("/data/params/d")
    dst_params = Path(f"/data/params/{prefix}")
    dst_params.mkdir(parents=True, exist_ok=True)

    # Params that affect UI display
    copied = []
    for key in ("IsMetric", "LanguageSetting"):
        src = src_params / key
        if src.exists():
            try:
                (dst_params / key).write_bytes(src.read_bytes())
                copied.append(key)
            except Exception:
                pass
    if copied:
        logger.info("Copied params to /%s: %s", prefix, ", ".join(copied))


def _switch_to_patched_weston():
    logger.info("Stopping stock weston...")
    subprocess.run(["sudo", "pkill", "-f", "weston"], capture_output=True)
    time.sleep(2)

    os.makedirs(XDG_RUNTIME_DIR, exist_ok=True)

    logger.info("Starting patched weston...")
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = XDG_RUNTIME_DIR
    proc = subprocess.Popen(
        ["sudo", "-E", WESTON_PATCHED, "--idle-time=0", "--tty=1",
         f"--config={WESTON_CONFIG}"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=open("/tmp/hud_weston.log", "w"),
    )
    time.sleep(3)

    if proc.poll() is not None:
        logger.error("Patched weston failed (exit %s)", proc.returncode)
        return None

    socket_path = os.path.join(XDG_RUNTIME_DIR, WAYLAND_DISPLAY)
    subprocess.run(["sudo", "chmod", "777", socket_path], capture_output=True)

    logger.info("Patched weston running (PID %d)", proc.pid)
    return proc


def _restore_stock_weston():
    logger.info("Restoring stock weston...")
    subprocess.run(["sudo", "pkill", "-f", "weston_patched"], capture_output=True)
    subprocess.run(["sudo", "pkill", "-f", "weston"], capture_output=True)
    time.sleep(2)

    os.makedirs(XDG_RUNTIME_DIR, exist_ok=True)

    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = XDG_RUNTIME_DIR
    proc = subprocess.Popen(
        ["sudo", "-E", WESTON_STOCK, "--idle-time=0", "--tty=1",
         f"--config={WESTON_CONFIG}"],
        env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Wait for wayland socket to appear (up to 5s)
    socket_path = os.path.join(XDG_RUNTIME_DIR, WAYLAND_DISPLAY)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            logger.error("Stock weston exited immediately (code %s)", proc.returncode)
            break
        if os.path.exists(socket_path):
            break
        time.sleep(0.3)

    # Open socket permissions so openpilot UI can connect
    if os.path.exists(socket_path):
        subprocess.run(["sudo", "chmod", "777", socket_path], capture_output=True)
        logger.info("Stock weston restored (socket ready)")
    else:
        logger.error("Stock weston socket not found at %s", socket_path)

    # Kill any stale openpilot UI so the manager restarts it on the new weston
    subprocess.run(["pkill", "-f", "selfdrive/ui/ui"], capture_output=True)
    logger.info("Signaled openpilot UI restart")
