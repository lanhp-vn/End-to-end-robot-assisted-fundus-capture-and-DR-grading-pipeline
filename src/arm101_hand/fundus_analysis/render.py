"""Pure composition of the DR results panel + thin cv2 image-codec wrappers.

No model, no network, no disk I/O. The demo composes the ``[ image | panel ]``
frame here (numpy + ``cv2.putText``/``cv2.rectangle`` — NOT cv2-window calls),
PNG-encodes it, and hands the bytes to ``WebcamPreview.show_still``. That keeps
the capture thread the sole cv2-window owner (see ``system_camera/preview.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from arm101_hand.config.fundus_analysis_config import DR_LABELS
from arm101_hand.fundus_analysis.grader import GradeResult

# Severity colours (BGR), grade 0 (No DR) -> grade 4 (Proliferative).
_GRADE_BGR: dict[int, tuple[int, int, int]] = {
    0: (0, 160, 0),
    1: (0, 200, 160),
    2: (0, 190, 230),
    3: (0, 120, 240),
    4: (40, 40, 230),
}
_PANEL_BG = (32, 32, 32)
_TEXT = (235, 235, 235)
_MUTED = (170, 170, 170)
_BAR_BG = (70, 70, 70)
_BAR_FG = (180, 180, 180)
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_ABBREV = {"No DR": "No DR", "Mild": "Mild", "Moderate": "Mod", "Severe": "Severe", "Proliferative": "Prolif"}
_DISCLAIMER_LINES = ("Not a medical device.", "Research/educational use only.")


@dataclass(eq=False)
class GradedShot:
    """One captured shot + its grade (``None`` if grading was unavailable/failed)."""

    image_bgr: np.ndarray
    result: GradeResult | None
    source_name: str = ""


def _bar_width(prob: float, max_px: int) -> int:
    """Pixel width of a probability bar; clamps ``prob`` to ``[0, 1]``."""
    p = min(1.0, max(0.0, prob))
    return int(round(p * max_px))


def _most_severe(results: list[GradeResult]) -> int:
    """Index of the most-severe result: max grade, tie -> max top-prob, tie -> latest."""
    best = 0
    for i in range(1, len(results)):
        a, b = results[i], results[best]
        if (a.grade, a.probabilities[a.label]) >= (b.grade, b.probabilities[b.label]):
            best = i
    return best


def _scale_to_height(image_bgr: np.ndarray, height: int) -> np.ndarray:
    """Resize ``image_bgr`` to ``height`` px tall, preserving aspect ratio."""
    h, w = image_bgr.shape[:2]
    if h <= 0:
        return image_bgr
    scale = height / h
    new_w = max(1, int(round(w * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    return cv2.resize(image_bgr, (new_w, height), interpolation=interp)


def _put_disclaimer(panel: np.ndarray, display_height: int) -> None:
    base = display_height - 14
    for i, line in enumerate(reversed(_DISCLAIMER_LINES)):
        cv2.putText(panel, line, (16, base - i * 18), _FONT, 0.42, _MUTED, 1, cv2.LINE_AA)


def _draw_bars(
    panel: np.ndarray, result: GradeResult, pred_grade: int, x: int, y: int, panel_width: int
) -> None:
    label_w, pct_w, gap, row_h = 86, 46, 8, 24
    bar_x = x + label_w + pct_w + gap
    bar_max = max(10, panel_width - bar_x - 16)
    for i in range(len(DR_LABELS)):
        lbl = DR_LABELS[i]
        p = result.probabilities[lbl]
        pred = i == pred_grade
        tc = _GRADE_BGR.get(pred_grade, _TEXT) if pred else _TEXT
        base = y + i * row_h
        cv2.putText(panel, _ABBREV.get(lbl, lbl), (x, base + 13), _FONT, 0.45, tc, 1, cv2.LINE_AA)
        cv2.putText(panel, f"{p * 100:3.0f}%", (x + label_w, base + 13), _FONT, 0.45, tc, 1, cv2.LINE_AA)
        cv2.rectangle(panel, (bar_x, base + 2), (bar_x + bar_max, base + 16), _BAR_BG, -1)
        w = _bar_width(p, bar_max)
        if w > 0:
            fg = _GRADE_BGR.get(pred_grade, _BAR_FG) if pred else _BAR_FG
            cv2.rectangle(panel, (bar_x, base + 2), (bar_x + w, base + 16), fg, -1)


def _panel_graded(
    graded: list[tuple[np.ndarray, GradeResult]],
    sev: int,
    n_total: int,
    panel_width: int,
    display_height: int,
) -> np.ndarray:
    panel: np.ndarray = np.full((display_height, panel_width, 3), _PANEL_BG, np.uint8)
    lead = graded[sev][1]
    gc = _GRADE_BGR.get(lead.grade, _TEXT)
    x = 16
    cv2.putText(panel, f"DR GRADE {lead.grade}", (x, 50), _FONT, 1.1, gc, 2, cv2.LINE_AA)
    cv2.putText(panel, lead.label, (x, 88), _FONT, 0.8, gc, 2, cv2.LINE_AA)
    cv2.putText(panel, f"Confidence: {lead.confidence}", (x, 120), _FONT, 0.55, _TEXT, 1, cv2.LINE_AA)
    y = 152
    if n_total > 1:
        cv2.putText(
            panel, f"Shots ({len(graded)}/{n_total} graded):", (x, y), _FONT, 0.5, _MUTED, 1, cv2.LINE_AA
        )
        y += 24
        for i, (_, r) in enumerate(graded):
            sel = i == sev
            color = _GRADE_BGR.get(r.grade, _TEXT) if sel else _TEXT
            prefix = ">" if sel else " "
            line = f"{prefix} {i + 1}. G{r.grade} {r.label} {r.probabilities[r.label] * 100:3.0f}%"
            cv2.putText(panel, line, (x, y), _FONT, 0.48, color, 1, cv2.LINE_AA)
            y += 22
        y += 12
    cv2.putText(panel, "Class probabilities:", (x, y), _FONT, 0.5, _MUTED, 1, cv2.LINE_AA)
    y += 22
    _draw_bars(panel, lead, lead.grade, x, y, panel_width)
    _put_disclaimer(panel, display_height)
    return panel


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _panel_unavailable(
    shots: list[GradedShot], reason: str | None, panel_width: int, display_height: int
) -> np.ndarray:
    panel: np.ndarray = np.full((display_height, panel_width, 3), _PANEL_BG, np.uint8)
    x = 16
    cv2.putText(panel, "DR GRADING", (x, 50), _FONT, 0.95, _TEXT, 2, cv2.LINE_AA)
    y = 92
    for line in _wrap(reason or "grading unavailable", 30):
        cv2.putText(panel, line, (x, y), _FONT, 0.5, (40, 160, 230), 1, cv2.LINE_AA)
        y += 24
    y += 12
    if shots:
        cv2.putText(panel, f"{len(shots)} shot(s) captured:", (x, y), _FONT, 0.5, _MUTED, 1, cv2.LINE_AA)
        y += 24
        for i, shot in enumerate(shots):
            name = shot.source_name or (shot.result.source_image if shot.result else f"shot {i + 1}")
            cv2.putText(panel, f"  {i + 1}. {name[:24]}", (x, y), _FONT, 0.45, _TEXT, 1, cv2.LINE_AA)
            y += 22
    _put_disclaimer(panel, display_height)
    return panel


def compose_summary_panel(
    shots: list[GradedShot],
    *,
    unavailable_reason: str | None = None,
    panel_width: int = 420,
    display_height: int = 720,
) -> np.ndarray:
    """Compose the ``[ most-severe image | summary panel ]`` BGR frame for the popup.

    Handles ``N >= 1`` shots (``N == 1`` is the single-shot view). Never raises for
    display: with no graded shot it shows ``unavailable_reason`` beside the most
    recent shot's image.
    """
    graded: list[tuple[np.ndarray, GradeResult]] = []
    for s in shots:
        r = s.result
        if r is not None:
            graded.append((s.image_bgr, r))
    if graded:
        sev = _most_severe([r for _, r in graded])
        image = graded[sev][0]
        panel = _panel_graded(graded, sev, len(shots), panel_width, display_height)
    else:
        image = (
            shots[-1].image_bgr if shots else np.zeros((display_height, max(1, display_height), 3), np.uint8)
        )
        panel = _panel_unavailable(shots, unavailable_reason, panel_width, display_height)
    left = _scale_to_height(image, display_height)
    return np.hstack([left, panel])


def encode_png(frame: np.ndarray) -> bytes:
    """PNG-encode a BGR frame (lossless → crisp text) to bytes for ``show_still``."""
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise ValueError("failed to PNG-encode frame")
    return buf.tobytes()


def decode_bgr(data: bytes) -> np.ndarray | None:
    """Decode JPEG/PNG bytes to a BGR array; ``None`` if the bytes are not an image."""
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
