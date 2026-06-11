# scripts/diagnostics/aurora_probe.py
"""Read-only Aurora reachability + read-path probe (built on PictorClient).

Discovers the camera, prints status + a short filelist, and (with --get-file) pulls one
file to the OS temp dir. Never writes to the camera. Mirrors the other diagnostics
(scan.py / find_port.py): a quick health check, safe to run anytime Optomed Client is closed.

Usage:
  uv run python scripts/diagnostics/aurora_probe.py [--host IP] [--get-file PATH]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.camera import CameraError, PictorClient  # noqa: E402
from arm101_hand.config import load_camera_config  # noqa: E402

_CONFIG = _REPO_ROOT / "src" / "arm101_hand" / "data" / "camera_config.yaml"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None, help="static camera IP (default: discover by broadcast)")
    ap.add_argument("--get-file", default=None, help="camera path to pull, e.g. \\DCIM\\P0001\\IM0002EY.JPG")
    args = ap.parse_args()

    conn = load_camera_config(_CONFIG).connection
    camera = PictorClient(
        host=args.host or conn.host,
        discovery_port=conn.discovery_port,
        message_port=conn.message_port,
        discover_timeout_s=conn.discover_timeout_s,
        connect_timeout_s=conn.connect_timeout_s,
        io_timeout_s=conn.io_timeout_s,
    )
    try:
        camera.ensure_connected()
    except CameraError as e:
        print(f"camera not ready: {e}", file=sys.stderr)
        return 1
    info, status = camera.info, camera.get_status()
    print(
        f"CAMERA serial={info.serial} mac={info.mac} interface={info.interface_level} "
        f"customization={info.customization}"
    )
    print(f"STATUS sw={status.sw_version} wifi={status.wifi_version} subscribed={status.client_subscribed}")
    files = camera.get_filelist("\\DCIM")
    print(f"FILELIST \\DCIM: {len(files)} entries")
    for f in files[:10]:
        print(f"  {f.filename!r} size={f.filesize} type=0x{f.file_type:X}")

    if args.get_file:
        fi, data = camera.get_file(args.get_file)
        out = os.path.join(
            tempfile.gettempdir(), "aurora_" + os.path.basename(args.get_file).replace("\\", "_")
        )
        Path(out).write_bytes(data)
        print(f"GET_FILE {fi.filename!r}: {len(data)} bytes (jpeg={data[:3] == b'\xff\xd8\xff'}) -> {out}")
    camera.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
