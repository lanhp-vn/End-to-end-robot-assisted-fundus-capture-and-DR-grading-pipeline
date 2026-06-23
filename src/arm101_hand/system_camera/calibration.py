"""Pure detection helpers for the system-camera view calibration (device layer).

Re-derives the Aurora screen ROI, the two alignment-arc regions, and the red/green HSV bands
from three sample frames (white startup screen, red arcs, green arcs). Pure numpy + cv2 imgproc
-- no HighGUI window; the one I/O function is ``write_calibration_values`` (ruamel round-trip).
Synthetic-numpy unit-testable, mirroring ``arc_detector.py``. The interactive capture/confirm
shell lives in ``scripts/calibration/system_camera/calibrate_view.py``.

Screen detection sweeps several HIGH brightness thresholds (the backlit Aurora LCD is near-
saturated white, unlike painted walls / daylight) and ranks INTERIOR rectangles by
fill x aspect x size x not-touching-the-border -- the screen is the only large bright rectangle
that does not run into the frame edge. Plain opencv-python only (no contrib); patterns adapted
from the OpenCV samples, never imported from references/.
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

# Screen detection tuning. The Aurora screen is a backlit LCD (near-saturated white) sitting as an
# island inside the frame; walls / daylight / doorway are bright too but run into the frame border.
_SCREEN_ASPECT_LO, _SCREEN_ASPECT_HI = 1.2, 2.0  # landscape ~16:10..16:9
_SCREEN_MIN_AREA_FRAC = 0.01  # ignore blobs smaller than 1% of the frame
_SCREEN_BORDER_FRAC = 0.01  # within 1% of an edge counts as "touching the border"
_SCREEN_TARGET_AREA_FRAC = 0.04  # size score saturates around 4% of the frame
_SCREEN_THRESH_FRACS = (0.4, 0.6, 0.8)  # high cuts between Otsu and 255 to isolate the backlit LCD


def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union of two ``(x, y, w, h)`` boxes (to de-dupe candidates across cuts)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _screen_likeness(bbox: tuple[int, int, int, int], area: float, frame_w: int, frame_h: int) -> float:
    """Score a bright blob on how screen-like it is: solid (high fill), landscape aspect, sizeable,
    and INTERIOR -- not touching the frame border (walls / daylight do; the screen does not)."""
    x, y, w, h = bbox
    rect_area = w * h
    if rect_area <= 0:
        return 0.0
    fill = area / rect_area
    aspect = w / h if h else 0.0
    aspect_score = 1.0 if _SCREEN_ASPECT_LO <= aspect <= _SCREEN_ASPECT_HI else 0.25
    size = min(area / (frame_w * frame_h) / _SCREEN_TARGET_AREA_FRAC, 1.0)
    margin = max(2, int(_SCREEN_BORDER_FRAC * min(frame_w, frame_h)))
    touches = x <= margin or y <= margin or x + w >= frame_w - margin or y + h >= frame_h - margin
    interior = 0.1 if touches else 1.0
    return fill * aspect_score * size * interior


def detect_screen_rect(white_bgr: np.ndarray, *, top_n: int = 3) -> list[tuple[int, int, int, int]]:
    """Ranked candidate ``(x, y, w, h)`` bounding boxes of the backlit Aurora screen, best first.

    Sweeps several HIGH brightness thresholds (between Otsu and 255): the backlit LCD saturates near
    white while painted walls / daylight are dimmer, so a high cut isolates the screen instead of
    fusing it into the bright room (plain Otsu lumps screen + walls + doorway into one blob). Each
    cut's external contours are scored by :func:`_screen_likeness` (fill x aspect x size x interior);
    candidates overlapping across cuts are de-duped (IoU >= 0.5, highest score wins). Returns up to
    ``top_n`` boxes with a positive score.
    """
    gray = cv2.cvtColor(white_bgr, cv2.COLOR_BGR2GRAY)
    fh, fw = gray.shape
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    min_area = _SCREEN_MIN_AREA_FRAC * fw * fh
    scored: list[tuple[float, tuple[int, int, int, int]]] = []
    for frac in _SCREEN_THRESH_FRACS:
        thr = int(otsu + (255 - otsu) * frac)
        _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            bbox = cv2.boundingRect(c)
            score = _screen_likeness(bbox, area, fw, fh)
            if score > 0.0:
                scored.append((score, bbox))
    scored.sort(key=lambda t: t[0], reverse=True)
    kept: list[tuple[int, int, int, int]] = []
    for _, bbox in scored:
        if all(_bbox_iou(bbox, kb) < 0.5 for kb in kept):
            kept.append(bbox)
        if len(kept) >= top_n:
            break
    return kept


def to_roi_candidate(
    bbox: tuple[int, int, int, int], frame_w: int, frame_h: int, *, target_aspect: float = 4 / 3
) -> tuple[int, int, int, int]:
    """Expand ``bbox`` about its centre to ``target_aspect`` (never shrinks -> no content lost),
    then clamp inside the frame. Distortion-free because the crop is later resized to a same-aspect
    reference."""
    x, y, w, h = bbox
    cx, cy = x + w / 2.0, y + h / 2.0
    fw, fh = float(w), float(h)
    if fw / fh < target_aspect:
        fw = target_aspect * fh
    else:
        fh = fw / target_aspect
    nx = int(round(cx - fw / 2.0))
    ny = int(round(cy - fh / 2.0))
    nw = int(round(fw))
    nh = int(round(fh))
    nx = max(0, min(nx, frame_w - 1))
    ny = max(0, min(ny, frame_h - 1))
    nw = max(1, min(nw, frame_w - nx))
    nh = max(1, min(nh, frame_h - ny))
    return nx, ny, nw, nh


# Broad colour priors (the schema defaults) used to FIND candidate pixels before percentile
# sampling tightens the band. Red wraps hue 0/180 -> two prior bands.
_RED_PRIOR: list[HsvBand] = [
    HsvBand(h_lo=0, s_lo=80, v_lo=80, h_hi=10, s_hi=255, v_hi=255),
    HsvBand(h_lo=170, s_lo=80, v_lo=80, h_hi=180, s_hi=255, v_hi=255),
]
_GREEN_PRIOR: list[HsvBand] = [HsvBand(h_lo=35, s_lo=40, v_lo=50, h_hi=95, s_hi=255, v_hi=255)]


def _prior_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR the inRange masks for every prior band, then MORPH_OPEN(3) to despeckle."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)


def _bbox_of_nonzero(half: np.ndarray, pad: int, full_w: int, full_h: int, x_off: int) -> RoiBox:
    ys, xs = np.nonzero(half)
    if xs.size == 0:
        raise ValueError("no matching pixels in this half of the ROI")
    x0, x1 = int(xs.min()) + x_off, int(xs.max()) + x_off
    y0, y1 = int(ys.min()), int(ys.max())
    x = max(0, x0 - pad)
    y = max(0, y0 - pad)
    w = min(full_w - x, (x1 - x0) + 1 + 2 * pad)
    h = min(full_h - y, (y1 - y0) + 1 + 2 * pad)
    return RoiBox(x=x, y=y, w=w, h=h, ref_w=full_w, ref_h=full_h)


def detect_arc_regions(
    roi_ref_bgr: np.ndarray, *, red_prior: list[HsvBand] | None = None, pad: int = 4
) -> tuple[RoiBox, RoiBox]:
    """``(left_arc, right_arc)`` regions found from red pixels in the left/right halves of the ROI.

    ``roi_ref_bgr`` is the chosen ROI crop resized to the detection reference. Raises ``ValueError``
    if either half has no red pixels (caller offers redo / manual selectROI)."""
    prior = red_prior if red_prior is not None else _RED_PRIOR
    hsv = cv2.cvtColor(roi_ref_bgr, cv2.COLOR_BGR2HSV)
    mask = _prior_mask(hsv, prior)
    h, w = mask.shape
    mid = w // 2
    left = _bbox_of_nonzero(mask[:, :mid], pad, w, h, x_off=0)
    right = _bbox_of_nonzero(mask[:, mid:], pad, w, h, x_off=mid)
    return left, right


def sample_hsv_band(
    region_bgr: np.ndarray, color: str, *, lo_pct: float = 5, hi_pct: float = 95
) -> list[HsvBand]:
    """Bracket the matched pixels' HSV percentiles into 1 band (green) or up to 2 (red hue-wrap).

    ``color`` is ``"red"`` or ``"green"``. Raises ``ValueError`` if no pixels match the prior."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    prior = _RED_PRIOR if color == "red" else _GREEN_PRIOR
    pts = hsv[_prior_mask(hsv, prior) > 0]
    if pts.size == 0:
        raise ValueError(f"no {color} pixels to sample in this region")
    s_lo = int(np.percentile(pts[:, 1], lo_pct))
    v_lo = int(np.percentile(pts[:, 2], lo_pct))
    hue = pts[:, 0]
    if color == "green":
        h_lo = int(np.percentile(hue, lo_pct))
        h_hi = int(np.percentile(hue, hi_pct))
        return [HsvBand(h_lo=h_lo, s_lo=s_lo, v_lo=v_lo, h_hi=h_hi, s_hi=255, v_hi=255)]
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
    green_cov_on_green: float, green_cov_on_red: float, *, floor: float = 0.02
) -> float:
    """Midpoint between the green-frame (high) and red-frame (low) green coverage, floored."""
    return round(max((green_cov_on_green + green_cov_on_red) / 2.0, floor), 4)


