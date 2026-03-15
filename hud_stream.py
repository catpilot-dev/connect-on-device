"""HUD live streaming pipeline manager for C3.

Two streaming modes:
  HLS:  replay(1x) → UI(RECORD_HLS, libx264) → ffmpeg(HLS) → browser(hls.js)
  WS:   replay(1x) → UI(RECORD_FRAG_MP4, h264_v4l2m2m) → FIFO → WebSocket → browser(MSE)

WebSocket mode uses hardware H264 encoding (h264_v4l2m2m) and fragmented MP4 output,
streamed over WebSocket to the browser's Media Source Extensions API.  This avoids the
HLS segment accumulation delay and leverages the Snapdragon 845 hardware encoder.

Only one stream can be active at a time since DRM requires exclusive display access.
"""

import asyncio
import logging
import os
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger("hud_stream")

from config import (OPENPILOT_DIR, REPLAY_BIN, PYTHON_BIN, PARAMS_DIR,
                     PARAMS_BASE, COD_HLS_TMP_DIR)
UI_SCRIPT = "selfdrive/ui/ui.py"
DRM_REPLAY_SPEED = 1.0   # Real-time replay
DRM_UI_FPS = 5           # UI renders at 5fps (C3 GPU limited to ~2.4fps)
DRM_RECORD_SKIP = 0      # Capture every frame

# HLS output
HLS_DIR = Path("/tmp/hud_live")
HLS_TIME = 2          # seconds per segment
HLS_LIST_SIZE = 5     # segments in playlist (~10s buffer)

# WebSocket fMP4 output
WS_FIFO_PATH = "/tmp/hud_ws.fifo"
WS_CHUNK_SIZE = 32 * 1024   # 32KB chunks for WebSocket frames
WS_HW_CODEC = "h264_v4l2m2m"  # Snapdragon 845 hardware H264 encoder
WS_SW_CODEC = "libx264"       # Fallback software encoder


def _is_drm_available() -> bool:
    """Check if DRM streaming mode can be used."""
    try:
        import raylib
        has_raylib = True
    except ImportError:
        has_raylib = False
    return has_raylib and Path(OPENPILOT_DIR, UI_SCRIPT).is_file()


def is_available() -> bool:
    """Check if streaming is available."""
    return _is_drm_available()



MANAGER_CMD = [PYTHON_BIN, "./manager.py"]
MANAGER_CWD = os.path.join(OPENPILOT_DIR, "system/manager")


