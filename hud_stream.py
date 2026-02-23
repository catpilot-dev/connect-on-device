"""HUD live streaming pipeline manager for C3.

Manages the full pipeline for streaming openpilot UI to browser via HLS:
  patched_weston → replay + UI(wayland-egl) → stream_capture → ffmpeg → HLS

Only one stream can be active at a time since it requires exclusive compositor access.
The stream swaps the display compositor from stock weston to a patched version with
screenshooter protocol enabled, captures frames, and encodes to HLS segments.

When stopped, the stock weston is restored.
"""

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import tempfile
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

# Capture settings — C3 display is 1080x2160 portrait
CAPTURE_WIDTH = 1080
CAPTURE_HEIGHT = 2160
CAPTURE_FPS = 5       # Achievable rate with GPU readback
WARMUP_SEC = 5
XDG_RUNTIME_DIR = "/var/tmp/weston"
WAYLAND_DISPLAY = "wayland-0"

# HLS output
HLS_DIR = Path("/tmp/hud_live")
HLS_TIME = 2          # seconds per segment
HLS_LIST_SIZE = 5     # segments in playlist


def is_available() -> bool:
    """Check if streaming prerequisites exist on this device."""
    return all(os.path.isfile(p) for p in [
        WESTON_PATCHED, STREAM_CAPTURE, UI_BIN, REPLAY_BIN,
    ])


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

    @property
    def status(self) -> dict:
        result = {"status": self._status}
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
        """Check if capture and ffmpeg processes are still running."""
        if not self._procs:
            return False
        # Last two procs are capture and ffmpeg
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
        """Synchronous pipeline startup (runs in thread pool executor)."""
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

            # stream_capture → ffmpeg → HLS pipeline
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

            # GOP size = fps * hls_time → keyframe every segment boundary
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
        """Stop the streaming pipeline and restore stock weston."""
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
        """Kill all child processes, restore weston, clean up temp files."""
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

        # Restore stock compositor
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


# ─── Module-level helpers (shared with render_clip.py logic) ─────────

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