def _flow_map(d: dict[str, int]) -> CommentedMap:
    """A ruamel mapping rendered inline ``{a: 1, b: 2}`` to match the file's arc/band style."""
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


def _roibox_map(r: RoiBox) -> CommentedMap:
    return _flow_map({"x": r.x, "y": r.y, "w": r.w, "h": r.h, "ref_w": r.ref_w, "ref_h": r.ref_h})


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
    green_bands: list[HsvBand],
    coverage_threshold: float,
) -> None:
    """Round-trip ``system_camera_config.yaml`` (preserving comments), updating screen_roi + the
    auto_trigger regions/bands/threshold. Validates the result via pydantic and writes a ``.bak``
    copy BEFORE touching the original; raises (without writing) if validation fails."""
    yaml_rt = YAML()  # round-trip mode preserves comments + key order
    yaml_rt.preserve_quotes = True
    data = yaml_rt.load(config_path.read_text(encoding="utf-8"))

    data["screen_roi"] = _roibox_map(screen_roi)
    at = data["auto_trigger"]
    at["left_arc"] = _roibox_map(left_arc)
    at["right_arc"] = _roibox_map(right_arc)
    at["red_bands"] = [_hsv_map(b) for b in red_bands]
    at["green_bands"] = [_hsv_map(b) for b in green_bands]
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