def _stop_manager():
    """Stop openpilot manager (and all its children including UI) to free DRM master."""
    logger.info("Stopping openpilot manager...")
    # Kill the manager process tree — this stops UI, pandad, plugind, etc.
    subprocess.run(["pkill", "-f", "manager.py"], capture_output=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        r = subprocess.run(["pgrep", "-f", "manager.py"], capture_output=True)
        if r.returncode != 0:
            break
        time.sleep(0.3)
    else:
        subprocess.run(["pkill", "-9", "-f", "manager.py"], capture_output=True)
        time.sleep(0.5)
    # Also ensure UI is dead (child may linger)
    subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"], capture_output=True)
    time.sleep(1)
    logger.info("Manager stopped")


def _start_manager():
    """Restart openpilot manager after HUD operations."""
    logger.info("Restarting openpilot manager...")
    # Kill any leftover UI/replay processes
    subprocess.run(["pkill", "-f", "selfdrive.ui.ui"], capture_output=True)
    time.sleep(0.5)

    env = os.environ.copy()
    env["PYTHONPATH"] = OPENPILOT_DIR
    env["PATH"] = "/usr/local/venv/bin:/usr/local/bin:/usr/bin:/bin"
    env["HOME"] = os.environ.get("HOME", "/root")

    try:
        subprocess.Popen(
            MANAGER_CMD,
            cwd=MANAGER_CWD,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Manager started")
    except Exception as e:
        logger.error("Failed to start manager: %s", e)


def _find_rlog(data_dir: str, local_id: str) -> str | None:
    """Find rlog in segment 0."""
    seg0 = os.path.join(data_dir, f"{local_id}--0")
    for fname in ["rlog.zst", "rlog"]:
        path = os.path.join(seg0, fname)
        if os.path.exists(path):
            return path
    return None


def _hw_encoder_available() -> bool:
    """Check if h264_v4l2m2m hardware encoder actually works (not just listed).

    The encoder may appear in ffmpeg -encoders but fail to open the V4L2 device
    (e.g. kernel 4.9 on Snapdragon 845 has VIDC but not full M2M support).
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "nullsrc=s=320x240:d=0.1:r=1",
             "-vf", "format=yuv420p", "-c:v", "h264_v4l2m2m",
             "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


class HudStreamManager:
    """Manages the HUD live streaming pipeline. Only one stream at a time."""

    def __init__(self):
        self._procs: list = []
        self._status = "idle"   # idle | starting | streaming | error | stopping
        self._error = None
        self._route_name = None
        self._symlink_dir = None
        self._prefix = None
        self._lock = asyncio.Lock()
        self._mode = None       # "hls" | "ws"
        # WebSocket streaming state (thread-safe queue for cross-thread FIFO→WS delivery)
        self._ws_queue: queue.Queue | None = None
        self._fifo_thread: threading.Thread | None = None
        self._fifo_stop = threading.Event()

    @property
    def status(self) -> dict:
        result = {"status": self._status, "mode": self._mode or "none"}
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
        # The UI process is the pipeline
        return self._procs[-1].poll() is None

    async def start(self, route_name: str, local_id: str, dongle_id: str,
                    data_dir: str, start_sec: float = 0, max_seg: int = -1,
                    hd: bool = False, mode: str = "ws"):
        """Start the HUD streaming pipeline.

        mode="ws": WebSocket + fMP4 (preferred, uses HW encoder)
        mode="hls": Legacy HLS mode (fallback)
        """
        async with self._lock:
            if self.is_active:
                if self._route_name == route_name:
                    return  # Already streaming this route
                await self._stop_internal()

            self._route_name = route_name
            self._status = "starting"
            self._error = None
            self._mode = mode

        loop = asyncio.get_event_loop()
        if mode == "ws":
            self._ws_queue = queue.Queue(maxsize=200)
            loop.run_in_executor(
                None, self._start_sync_ws, route_name, local_id, dongle_id,
                data_dir, start_sec, max_seg, hd,
            )
        else:
            loop.run_in_executor(
                None, self._start_sync_drm, route_name, local_id, dongle_id,
                data_dir, start_sec, max_seg, hd,
            )

    def _setup_replay(self, route_name, local_id, dongle_id, data_dir, start_sec, max_seg, hd):
        """Common setup: prefix, symlinks, CarParams, replay process.

        Returns True on success, False on error (sets self._status/self._error).
        """
        self._prefix = f"stream_{os.getpid()}_{int(time.time())}"

        # Find segments
        if max_seg < 0:
            max_seg = _find_max_segment(data_dir, local_id)
        if max_seg < 0:
            self._status = "error"
            self._error = "No segments found"
            return False

        # Create symlinks for replay
        self._symlink_dir = _create_symlink_dir(
            data_dir, local_id, dongle_id, max_seg + 1)

        # Populate CarParams from rlog
        rlog_path = _find_rlog(data_dir, local_id)
        if not rlog_path:
            self._status = "error"
            self._error = "No rlog found in segment 0"
            return False

        if OPENPILOT_DIR not in sys.path:
            sys.path.insert(0, OPENPILOT_DIR)
        from common.prefix import OpenpilotPrefix

        self._op_prefix = OpenpilotPrefix(self._prefix, shared_download_cache=True)
        self._op_prefix.__enter__()

        from tools.lib.logreader import LogReader
        from tools.clip.run import populate_car_params
        lr = LogReader(rlog_path)
        populate_car_params(lr)

        _copy_user_params(self._prefix)

        os.makedirs(f"/dev/shm/msgq_{self._prefix}", exist_ok=True)

        # Stop production UI to free DRM master
        _stop_manager()

        # Start replay at 1x speed
        canonical = f"{dongle_id}|{local_id}"
        replay_env = os.environ.copy()
        replay_env["TERM"] = "xterm"
        replay_env["OPENPILOT_PREFIX"] = self._prefix

        replay_cmd = [
            REPLAY_BIN, "-c", "1",
            "--no-hw-decoder",
            "-s", str(int(max(start_sec, 0))),
            "-x", str(DRM_REPLAY_SPEED),
            "--data_dir", self._symlink_dir,
            "--prefix", self._prefix,
            canonical,
        ]
        if not hd:
            replay_cmd.insert(3, "--qcam")

        replay_proc = subprocess.Popen(
            replay_cmd,
            env=replay_env,
            stdout=subprocess.DEVNULL,
            stderr=open("/tmp/hud_replay.log", "w"),
        )
        self._procs.append(replay_proc)

        # Wait for replay to create cereal sockets
        msgq_shm_dir = f"/dev/shm/msgq_{self._prefix}"
        deadline = time.monotonic() + 15
        replay_ready = False
        while time.monotonic() < deadline:
            if replay_proc.poll() is not None:
                break
            if (os.path.isdir(msgq_shm_dir) and
                    os.path.exists(os.path.join(msgq_shm_dir, "modelV2"))):
                replay_ready = True
                break
            time.sleep(0.3)

        if not replay_ready:
            self._status = "error"
            self._error = "Replay failed to start"
            self._cleanup_sync()
            return False

        cam_mode = "fcamera+SW" if hd else "qcam+SW"
        logger.info("Replay ready for %s (%s, %.1fx)", route_name, cam_mode, DRM_REPLAY_SPEED)
        return True

    def _start_sync_ws(self, route_name, local_id, dongle_id, data_dir, start_sec, max_seg, hd=False):
        """WebSocket mode: replay(1x) → UI(RECORD_FRAG_MP4) → FIFO → WebSocket."""
        try:
            if not self._setup_replay(route_name, local_id, dongle_id, data_dir,
                                       start_sec, max_seg, hd):
                return

            # Determine codec — try HW encoder, fall back to SW
            codec = WS_HW_CODEC if _hw_encoder_available() else WS_SW_CODEC
            logger.info("WebSocket stream codec: %s", codec)

            # Create FIFO for fMP4 output
            fifo = WS_FIFO_PATH
            if os.path.exists(fifo):
                os.unlink(fifo)
            os.mkfifo(fifo)

            record_skip = 9 if hd else DRM_RECORD_SKIP
            ui_fps = 20 if hd else DRM_UI_FPS

            # Downscale to 1080x540 for SW encoding — 4x less pixels to encode
            vf_scale = "scale=1080:540" if codec == WS_SW_CODEC else ""

            ui_env = {
                "RECORD": "1",
                "RECORD_FRAG_MP4": "1",
                "RECORD_CODEC": codec,
                "RECORD_VF": vf_scale,
                "RECORD_OUTPUT": fifo,
                "FPS": str(ui_fps),
                "RECORD_SKIP": str(record_skip),
                "BIG": "1",
                "PYTHONPATH": OPENPILOT_DIR,
                "OPENPILOT_PREFIX": self._prefix,
                "PATH": "/usr/local/venv/bin:/usr/local/bin:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/root"),
                "TERM": "xterm",
            }

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

            # Start FIFO reader thread — opens the FIFO (blocks until ffmpeg opens write end)
            self._fifo_stop.clear()
            self._fifo_thread = threading.Thread(
                target=self._fifo_reader, args=(fifo,), daemon=True)
            self._fifo_thread.start()

            # Wait for first data on the queue (indicates ffmpeg opened the FIFO and is writing)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self._ws_queue and not self._ws_queue.empty():
                    break
                if ui_proc.poll() is not None:
                    break
                time.sleep(0.5)

            if self._ws_queue and not self._ws_queue.empty():
                self._status = "streaming"
                quality = "HD" if hd else "SD"
                logger.info("WebSocket streaming active for %s (%s, codec=%s)",
                            route_name, quality, codec)
            else:
                self._status = "error"
                self._error = "fMP4 output not generated"
                try:
                    log = Path("/tmp/hud_ui_drm.log").read_text()[-500:]
                    if log.strip():
                        self._error += f" (UI: {log.strip()[-200:]})"
                except Exception:
                    pass
                self._cleanup_sync()

        except Exception as e:
            logger.exception("WebSocket stream start failed")
            self._status = "error"
            self._error = str(e)
            self._cleanup_sync()

    def _fifo_reader(self, fifo_path: str):
        """Thread: reads fMP4 chunks from FIFO and enqueues for WebSocket delivery."""
        try:
            logger.info("FIFO reader opening %s", fifo_path)
            with open(fifo_path, "rb") as f:
                logger.info("FIFO reader connected")
                while not self._fifo_stop.is_set():
                    chunk = f.read(WS_CHUNK_SIZE)
                    if not chunk:
                        break
                    if self._ws_queue is not None:
                        try:
                            self._ws_queue.put_nowait(chunk)
                        except queue.Full:
                            # Drop oldest to prevent backpressure stall
                            try:
                                self._ws_queue.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                self._ws_queue.put_nowait(chunk)
                            except queue.Full:
                                pass
            logger.info("FIFO reader finished (EOF)")
        except Exception as e:
            if not self._fifo_stop.is_set():
                logger.error("FIFO reader error: %s", e)

    async def ws_get_chunk(self, timeout: float = 5.0) -> bytes | None:
        """Get next fMP4 chunk for WebSocket delivery. Returns None on timeout/closed."""
        if not self._ws_queue:
            return None
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._ws_queue.get, True, timeout),
                timeout=timeout + 1,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError, queue.Empty):
            return None

    def _start_sync_drm(self, route_name, local_id, dongle_id, data_dir, start_sec, max_seg, hd=False):
        """HLS mode: replay(1x) → UI(RECORD_HLS) → HLS output."""
        try:
            # Prepare HLS output directory
            if HLS_DIR.exists():
                shutil.rmtree(HLS_DIR)
            HLS_DIR.mkdir(parents=True)

            if not self._setup_replay(route_name, local_id, dongle_id, data_dir,
                                       start_sec, max_seg, hd):
                return

            record_skip = 9 if hd else DRM_RECORD_SKIP
            ui_fps = 20 if hd else DRM_UI_FPS
            hls_output = str(HLS_DIR / "stream.m3u8")
            ui_env = {
                "RECORD": "1",
                "RECORD_HLS": "1",
                "RECORD_OUTPUT": hls_output,
                "RECORD_HLS_TIME": str(HLS_TIME),
                "RECORD_HLS_LIST_SIZE": str(HLS_LIST_SIZE),
                "FPS": str(ui_fps),
                "RECORD_SKIP": str(record_skip),
                "BIG": "1",
                "PYTHONPATH": OPENPILOT_DIR,
                "OPENPILOT_PREFIX": self._prefix,
                "PATH": "/usr/local/venv/bin:/usr/local/bin:/usr/bin:/bin",
                "HOME": os.environ.get("HOME", "/root"),
                "TERM": "xterm",
            }

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
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if m3u8_path.exists() and m3u8_path.stat().st_size > 20:
                    break
                if ui_proc.poll() is not None:
                    break
                time.sleep(0.5)

            if m3u8_path.exists() and m3u8_path.stat().st_size > 20:
                self._status = "streaming"
                quality = "HD" if hd else "SD"
                logger.info("HLS streaming active for %s (%s, skip=%d, 1x)",
                            route_name, quality, record_skip)
            else:
                self._status = "error"
                self._error = "HLS output not generated"
                try:
                    log = Path("/tmp/hud_ui_drm.log").read_text()[-500:]
                    if log.strip():
                        self._error += f" (UI: {log.strip()[-200:]})"
                except Exception:
                    pass
                self._cleanup_sync()

        except Exception as e:
            logger.exception("HLS stream start failed")
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
        self._mode = None

    def _cleanup_sync(self):
        """Kill all child processes, restore display, clean up temp files."""
        # Stop FIFO reader thread
        self._fifo_stop.set()
        if self._fifo_thread and self._fifo_thread.is_alive():
            self._fifo_thread.join(timeout=3)
        self._fifo_thread = None
        self._ws_queue = None

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

        # Exit OpenpilotPrefix context if active
        if hasattr(self, '_op_prefix') and self._op_prefix:
            try:
                self._op_prefix.__exit__(None, None, None)
            except Exception:
                pass
            self._op_prefix = None

        # Restart production UI
        _start_manager()

        # Clean up symlink directory
        if self._symlink_dir:
            shutil.rmtree(self._symlink_dir, ignore_errors=True)
            self._symlink_dir = None

        # Clean up HLS output
        if HLS_DIR.exists():
            shutil.rmtree(HLS_DIR, ignore_errors=True)

        # Clean up FIFO
        if os.path.exists(WS_FIFO_PATH):
            try:
                os.unlink(WS_FIFO_PATH)
            except OSError:
                pass

        # Clean up shared memory and params
        if self._prefix:
            shutil.rmtree(f"/dev/shm/{self._prefix}", ignore_errors=True)
            shutil.rmtree(f"/dev/shm/msgq_{self._prefix}", ignore_errors=True)
            shutil.rmtree(f"{PARAMS_BASE}/{self._prefix}", ignore_errors=True)
            self._prefix = None


# --- Module-level helpers ---

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
    """Copy key user params (IsMetric, etc.) into the isolated prefix's params dir."""
    src_params = Path(PARAMS_DIR)
    dst_params = Path(f"{PARAMS_BASE}/{prefix}")
    dst_params.mkdir(parents=True, exist_ok=True)

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
