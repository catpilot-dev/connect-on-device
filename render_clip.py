#!/usr/bin/env python3
"""Render openpilot UI HUD to MP4 — C3 wayland capture version.

Based on tools/clip/run.py, adapted for C3's wayland compositor + screenshooter.
Single-pass recording at 0.2x replay speed gives 25fps of unique route content.

Pipeline: patched_weston → UI(wayland-egl) → stream_capture → ffmpeg → MP4
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from subprocess import Popen

# Handle SIGTERM gracefully — raises SystemExit so finally blocks run
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))

OPENPILOT_DIR = "/data/openpilot"
if OPENPILOT_DIR not in sys.path:
    sys.path.insert(0, OPENPILOT_DIR)

from common.prefix import OpenpilotPrefix
from tools.lib.logreader import LogReader
from tools.clip.run import populate_car_params, wait_for_frames, check_for_failure

BIN_DIR = "/data/connect_on_device/bin"
UI_BIN = str(Path(OPENPILOT_DIR, "selfdrive/ui/ui").resolve())
REPLAY_BIN = str(Path(OPENPILOT_DIR, "tools/replay/replay").resolve())
WESTON_PATCHED = os.path.join(BIN_DIR, "weston_patched")
STREAM_CAPTURE = os.path.join(BIN_DIR, "stream_capture")
WESTON_STOCK = "/usr/bin/weston"
WESTON_CONFIG = "/usr/comma/weston.ini"

CAPTURE_WIDTH = 1080
CAPTURE_HEIGHT = 2160
CAPTURE_FPS = 5
OUTPUT_FPS = 20
SECONDS_TO_WARM = 2  # match tools/clip/run.py
RECORD_SPEED = 0.2  # 5fps capture / 0.2 = 25fps route content
XDG_RUNTIME_DIR = "/var/tmp/weston"
WAYLAND_DISPLAY = "wayland-0"


def start_selfdrive_publisher(stop_event: threading.Event):
    """Background thread: publish fake selfdriveState to prevent 'System Unresponsive' alert.

    On C3, Hardware::PC() is false (compile-time QCOM2), so the UI checks for stale
    selfdriveState and shows a full-screen red alert after 5s. tools/clip/run.py avoids
    this because it runs on PC where Hardware::PC() is true.
    Publishing at 20Hz keeps the message fresh well within the 5s timeout.
    """
    import cereal.messaging as messaging
    pm = messaging.PubMaster(['selfdriveState'])
    while not stop_event.is_set():
        msg = messaging.new_message('selfdriveState')
        msg.valid = True
        ss = msg.selfdriveState
        ss.enabled = False
        ss.active = False
        ss.alertSize = 0  # NONE — no alert overlay
        pm.send('selfdriveState', msg)
        stop_event.wait(0.05)  # 20Hz


def write_status(status_file: str, data: dict):
    """Atomically write status JSON."""
    tmp = status_file + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.rename(tmp, status_file)
    except Exception:
        pass


def create_symlink_dir(data_dir: str, local_id: str, dongle_id: str, num_segments: int) -> str:
    """Create temp directory with canonical-name symlinks for replay."""
    tmpdir = tempfile.mkdtemp(prefix="hud_render_")
    for seg in range(num_segments):
        src = os.path.join(data_dir, f"{local_id}--{seg}")
        if not os.path.isdir(src):
            continue
        dst = os.path.join(tmpdir, f"{dongle_id}|{local_id}--{seg}")
        os.symlink(src, dst)
    return tmpdir


def find_max_segment(data_dir: str, local_id: str) -> int:
    """Find the highest segment number for a route."""
    max_seg = -1
    for entry in Path(data_dir).iterdir():
        if entry.name.startswith(f"{local_id}--") and entry.is_dir():
            try:
                max_seg = max(max_seg, int(entry.name.split("--")[-1]))
            except ValueError:
                pass
    return max_seg


def find_rlog(data_dir: str, local_id: str) -> str | None:
    """Find rlog in segment 0."""
    seg0 = os.path.join(data_dir, f"{local_id}--0")
    for fname in ["rlog.zst", "rlog"]:
        path = os.path.join(seg0, fname)
        if os.path.exists(path):
            return path
    return None


def switch_to_patched_weston():
    """Stop stock weston and start patched weston with screenshooter."""
    print("Stopping stock weston...", file=sys.stderr)
    subprocess.run(["sudo", "pkill", "-f", "weston"], capture_output=True)
    time.sleep(2)
    os.makedirs(XDG_RUNTIME_DIR, exist_ok=True)

    print("Starting patched weston...", file=sys.stderr)
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = XDG_RUNTIME_DIR
    proc = Popen(
        ["sudo", "-E", WESTON_PATCHED, "--idle-time=0", "--tty=1",
         f"--config={WESTON_CONFIG}"],
        env=env, stdout=subprocess.DEVNULL,
        stderr=open("/tmp/hud_weston.log", "w"),
    )
    time.sleep(3)
    if proc.poll() is not None:
        return None

    subprocess.run(["sudo", "chmod", "777",
                     os.path.join(XDG_RUNTIME_DIR, WAYLAND_DISPLAY)],
                    capture_output=True)
    print(f"Patched weston running (PID {proc.pid})", file=sys.stderr)
    return proc


def restore_stock_weston():
    """Kill patched weston and restart stock."""
    print("Restoring stock weston...", file=sys.stderr)
    subprocess.run(["sudo", "pkill", "-f", "weston"], capture_output=True)
    time.sleep(2)
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = XDG_RUNTIME_DIR
    Popen(["sudo", "-E", WESTON_STOCK, "--idle-time=0", "--tty=1",
           f"--config={WESTON_CONFIG}"],
          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    subprocess.run(["pkill", "-f", "selfdrive/ui/ui"], capture_output=True)
    print("Stock weston restored", file=sys.stderr)


def cleanup_procs(*procs):
    """Terminate all processes cleanly, with name-based fallback."""
    for p in procs:
        try:
            if p and p.poll() is None:
                p.terminate()
        except Exception:
            pass
    time.sleep(1)
    for p in procs:
        try:
            if p and p.poll() is None:
                p.kill()
        except Exception:
            pass
    # Name-based fallback — catches orphans if SIGTERM arrived before
    # Popen handles were assigned (e.g., during wait_for_frames)
    for pattern in ["stream_capture", "tools/replay/replay", "selfdrive/ui/ui"]:
        subprocess.run(["sudo", "pkill", "-KILL", "-f", pattern],
                       capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Render openpilot HUD video (C3)")
    parser.add_argument("--route-name", required=True)
    parser.add_argument("--local-id", required=True)
    parser.add_argument("--dongle-id", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--scale", type=str, default=None)
    parser.add_argument("--output-fps", type=int, default=OUTPUT_FPS)
    args = parser.parse_args()

    duration = args.end - args.start
    if duration <= 0:
        write_status(args.status_file, {"status": "error", "error": "Invalid time range"})
        sys.exit(1)

    for binary, name in [(UI_BIN, "ui"), (REPLAY_BIN, "replay"),
                          (WESTON_PATCHED, "weston_patched"), (STREAM_CAPTURE, "stream_capture")]:
        if not os.path.isfile(binary):
            write_status(args.status_file, {"status": "error", "error": f"{name} not found at {binary}"})
            sys.exit(1)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    max_seg = find_max_segment(args.data_dir, args.local_id)
    if max_seg < 0:
        write_status(args.status_file, {"status": "error", "error": "No segments found"})
        sys.exit(1)

    rlog_path = find_rlog(args.data_dir, args.local_id)
    if not rlog_path:
        write_status(args.status_file, {"status": "error", "error": "No rlog found in segment 0"})
        sys.exit(1)

    symlink_dir = create_symlink_dir(args.data_dir, args.local_id, args.dongle_id, max_seg + 1)
    canonical_name = f"{args.dongle_id}|{args.local_id}"
    prefix = f"clip_{os.getpid()}"

    weston_proc = None
    replay_proc = None
    ui_proc = None
    capture_proc = None
    ffmpeg_proc = None
    sd_stop = threading.Event()

    try:
        # ── Setup (reusing tools/clip/run.py patterns) ───────────────
        write_status(args.status_file, {"status": "rendering", "elapsed_sec": 0,
                                        "total_sec": duration, "phase": "loading params"})

        with OpenpilotPrefix(prefix, shared_download_cache=True):
            # Populate CarParams from rlog (same as tools/clip/run.py)
            lr = LogReader(rlog_path)
            populate_car_params(lr)

            # Prevent "System Unresponsive" alert on C3 (Hardware::PC() is false)
            sd_thread = threading.Thread(target=start_selfdrive_publisher,
                                         args=(sd_stop,), daemon=True)
            sd_thread.start()

            # Switch to patched weston (C3-specific)
            write_status(args.status_file, {"status": "rendering", "elapsed_sec": 0,
                                            "total_sec": duration, "phase": "starting compositor"})
            weston_proc = switch_to_patched_weston()
            if weston_proc is None:
                write_status(args.status_file, {"status": "error", "error": "Patched weston failed to start"})
                restore_stock_weston()
                sys.exit(1)

            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = XDG_RUNTIME_DIR
            env["WAYLAND_DISPLAY"] = WAYLAND_DISPLAY
            env["TERM"] = "xterm"

            # ── Start replay + UI (same flow as tools/clip/run.py) ───
            write_status(args.status_file, {"status": "rendering", "elapsed_sec": 0,
                                            "total_sec": duration, "phase": "starting replay"})

            begin_at = max(args.start - SECONDS_TO_WARM, 0)
            speed = RECORD_SPEED

            replay_cmd = [
                REPLAY_BIN, "-c", "1",
                "-s", str(int(begin_at)),
                "-x", str(speed),
                "--no-loop",
                "--data_dir", symlink_dir,
                "--prefix", prefix,
                canonical_name,
            ]

            ui_cmd = [UI_BIN, "-platform", "wayland-egl"]

            replay_proc = Popen(replay_cmd, env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=open("/tmp/hud_replay.log", "w"))
            ui_proc = Popen(ui_cmd, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=open("/tmp/hud_ui.log", "w"))

            procs = [replay_proc, ui_proc]

            # Wait for UI to draw frames (from tools/clip/run.py)
            print("Waiting for UI to start drawing...", file=sys.stderr)
            wait_for_frames(procs)
            print(f"UI drawing, warming up ({SECONDS_TO_WARM}s)...", file=sys.stderr)
            time.sleep(SECONDS_TO_WARM)
            check_for_failure(procs)

            # ── Start capture + ffmpeg ───────────────────────────────
            write_status(args.status_file, {"status": "rendering", "elapsed_sec": 0,
                                            "total_sec": duration, "phase": "recording"})

            wall_duration = duration / speed
            total_frames = int(wall_duration * CAPTURE_FPS)
            effective_fps = CAPTURE_FPS / speed
            output_fps = args.output_fps

            # Request extra frames — ffmpeg -t auto-stops cleanly
            capture_cmd = [
                "sudo", "-E",
                STREAM_CAPTURE,
                "-w", str(CAPTURE_WIDTH),
                "-h", str(CAPTURE_HEIGHT),
                "-n", str(total_frames + int(30 * CAPTURE_FPS)),
                "-r", str(CAPTURE_FPS),
                "-v",
            ]

            # Video filter: transpose portrait→landscape + optional scale + fps duplication
            vf_parts = ["transpose=2"]
            if args.scale:
                vf_parts.append(f"scale={args.scale}")
            vf_parts.append(f"fps={output_fps}")

            # Key from tools/clip/run.py: -t duration auto-stops ffmpeg.
            # When ffmpeg exits → closes stdin → stream_capture gets SIGPIPE → clean exit.
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-f", "rawvideo",
                "-pixel_format", "bgra",
                "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
                "-framerate", str(effective_fps),
                "-i", "pipe:0",
                "-t", str(duration),
                "-vf", ",".join(vf_parts),
                "-c:v", "libx264",
                "-crf", "23",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                args.output,
            ]

            print(f"Recording: speed={speed}x, effective_fps={effective_fps}, "
                  f"output_fps={output_fps}, wall_duration={wall_duration:.0f}s, "
                  f"-t {duration}s", file=sys.stderr)

            capture_env = env.copy()
            capture_proc = Popen(capture_cmd, env=capture_env,
                                 stdout=subprocess.PIPE,
                                 stderr=open("/tmp/hud_capture.log", "w"))
            ffmpeg_proc = Popen(ffmpeg_cmd,
                                stdin=capture_proc.stdout,
                                stdout=subprocess.DEVNULL,
                                stderr=open("/tmp/hud_ffmpeg.log", "w"))
            capture_proc.stdout.close()

            start_time = time.monotonic()

            # ── Monitor progress — ffmpeg auto-stops via -t ──────────
            print(f"Recording in progress ({duration}s clip, {wall_duration:.0f}s wall)...",
                  file=sys.stderr)

            while ffmpeg_proc.poll() is None:
                elapsed = time.monotonic() - start_time
                progress = min(elapsed / max(wall_duration, 1), 0.99)
                current_frame = min(int(progress * total_frames), total_frames)
                remaining_sec = max(0, wall_duration - elapsed)

                write_status(args.status_file, {
                    "status": "rendering",
                    "elapsed_sec": round(progress * duration, 1),
                    "total_sec": duration,
                    "frame": current_frame,
                    "total_frames": total_frames,
                    "remaining_sec": round(remaining_sec),
                    "phase": "recording",
                })
                time.sleep(2)

            if ffmpeg_proc.returncode is None:
                ffmpeg_proc.wait(timeout=60)

            print(f"ffmpeg exited (code {ffmpeg_proc.returncode})", file=sys.stderr)

            # Verify output
            if not os.path.isfile(args.output) or os.path.getsize(args.output) < 1000:
                write_status(args.status_file, {"status": "error",
                                                "error": "Output file missing or too small"})
                cleanup_procs(replay_proc, ui_proc, capture_proc)
                restore_stock_weston()
                sys.exit(1)

            output_kb = os.path.getsize(args.output) / 1024
            write_status(args.status_file, {
                "status": "complete",
                "output": args.output,
                "elapsed_sec": round(duration, 1),
                "total_sec": duration,
            })
            print(f"Render complete: {args.output} ({output_kb:.0f}KB)", file=sys.stderr)

    except Exception as e:
        write_status(args.status_file, {"status": "error", "error": str(e)})
        sys.exit(1)

    finally:
        sd_stop.set()
        cleanup_procs(replay_proc, ui_proc, capture_proc, ffmpeg_proc)
        restore_stock_weston()
        try:
            import shutil
            shutil.rmtree(symlink_dir, ignore_errors=True)
            shutil.rmtree(f"/dev/shm/{prefix}", ignore_errors=True)
            shutil.rmtree(f"/data/params/{prefix}", ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
