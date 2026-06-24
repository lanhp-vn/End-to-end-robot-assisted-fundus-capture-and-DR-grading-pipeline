"""Pure detection helpers for the system-camera view calibration (device layer).

Re-derives the Aurora screen ROI (deskewed to an upright 5:3 crop), the two mirror-symmetric
alignment-arc bands on the camera circle, and the red HSV band(s) from three sample frames
(white startup screen, red/misaligned arcs, bright/aligned screen). Pure numpy + cv2 imgproc
-- no HighGUI window; the one I/O function is ``write_calibration_values`` (ruamel round-trip).
Synthetic-numpy unit-testable, mirroring ``arc_detector.py``. The interactive capture/confirm
shell lives in ``scripts/calibration/system_camera/calibrate_view.py``.

The ROI is chosen interactively (manual drag + arrow-key deskew) in the calibrate_view shell; this
module only normalises the chosen rect to a 5:3 deskewed RoiBox, fits the camera circle, derives the
symmetric arc bands, samples the red HSV band, and round-trips the config. Plain opencv-python only
(no contrib).
"""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from arm101_hand.config.system_camera_config import HsvBand, RoiBox, SystemCameraConfig

from .roi import roi_from_region

_Rect = tuple[tuple[float, float], tuple[float, float], float]


def screen_roi_from_rect(
    rect: _Rect, frame_w: int, frame_h: int, *, ref_w: int = 800, ref_h: int = 480
) -> RoiBox:
    """Normalise a rotated screen rect to 5:3 and store it at the ``ref_w x ref_h`` detection ref
    (so ``Roi.crop`` -> resize lands a 5:3 deskewed image). Carries the deskew ``angle``."""
    (cx, cy), (w, h), angle = rect
    target = 5 / 3
    if w / h < target:  # widen the short side to 5:3 (never shrink -> no content lost)
        w = target * h
    else:
        h = w / target
    sx, sy = ref_w / frame_w, ref_h / frame_h
    bx = int(round((cx - w / 2) * sx))
    by = int(round((cy - h / 2) * sy))
    return RoiBox(
        x=max(0, bx),
        y=max(0, by),
        w=max(1, int(round(w * sx))),
        h=max(1, int(round(h * sy))),
        ref_w=ref_w,
        ref_h=ref_h,
        angle=float(angle),
    )


def deskew_crop(frame: np.ndarray, region: RoiBox, *, out: tuple[int, int] = (800, 480)) -> np.ndarray:
    """Deskewed 5:3 crop of ``frame`` for ``region`` (screen_roi), resized to ``out``."""
    return cv2.resize(roi_from_region(region).crop(frame), out, interpolation=cv2.INTER_AREA)


def fit_camera_circle(bright_ref_bgr: np.ndarray) -> tuple[float, float, float]:
    """Centre + radius of the bright central disc in a deskewed ``out``-sized aligned frame.

    Splits the bright disc from the dark screen bezel with **Otsu** (the histogram is bimodal: dark
    plastic surround vs bright disc), takes the largest blob, and fits a circle via the contour's
    area (radius = sqrt(area/pi)) about its centroid -- tighter than minEnclosingCircle, which the
    glare streak inflates. A fixed/percentile cut was tried first but FRAGMENTS a non-uniformly-lit
    disc (the real Aurora disc has a bright lobe + a dimmer side), keeping only the bright part and
    mis-centring the fit; Otsu adapts to the disc-vs-bezel split and recovers the whole disc. A
    MORPH_CLOSE bridges the within-disc gradient dips / glare gaps into one solid blob."""
    g = cv2.cvtColor(bright_ref_bgr, cv2.COLOR_BGR2GRAY)
    _, m = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("no bright disc found for the camera circle")
    disc = max(contours, key=cv2.contourArea)
    mo = cv2.moments(disc)
    area = cv2.contourArea(disc)
    cx = mo["m10"] / mo["m00"] if mo["m00"] else bright_ref_bgr.shape[1] / 2
    cy = mo["m01"] / mo["m00"] if mo["m00"] else bright_ref_bgr.shape[0] / 2
    r = float(np.sqrt(area / np.pi))
    return float(cx), float(cy), r


def arc_bands_from_circle(
    cx: float, cy: float, r: float, *, ref_w: int = 800, ref_h: int = 480, pad: int = 4
) -> tuple[RoiBox, RoiBox]:
    """Two mirror-symmetric bands on the circle's left/right edges (outer ~35% of the radius,
    middle 60% vertically) -- excludes the top corners (battery) and the top/bottom of the disc.

    Mirrored about the CIRCLE centre ``cx`` (so each band sits on the disc's own left/right edge),
    NOT about the frame centre: the deskewed disc is not necessarily centred in the crop, and a
    frame-centre mirror misplaces the right band when it is off-centre (bench bug: a disc at cx=511
    put both bands near the frame centre, missing both arcs)."""
    bw = max(1, int(round(0.35 * r)))
    y = max(0, int(round(cy - 0.6 * r)) - pad)
    h = min(ref_h - y, int(round(1.2 * r)) + 2 * pad)
    lx = int(round(cx - r)) - pad
    rx = int(round(2 * cx - lx - bw))  # mirror the left band about cx
    lx = max(0, min(ref_w - bw, lx))
    rx = max(0, min(ref_w - bw, rx))
    left = RoiBox(x=lx, y=y, w=bw, h=h, ref_w=ref_w, ref_h=ref_h)
    right = RoiBox(x=rx, y=y, w=bw, h=h, ref_w=ref_w, ref_h=ref_h)
    return left, right


