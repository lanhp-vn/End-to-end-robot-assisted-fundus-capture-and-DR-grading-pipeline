"""Device-layer tests for the capture-format plumbing in ``open_capture`` / ``WebcamPreview``.

No hardware: ``cv2.VideoCapture`` is monkeypatched with a fake that records ``set`` calls, so we
assert WHAT the device layer asks the driver for (MJPG format + resolution) without a real camera.
"""

from pathlib import Path

import cv2
import numpy as np

from arm101_hand.system_camera import (
    WebcamPreview,
    grab_full_res_frame,
    open_capture,
    read_settled_frame,
)
from arm101_hand.system_camera.preview import _MAX_DIM_REQUEST


class _FakeCap:
    def __init__(self, opened: bool = True) -> None:
        self._opened = opened
        self.set_calls: list[tuple[int, float]] = []

    def isOpened(self) -> bool:  # noqa: N802 -- mirrors cv2.VideoCapture's camelCase API
        return self._opened

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        return True

    def get(self, prop: int) -> float:
        return 0.0

    def release(self) -> None:
        pass


def _props(fake: _FakeCap) -> list[int]:
    return [p for p, _ in fake.set_calls]


def _value_for(fake: _FakeCap, prop: int) -> float:
    return next(v for p, v in fake.set_calls if p == prop)


def test_open_capture_requests_mjpg_then_max(monkeypatch):
    fake = _FakeCap()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: fake)

    cap = open_capture(0)

    assert cap is fake
    props = _props(fake)
    # FOURCC must be set BEFORE resolution -- UVC only exposes high-res modes once MJPG is selected.
    assert props.index(cv2.CAP_PROP_FOURCC) < props.index(cv2.CAP_PROP_FRAME_WIDTH)
    assert _value_for(fake, cv2.CAP_PROP_FOURCC) == cv2.VideoWriter.fourcc(*"MJPG")
    # width/height default (None) => request the driver's max via a large sentinel.
    assert _value_for(fake, cv2.CAP_PROP_FRAME_WIDTH) == _MAX_DIM_REQUEST
    assert _value_for(fake, cv2.CAP_PROP_FRAME_HEIGHT) == _MAX_DIM_REQUEST


def test_open_capture_explicit_resolution_and_format(monkeypatch):
    fake = _FakeCap()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: fake)

    open_capture(0, width=4000, height=3000, fourcc="YUY2")

    assert _value_for(fake, cv2.CAP_PROP_FOURCC) == cv2.VideoWriter.fourcc(*"YUY2")
    assert _value_for(fake, cv2.CAP_PROP_FRAME_WIDTH) == 4000
    assert _value_for(fake, cv2.CAP_PROP_FRAME_HEIGHT) == 3000


def test_open_capture_default_leaves_autofocus_on(monkeypatch):
    # Default: autofocus=True + focus=None => request AF on, never touch the focus position. Callers
    # that don't lock focus (e.g. usb_camera_probe with the schema defaults) keep the cam's autofocus.
    fake = _FakeCap()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: fake)

    open_capture(0)

    assert _value_for(fake, cv2.CAP_PROP_AUTOFOCUS) == 1.0
    assert cv2.CAP_PROP_FOCUS not in _props(fake)  # focus position untouched when focus=None


def test_open_capture_manual_focus(monkeypatch):
    # autofocus=False + focus=600 => AF off (0.0) then the manual lens position. This is what the
    # data yaml asks for so the Aurora screen stays sharp (DSHOW-only; see system_camera_manual_focus).
    fake = _FakeCap()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: fake)

    open_capture(0, autofocus=False, focus=600)

    assert _value_for(fake, cv2.CAP_PROP_AUTOFOCUS) == 0.0
    assert _value_for(fake, cv2.CAP_PROP_FOCUS) == 600.0
    # AF must be turned off BEFORE the manual focus position takes hold.
    assert _props(fake).index(cv2.CAP_PROP_AUTOFOCUS) < _props(fake).index(cv2.CAP_PROP_FOCUS)


def test_open_capture_skips_format_when_device_will_not_open(monkeypatch):
    fake = _FakeCap(opened=False)
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: fake)

    cap = open_capture(0)

    assert cap is fake
    assert fake.set_calls == []  # nothing configured on a device that never opened


