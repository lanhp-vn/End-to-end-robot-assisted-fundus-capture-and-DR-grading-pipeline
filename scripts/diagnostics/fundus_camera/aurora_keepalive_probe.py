"""Keep-alive bench probe for the Optomed Aurora (read-only diagnostic).

Answers the one question the documentation cannot: does periodic API traffic keep the camera
awake between triggers? The Aurora User Manual (p.20) states two FIXED idle timers -- power
save after 2 minutes "not used", power off after 10 minutes -- woken "by using any control
button" (a physical press). The manual exposes NO menu setting to disable either timer, and the
Pictor API spec has no stay-awake / power command. So whether a PING_CAMERA every few seconds
counts as "use" and resets those timers is undocumented and must be measured on the bench.

How to read the result -- start the probe, then DO NOT touch the camera (lift it OFF the
charging station first; the cradle forces power save, manual p.18):
  * Stays RESPONSIVE past the 2:00 and 10:00 marks -> API polling DOES count as "use"; a
    heartbeat keeps it awake. Worth wiring one into the demo's idle loop.
  * Polls start FAILING near ~2:00 (or ~10:00) -> polling does NOT reset the device timer; the
    camera sleeps / powers off regardless of API traffic and must be woken another way
    (physical control button, or keeping inter-patient gaps under two minutes).

Two failure shapes are separated in the log:
  * "(socket reconnected)" on an otherwise-OK poll -> only the idle TCP socket was dropped and
    transparently re-established (connection keep-alive); the device itself was still awake.
  * POLL FAIL (discovery + reconnect both fail, or the reply times out) -> the device itself is
    unreachable: power save or powered off.

Never writes to the camera (PING_CAMERA / GET_CAMERA_STATUS only). Run with Optomed Client
CLOSED -- the camera serves one client at a time.

Usage:
  uv run python scripts/diagnostics/fundus_camera/aurora_keepalive_probe.py \
      [--interval 15] [--settle 0] [--duration 0] [--use-status] [--host IP]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_fundus_config  # noqa: E402
from arm101_hand.fundus_camera import CameraError, PictorClient  # noqa: E402

_CONFIG = _REPO_ROOT / "src" / "arm101_hand" / "data" / "fundus_config.yaml"

# Documented fixed idle timers (Aurora User Manual p.20) -- annotated on the timeline below.
_POWER_SAVE_S = 120.0  # "not used for more than two minutes -> power save mode"
_POWER_OFF_S = 600.0  # "power off if not used for 10 minutes"


def _poll_once(camera: PictorClient, *, use_status: bool) -> tuple[float, bool]:
    """One liveness round-trip. Returns (rtt_seconds, reconnected); raises on failure.

    Re-establishes the connection first if a previous failure left it down, so a wake-up can be
    detected. ``reconnected`` is True when the socket object changed -- the Pictor API drops idle
    sockets and ``client._send`` re-discovers + reconnects transparently, which we want to SEE
    here rather than have masked. Reads ``camera._sock`` directly (same diagnostic peek as
    aurora_wiredump.py); never alters what is sent.
    """
    sock_before = camera._sock
    if camera._sock is None:
        camera.ensure_connected()
    t0 = time.monotonic()
    if use_status:
        camera.get_status()
    else:
        camera.ping()
    return time.monotonic() - t0, sock_before is not camera._sock


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=None, help="static camera IP (default: discover by broadcast)")
    ap.add_argument("--interval", type=float, default=15.0, help="seconds between polls (default: 15)")
    ap.add_argument(
        "--settle",
        type=float,
        default=0.0,
        help="stay SILENT this many seconds before the first poll, to probe the baseline idle "
        "window (default: 0 = start polling immediately)",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="stop after this many seconds of total runtime (default: 0 = run until Ctrl+C)",
    )
    ap.add_argument(
        "--use-status",
        action="store_true",
        help="poll with GET_CAMERA_STATUS instead of the lighter PING_CAMERA",
    )
    args = ap.parse_args()

    conn = load_fundus_config(_CONFIG).connection
    camera = PictorClient(
        host=args.host or conn.host,
        discovery_port=conn.discovery_port,
        message_port=conn.message_port,
        discover_timeout_s=conn.discover_timeout_s,
        connect_timeout_s=conn.connect_timeout_s,
        io_timeout_s=conn.io_timeout_s,
    )
    cmd = "GET_CAMERA_STATUS" if args.use_status else "PING_CAMERA"
    print("Connecting (Optomed Client must be CLOSED) ...")
    try:
        camera.ensure_connected()
    except CameraError as e:
        print(f"camera not ready: {e}", file=sys.stderr)
        return 1
    serial = camera.info.serial if camera.info else "?"
    print(f"Camera ready: serial={serial}. Probing with {cmd} every {args.interval:g}s.")
    print(
        "DO NOT touch the camera now (lift it OFF the charging station -- the cradle forces "
        "power save). Watching the documented 2:00 power-save / 10:00 power-off timers.\n"
    )

    run_start = time.monotonic()  # idle clock starts now, INCLUDING any silent settle window
    last_ok_mono = run_start
    last_ok: bool | None = None
    first_fail_mono: float | None = None
    n_ok = n_fail = n_reconnect = 0
    crossed_save = crossed_off = False

    if args.settle > 0:
        print(f"Staying SILENT for {args.settle:g}s (baseline idle test) ...")
        time.sleep(args.settle)

    try:
        while True:
            now = time.monotonic()
            elapsed = now - run_start
            stamp = datetime.now().strftime("%H:%M:%S")
            try:
                rtt, reconnected = _poll_once(camera, use_status=args.use_status)
                n_ok += 1
                tag = ""
                if reconnected:
                    n_reconnect += 1
                    tag = "  (socket reconnected)"
                print(f"[{stamp}  +{elapsed:6.1f}s]  {cmd} ok    rtt={rtt * 1000:7.1f}ms{tag}")
                if last_ok is False:  # transitioned down -> up
                    print(
                        f"    *** RESPONSIVE again at +{elapsed:.1f}s (was down ~{now - last_ok_mono:.0f}s) ***"
                    )
                last_ok, last_ok_mono = True, now
            except (CameraError, OSError) as e:
                n_fail += 1
                if first_fail_mono is None:
                    first_fail_mono = now
                print(f"[{stamp}  +{elapsed:6.1f}s]  {cmd} FAIL  {type(e).__name__}: {e}")
                if last_ok:  # transitioned up -> down
                    print(
                        f"    *** UNRESPONSIVE at +{elapsed:.1f}s (last good poll {now - last_ok_mono:.0f}s ago) ***"
                    )
                last_ok = False

            if not crossed_save and elapsed >= _POWER_SAVE_S:
                crossed_save = True
                print(
                    f"    --- crossed 2:00 power-save threshold ({'still RESPONSIVE' if last_ok else 'already DOWN'}) ---"
                )
            if not crossed_off and elapsed >= _POWER_OFF_S:
                crossed_off = True
                print(
                    f"    --- crossed 10:00 power-off threshold ({'still RESPONSIVE' if last_ok else 'already DOWN'}) ---"
                )

            if args.duration > 0 and elapsed >= args.duration:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n^C -- stopping.")
    finally:
        camera.close()

    total = time.monotonic() - run_start
    print("\n--- summary ---")
    print(f"  ran {total:.0f}s; polls: {n_ok} ok / {n_fail} fail; transparent reconnects: {n_reconnect}")
    if n_fail == 0:
        print(f"  stayed RESPONSIVE the whole run with {cmd} every {args.interval:g}s.")
        if total >= _POWER_OFF_S:
            print(
                "  -> survived BOTH the 2-min and 10-min idle timers: API polling counts as 'use' "
                "and keeps the device awake. A heartbeat in the demo's idle loop will work."
            )
        elif total >= _POWER_SAVE_S:
            print(
                "  -> survived the 2-min power-save timer, but the run did not reach 10:00. Re-run "
                "with --duration 720 to confirm it also beats the power-off timer."
            )
        else:
            print("  -> run was shorter than the 2-min power-save timer; run longer to conclude.")
    else:
        first = (first_fail_mono - run_start) if first_fail_mono is not None else total
        print(f"  first failure at +{first:.0f}s.")
        print(
            "  -> API polling did NOT keep it alive past that point. Compare with the 2:00 / 10:00 "
            "marks above: a failure near 2:00 = power save despite polls; near 10:00 = power off. "
            "The camera must then be woken another way (physical control button)."
        )
    print("Camera connection closed. (Wake the camera with a control button if it slept.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
