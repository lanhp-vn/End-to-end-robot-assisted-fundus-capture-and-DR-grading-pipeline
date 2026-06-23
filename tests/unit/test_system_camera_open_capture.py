"""Device-layer tests for the capture-format plumbing in ``open_capture`` / ``WebcamPreview``.

No hardware: ``cv2.VideoCapture`` is monkeypatched with a fake that records ``set`` calls, so we
assert WHAT the device layer asks the driver for (MJPG format + resolution) without a real camera.
"""

from pathlib import Path

import cv2

from arm101_hand.system_camera import (
    WebcamPreview,
    open_capture,
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


def test_open_capture_reasserts_mjpg_after_resolution(monkeypatch):
    # This camera reverts the format to uncompressed YUY2 when the resolution is set AFTER MJPG, which
    # collapses the high-res modes to ~2 fps over USB 2.0. So MJPG is set TWICE: once before the size
    # (to expose the high-res mode) and once after (so the size-set can't silently revert to YUY2).
    fake = _FakeCap()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: fake)

    open_capture(0, width=2592, height=1944)

    props = _props(fake)
    fourcc_at = [i for i, p in enumerate(props) if p == cv2.CAP_PROP_FOURCC]
    assert len(fourcc_at) == 2  # bracketed: one before the size, one after
    assert fourcc_at[0] < props.index(cv2.CAP_PROP_FRAME_WIDTH)
    assert fourcc_at[1] > props.index(cv2.CAP_PROP_FRAME_HEIGHT)
    mjpg = cv2.VideoWriter.fourcc(*"MJPG")
    assert [v for p, v in fake.set_calls if p == cv2.CAP_PROP_FOURCC] == [mjpg, mjpg]


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


def test_latest_frame_none_before_start(tmp_path):
    p = WebcamPreview(index=0, window_title="t", record_dir=tmp_path)
    assert p.latest_frame() is None


def test_set_status_text_does_not_raise(tmp_path):
    p = WebcamPreview(index=0, window_title="t", record_dir=tmp_path)
    p.set_status_text("AUTO armed", (0, 255, 0))  # safe before the thread starts
