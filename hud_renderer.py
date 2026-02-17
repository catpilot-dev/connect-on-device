#!/usr/bin/env python3
"""
Full server-side HUD overlay renderer for connect_on_device.

Subscribes to cereal messages and renders all HUD elements onto a
1928x1208 transparent RGBA PIL Image, then encodes to WebP for
WebSocket delivery to the browser at 20Hz.

Elements rendered (bottom to top):
  1. Engagement border (30px)
  2. Header gradient (black fade)
  3. Road edges (red polygons)
  4. Lane lines (white polygons, prob-weighted alpha)
  5. Driving path (accel-gradient filled polygon)
  6. Lead chevron (glow triangle + fill triangle)
  7. MAX speed box (rounded rect + text)
  8. Speed display (large centered text)
  9. Turn signals (green arrows, blink-toggled)
 10. Alerts (colored rounded rect + text)

Usage:
  Standalone test:  python hud_renderer.py
  From server.py:   renderer = HudRenderer(); frame_bytes = renderer.render_frame()
"""

import colorsys
import io
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ─── Constants ────────────────────────────────────────────────────────

WIDTH = 1928
HEIGHT = 1208
BORDER_SIZE = 30
HEADER_HEIGHT = 300

# Camera intrinsics (Comma 3 road camera AR0231)
FCAM_FX = 2648.0
FCAM_FY = 2648.0
FCAM_CX = 964.0
FCAM_CY = 604.0

FCAM_INTRINSICS = np.array([
    [FCAM_FX, 0.0, FCAM_CX],
    [0.0, FCAM_FY, FCAM_CY],
    [0.0, 0.0, 1.0],
], dtype=np.float64)

# Device frame (X=fwd, Y=right, Z=down) -> View frame (X=right, Y=down, Z=fwd)
VIEW_FROM_DEVICE = np.array([
    [0.0, 1.0, 0.0],
    [0.0, 0.0, 1.0],
    [1.0, 0.0, 0.0],
], dtype=np.float64)

HEIGHT_INIT = 1.22  # Camera height above road surface (meters)

# UI Colors (RGBA tuples)
BORDER_DISENGAGED = (0x17, 0x33, 0x49, 0xC8)
BORDER_OVERRIDE = (0x91, 0x9B, 0x95, 0xF1)
BORDER_ENGAGED = (0x17, 0x86, 0x44, 0xF1)

COLOR_WHITE = (255, 255, 255, 255)
COLOR_WHITE_200 = (255, 255, 255, 200)
COLOR_GREY = (166, 166, 166, 255)
COLOR_DARK_GREY = (114, 114, 114, 255)
COLOR_BLACK_TRANSLUCENT = (0, 0, 0, 166)
COLOR_BORDER_TRANSLUCENT = (255, 255, 255, 75)

ALERT_COLOR_NORMAL = (0, 0, 0, 235)
ALERT_COLOR_USER_PROMPT = (0xFE, 0x8C, 0x34, 235)
ALERT_COLOR_CRITICAL = (0xC9, 0x22, 0x31, 235)

LEAD_GLOW_COLOR = (218, 202, 37, 255)
LEAD_FILL_COLOR_BASE = (201, 34, 49)

TURN_SIGNAL_COLOR = (30, 200, 60, 220)

SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
MS_TO_KPH = 3.6
MS_TO_MPH = 2.23694

MIN_DRAW_DISTANCE = 10.0
MAX_DRAW_DISTANCE = 100.0

# Font paths (openpilot assets)
FONT_DIR = Path("/data/openpilot/selfdrive/assets/fonts")
FONT_DIR_ALT = Path("/home/oxygen/openpilot/selfdrive/assets/fonts")


def _find_font_dir():
    if FONT_DIR.exists():
        return FONT_DIR
    if FONT_DIR_ALT.exists():
        return FONT_DIR_ALT
    return None


# ─── Projection math ─────────────────────────────────────────────────