# Broad colour prior used to FIND candidate red arc pixels before percentile sampling tightens the
# band. Deliberately GENEROUS on saturation/value: the Aurora alignment arcs are PALE (low-S) and
# bright, sitting over a tinted near-white disc -- bench captures measured red arcs at S~50 V~227
# hue~170, so tight S/V floors miss them. Red wraps hue 0/180 -> two bands.
_RED_PRIOR: list[HsvBand] = [
    HsvBand(h_lo=0, s_lo=35, v_lo=50, h_hi=12, s_hi=255, v_hi=255),
    HsvBand(h_lo=158, s_lo=35, v_lo=50, h_hi=180, s_hi=255, v_hi=255),
]


def _prior_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR the inRange masks for every prior band, then MORPH_OPEN(3) to despeckle."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)


def sample_red_band(region_bgr: np.ndarray, *, lo_pct: float = 5, hi_pct: float = 95) -> list[HsvBand]:
    """Percentile-bracket the red arc pixels into 1-2 bands (0/180 hue wrap). Raises if none match."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    pts = hsv[_prior_mask(hsv, _RED_PRIOR) > 0]
    if pts.size == 0:
        raise ValueError("no red pixels to sample in this region")
    s_lo = int(np.percentile(pts[:, 1], lo_pct))
    v_lo = int(np.percentile(pts[:, 2], lo_pct))
    hue = pts[:, 0]
    bands: list[HsvBand] = []
    near_zero = hue[hue <= 90]
    near_180 = hue[hue > 90]
    if near_zero.size:
        bands.append(
            HsvBand(
                h_lo=0, s_lo=s_lo, v_lo=v_lo, h_hi=int(np.percentile(near_zero, hi_pct)), s_hi=255, v_hi=255
            )
        )
    if near_180.size:
        bands.append(
            HsvBand(
                h_lo=int(np.percentile(near_180, lo_pct)), s_lo=s_lo, v_lo=v_lo, h_hi=180, s_hi=255, v_hi=255
            )
        )
    return bands


def suggest_coverage_threshold(
    red_cov_on_red: float, red_cov_on_bright: float, *, floor: float = 0.02
) -> float:
    """Midpoint between the red-frame (high) and bright-frame (low) red coverage, floored."""
    return round(max((red_cov_on_red + red_cov_on_bright) / 2.0, floor), 4)


def _flow_map(d: dict[str, float]) -> CommentedMap:
    """A ruamel mapping rendered inline ``{a: 1, b: 2}`` to match the file's arc/band style."""
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


def _roibox_map(r: RoiBox) -> CommentedMap:
    return _flow_map(
        {"x": r.x, "y": r.y, "w": r.w, "h": r.h, "ref_w": r.ref_w, "ref_h": r.ref_h, "angle": r.angle}
    )


def _hsv_map(b: HsvBand) -> CommentedMap:
    return _flow_map(
        {"h_lo": b.h_lo, "s_lo": b.s_lo, "v_lo": b.v_lo, "h_hi": b.h_hi, "s_hi": b.s_hi, "v_hi": b.v_hi}
    )


def write_calibration_values(
    config_path: Path,
    *,
    screen_roi: RoiBox,
    left_arc: RoiBox,
    right_arc: RoiBox,
    red_bands: list[HsvBand],
    coverage_threshold: float,
) -> None:
    """Round-trip ``system_camera_config.yaml`` (preserving comments), updating screen_roi + the
    auto_trigger arc regions / red bands / threshold. Validates the result via pydantic and writes a
    ``.bak`` copy BEFORE touching the original; raises (without writing) if validation fails."""
    yaml_rt = YAML()  # round-trip mode preserves comments + key order
    yaml_rt.preserve_quotes = True
    data = yaml_rt.load(config_path.read_text(encoding="utf-8"))

    data["screen_roi"] = _roibox_map(screen_roi)
    at = data["auto_trigger"]
    at["left_arc"] = _roibox_map(left_arc)
    at["right_arc"] = _roibox_map(right_arc)
    at["red_bands"] = [_hsv_map(b) for b in red_bands]
    at["coverage_threshold"] = float(coverage_threshold)

    # Validate the would-be file via pydantic BEFORE writing (plain safe_load of the dumped text).
    buf = io.StringIO()
    yaml_rt.dump(data, buf)
    SystemCameraConfig.model_validate(yaml.safe_load(buf.getvalue()))

    config_path.with_suffix(config_path.suffix + ".bak").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with config_path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)
