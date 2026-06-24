"""Offline replay of the red-detection sweep over saved arc-calibration cases (host-only diagnostic).

Re-runs the calibration red-detection sweep (``sweep_red_detection``) over the labelled cases saved
by ``calibrate_view.py`` (and the ``usb_camera_arc_debug.py`` collector) with NO hardware -- no arm,
no hand, no camera, no Aurora. Each case is a ``<stem>.json`` sidecar (carrying the operator's
``expected`` 'red'/'clear' label + the dragged arc geometry) beside a ``<stem>_clean.png`` (the
800x480 deskewed ROI the detector runs on). The tool loads every labelled case, rebuilds the
``ArcCase`` list, runs the sweep, and prints the chosen red HSV band + coverage threshold + per-case
outcome -- including the transitional one-arc-blank 'red' frames the sweep excludes from the fit.

Use it to validate a ``calibration.py`` / sweep change against the real collected evidence (e.g. after
retuning the coverage floor, hue pooling, or ``blank_eps``) WITHOUT re-running the staged bench grab.
It only READS the saved cases (under git-ignored ``media_outputs/``); it writes nothing and never
touches ``system_camera_config.yaml`` (IL-5) -- re-derive the live config with ``calibrate_view.py``.

Cases without an ``expected`` label (e.g. raw ``usb_camera_arc_debug.py`` captures, which carry only
a free ``expected_note``) are skipped -- capture labelled cases via ``calibrate_view.py``'s test loop,
or add an ``expected`` field to their sidecars first.

Usage:
  uv run python scripts/diagnostics/system_camera/arc_sweep_replay.py [--from DIR]

  --from DIR   folder of saved cases (default: <repo>/media_outputs/arc_calib)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config.system_camera_config import RoiBox  # noqa: E402
from arm101_hand.system_camera.calibration import (  # noqa: E402
    ArcCase,
    SweepResult,
    describe_case,
    sweep_red_detection,
)

_DEFAULT_DIR = _REPO_ROOT / "media_outputs" / "arc_calib"


@dataclass(frozen=True)
class LabeledCase:
    """One saved case loaded from disk: the file stem, the labelled ArcCase (deskewed-ROI frame +
    'red'/'clear' expected), and the arc geometry recorded in its sidecar."""

    stem: str
    case: ArcCase
    left_arc: RoiBox
    right_arc: RoiBox


def load_arc_cases(case_dir: Path) -> list[LabeledCase]:
    """Load every ``<stem>.json`` carrying a valid ``expected`` ('red'/'clear') label + its
    ``<stem>_clean.png`` from ``case_dir``. Unlabelled sidecars (missing/invalid ``expected``) and
    cases whose clean PNG is missing or unreadable are skipped. Sorted by stem for stable indices."""
    cases: list[LabeledCase] = []
    for jp in sorted(case_dir.glob("*.json")):
        meta = json.loads(jp.read_text(encoding="utf-8"))
        expected = meta.get("expected")
        if expected not in ("red", "clear"):
            continue
        frame = cv2.imread(str(jp.with_name(jp.stem + "_clean.png")))
        if frame is None:
            continue
        cases.append(
            LabeledCase(
                stem=jp.stem,
                case=ArcCase(frame=frame, expected=expected),
                left_arc=RoiBox(**meta["left_arc"]),
                right_arc=RoiBox(**meta["right_arc"]),
            )
        )
    return cases


def _arc_key(lc: LabeledCase) -> tuple[int, ...]:
    a, b = lc.left_arc, lc.right_arc
    return (a.x, a.y, a.w, a.h, b.x, b.y, b.w, b.h)


def format_sweep_report(result: SweepResult, cases: list[LabeledCase]) -> str:
    """Render the sweep outcome as text: chosen bands + threshold, separability over the FITTED set,
    the fitted/excluded tally, and a per-case line (ok / WRONG / EXCLUDED) with the describe_case
    sentence. Pure (no I/O) so it is unit-testable."""
    n_excluded = len(result.excluded_transitional)
    n_fit = len(cases) - n_excluded
    ok = n_fit - len(result.unsatisfied)
    lines = [
        f"coverage_threshold = {result.coverage_threshold}",
        f"separable (over fitted set) = {result.separable}",
        "red_bands:",
    ]
    lines += [f"  hue[{b.h_lo}-{b.h_hi}]  S>={b.s_lo}  V>={b.v_lo}" for b in result.red_bands]
    lines += [
        "",
        f"{ok}/{n_fit} fitted cases correct ({n_excluded} excluded as transitional)",
        "",
        "per-case:",
    ]
    excluded = set(result.excluded_transitional)
    unsatisfied = set(result.unsatisfied)
    for i, lc in enumerate(cases):
        det_left, det_right = result.case_detections[i]
        tag = "EXCLUDED" if i in excluded else ("WRONG" if i in unsatisfied else "ok")
        lines.append(
            f"  [{i:2d}] {tag:8s} exp={lc.case.expected:5s}  "
            f"{describe_case(lc.case.expected, det_left, det_right)}"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--from",
        dest="from_dir",
        default=str(_DEFAULT_DIR),
        metavar="DIR",
        help="folder of saved cases (default: <repo>/media_outputs/arc_calib)",
    )
    args = ap.parse_args()

    case_dir = Path(args.from_dir)
    if not case_dir.is_dir():
        print(f"ERROR: no such directory: {case_dir}", file=sys.stderr)
        return 1

    n_json = len(list(case_dir.glob("*.json")))
    cases = load_arc_cases(case_dir)
    if not cases:
        print(
            f"ERROR: no labelled cases in {case_dir} ({n_json} .json file(s) present, none with a valid "
            "'expected' label + readable _clean.png). Capture labelled cases with calibrate_view.py.",
            file=sys.stderr,
        )
        return 1

    n_red = sum(lc.case.expected == "red" for lc in cases)
    n_clear = sum(lc.case.expected == "clear" for lc in cases)
    skipped = n_json - len(cases)
    suffix = f", {skipped} skipped" if skipped else ""
    print(f"Loaded {len(cases)} labelled case(s) from {case_dir}  ({n_red} red, {n_clear} clear{suffix})")

    left_arc, right_arc = cases[0].left_arc, cases[0].right_arc
    distinct = {_arc_key(lc) for lc in cases}
    if len(distinct) > 1:
        print(f"  NOTE: {len(distinct)} distinct arc geometries across cases; using the first.")
    print(
        f"  arcs: left={left_arc.x},{left_arc.y},{left_arc.w},{left_arc.h}  "
        f"right={right_arc.x},{right_arc.y},{right_arc.w},{right_arc.h}"
    )

    try:
        result = sweep_red_detection([lc.case for lc in cases], left_arc, right_arc)
    except ValueError as exc:
        print(f"\nSweep could not fit a band: {exc}", file=sys.stderr)
        return 1

    print("\n=== SWEEP RESULT ===")
    print(format_sweep_report(result, cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
