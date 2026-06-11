# src/arm101_hand/camera/capture.py
"""Capture-pipeline I/O: wait for the new file to land, then save it + a sidecar.

The new-file wait guards the write race (a file can appear in the filelist before its
bytes are fully flushed) by requiring its size to be nonzero and unchanged across
``stable_polls`` consecutive filelist reads.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from arm101_hand.camera.protocol import FileInfo, capture_filename, diff_new_files, sidecar_dict


def wait_for_new_files(
    client,
    before: set[str],
    *,
    dcim_root: str,
    timeout_s: float,
    poll_s: float,
    stable_polls: int,
) -> list[FileInfo]:
    """Poll until new (non-dir) files appear with a size stable across ``stable_polls``
    consecutive reads. Returns the stabilized files, or ``[]`` if none stabilized in time.
    """
    prev: dict[str, int] = {}
    rounds = 0
    deadline = time.monotonic() + timeout_s
    while True:
        new = {f.filename: f for f in diff_new_files(before, client.get_filelist(dcim_root))}
        sizes = {name: f.filesize for name, f in new.items()}
        if new and sizes == prev and all(s > 0 for s in sizes.values()):
            rounds += 1
            if rounds >= stable_polls:
                return list(new.values())
        else:
            rounds = 1 if (new and all(s > 0 for s in sizes.values())) else 0
        prev = sizes
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll_s)


def save_capture(
    info: FileInfo,
    data: bytes,
    dest_dir: Path,
    *,
    captured_at: datetime,
    trigger_no: int,
    camera_serial: str,
    camera_sw: str,
    camera_wifi: str,
) -> Path:
    """Write ``data`` to ``dest_dir/<timestamped-name>`` plus a ``.json`` sidecar; return the image path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / capture_filename(info, captured_at)
    out.write_bytes(data)
    meta = sidecar_dict(
        info, captured_at=captured_at, trigger_no=trigger_no,
        camera_serial=camera_serial, camera_sw=camera_sw, camera_wifi=camera_wifi,
    )
    out.with_suffix(out.suffix + ".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out