def euler2rot(rpy):
    """Convert roll-pitch-yaw to rotation matrix."""
    roll, pitch, yaw = rpy
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array([
        [cp * cy, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [cp * sy, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [   -sp,             cp * sr,             cp * cr        ],
    ], dtype=np.float64)


def build_transform(rpy_calib, zoom=1.1):
    """Build the 3x3 car-space-to-screen transform matrix.

    video_transform @ FCAM_INTRINSICS @ VIEW_FROM_DEVICE @ euler2rot(rpyCalib)
    """
    calib_rot = euler2rot(rpy_calib)
    calib_transform = FCAM_INTRINSICS @ VIEW_FROM_DEVICE @ calib_rot

    # Vanishing point for straight-ahead
    inf_point = np.array([1000.0, 0.0, 0.0])
    kep = calib_transform @ inf_point

    w, h = float(WIDTH), float(HEIGHT)
    cx, cy = FCAM_CX, FCAM_CY

    margin = 5.0
    max_x_offset = cx * zoom - w / 2.0 - margin
    max_y_offset = cy * zoom - h / 2.0 - margin

    if abs(kep[2]) > 1e-6:
        x_offset = np.clip((kep[0] / kep[2] - cx) * zoom, -max_x_offset, max_x_offset)
        y_offset = np.clip((kep[1] / kep[2] - cy) * zoom, -max_y_offset, max_y_offset)
    else:
        x_offset, y_offset = 0.0, 0.0

    video_transform = np.array([
        [zoom, 0.0, (w / 2.0 - x_offset) - (cx * zoom)],
        [0.0, zoom, (h / 2.0 - y_offset) - (cy * zoom)],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    return video_transform @ calib_transform


def project_points(T, points_3d):
    """Project Nx3 car-space points to Nx2 screen points.

    Returns screen coords and a boolean valid mask.
    """
    if points_3d.shape[0] == 0:
        return np.empty((0, 2), dtype=np.float64), np.empty(0, dtype=bool)
    proj = (T @ points_3d.T)  # 3xN
    z = proj[2]
    valid = np.abs(z) >= 1e-6
    screen = np.zeros((points_3d.shape[0], 2), dtype=np.float64)
    screen[valid, 0] = proj[0, valid] / z[valid]
    screen[valid, 1] = proj[1, valid] / z[valid]
    # Clip region check
    clip_valid = (
        valid &
        (screen[:, 0] >= -500) & (screen[:, 0] <= WIDTH + 500) &
        (screen[:, 1] >= -500) & (screen[:, 1] <= HEIGHT + 500)
    )
    return screen, clip_valid


def map_line_to_polygon(T, line_3d, y_off, z_off, max_idx, allow_invert=True):
    """Convert 3D line to 2D polygon points for rendering.

    Returns list of (x, y) tuples forming a closed polygon.
    """
    if line_3d.shape[0] == 0:
        return []

    points = line_3d[:max_idx + 1]
    points = points[points[:, 0] >= 0]
    if points.shape[0] == 0:
        return []

    N = points.shape[0]
    # Left and right offset points
    left_3d = points.copy()
    left_3d[:, 1] -= y_off
    left_3d[:, 2] += z_off

    right_3d = points.copy()
    right_3d[:, 1] += y_off
    right_3d[:, 2] += z_off

    left_screen, left_valid = project_points(T, left_3d)
    right_screen, right_valid = project_points(T, right_3d)

    both_valid = left_valid & right_valid
    if not np.any(both_valid):
        return []

    left_pts = left_screen[both_valid]
    right_pts = right_screen[both_valid]

    # Handle Y-coordinate inversion on hills
    if not allow_invert and left_pts.shape[0] > 1:
        y = left_pts[:, 1]
        keep = y == np.minimum.accumulate(y)
        if not np.any(keep):
            return []
        left_pts = left_pts[keep]
        right_pts = right_pts[keep]

    # Form polygon: left forward, right reversed
    polygon = np.vstack([left_pts, right_pts[::-1]])
    return [(float(p[0]), float(p[1])) for p in polygon]


def get_path_length_idx(pos_x, path_height):
    """Get the index corresponding to a given forward distance."""
    if len(pos_x) == 0:
        return 0
    indices = np.where(pos_x <= path_height)[0]
    return int(indices[-1]) if indices.size > 0 else 0


def map_to_screen(T, x, y, z):
    """Project a single 3D point to screen coordinates."""
    pt = T @ np.array([x, y, z])
    if abs(pt[2]) < 1e-6:
        return None
    sx, sy = pt[0] / pt[2], pt[1] / pt[2]
    if not (-500 <= sx <= WIDTH + 500 and -500 <= sy <= HEIGHT + 500):
        return None
    return (float(sx), float(sy))


# ─── Font loading ─────────────────────────────────────────────────────

class FontCache:
    """Lazy-load and cache Inter fonts at various sizes."""

    def __init__(self):
        self._cache = {}
        self._font_dir = _find_font_dir()

    def get(self, weight: str, size: int) -> ImageFont.FreeTypeFont:
        key = (weight, size)
        if key not in self._cache:
            self._cache[key] = self._load(weight, size)
        return self._cache[key]

    def _load(self, weight: str, size: int) -> ImageFont.FreeTypeFont:
        if self._font_dir:
            path = self._font_dir / f"Inter-{weight}.ttf"
            if path.exists():
                return ImageFont.truetype(str(path), size)
        # Fallback
        try:
            return ImageFont.truetype("Inter", size)
        except OSError:
            return ImageFont.load_default()


_fonts = FontCache()


# ─── Drawing helpers ──────────────────────────────────────────────────

def _draw_rounded_rect(draw, xy, radius, fill=None, outline=None, width=0):
    """Draw a rounded rectangle on an ImageDraw context."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _draw_gradient_v(img, x, y, w, h, color_top, color_bottom):
    """Draw a vertical gradient rectangle onto an RGBA image."""
    if h <= 0 or w <= 0:
        return
    arr = np.array(img)
    for row_offset in range(h):
        row = y + row_offset
        if row < 0 or row >= arr.shape[0]:
            continue
        t = row_offset / max(h - 1, 1)
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * t)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * t)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * t)
        a = int(color_top[3] + (color_bottom[3] - color_top[3]) * t)
        x_start = max(x, 0)
        x_end = min(x + w, arr.shape[1])
        if x_start < x_end:
            arr[row, x_start:x_end] = [r, g, b, a]
    return Image.fromarray(arr)


def _hsla_to_rgba(h, s, l, a):
    """Convert HSLA (h in 0-1 range) to RGBA tuple."""
    rgb = colorsys.hls_to_rgb(h, l, s)
    return (int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255), int(a * 255))


def _map_val(x, x0, x1, y0, y1):
    x = np.clip(x, x0, x1)
    ra = x1 - x0
    rb = y1 - y0
    return float((x - x0) * rb / ra + y0) if ra != 0 else y0


# ─── HudRenderer ──────────────────────────────────────────────────────

class HudRenderer:
    """Renders the complete HUD overlay as a transparent WebP image."""

    def __init__(self):
        self._sm = None
        self._transform = None
        self._path_offset_z = HEIGHT_INIT
        self._blink_state = True
        self._blink_time = time.monotonic()
        self._is_metric = True  # Default to metric (km/h)
        self._v_ego_cluster_seen = False

        # Try to initialize cereal SubMaster
        try:
            from cereal import messaging
            self._sm = messaging.SubMaster([
                'carState', 'selfdriveState', 'radarState',
                'modelV2', 'liveCalibration',
            ])
            self._messaging = messaging
        except ImportError:
            self._sm = None
            self._messaging = None

        # Try to read IsMetric param
        try:
            from openpilot.common.params import Params
            self._is_metric = Params().get_bool("IsMetric")
        except Exception:
            pass

    def close(self):
        """Cleanup resources."""
        pass

    def update(self):
        """Poll cereal for new messages."""
        if self._sm is not None:
            self._sm.update(0)

    def render_frame(self) -> bytes | None:
        """Render one overlay frame. Returns WebP bytes or None."""
        self.update()
        self._update_blink()

        img = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        sm = self._sm

        # Extract state from SubMaster
        car_state = None
        selfdrive_state = None
        model_v2 = None
        radar_state = None
        live_calib = None

        if sm is not None:
            car_state = sm['carState'] if sm.alive.get('carState', False) else None
            selfdrive_state = sm['selfdriveState'] if sm.alive.get('selfdriveState', False) else None
            model_v2 = sm['modelV2'] if sm.alive.get('modelV2', False) else None
            radar_state = sm['radarState'] if sm.alive.get('radarState', False) else None
            live_calib = sm['liveCalibration'] if sm.alive.get('liveCalibration', False) else None

        # Update calibration transform
        if live_calib is not None:
            rpy = list(live_calib.rpyCalib)
            if len(rpy) == 3:
                self._transform = build_transform(rpy)
                if live_calib.height:
                    self._path_offset_z = live_calib.height[0]

        # 1. Engagement border
        self._draw_engagement_border(draw, selfdrive_state)

        # 2. Header gradient
        img = self._draw_header_gradient(img)
        draw = ImageDraw.Draw(img)

        # 3-6. Model elements (road edges, lane lines, path, lead)
        if model_v2 is not None and self._transform is not None:
            self._draw_model_elements(draw, img, model_v2, radar_state)

        # 7. MAX speed box
        self._draw_max_speed_box(draw, car_state, selfdrive_state)

        # 8. Speed display
        self._draw_speed_display(draw, car_state)

        # 9. Turn signals
        self._draw_turn_signals(draw, car_state)

        # 10. Alerts
        self._draw_alerts(draw, selfdrive_state)

        # Encode to WebP
        buf = io.BytesIO()
        img.save(buf, format='WEBP', lossless=True)
        return buf.getvalue()

    def _update_blink(self):
        now = time.monotonic()
        if now - self._blink_time >= 0.5:
            self._blink_state = not self._blink_state
            self._blink_time = now

    # ─── Drawing methods ──────────────────────────────────────────────

    def _draw_engagement_border(self, draw, selfdrive_state):
        """Draw 30px engagement border around the frame."""
        color = BORDER_DISENGAGED
        if selfdrive_state is not None:
            state = selfdrive_state.state
            enabled = selfdrive_state.enabled
            # state enum: preEnabled, overriding -> OVERRIDE
            state_str = str(state)
            if 'preEnabled' in state_str or 'overriding' in state_str:
                color = BORDER_OVERRIDE
            elif enabled:
                color = BORDER_ENGAGED

        b = BORDER_SIZE
        # Top
        draw.rectangle([0, 0, WIDTH - 1, b - 1], fill=color)
        # Bottom
        draw.rectangle([0, HEIGHT - b, WIDTH - 1, HEIGHT - 1], fill=color)
        # Left
        draw.rectangle([0, b, b - 1, HEIGHT - b - 1], fill=color)
        # Right
        draw.rectangle([WIDTH - b, b, WIDTH - 1, HEIGHT - b - 1], fill=color)

    def _draw_header_gradient(self, img):
        """Draw header gradient (black fading to transparent, 0-300px)."""
        return _draw_gradient_v(
            img, 0, 0, WIDTH, HEADER_HEIGHT,
            (0, 0, 0, 114), (0, 0, 0, 0),
        )

    def _draw_model_elements(self, draw, img, model, radar_state):
        """Draw road edges, lane lines, path, and lead chevron."""
        T = self._transform

        # Extract model data
        pos = model.position
        pos_x = np.array(pos.x, dtype=np.float64)
        pos_y = np.array(pos.y, dtype=np.float64)
        pos_z = np.array(pos.z, dtype=np.float64)

        if pos_x.size == 0:
            return

        path_3d = np.column_stack([pos_x, pos_y, pos_z])
        max_distance = float(np.clip(pos_x[-1], MIN_DRAW_DISTANCE, MAX_DRAW_DISTANCE))

        lane_line_probs = np.array(model.laneLineProbs, dtype=np.float64)
        road_edge_stds = np.array(model.roadEdgeStds, dtype=np.float64)

        max_idx_lanes = get_path_length_idx(
            np.array(model.laneLines[0].x, dtype=np.float64) if len(model.laneLines) > 0 else np.array([]),
            max_distance
        )

        # 3. Road edges (red)
        for i, edge in enumerate(model.roadEdges):
            edge_3d = np.column_stack([
                np.array(edge.x, dtype=np.float64),
                np.array(edge.y, dtype=np.float64),
                np.array(edge.z, dtype=np.float64),
            ])
            std = road_edge_stds[i] if i < len(road_edge_stds) else 1.0
            alpha = float(np.clip(1.0 - std, 0.0, 1.0))
            if alpha < 0.05:
                continue
            poly = map_line_to_polygon(T, edge_3d, 0.025, 0.0, max_idx_lanes)
            if len(poly) >= 3:
                color = (255, 0, 0, int(alpha * 255))
                draw.polygon(poly, fill=color)

        # 4. Lane lines (white, prob-weighted)
        for i, lane in enumerate(model.laneLines):
            lane_3d = np.column_stack([
                np.array(lane.x, dtype=np.float64),
                np.array(lane.y, dtype=np.float64),
                np.array(lane.z, dtype=np.float64),
            ])
            prob = lane_line_probs[i] if i < len(lane_line_probs) else 0.0
            if prob < 0.1:
                continue
            alpha = float(np.clip(prob, 0.0, 0.7))
            poly = map_line_to_polygon(T, lane_3d, 0.025 * prob, 0.0, max_idx_lanes)
            if len(poly) >= 3:
                color = (255, 255, 255, int(alpha * 255))
                draw.polygon(poly, fill=color)

        # 5. Driving path (with acceleration gradient)
        lead = None
        if radar_state is not None and radar_state.leadOne.status:
            lead = radar_state.leadOne
            lead_d = lead.dRel * 2.0
            max_distance = float(np.clip(lead_d - min(lead_d * 0.35, 10.0), 0.0, max_distance))

        max_idx_path = get_path_length_idx(pos_x, max_distance)
        path_poly = map_line_to_polygon(T, path_3d, 0.9, self._path_offset_z, max_idx_path, allow_invert=False)

        if len(path_poly) >= 3:
            accel_x = np.array(model.acceleration.x, dtype=np.float64)
            self._draw_accel_path(img, path_poly, accel_x)
            # Re-create draw after modifying img array
            draw = ImageDraw.Draw(img)

        # 6. Lead chevron
        if lead is not None and lead.status and lead.dRel > 0:
            self._draw_lead_chevron(draw, T, lead, path_3d)

    def _draw_accel_path(self, img, poly_pts, accel_x):
        """Draw the driving path with acceleration-based color gradient.

        Uses a simpler approach: fill with averaged accel color since PIL
        doesn't support per-vertex gradients easily.
        """
        if len(poly_pts) < 3:
            return

        # Compute average acceleration for path color
        if accel_x.size > 0:
            avg_accel = float(np.mean(accel_x[:min(20, len(accel_x))]))
        else:
            avg_accel = 0.0

        hue = max(min(60 + avg_accel * 35, 120), 0) / 360.0
        saturation = min(abs(avg_accel * 1.5), 1.0)
        lightness = _map_val(saturation, 0.0, 1.0, 0.95, 0.62)
        alpha = 0.35

        rgba = _hsla_to_rgba(hue, saturation, lightness, alpha)

        draw = ImageDraw.Draw(img)
        draw.polygon(poly_pts, fill=rgba)

    def _draw_lead_chevron(self, draw, T, lead, path_3d):
        """Draw lead vehicle chevron indicator."""
        d_rel = lead.dRel
        y_rel = lead.yRel
        v_rel = lead.vRel

        # Get z from path at lead distance
        idx = get_path_length_idx(path_3d[:, 0], d_rel)
        z = path_3d[idx, 2] if idx < len(path_3d) else 0.0

        pt = map_to_screen(T, d_rel, -y_rel, z + self._path_offset_z)
        if pt is None:
            return

        sx, sy = pt
        speed_buff, lead_buff = 10.0, 40.0

        # Fill alpha
        fill_alpha = 0
        if d_rel < lead_buff:
            fill_alpha = 255 * (1.0 - (d_rel / lead_buff))
            if v_rel < 0:
                fill_alpha += 255 * (-1 * (v_rel / speed_buff))
            fill_alpha = int(min(fill_alpha, 255))

        # Size
        sz = float(np.clip((25 * 30) / (d_rel / 3 + 30), 15.0, 30.0) * 2.35)
        sx = float(np.clip(sx, 0.0, WIDTH - sz / 2))
        sy = min(sy, HEIGHT - sz * 0.6)

        g_xo = sz / 5
        g_yo = sz / 10

        # Glow triangle (slightly larger)
        glow = [
            (sx + sz * 1.35 + g_xo, sy + sz + g_yo),
            (sx, sy - g_yo),
            (sx - sz * 1.35 - g_xo, sy + sz + g_yo),
        ]
        draw.polygon(glow, fill=LEAD_GLOW_COLOR)

        # Fill triangle
        chevron = [
            (sx + sz * 1.25, sy + sz),
            (sx, sy),
            (sx - sz * 1.25, sy + sz),
        ]
        fill_color = LEAD_FILL_COLOR_BASE + (fill_alpha,)
        draw.polygon(chevron, fill=fill_color)

    def _draw_max_speed_box(self, draw, car_state, selfdrive_state):
        """Draw the MAX speed indicator box (top-left)."""
        if car_state is None:
            return

        v_cruise_cluster = car_state.vCruiseCluster if hasattr(car_state, 'vCruiseCluster') else 0.0
        cruise_speed = v_cruise_cluster if v_cruise_cluster != 0.0 else getattr(car_state, 'cruiseState', None)
        if cruise_speed is None or cruise_speed == 0.0:
            cs = getattr(car_state, 'cruiseState', None)
            if cs is not None:
                cruise_speed = cs.speed
            else:
                cruise_speed = 0.0

        # Check if cruise is set (-1 = unavailable, 0 = not set)
        is_cruise_available = cruise_speed != -1
        is_cruise_set = 0 < cruise_speed < SET_SPEED_NA

        if not is_cruise_available:
            return

        # Convert to display speed
        if is_cruise_set and self._is_metric:
            display_speed = round(cruise_speed * MS_TO_KPH)
        elif is_cruise_set:
            display_speed = round(cruise_speed * MS_TO_MPH)
        else:
            display_speed = None

        set_speed_width = 200 if self._is_metric else 172
        box_x = 60 + (172 - set_speed_width) // 2
        box_y = 45
        box_h = 204
        radius = 32

        # Background
        _draw_rounded_rect(draw, (box_x, box_y, box_x + set_speed_width, box_y + box_h),
                           radius, fill=COLOR_BLACK_TRANSLUCENT, outline=COLOR_BORDER_TRANSLUCENT, width=6)

        # "MAX" label color
        max_color = COLOR_GREY
        speed_color = COLOR_DARK_GREY
        if is_cruise_set:
            speed_color = COLOR_WHITE
            # Determine status
            if selfdrive_state is not None:
                state_str = str(selfdrive_state.state)
                if selfdrive_state.enabled:
                    if 'preEnabled' in state_str or 'overriding' in state_str:
                        max_color = (145, 155, 149, 255)
                    else:
                        max_color = (128, 216, 166, 255)
                else:
                    max_color = (145, 155, 149, 255)

        # "MAX" text
        font_max = _fonts.get("SemiBold", 40)
        text_max = "MAX"
        bbox = draw.textbbox((0, 0), text_max, font=font_max)
        tw = bbox[2] - bbox[0]
        draw.text((box_x + (set_speed_width - tw) // 2, box_y + 27), text_max, fill=max_color, font=font_max)

        # Speed text
        font_speed = _fonts.get("Bold", 90)
        speed_text = str(display_speed) if display_speed is not None else "\u2013"
        bbox = draw.textbbox((0, 0), speed_text, font=font_speed)
        tw = bbox[2] - bbox[0]
        draw.text((box_x + (set_speed_width - tw) // 2, box_y + 77), speed_text, fill=speed_color, font=font_speed)

    def _draw_speed_display(self, draw, car_state):
        """Draw current speed (large, centered)."""
        if car_state is None:
            return

        v_ego_cluster = car_state.vEgoCluster if hasattr(car_state, 'vEgoCluster') else 0.0
        self._v_ego_cluster_seen = self._v_ego_cluster_seen or v_ego_cluster != 0.0
        v_ego = v_ego_cluster if self._v_ego_cluster_seen else car_state.vEgo

        speed_conversion = MS_TO_KPH if self._is_metric else MS_TO_MPH
        speed = max(0.0, v_ego * speed_conversion)
        speed_text = str(round(speed))

        # Speed number
        font_speed = _fonts.get("Bold", 176)
        bbox = draw.textbbox((0, 0), speed_text, font=font_speed)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (WIDTH - tw) // 2
        y = 180 - th // 2
        draw.text((x, y), speed_text, fill=COLOR_WHITE, font=font_speed)

        # Unit
        unit_text = "km/h" if self._is_metric else "mph"
        font_unit = _fonts.get("Medium", 66)
        bbox = draw.textbbox((0, 0), unit_text, font=font_unit)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (WIDTH - tw) // 2
        y = 290 - th // 2
        draw.text((x, y), unit_text, fill=COLOR_WHITE_200, font=font_unit)

    def _draw_turn_signals(self, draw, car_state):
        """Draw green turn signal arrows (left/right edges, blink-toggled)."""
        if car_state is None:
            return
        if not self._blink_state:
            return

        left = car_state.leftBlinker
        right = car_state.rightBlinker
        if not left and not right:
            return

        arrow_w = 60
        arrow_h = 120
        margin = 50
        y_center = int(HEIGHT * 0.4)

        if left:
            pts = [
                (margin, y_center),
                (margin + arrow_w, y_center - arrow_h // 2),
                (margin + arrow_w, y_center + arrow_h // 2),
            ]
            draw.polygon(pts, fill=TURN_SIGNAL_COLOR)

        if right:
            pts = [
                (WIDTH - margin, y_center),
                (WIDTH - margin - arrow_w, y_center - arrow_h // 2),
                (WIDTH - margin - arrow_w, y_center + arrow_h // 2),
            ]
            draw.polygon(pts, fill=TURN_SIGNAL_COLOR)

    def _draw_alerts(self, draw, selfdrive_state):
        """Draw alert box at bottom of screen."""
        if selfdrive_state is None:
            return

        alert_size = selfdrive_state.alertSize
        if alert_size == 0:
            return

        text1 = selfdrive_state.alertText1 or ''
        text2 = selfdrive_state.alertText2 or ''
        if not text1 and not text2:
            return

        alert_status = selfdrive_state.alertStatus
        status_raw = alert_status.raw if hasattr(alert_status, 'raw') else int(alert_status)

        if status_raw == 1:
            bg_color = ALERT_COLOR_USER_PROMPT
        elif status_raw == 2:
            bg_color = ALERT_COLOR_CRITICAL
        else:
            bg_color = ALERT_COLOR_NORMAL

        alert_margin = 40
        alert_padding = 60
        alert_line_spacing = 45
        alert_border_radius = 30

        size_raw = alert_size.raw if hasattr(alert_size, 'raw') else int(alert_size)

        if size_raw == 1:
            # Small alert
            font_size = 74
            font = _fonts.get("Bold", font_size)
            bbox = draw.textbbox((0, 0), text1, font=font)
            text_w = bbox[2] - bbox[0]
            box_h = font_size + 2 * alert_padding
            box_w = max(text_w + 2 * alert_padding, 400)
            box_x = (WIDTH - box_w) // 2
            box_y = HEIGHT - alert_margin - box_h

            _draw_rounded_rect(draw, (box_x, box_y, box_x + box_w, box_y + box_h),
                               alert_border_radius, fill=bg_color)

            # Center text
            bbox = draw.textbbox((0, 0), text1, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(((WIDTH - tw) // 2, box_y + (box_h - th) // 2), text1, fill=COLOR_WHITE, font=font)

        elif size_raw == 2:
            # Mid alert
            font1 = _fonts.get("Bold", 88)
            font2 = _fonts.get("Medium", 66)
            bbox1 = draw.textbbox((0, 0), text1, font=font1)
            bbox2 = draw.textbbox((0, 0), text2, font=font2)
            text_w = max(bbox1[2] - bbox1[0], bbox2[2] - bbox2[0])
            box_h = 88 + alert_line_spacing + 66 + 2 * alert_padding
            box_w = max(text_w + 2 * alert_padding, 500)
            box_x = (WIDTH - box_w) // 2
            box_y = HEIGHT - alert_margin - box_h

            _draw_rounded_rect(draw, (box_x, box_y, box_x + box_w, box_y + box_h),
                               alert_border_radius, fill=bg_color)

            # Text 1 (bold, centered)
            bbox = draw.textbbox((0, 0), text1, font=font1)
            tw = bbox[2] - bbox[0]
            draw.text(((WIDTH - tw) // 2, box_y + alert_padding), text1, fill=COLOR_WHITE, font=font1)

            # Text 2 (regular, centered)
            bbox = draw.textbbox((0, 0), text2, font=font2)
            tw = bbox[2] - bbox[0]
            draw.text(((WIDTH - tw) // 2, box_y + alert_padding + 88 + alert_line_spacing),
                      text2, fill=COLOR_WHITE, font=font2)

        else:
            # Full alert
            _draw_rounded_rect(draw, (0, 0, WIDTH, HEIGHT), 0, fill=bg_color)

            is_long = len(text1) > 15
            font_size1 = 132 if is_long else 177
            font1 = _fonts.get("Bold", font_size1)
            font2 = _fonts.get("Bold", 88)

            bbox = draw.textbbox((0, 0), text1, font=font1)
            tw = bbox[2] - bbox[0]
            draw.text(((WIDTH - tw) // 2, HEIGHT // 4), text1, fill=COLOR_WHITE, font=font1)

            bbox = draw.textbbox((0, 0), text2, font=font2)
            tw = bbox[2] - bbox[0]
            draw.text(((WIDTH - tw) // 2, HEIGHT // 2 + 20), text2, fill=COLOR_WHITE, font=font2)


# ─── Standalone test ──────────────────────────────────────────────────

def _test_render():
    """Render a test frame with mock data and save as PNG."""
    print("HudRenderer standalone test")
    print(f"  Image size: {WIDTH}x{HEIGHT}")
    print(f"  Font dir: {_find_font_dir()}")

    renderer = HudRenderer()
    img = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Mock engagement border (engaged)
    renderer._draw_engagement_border(draw, None)

    # Header gradient
    img = renderer._draw_header_gradient(img)
    draw = ImageDraw.Draw(img)

    # Mock speed display
    class MockCarState:
        vEgo = 22.2  # ~80 km/h
        vEgoCluster = 22.2
        leftBlinker = True
        rightBlinker = False
        cruiseState = type('obj', (object,), {'speed': 33.3, 'enabled': True})()
        vCruiseCluster = 33.3

    class MockSelfdriveState:
        enabled = True
        state = 'enabled'
        alertSize = 0
        alertText1 = ''
        alertText2 = ''
        alertStatus = 0
        experimentalMode = True

    renderer._draw_speed_display(draw, MockCarState())
    renderer._draw_max_speed_box(draw, MockCarState(), MockSelfdriveState())
    renderer._draw_turn_signals(draw, MockCarState())

    # Mock path
    T = build_transform([0.0, 0.0, 0.0])
    x_vals = np.arange(0, 60, 0.5)
    y_vals = np.zeros_like(x_vals)
    z_vals = np.zeros_like(x_vals)
    path_3d = np.column_stack([x_vals, y_vals, z_vals])
    path_poly = map_line_to_polygon(T, path_3d, 0.9, HEIGHT_INIT, len(x_vals) - 1, allow_invert=False)
    if len(path_poly) >= 3:
        draw.polygon(path_poly, fill=(80, 255, 120, 90))

    # Lane lines
    for offset in [-1.85, 1.85]:
        lane_3d = np.column_stack([x_vals, np.full_like(x_vals, offset), z_vals])
        lane_poly = map_line_to_polygon(T, lane_3d, 0.025 * 0.5, 0.0, len(x_vals) - 1)
        if len(lane_poly) >= 3:
            draw.polygon(lane_poly, fill=(255, 255, 255, int(0.5 * 255)))

    # Road edges
    for offset in [-3.7, 3.7]:
        edge_3d = np.column_stack([x_vals, np.full_like(x_vals, offset), z_vals])
        edge_poly = map_line_to_polygon(T, edge_3d, 0.025, 0.0, len(x_vals) - 1)
        if len(edge_poly) >= 3:
            draw.polygon(edge_poly, fill=(255, 0, 0, int(0.8 * 255)))

    # Lead chevron mock
    pt = map_to_screen(T, 30.0, 0.0, HEIGHT_INIT)
    if pt:
        sx, sy = pt
        sz = 40.0
        glow = [(sx + sz * 1.35, sy + sz), (sx, sy), (sx - sz * 1.35, sy + sz)]
        draw.polygon(glow, fill=LEAD_GLOW_COLOR)
        chevron = [(sx + sz * 1.25, sy + sz), (sx, sy), (sx - sz * 1.25, sy + sz)]
        draw.polygon(chevron, fill=(201, 34, 49, 180))

    # Save test output
    out_path = Path(__file__).parent / "test_overlay.png"
    img.save(str(out_path))
    print(f"  Saved: {out_path}")

    # Also test WebP encoding
    buf = io.BytesIO()
    img.save(buf, format='WEBP', lossless=True)
    webp_size = buf.tell()
    print(f"  WebP size: {webp_size:,} bytes ({webp_size / 1024:.1f} KB)")

    renderer.close()
    print("  Done!")


if __name__ == "__main__":
    _test_render()
