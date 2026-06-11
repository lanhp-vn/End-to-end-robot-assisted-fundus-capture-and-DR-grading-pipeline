# tests/unit/test_camera_capture.py
import json
from datetime import UTC, datetime

from arm101_hand.camera.capture import save_capture, wait_for_new_files
from arm101_hand.camera.protocol import FILE, FileInfo


class _FakeClient:
    def __init__(self, listings):
        self._listings = list(listings)

    def get_filelist(self, path):
        return self._listings.pop(0) if len(self._listings) > 1 else self._listings[0]


def _fi(name, size):
    return FileInfo(filesize=size, file_type=FILE, file_date=0, file_time=0, filename=name)


def test_wait_for_new_files_returns_when_size_stable():
    before = {"\\DCIM\\P0001\\IM0001EY.JPG"}
    new = _fi("\\DCIM\\P0001\\IM0002EY.JPG", 500)
    # poll 1: appears (size 500); poll 2: same size 500 -> stable (stable_polls=2)
    client = _FakeClient([[new], [new]])
    out = wait_for_new_files(client, before, dcim_root="\\DCIM", timeout_s=5, poll_s=0.0, stable_polls=2)
    assert [f.filename for f in out] == ["\\DCIM\\P0001\\IM0002EY.JPG"]


def test_wait_for_new_files_empty_on_timeout():
    before = set()
    client = _FakeClient([[]])  # nothing ever appears
    out = wait_for_new_files(client, before, dcim_root="\\DCIM", timeout_s=0.05, poll_s=0.0, stable_polls=2)
    assert out == []


def test_save_capture_writes_jpeg_and_sidecar(tmp_path):
    info = _fi("\\DCIM\\P0001\\IM0010EY.JPG", 4)
    ts = datetime(2026, 6, 10, 14, 15, 30, tzinfo=UTC)
    path = save_capture(
        info,
        b"\xff\xd8\xff\xe0",
        tmp_path,
        captured_at=ts,
        trigger_no=1,
        camera_serial="S",
        camera_sw="sw",
        camera_wifi="wifi",
    )
    assert path.name == "20260610T141530Z_DCIM_P0001_IM0010EY.JPG"
    assert path.read_bytes() == b"\xff\xd8\xff\xe0"
    sidecar = path.with_suffix(path.suffix + ".json")
    meta = json.loads(sidecar.read_text())
    assert meta["filesize"] == 4 and meta["trigger_no"] == 1