def test_webcampreview_forwards_format_to_open_capture(monkeypatch):
    captured: dict[str, object] = {}

    def _spy(index, backend="auto", *, fourcc="MJPG", width=None, height=None, autofocus=True, focus=None):
        captured.update(
            index=index,
            backend=backend,
            fourcc=fourcc,
            width=width,
            height=height,
            autofocus=autofocus,
            focus=focus,
        )
        return _FakeCap(opened=False)

    monkeypatch.setattr("arm101_hand.system_camera.preview.open_capture", _spy)

    preview = WebcamPreview(
        index=2,
        window_title="t",
        record_dir=Path("."),
        backend="dshow",
        fourcc="MJPG",
        width=4000,
        height=3000,
        autofocus=False,
        focus=600,
    )
    preview._open_capture()

    assert captured == {
        "index": 2,
        "backend": "dshow",
        "fourcc": "MJPG",
        "width": 4000,
        "height": 3000,
        "autofocus": False,
        "focus": 600,
    }


class _FrameCap:
    """Fake capture that always delivers frames of a fixed size and records release(). The
    reopen-based grab opens a fresh capture per resolution, so we model each open as its own cap."""

    def __init__(self, width: int, height: int, *, deliver: bool = True) -> None:
        self._w, self._h = width, height
        self._deliver = deliver
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 -- mirrors cv2.VideoCapture's camelCase API
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self._deliver:
            return False, None
        return True, np.zeros((self._h, self._w, 3), dtype=np.uint8)

    def get(self, prop: int) -> float:
        return 0.0

    def release(self) -> None:
        self.released = True


def test_read_settled_frame_returns_freshest_target_size_frame():
    cap = _FrameCap(4000, 3000)
    frame = read_settled_frame(cap, warmup_frames=2, target=(3000, 4000))
    assert frame is not None
    assert frame.shape[:2] == (3000, 4000)


def test_read_settled_frame_returns_none_when_no_frame_arrives():
    cap = _FrameCap(4000, 3000, deliver=False)
    assert read_settled_frame(cap, warmup_frames=2) is None


def test_grab_full_res_frame_reopens_at_still_then_stream(monkeypatch):
    # MSMF can't switch an open capture's resolution; the grab reopens the device instead.
    original = _FrameCap(640, 480)
    grab_cap = _FrameCap(4000, 3000)
    stream_cap = _FrameCap(640, 480)
    opened = [grab_cap, stream_cap]
    calls = []

    def _spy(index, backend="auto", *, fourcc="MJPG", width=None, height=None, autofocus=True, focus=None):
        calls.append((index, backend, fourcc, width, height, autofocus, focus))
        return opened.pop(0)

    monkeypatch.setattr("arm101_hand.system_camera.preview.open_capture", _spy)

    frame, new_cap = grab_full_res_frame(
        original,
        1,
        "dshow",
        fourcc="MJPG",
        still_width=4000,
        still_height=3000,
        stream_width=640,
        stream_height=480,
        warmup_frames=2,
        autofocus=False,
        focus=600,
    )

    assert original.released is True  # the live stream capture is freed BEFORE reopening
    assert calls[0][3:5] == (4000, 3000)  # reopened at the still size for the grab
    assert calls[1][3:5] == (640, 480)  # then reopened at the stream size
    # Manual focus must hold through BOTH reopens (the still grab AND the resumed stream), else the
    # full-res grab on DSHOW comes back soft / the stream goes back to autofocus hunting.
    assert calls[0][5:] == (False, 600)
    assert calls[1][5:] == (False, 600)
    assert grab_cap.released is True  # the full-res grab capture is freed after the grab
    assert frame is not None and frame.shape[:2] == (3000, 4000)
    assert new_cap is stream_cap  # caller must adopt the returned stream capture


def test_grab_full_res_frame_returns_none_frame_but_still_reopens_stream(monkeypatch):
    original = _FrameCap(640, 480)
    grab_cap = _FrameCap(4000, 3000, deliver=False)  # full-res open delivers nothing
    stream_cap = _FrameCap(640, 480)
    opened = [grab_cap, stream_cap]

    def _spy(index, backend="auto", *, fourcc="MJPG", width=None, height=None, autofocus=True, focus=None):
        return opened.pop(0)

    monkeypatch.setattr("arm101_hand.system_camera.preview.open_capture", _spy)

    frame, new_cap = grab_full_res_frame(
        original,
        1,
        "auto",
        fourcc="MJPG",
        still_width=4000,
        still_height=3000,
        stream_width=640,
        stream_height=480,
        warmup_frames=2,
    )

    assert frame is None
    assert new_cap is stream_cap  # stream is reopened either way so the caller can keep going
