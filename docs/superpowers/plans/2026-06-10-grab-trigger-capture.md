# grab_trigger_capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `grab_trigger_capture.py` demo where the AmazingHand index finger presses the Optomed Aurora's shutter, then the workspace pulls the freshly-captured fundus image (+ metadata) over the Pictor Wi-Fi protocol into `fundus_images/`.

**Architecture:** A new read-only `src/arm101_hand/camera/` package (pure `protocol.py` + socket `client.py` + I/O `capture.py`) mirrors the hand's pure-core/thin-shell split. The demo reuses `grab_common.run_grab_demo` (full staged arm+hand grab) with a new `on_hold` hook that, per `SPACE`, runs a press→hold→release cycle then diffs `GET_FILELIST` to find and `GET_FILE` the new capture. Finger read/drive is factored into `hand/finger_io.py` (shared with `grab_toggle`); press-depth math is reused from `hand/index_toggle.py`.

**Tech Stack:** Python 3.12, pydantic v2, PyYAML, `rustypot` (hand bus), stdlib `socket`/`struct` (camera), `pytest`, `ruff`, `mypy`. Spec: `docs/superpowers/specs/2026-06-10-grab-trigger-capture-design.md`.

**Branch:** `feat/grab-trigger-capture` (already created; spec already committed).

**Protocol reference values (verified on hardware 2026-06-10):** header `<III` = `cmdId, code, seqId` (12 B LE). Discovery UDP `:3000`, messages TCP `:8000`. `CODE_OK=0x16AC5001`, `CODE_FAIL=0x16AC5002`, `CODE_REQUEST=0x16AC5003`. `CAMERA_DETECTED` payload (after the 4-byte cmdId, total 56 B): `interfaceLevel u32`, `mac[20]`, `cameraReserved u32`, `cameraCustomization u32`, `serial[20]`. `GET_CAMERA_STATUS` OK payload (36 B): `clientSubscribed u32`, `cameraSwVersion[16]`, `wifiSwVersion[16]`. `GET_FILELIST` request payload = `filepath[64]`; OK payload = `count u32` + `count × fileInfo(40 B)`. `fileInfo` = `filesize u32, fileType u32, fileDate u16, fileTime u16, filename[28]`. `GET_FILE` request = `filepath[64]`; OK payload = `fileInfo(40 B)` + `filesize` data bytes. `CODE_FAIL` payload = `errCode u32` + `errMsg[64]` (68 B). `fileType` bit `DIRECTORY=0x10`, `FILE=0x20`.

---

## Task 1: Camera protocol — constants, header, parsers, FAT32

**Files:**
- Create: `src/arm101_hand/camera/protocol.py`
- Test: `tests/unit/test_camera_protocol.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_camera_protocol.py
import struct

from arm101_hand.camera.protocol import (
    CODE_OK,
    CODE_REQUEST,
    DETECT_CAMERA,
    DIRECTORY,
    GET_FILELIST,
    CameraInfo,
    CameraStatus,
    FileInfo,
    MessageFail,
    decode_fat32_datetime,
    pack_header,
    unpack_header,
)


def test_header_roundtrip():
    raw = pack_header(GET_FILELIST, CODE_REQUEST, 7)
    assert raw == struct.pack("<III", GET_FILELIST, CODE_REQUEST, 7)
    assert unpack_header(raw) == (GET_FILELIST, CODE_REQUEST, 7)


def test_detect_camera_command_value():
    # On the wire little-endian this is the bytes 00 30 ac 16 used in discovery.
    assert struct.pack("<I", DETECT_CAMERA) == b"\x00\x30\xac\x16"


def test_camera_info_parse_real_reply():
    # Real CAMERA_DETECTED bytes captured on hardware (56 B).
    data = bytes.fromhex(
        "0130ac16"  # cmdId 0x16AC3001
        "02000000"  # interfaceLevel=2
        "64632d66332d31632d33662d32312d613000000000"  # mac "dc-f3-1c-3f-21-a0" + NUL pad (20 B)
        "01000000"  # cameraReserved=1
        "01000000"  # cameraCustomization=1
        "313132353538313039333432320000000000000000"  # serial "1125581093422" + NUL pad (20 B)
    )
    info = CameraInfo.parse(data)
    assert info.interface_level == 2
    assert info.mac == "dc-f3-1c-3f-21-a0"
    assert info.reserved == 1
    assert info.customization == 1
    assert info.serial == "1125581093422"


def test_camera_status_parse():
    payload = struct.pack("<I", 0) + b"3.3.7.11860".ljust(16, b"\x00") + b"1.3.0.2563".ljust(16, b"\x00")
    st = CameraStatus.parse(payload)
    assert st.client_subscribed == 0
    assert st.sw_version == "3.3.7.11860"
    assert st.wifi_version == "1.3.0.2563"


def test_file_info_parse_and_is_dir():
    rec = (
        struct.pack("<I", 1790736)  # filesize
        + struct.pack("<I", 0x20)  # fileType FILE
        + struct.pack("<H", 0)  # date
        + struct.pack("<H", 0)  # time
        + b"\\DCIM\\P0001\\IM0002EY.JPG".ljust(28, b"\x00")
    )
    info = FileInfo.parse(rec)
    assert info.filesize == 1790736
    assert info.filename == "\\DCIM\\P0001\\IM0002EY.JPG"
    assert info.is_dir is False
    dir_rec = struct.pack("<IIHH", 0, DIRECTORY, 0, 0) + b"\\DCIM\\P0001".ljust(28, b"\x00")
    assert FileInfo.parse(dir_rec).is_dir is True


def test_message_fail_parse():
    payload = struct.pack("<I", 0x16AC6005) + b"file not found".ljust(64, b"\x00")
    fail = MessageFail.parse(payload)
    assert fail.err_code == 0x16AC6005
    assert fail.message == "file not found"


def test_decode_fat32_datetime():
    # 2024-03-21 14:30:08 -> date bits, time bits
    date = ((2024 - 1980) << 9) | (3 << 5) | 21
    time = (14 << 11) | (30 << 5) | (8 // 2)
    dt = decode_fat32_datetime(date, time)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (2024, 3, 21, 14, 30, 8)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_camera_protocol.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.camera'`

- [ ] **Step 3: Write the implementation**

```python
# src/arm101_hand/camera/protocol.py
"""Pure framing/parsing for the Volk Pictor Prestige Camera-Client API (no sockets).

Testable core of the camera device layer (analogous to ``hand/kinematics.py``).
Verified against an Optomed Aurora (interface level 2) on 2026-06-10. Read paths
only -- POST_FILE / SUBSCRIBE / events are intentionally absent (YAGNI + safety).
All multi-byte fields are little-endian; char arrays are NUL-terminated ASCII.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime

# --- command ids ---
DETECT_CAMERA = 0x16AC3000
CAMERA_DETECTED = 0x16AC3001
PING_CAMERA = 0x16AC4010
GET_CAMERA_STATUS = 0x16AC4008
GET_FILELIST = 0x16AC4007
GET_FILE = 0x16AC4004

# --- header codes ---
CODE_OK = 0x16AC5001
CODE_FAIL = 0x16AC5002
CODE_REQUEST = 0x16AC5003
CODE_EVENT = 0x16AC5004

# --- fileType bit flags ---
READONLY = 0x1
HIDDEN = 0x2
SYSTEM = 0x4
VOLUME = 0x8
DIRECTORY = 0x10
FILE = 0x20
UNKNOWN = 0x40

_HEADER = struct.Struct("<III")  # cmdId, code, seqId


def pack_header(cmd: int, code: int, seq: int) -> bytes:
    """The 12-byte message header."""
    return _HEADER.pack(cmd, code, seq)


def unpack_header(raw: bytes) -> tuple[int, int, int]:
    """(cmdId, code, seqId) from the first 12 bytes."""
    return _HEADER.unpack(raw[:12])


def _cstr(raw: bytes) -> str:
    """Decode a NUL-terminated ASCII field (rest of buffer ignored)."""
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace")


def decode_fat32_datetime(date: int, time: int) -> datetime:
    """Decode FAT32 packed date+time (as stored in fileInfo)."""
    year = 1980 + (date >> 9)
    month = (date >> 5) & 0x0F
    day = date & 0x1F
    hour = time >> 11
    minute = (time >> 5) & 0x3F
    second = (time & 0x1F) * 2
    return datetime(year, month or 1, day or 1, hour, minute, second)


@dataclass(frozen=True)
class CameraInfo:
    """Parsed CAMERA_DETECTED discovery reply."""

    interface_level: int
    mac: str
    reserved: int
    customization: int
    serial: str

    @classmethod
    def parse(cls, data: bytes) -> CameraInfo:
        # data[0:4] = cmdId (CAMERA_DETECTED); fields follow.
        interface_level = struct.unpack_from("<I", data, 4)[0]
        mac = _cstr(data[8:28])
        reserved = struct.unpack_from("<I", data, 28)[0]
        customization = struct.unpack_from("<I", data, 32)[0]
        serial = _cstr(data[36:56])
        return cls(interface_level, mac, reserved, customization, serial)


@dataclass(frozen=True)
class CameraStatus:
    """Parsed GET_CAMERA_STATUS OK payload (36 bytes)."""

    client_subscribed: int
    sw_version: str
    wifi_version: str

    @classmethod
    def parse(cls, payload: bytes) -> CameraStatus:
        client_subscribed = struct.unpack_from("<I", payload, 0)[0]
        return cls(client_subscribed, _cstr(payload[4:20]), _cstr(payload[20:36]))


@dataclass(frozen=True)
class FileInfo:
    """One 40-byte fileInfo record from GET_FILELIST / GET_FILE."""

    filesize: int
    file_type: int
    file_date: int
    file_time: int
    filename: str

    SIZE = 40

    @classmethod
    def parse(cls, rec: bytes) -> FileInfo:
        filesize, file_type, file_date, file_time = struct.unpack_from("<IIHH", rec, 0)
        return cls(filesize, file_type, file_date, file_time, _cstr(rec[12:40]))

    @property
    def is_dir(self) -> bool:
        return bool(self.file_type & DIRECTORY)


@dataclass(frozen=True)
class MessageFail:
    """Parsed CODE_FAIL payload (68 bytes)."""

    err_code: int
    message: str

    @classmethod
    def parse(cls, payload: bytes) -> MessageFail:
        err_code = struct.unpack_from("<I", payload, 0)[0]
        return cls(err_code, _cstr(payload[4:68]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_camera_protocol.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/camera/protocol.py tests/unit/test_camera_protocol.py
git commit -m "feat(camera): pure Pictor protocol framing + parsers"
```

---

## Task 2: Camera protocol — diff, classify, naming helpers

**Files:**
- Modify: `src/arm101_hand/camera/protocol.py` (append)
- Test: `tests/unit/test_camera_protocol.py` (append)

- [ ] **Step 1: Append the failing tests**

```python
# tests/unit/test_camera_protocol.py  (append)
import struct as _struct
from datetime import datetime, timezone

from arm101_hand.camera.protocol import (
    DIRECTORY,
    FILE,
    FileInfo,
    capture_filename,
    classify_capture,
    diff_new_files,
    sidecar_dict,
)


def _fi(name, size=100, ftype=FILE):
    return FileInfo(filesize=size, file_type=ftype, file_date=0, file_time=0, filename=name)


def test_diff_new_files_finds_new_excludes_dirs():
    before = {"\\DCIM\\P0001\\IM0001EY.JPG"}
    after = [
        _fi("\\DCIM\\P0001", ftype=DIRECTORY),  # directory -> excluded
        _fi("\\DCIM\\P0001\\IM0001EY.JPG"),  # already seen
        _fi("\\DCIM\\P0001\\IM0002EY.JPG"),  # new
    ]
    new = diff_new_files(before, after)
    assert [f.filename for f in new] == ["\\DCIM\\P0001\\IM0002EY.JPG"]


def test_classify_capture():
    assert classify_capture(_fi("\\DCIM\\P0001\\IM0002EY.JPG")) == "still"
    assert classify_capture(_fi("\\DCIM\\P0001\\VID0001.MP4")) == "video"
    assert classify_capture(_fi("\\299E51C4.PEF")) == "other"


def test_capture_filename_prefixes_timestamp_and_sanitizes():
    ts = datetime(2026, 6, 10, 14, 15, 30, tzinfo=timezone.utc)
    name = capture_filename(_fi("\\DCIM\\P0001\\IM0010EY.JPG"), ts)
    assert name == "20260610T141530Z_DCIM_P0001_IM0010EY.JPG"


def test_sidecar_dict_has_provenance():
    ts = datetime(2026, 6, 10, 14, 15, 30, tzinfo=timezone.utc)
    info = _fi("\\DCIM\\P0001\\IM0010EY.JPG", size=123)
    d = sidecar_dict(
        info, captured_at=ts, trigger_no=3,
        camera_serial="1125581093422", camera_sw="3.3.7.11860", camera_wifi="1.3.0.2563",
    )
    assert d["camera_filename"] == "\\DCIM\\P0001\\IM0010EY.JPG"
    assert d["filesize"] == 123
    assert d["trigger_no"] == 3
    assert d["camera_serial"] == "1125581093422"
    assert d["captured_at_utc"] == ts.isoformat()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_camera_protocol.py -q`
Expected: FAIL — `ImportError: cannot import name 'diff_new_files'`

- [ ] **Step 3: Append the implementation**

```python
# src/arm101_hand/camera/protocol.py  (append)

_VIDEO_EXTS = {"mp4", "avi", "mov", "mpg", "mpeg", "m4v"}
_STILL_EXTS = {"jpg", "jpeg"}


def diff_new_files(before: set[str], after: list[FileInfo]) -> list[FileInfo]:
    """New (non-directory) files in ``after`` whose path was not in ``before``.

    Does NOT filter by extension -- a stray video/raw sibling must be visible so the
    caller can warn, not silently drop it (the camera's capture mode is operator-set).
    """
    return [f for f in after if not f.is_dir and f.filename not in before]


def classify_capture(info: FileInfo) -> str:
    """``"still"`` | ``"video"`` | ``"other"`` from the filename extension."""
    ext = info.filename.rsplit(".", 1)[-1].lower() if "." in info.filename else ""
    if ext in _STILL_EXTS:
        return "still"
    if ext in _VIDEO_EXTS:
        return "video"
    return "other"


def capture_filename(info: FileInfo, captured_at: datetime) -> str:
    """``<UTC-compact>_<sanitized-camera-path>`` (collision-free local name)."""
    flat = info.filename.lstrip("\\").replace("\\", "_")
    return f"{captured_at:%Y%m%dT%H%M%SZ}_{flat}"


def sidecar_dict(
    info: FileInfo,
    *,
    captured_at: datetime,
    trigger_no: int,
    camera_serial: str,
    camera_sw: str,
    camera_wifi: str,
) -> dict:
    """JSON-serializable provenance record saved beside each pulled image."""
    fat = decode_fat32_datetime(info.file_date, info.file_time)
    return {
        "camera_filename": info.filename,
        "filesize": info.filesize,
        "file_type": f"0x{info.file_type:X}",
        "classification": classify_capture(info),
        "fat32_datetime": fat.isoformat(),
        "captured_at_utc": captured_at.isoformat(),
        "trigger_no": trigger_no,
        "camera_serial": camera_serial,
        "camera_sw_version": camera_sw,
        "camera_wifi_version": camera_wifi,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_camera_protocol.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/camera/protocol.py tests/unit/test_camera_protocol.py
git commit -m "feat(camera): filelist diff, capture classify, naming + sidecar"
```

---

## Task 3: Camera config schema + operator YAML

**Files:**
- Create: `src/arm101_hand/config/camera_config.py`
- Create: `src/arm101_hand/data/camera_config.yaml`
- Modify: `src/arm101_hand/config/__init__.py`
- Test: `tests/unit/test_camera_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_camera_config.py
from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import CameraConfig, load_camera_config

_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "camera_config.yaml"


def test_defaults():
    cfg = CameraConfig()
    assert cfg.connection.message_port == 8000
    assert cfg.connection.discovery_port == 3000
    assert cfg.capture.hold_seconds == 3.0
    assert cfg.capture.dcim_root == "\\DCIM"
    assert cfg.capture.fundus_dir == "fundus_images"


def test_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        CameraConfig.model_validate({"connection": {"bogus": 1}})


def test_data_yaml_loads():
    cfg = load_camera_config(_DATA)
    assert cfg.connection.message_port == 8000
    assert cfg.capture.stable_polls >= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_camera_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'CameraConfig'`

- [ ] **Step 3: Write the schema**

```python
# src/arm101_hand/config/camera_config.py
"""Pydantic schema for ``src/arm101_hand/data/camera_config.yaml`` (primitive layer).

Operator config for the Optomed Aurora over the Pictor Wi-Fi API: connection ports +
timeouts, and capture-loop tuning (hold, new-file wait, paths). Hand-editable; never
written by runtime code (IL-5). Press-depth bounds reuse ``index_toggle`` constants,
so they are deliberately absent here.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class CameraConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str | None = Field(default=None, description="static camera IP; null = broadcast discover")
    discovery_port: int = Field(default=3000, ge=1, le=65535, description="UDP discovery port")
    message_port: int = Field(default=8000, ge=1, le=65535, description="TCP message socket port")
    discover_timeout_s: float = Field(default=4.0, gt=0, description="UDP discovery reply wait (s)")
    connect_timeout_s: float = Field(default=4.0, gt=0, description="TCP connect timeout (s)")
    io_timeout_s: float = Field(default=8.0, gt=0, description="TCP send/recv timeout (s)")


class CameraCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hold_seconds: float = Field(default=3.0, ge=0, description="shutter hold dwell after press reaches")
    new_file_timeout_s: float = Field(default=15.0, gt=0, description="max wait for the capture to land")
    poll_s: float = Field(default=0.5, gt=0, description="filelist poll interval while waiting (s)")
    stable_polls: int = Field(default=2, ge=1, description="consecutive equal-size polls before pulling")
    dcim_root: str = Field(default="\\DCIM", description="camera image root for filelist/diff")
    fundus_dir: str = Field(default="fundus_images", description="repo-relative save folder")


class CameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    connection: CameraConnection = Field(default_factory=CameraConnection)
    capture: CameraCapture = Field(default_factory=CameraCapture)


def load_camera_config(path: Path) -> CameraConfig:
    """Parse and validate ``camera_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CameraConfig.model_validate(raw)
```

- [ ] **Step 4: Write the operator YAML**

```yaml
# src/arm101_hand/data/camera_config.yaml
# camera_config.yaml -- Optomed Aurora operator config (Pictor Wi-Fi API).
# Hand-editable; never written by runtime code (IL-5). host:null -> broadcast discover.
schema_version: 1
connection:
  host: null
  discovery_port: 3000
  message_port: 8000
  discover_timeout_s: 4.0
  connect_timeout_s: 4.0
  io_timeout_s: 8.0
capture:
  hold_seconds: 3.0
  new_file_timeout_s: 15.0
  poll_s: 0.5
  stable_polls: 2
  dcim_root: "\\DCIM"
  fundus_dir: fundus_images
```

- [ ] **Step 5: Export from the config package**

In `src/arm101_hand/config/__init__.py`, add after the `hand_config` import block:

```python
from .camera_config import (
    CameraCapture,
    CameraConfig,
    CameraConnection,
    load_camera_config,
)
```

And add to `__all__`: `"CameraCapture", "CameraConfig", "CameraConnection", "load_camera_config",`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_camera_config.py -q`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/config/camera_config.py src/arm101_hand/data/camera_config.yaml src/arm101_hand/config/__init__.py tests/unit/test_camera_config.py
git commit -m "feat(camera): camera_config schema + operator YAML"
```

---

## Task 4: PictorClient (read-only sockets)

**Files:**
- Create: `src/arm101_hand/camera/client.py`
- Test: `tests/unit/test_camera_client.py`

- [ ] **Step 1: Write the failing tests** (a fake socket feeds canned responses)

```python
# tests/unit/test_camera_client.py
import struct

import pytest

from arm101_hand.camera.client import CameraError, PictorClient, recv_exact
from arm101_hand.camera.protocol import (
    CODE_FAIL,
    CODE_OK,
    FILE,
    GET_CAMERA_STATUS,
    GET_FILE,
    GET_FILELIST,
    pack_header,
)


class FakeSocket:
    def __init__(self, to_recv: bytes):
        self._buf = bytearray(to_recv)
        self.sent = bytearray()

    def sendall(self, b):
        self.sent += b

    def recv(self, n):
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk

    def settimeout(self, t):
        pass

    def close(self):
        pass


def _client_with(sock):
    c = PictorClient(host="x", discovery_port=3000, message_port=8000,
                     discover_timeout_s=1, connect_timeout_s=1, io_timeout_s=1)
    c._sock = sock
    return c


def test_recv_exact_reassembles():
    assert recv_exact(FakeSocket(b"abcdef"), 6) == b"abcdef"


def test_recv_exact_raises_on_short_close():
    with pytest.raises(ConnectionError):
        recv_exact(FakeSocket(b"ab"), 6)


def test_get_status_parses_ok():
    payload = struct.pack("<I", 0) + b"3.3.7.11860".ljust(16, b"\x00") + b"1.3.0.2563".ljust(16, b"\x00")
    sock = FakeSocket(pack_header(GET_CAMERA_STATUS, CODE_OK, 1) + payload)
    st = _client_with(sock).get_status()
    assert st.sw_version == "3.3.7.11860"
    # request was sent with a filepath-free header
    assert struct.unpack("<III", bytes(sock.sent[:12]))[0] == GET_CAMERA_STATUS


def test_get_filelist_parses_records():
    rec = struct.pack("<IIHH", 100, FILE, 0, 0) + b"\\DCIM\\P0001\\IM0001EY.JPG".ljust(28, b"\x00")
    body = struct.pack("<I", 1) + rec
    sock = FakeSocket(pack_header(GET_FILELIST, CODE_OK, 1) + body)
    files = _client_with(sock).get_filelist("\\DCIM")
    assert len(files) == 1 and files[0].filesize == 100
    # request payload includes a 64-byte filepath field
    assert len(sock.sent) == 12 + 64


def test_get_file_returns_info_and_bytes():
    info = struct.pack("<IIHH", 4, FILE, 0, 0) + b"\\DCIM\\P0001\\IM0001EY.JPG".ljust(28, b"\x00")
    sock = FakeSocket(pack_header(GET_FILE, CODE_OK, 1) + info + b"\xff\xd8\xff\xe0")
    fi, data = _client_with(sock).get_file("\\DCIM\\P0001\\IM0001EY.JPG")
    assert fi.filesize == 4 and data == b"\xff\xd8\xff\xe0"


def test_fail_response_raises_camera_error():
    payload = struct.pack("<I", 0x16AC6005) + b"nope".ljust(64, b"\x00")
    sock = FakeSocket(pack_header(GET_FILE, CODE_FAIL, 1) + payload)
    with pytest.raises(CameraError) as ei:
        _client_with(sock).get_file("\\bad")
    assert "0x16AC6005" in str(ei.value)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_camera_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.camera.client'`

- [ ] **Step 3: Write the client**

```python
# src/arm101_hand/camera/client.py
"""Read-only TCP/UDP client for the Pictor Prestige Camera API (device layer).

Discovery (UDP) then a held TCP message socket on :8000. Only read commands are
implemented -- never POST_FILE (IL: no accidental writes to the camera). The camera
opens its TCP listener in response to discovery, so a dead socket is re-established by
re-discovering: ``ensure_connected`` does that idempotently. Verified on an Optomed
Aurora 2026-06-10.
"""

from __future__ import annotations

import socket

from arm101_hand.camera.protocol import (
    CODE_FAIL,
    CODE_REQUEST,
    DETECT_CAMERA,
    GET_CAMERA_STATUS,
    GET_FILE,
    GET_FILELIST,
    PING_CAMERA,
    CameraInfo,
    CameraStatus,
    FileInfo,
    MessageFail,
    pack_header,
    unpack_header,
)

_DETECT_PACKET = pack_header(DETECT_CAMERA, 0, 0)[:4]  # discovery payload = the 4-byte cmdId


class CameraError(RuntimeError):
    """A camera request failed (CODE_FAIL) or the camera is unreachable/busy."""


def recv_exact(sock, n: int) -> bytes:
    """Read exactly ``n`` bytes or raise ConnectionError if the socket closes early."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError(f"socket closed after {len(buf)}/{n} bytes")
        buf += chunk
    return bytes(buf)


class PictorClient:
    def __init__(
        self,
        *,
        host: str | None,
        discovery_port: int,
        message_port: int,
        discover_timeout_s: float,
        connect_timeout_s: float,
        io_timeout_s: float,
    ):
        self.host = host
        self.discovery_port = discovery_port
        self.message_port = message_port
        self.discover_timeout_s = discover_timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.io_timeout_s = io_timeout_s
        self._sock: socket.socket | None = None
        self._seq = 0
        self.info: CameraInfo | None = None

    # -- discovery / connection --
    def discover(self) -> CameraInfo | None:
        """Broadcast (or unicast) DETECT_CAMERA; return the parsed reply or None."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(self.discover_timeout_s)
        targets = [self.host] if self.host else ["255.255.255.255"]
        try:
            for dest in targets:
                s.sendto(_DETECT_PACKET, (dest, self.discovery_port))
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                return None
        finally:
            s.close()
        if len(data) < 56:
            return None
        info = CameraInfo.parse(data)
        if self.host is None:  # remember where the reply came from for the TCP dial
            self.host = addr[0]
        self.info = info
        return info

    def connect(self) -> None:
        """Open the TCP message socket (call promptly after ``discover``)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.connect_timeout_s)
        s.connect((self.host, self.message_port))
        s.settimeout(self.io_timeout_s)
        self._sock = s

    def ensure_connected(self) -> None:
        """Idempotent: (re)discover + (re)connect if not currently connected."""
        if self._sock is not None:
            return
        info = self.discover()
        if info is None:
            raise CameraError("no camera responded to discovery (on the same Wi-Fi?)")
        if info.reserved:
            raise CameraError("camera is busy (cameraReserved=1) -- close Optomed Client")
        self.connect()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- requests --
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _send(self, cmd: int, payload: bytes = b"") -> tuple[int, int, int]:
        assert self._sock is not None, "not connected"
        self._sock.sendall(pack_header(cmd, CODE_REQUEST, self._next_seq()) + payload)
        return unpack_header(recv_exact(self._sock, 12))

    def _raise_if_fail(self, code: int) -> None:
        if code == CODE_FAIL:
            fail = MessageFail.parse(recv_exact(self._sock, 68))
            raise CameraError(f"camera CODE_FAIL errCode=0x{fail.err_code:X} msg={fail.message!r}")

    @staticmethod
    def _filepath(path: str) -> bytes:
        return path.encode("ascii").ljust(64, b"\x00")

    def ping(self) -> None:
        _, code, _ = self._send(PING_CAMERA)
        self._raise_if_fail(code)

    def get_status(self) -> CameraStatus:
        _, code, _ = self._send(GET_CAMERA_STATUS)
        self._raise_if_fail(code)
        return CameraStatus.parse(recv_exact(self._sock, 36))

    def get_filelist(self, path: str) -> list[FileInfo]:
        _, code, _ = self._send(GET_FILELIST, self._filepath(path))
        self._raise_if_fail(code)
        count = int.from_bytes(recv_exact(self._sock, 4), "little")
        return [FileInfo.parse(recv_exact(self._sock, FileInfo.SIZE)) for _ in range(count)]

    def get_file(self, path: str) -> tuple[FileInfo, bytes]:
        _, code, _ = self._send(GET_FILE, self._filepath(path))
        self._raise_if_fail(code)
        info = FileInfo.parse(recv_exact(self._sock, FileInfo.SIZE))
        return info, recv_exact(self._sock, info.filesize)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_camera_client.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/camera/client.py tests/unit/test_camera_client.py
git commit -m "feat(camera): read-only PictorClient (discover/status/filelist/get_file)"
```

---

## Task 5: Capture orchestration (wait-for-new + save)

**Files:**
- Create: `src/arm101_hand/camera/capture.py`
- Test: `tests/unit/test_camera_capture.py`

- [ ] **Step 1: Write the failing tests** (a fake client scripts successive filelists)

```python
# tests/unit/test_camera_capture.py
import json
from datetime import datetime, timezone

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
    out = wait_for_new_files(client, before, dcim_root="\\DCIM",
                             timeout_s=5, poll_s=0.0, stable_polls=2)
    assert [f.filename for f in out] == ["\\DCIM\\P0001\\IM0002EY.JPG"]


def test_wait_for_new_files_empty_on_timeout():
    before = set()
    client = _FakeClient([[]])  # nothing ever appears
    out = wait_for_new_files(client, before, dcim_root="\\DCIM",
                             timeout_s=0.05, poll_s=0.0, stable_polls=2)
    assert out == []


def test_save_capture_writes_jpeg_and_sidecar(tmp_path):
    info = _fi("\\DCIM\\P0001\\IM0010EY.JPG", 4)
    ts = datetime(2026, 6, 10, 14, 15, 30, tzinfo=timezone.utc)
    path = save_capture(
        info, b"\xff\xd8\xff\xe0", tmp_path,
        captured_at=ts, trigger_no=1,
        camera_serial="S", camera_sw="sw", camera_wifi="wifi",
    )
    assert path.name == "20260610T141530Z_DCIM_P0001_IM0010EY.JPG"
    assert path.read_bytes() == b"\xff\xd8\xff\xe0"
    sidecar = path.with_suffix(path.suffix + ".json")
    meta = json.loads(sidecar.read_text())
    assert meta["filesize"] == 4 and meta["trigger_no"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_camera_capture.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.camera.capture'`

- [ ] **Step 3: Write the implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_camera_capture.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/camera/capture.py tests/unit/test_camera_capture.py
git commit -m "feat(camera): wait-for-new-file (size-stable) + save image+sidecar"
```

---

## Task 6: Camera package exports

**Files:**
- Create: `src/arm101_hand/camera/__init__.py`

- [ ] **Step 1: Write the package init**

```python
# src/arm101_hand/camera/__init__.py
"""Optomed Aurora / Pictor Prestige camera device layer (read-only) + pure protocol."""

from .capture import save_capture, wait_for_new_files
from .client import CameraError, PictorClient, recv_exact
from .protocol import (
    CameraInfo,
    CameraStatus,
    FileInfo,
    MessageFail,
    capture_filename,
    classify_capture,
    decode_fat32_datetime,
    diff_new_files,
    sidecar_dict,
)

__all__ = [
    "CameraError",
    "CameraInfo",
    "CameraStatus",
    "FileInfo",
    "MessageFail",
    "PictorClient",
    "capture_filename",
    "classify_capture",
    "decode_fat32_datetime",
    "diff_new_files",
    "recv_exact",
    "save_capture",
    "sidecar_dict",
    "wait_for_new_files",
]
```

- [ ] **Step 2: Verify the package imports**

Run: `uv run python -c "from arm101_hand.camera import PictorClient, wait_for_new_files, save_capture; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/arm101_hand/camera/__init__.py
git commit -m "feat(camera): package exports"
```

---

## Task 7: Shared finger read/drive helper (DRY with grab_toggle)

**Files:**
- Create: `src/arm101_hand/hand/finger_io.py`
- Modify: `src/arm101_hand/hand/__init__.py`
- Modify: `scripts/demos/grab_toggle.py` (use the shared helper)
- Test: `tests/unit/test_finger_io.py`

- [ ] **Step 1: Write the failing test** (fake controller + SimpleNamespace calib block)

```python
# tests/unit/test_finger_io.py
from types import SimpleNamespace

from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand.finger_io import drive_finger, read_finger


class _FakeController:
    def __init__(self):
        self.goals = {}

    def read_present_position(self, sid):
        return [0.0]  # rustypot returns a single-element list

    def write_torque_enable(self, sid, on):
        pass

    def write_goal_speed(self, sid, sp):
        pass

    def write_goal_position(self, sid, rad):
        self.goals[sid] = rad


def _block():
    lim = SimpleNamespace(base_min=-20, base_max=70, side_min=-60, side_max=60)
    return SimpleNamespace(limits=lim, servo_1=SimpleNamespace(middle_pos=512),
                           servo_2=SimpleNamespace(middle_pos=512))


def test_read_finger_returns_int_pair():
    base, side = read_finger(_FakeController(), "index", _block())
    assert isinstance(base, int) and isinstance(side, int)


def test_drive_finger_commands_both_index_servos():
    c = _FakeController()
    drive_finger(c, "index", _block(), base=30, side=0, speed=3,
                 tolerance_rad=0.1, timeout_s=0.05, poll_s=0.0)
    id1, id2 = FINGER_SERVO_IDS["index"]
    assert id1 in c.goals and id2 in c.goals
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_finger_io.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.hand.finger_io'`

- [ ] **Step 3: Write the helper** (verbatim extraction of `grab_toggle`'s `_read_finger`/`_drive_index`, generalized to any finger)

```python
# src/arm101_hand/hand/finger_io.py
"""Read/drive one AmazingHand finger in the logical (base, side) frame (device layer).

Factored out of ``scripts/demos/grab_toggle.py`` so the trigger demo shares the exact
same finger read/drive (IL-7). Needs a live controller and the finger's calibration block.
"""

from __future__ import annotations

from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand.kinematics import (
    compose_finger,
    decompose_finger,
    degrees_to_servo_radians,
    servo_radians_to_degrees,
)
from arm101_hand.hand.motion import drive_hand_servos


def _scalar(v: object) -> float:
    if isinstance(v, (list, tuple)):
        return float(v[0])
    return float(v)  # type: ignore[arg-type]


def read_finger(controller, name, block) -> tuple[int, int]:
    """Present ``(base, side)`` (logical, clamped to limits) for finger ``name``."""
    lim = block.limits
    id1, id2 = FINGER_SERVO_IDS[name]
    pos1 = servo_radians_to_degrees(id1, _scalar(controller.read_present_position(id1)), block.servo_1.middle_pos)
    pos2 = servo_radians_to_degrees(id2, _scalar(controller.read_present_position(id2)), block.servo_2.middle_pos)
    base, side = decompose_finger(
        round(pos1), round(pos2),
        side_min=lim.side_min, side_max=lim.side_max,
        base_min=lim.base_min, base_max=lim.base_max,
    )
    return int(base), int(side)


def drive_finger(controller, name, block, base, side, speed, *, tolerance_rad, timeout_s, poll_s) -> None:
    """Command finger ``name`` to logical ``(base, side)`` and position-poll to completion."""
    lim = block.limits
    id1, id2 = FINGER_SERVO_IDS[name]
    pos1, pos2 = compose_finger(
        base, side,
        base_min=lim.base_min, base_max=lim.base_max,
        side_min=lim.side_min, side_max=lim.side_max,
    )
    targets = {
        id1: degrees_to_servo_radians(id1, pos1, block.servo_1.middle_pos),
        id2: degrees_to_servo_radians(id2, pos2, block.servo_2.middle_pos),
    }
    drive_hand_servos(controller, targets, speed, tolerance_rad=tolerance_rad, timeout_s=timeout_s, poll_s=poll_s)
```

- [ ] **Step 4: Export from the hand package**

In `src/arm101_hand/hand/__init__.py`, add an import block and `__all__` entries:

```python
from .finger_io import drive_finger, read_finger
```
Add `"drive_finger"` and `"read_finger"` to `__all__`.

- [ ] **Step 5: Refactor grab_toggle to use the shared helper**

In `scripts/demos/grab_toggle.py`:
1. Add to the `from arm101_hand.hand import (...)` block: `read_finger`, `drive_finger`.
2. Delete the local `_scalar`, `_read_finger`, and `_drive_index` functions.
3. Replace calls: `_read_finger(c, name, block)` → `read_finger(c, name, block)`; and the `_drive_index(c, index_block, tgt, side, speed, wait_kw)` call →
```python
drive_finger(c, "index", index_block, tgt, side, speed, **wait_kw)
```

- [ ] **Step 6: Run tests + import-check grab_toggle**

Run: `uv run pytest tests/unit/test_finger_io.py -q`
Expected: PASS (2 passed)
Run: `uv run python -c "import importlib.util,pathlib; importlib.util.spec_from_file_location('gt', pathlib.Path('scripts/demos/grab_toggle.py'))" && uv run ruff check scripts/demos/grab_toggle.py src/arm101_hand/hand/finger_io.py`
Expected: ruff clean (no errors)

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/hand/finger_io.py src/arm101_hand/hand/__init__.py scripts/demos/grab_toggle.py tests/unit/test_finger_io.py
git commit -m "refactor(hand): extract finger_io.read_finger/drive_finger; grab_toggle reuses it"
```

---

## Task 8: Pure one-shot trigger state

**Files:**
- Create: `src/arm101_hand/hand/index_trigger.py`
- Test: `tests/unit/test_index_trigger.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_index_trigger.py
from arm101_hand.hand.index_toggle import TOGGLE_DELTA_DEFAULT, TOGGLE_DELTA_MAX, TOGGLE_DELTA_MIN
from arm101_hand.hand.index_trigger import TriggerState, apply_action, key_to_action, press_base

BASE_MIN, BASE_MAX = -20, 70


def test_key_map():
    assert key_to_action(" ") == "fire"
    assert key_to_action("[") == "delta-"
    assert key_to_action("]") == "delta+"
    assert key_to_action("q") == "quit"
    assert key_to_action("z") is None


def test_default_delta():
    assert TriggerState(out_base=33, side=-39).delta == TOGGLE_DELTA_DEFAULT


def test_press_base_is_out_plus_delta_clamped():
    assert press_base(TriggerState(out_base=33, side=-39, delta=20), BASE_MIN, BASE_MAX) == 53
    assert press_base(TriggerState(out_base=33, side=-39, delta=40), BASE_MIN, BASE_MAX) == BASE_MAX  # 73>70


def test_delta_grows_shrinks_clamps():
    s = TriggerState(out_base=33, side=-39, delta=20)
    assert apply_action(s, "delta+").delta == 21
    assert apply_action(s, "delta-").delta == 19
    assert apply_action(TriggerState(33, -39, TOGGLE_DELTA_MAX), "delta+").delta == TOGGLE_DELTA_MAX
    assert apply_action(TriggerState(33, -39, TOGGLE_DELTA_MIN), "delta-").delta == TOGGLE_DELTA_MIN


def test_fire_and_quit_are_state_noops():
    s = TriggerState(out_base=33, side=-39, delta=20)
    assert apply_action(s, "fire") == s
    assert apply_action(s, "quit") == s
    assert apply_action(s, "nonsense") == s
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_index_trigger.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.hand.index_trigger'`

- [ ] **Step 3: Write the implementation** (reuses `index_toggle.in_base` for press geometry)

```python
# src/arm101_hand/hand/index_trigger.py
"""Pure one-shot trigger-cycle state for ``scripts/demos/grab_trigger_capture.py``.

Like ``index_toggle`` (the button click), but a single SPACE 'fires' a full
press->hold->release cycle rather than latching a pressed/unpressed flag. The press
geometry (out_base + delta, clamped to the calibrated window) is reused from
``index_toggle.in_base`` -- one source for the index press depth (IL-7). The script
owns the keys, the bus, and the hold dwell; this module is the depth cursor + key map.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.hand.index_toggle import (
    TOGGLE_DELTA_DEFAULT,
    TOGGLE_DELTA_MAX,
    TOGGLE_DELTA_MIN,
    ToggleState,
    in_base,
)
from arm101_hand.hand.kinematics import clamp

_KEY_ACTIONS: dict[str, str] = {" ": "fire", "[": "delta-", "]": "delta+", "q": "quit"}


@dataclass(frozen=True)
class TriggerState:
    """Index press-depth cursor: settled OUT ``(out_base, side)`` plus the live ``delta``."""

    out_base: int
    side: int
    delta: int = TOGGLE_DELTA_DEFAULT


def key_to_action(key: str) -> str | None:
    return _KEY_ACTIONS.get(key)


def press_base(state: TriggerState, base_min: int, base_max: int) -> int:
    """The IN base for a press: ``out_base + delta`` clamped to the calibrated window."""
    return in_base(ToggleState(out_base=state.out_base, side=state.side, delta=state.delta), base_min, base_max)


def apply_action(state: TriggerState, action: str) -> TriggerState:
    """Apply a key action. ``fire``/``quit``/unknown are state no-ops (handled by the shell);
    only the delta keys change state, and they never move the finger (no surprise movement)."""
    if action == "delta+":
        return replace(state, delta=int(clamp(state.delta + 1, TOGGLE_DELTA_MIN, TOGGLE_DELTA_MAX)))
    if action == "delta-":
        return replace(state, delta=int(clamp(state.delta - 1, TOGGLE_DELTA_MIN, TOGGLE_DELTA_MAX)))
    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_index_trigger.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/hand/index_trigger.py tests/unit/test_index_trigger.py
git commit -m "feat(hand): pure one-shot index trigger state (reuses index_toggle)"
```

---

## Task 9: The `grab_trigger_capture.py` demo

**Files:**
- Create: `scripts/demos/grab_trigger_capture.py`

No unit test (hardware demo; its pure pieces are covered by Tasks 1–8). Verified manually in Step 3.

- [ ] **Step 1: Write the demo**

```python
# scripts/demos/grab_trigger_capture.py
"""Grab the camera, then click the index finger to trigger a fundus capture and pull it.

Runs the staged arm+hand grab (see ``arm101_hand.scripts.grab_common``); once both
devices hold ``grab`` under torque, each SPACE runs ONE capture cycle:
  press the index onto the Aurora's shutter -> hold -> release ->
  diff the camera filelist -> pull the new image(s) + metadata into ``fundus_images/``.

PREREQUISITES (set on the camera; the API cannot):
  * Capture mode = STILL imaging  (a held press in VIDEO mode records a clip!)
  * Quick imaging = ON            (else each capture waits in an on-device preview)
  * Optomed Client CLOSED         (the Pictor API allows one client connection)
  * A study/patient selected      (new images land in the current study folder)

Controls (torque ON the whole time):
  SPACE       fire one capture cycle (press -> hold -> release -> pull)
  [ / ]       shrink / grow the press depth (applies to the NEXT press)
  q / Ctrl+C  stop and go to the exit prompt (Enter releases in place, 'h' reverses)

Usage:
  uv run python scripts/demos/grab_trigger_capture.py
"""

from __future__ import annotations

import datetime as _dt
import math
import msvcrt
import sys
from pathlib import Path

from arm101_hand.camera import (
    CameraError,
    PictorClient,
    classify_capture,
    save_capture,
    wait_for_new_files,
)
from arm101_hand.config import FINGER_SERVO_IDS, load_camera_config
from arm101_hand.hand import drive_finger, load_warning, read_finger
from arm101_hand.hand.index_trigger import TriggerState, apply_action, key_to_action, press_base
from arm101_hand.hand.pose_jog import HandJogState, format_hand_status
from arm101_hand.scripts.grab_common import GrabHoldContext, run_grab_demo

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CAMERA_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "camera_config.yaml"
_STATIC_FINGERS = ("middle", "ring", "thumb")


def _read_key() -> str:
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume + ignore
        msvcrt.getwch()
        return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _scalar(v: object) -> float:
    if isinstance(v, (list, tuple)):
        return float(v[0])
    return float(v)  # type: ignore[arg-type]


def _status_line(state: TriggerState, base: int, side: int, others: dict) -> str:
    fingers = {"index": (int(base), int(side)), **others}
    return format_hand_status(HandJogState(active="index", step=state.delta, fingers=fingers))


def _print_prereqs() -> None:
    print("PREREQUISITES (set on the camera): Still mode | Quick imaging ON | "
          "Optomed Client CLOSED | study selected")


def _do_capture(camera, cap_cfg, fundus_dir, trigger_no, serial, sw, wifi) -> None:
    """One capture pull: snapshot already taken by the caller is passed via closure args."""


def main() -> int:
    cfg = load_camera_config(_CAMERA_CONFIG_PATH)
    conn, cap = cfg.connection, cfg.capture
    fundus_dir = _REPO_ROOT / cap.fundus_dir

    camera = PictorClient(
        host=conn.host, discovery_port=conn.discovery_port, message_port=conn.message_port,
        discover_timeout_s=conn.discover_timeout_s, connect_timeout_s=conn.connect_timeout_s,
        io_timeout_s=conn.io_timeout_s,
    )
    _print_prereqs()
    print("Connecting to camera ...")
    try:
        camera.ensure_connected()
        status = camera.get_status()
    except CameraError as e:
        print(f"ERROR: camera not ready: {e}", file=sys.stderr)
        return 1
    serial = camera.info.serial if camera.info else "?"
    print(f"Camera ready: serial={serial} sw={status.sw_version} wifi={status.wifi_version}")

    trigger_no = {"n": 0}  # mutable counter shared with the hook

    def _trigger_loop(ctx: GrabHoldContext) -> None:
        c = ctx.hand
        calib = ctx.hand_calib
        tuning = ctx.hand_cfg.tuning
        index_block = calib.fingers["index"]
        lim = index_block.limits
        id1, id2 = FINGER_SERVO_IDS["index"]
        wait_kw = {
            "tolerance_rad": math.radians(tuning.pose_margin_deg),
            "timeout_s": tuning.pose_timeout_s,
            "poll_s": tuning.pose_poll_s,
        }
        out_base, side = read_finger(c, "index", index_block)
        others = {name: read_finger(c, name, calib.fingers[name]) for name in _STATIC_FINGERS}
        state = TriggerState(out_base=out_base, side=side)

        print("Trigger capture: SPACE = capture, [ / ] = press depth, q = exit")
        _print_prereqs()
        print("  " + _status_line(state, out_base, side, others))

        try:
            while True:
                action = key_to_action(_read_key())
                if action is None:
                    continue
                if action == "quit":
                    break
                if action != "fire":
                    state = apply_action(state, action)
                    base_now, side_now = read_finger(c, "index", index_block)
                    print("  " + _status_line(state, base_now, side_now, others))
                    continue

                # ---- fire one capture cycle ----
                try:
                    camera.ensure_connected()
                    before = {f.filename for f in camera.get_filelist(cap.dcim_root)}
                except CameraError as e:
                    print(f"  camera not ready: {e} -- skipping this trigger")
                    continue

                tgt = press_base(state, lim.base_min, lim.base_max)
                print(f"  pressing (base {out_base} -> {tgt}) ...")
                drive_finger(c, "index", index_block, tgt, side, tuning.speeds.close, **wait_kw)
                load1, load2 = int(_scalar(c.read_present_load(id1))), int(_scalar(c.read_present_load(id2)))
                warn = load_warning(load1, load2)
                if warn:
                    print("  " + warn)

                import time as _time
                _time.sleep(cap.hold_seconds)  # deliberate shutter hold (press already confirmed)

                drive_finger(c, "index", index_block, out_base, side, tuning.speeds.open, **wait_kw)
                print("  released; waiting for the captured image ...")

                try:
                    new = wait_for_new_files(
                        camera, before, dcim_root=cap.dcim_root,
                        timeout_s=cap.new_file_timeout_s, poll_s=cap.poll_s, stable_polls=cap.stable_polls,
                    )
                except CameraError as e:
                    print(f"  camera error while pulling: {e}")
                    new = []

                if not new:
                    print("  WARNING: no new image within timeout -- check Still mode / "
                          "Quick imaging ON / press depth ([ / ]).")
                    continue

                now = _dt.datetime.now(_dt.timezone.utc)
                for f in new:
                    kind = classify_capture(f)
                    if kind != "still":
                        print(f"  WARNING: {f.filename} looks like {kind!r}, not a still "
                              "(is the camera in VIDEO mode?) -- saving anyway.")
                    info, data = camera.get_file(f.filename)
                    if not data.startswith(b"\xff\xd8\xff") or len(data) != info.filesize:
                        print(f"  WARNING: {f.filename} failed validation "
                              f"(jpeg={data[:3].hex()} bytes={len(data)}/{info.filesize}) -- saving anyway.")
                    trigger_no["n"] += 1
                    saved = save_capture(
                        info, data, fundus_dir,
                        captured_at=now, trigger_no=trigger_no["n"],
                        camera_serial=serial, camera_sw=status.sw_version, camera_wifi=status.wifi_version,
                    )
                    print(f"  SUCCESS: saved {saved.name} ({len(data)} bytes). Ready for next trigger.")
        except KeyboardInterrupt:
            print("\n^C -- leaving trigger mode")

    try:
        return run_grab_demo(_trigger_loop)
    finally:
        camera.close()
        print("Camera connection closed.")


if __name__ == "__main__":
    sys.exit(main())
```

> Note: delete the empty `_do_capture` stub before finishing — it was scaffolding. (Remove the `def _do_capture(...)` block; logic lives inline in `_trigger_loop`.)

- [ ] **Step 2: Lint + import check**

Run: `uv run ruff check scripts/demos/grab_trigger_capture.py && uv run python -c "import ast,pathlib; ast.parse(pathlib.Path('scripts/demos/grab_trigger_capture.py').read_text())" && echo OK`
Expected: ruff clean, `OK`. (Fix any unused-import / ordering findings ruff reports.)

- [ ] **Step 3: Manual hardware verification** (camera on Wi-Fi, Optomed Client closed, Still + Quick-imaging ON, hand+arm powered)

Run: `uv run python scripts/demos/grab_trigger_capture.py`
Expected: prerequisite line prints; "Camera ready: serial=… sw=3.3.7.11860 …"; staged grab completes; pressing SPACE presses the index, holds ~3 s, releases, then prints `SUCCESS: saved <ts>_… (N bytes)`; the file exists under `fundus_images/` with a `.json` sidecar; `q` then Enter releases torque on both buses.

- [ ] **Step 4: Commit**

```bash
git add scripts/demos/grab_trigger_capture.py
git commit -m "feat(demo): grab_trigger_capture -- index-press shutter trigger + auto-pull"
```

---

## Task 10: Consolidate the diagnostic probes

**Files:**
- Create: `scripts/diagnostics/aurora_probe.py`
- Delete: `scripts/diagnostics/aurora_discover_probe.py`, `aurora_tcp_probe.py`, `aurora_read_probe.py`, `aurora_getfile_probe.py`

- [ ] **Step 1: Write the consolidated read-only diagnostic** (built on `PictorClient` — no duplicate protocol)

```python
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
        host=args.host or conn.host, discovery_port=conn.discovery_port, message_port=conn.message_port,
        discover_timeout_s=conn.discover_timeout_s, connect_timeout_s=conn.connect_timeout_s,
        io_timeout_s=conn.io_timeout_s,
    )
    try:
        camera.ensure_connected()
    except CameraError as e:
        print(f"camera not ready: {e}", file=sys.stderr)
        return 1
    info, status = camera.info, camera.get_status()
    print(f"CAMERA serial={info.serial} mac={info.mac} interface={info.interface_level} "
          f"customization={info.customization}")
    print(f"STATUS sw={status.sw_version} wifi={status.wifi_version} subscribed={status.client_subscribed}")
    files = camera.get_filelist("\\DCIM")
    print(f"FILELIST \\DCIM: {len(files)} entries")
    for f in files[:10]:
        print(f"  {f.filename!r} size={f.filesize} type=0x{f.file_type:X}")

    if args.get_file:
        fi, data = camera.get_file(args.get_file)
        out = os.path.join(tempfile.gettempdir(), "aurora_" + os.path.basename(args.get_file).replace("\\", "_"))
        Path(out).write_bytes(data)
        print(f"GET_FILE {fi.filename!r}: {len(data)} bytes (jpeg={data[:3] == b'\\xff\\xd8\\xff'}) -> {out}")
    camera.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Delete the superseded probes**

```bash
git rm scripts/diagnostics/aurora_discover_probe.py scripts/diagnostics/aurora_tcp_probe.py scripts/diagnostics/aurora_read_probe.py scripts/diagnostics/aurora_getfile_probe.py
```
(These four are currently untracked, so if `git rm` reports "did not match any files", just delete them from disk: `Remove-Item scripts/diagnostics/aurora_discover_probe.py, scripts/diagnostics/aurora_tcp_probe.py, scripts/diagnostics/aurora_read_probe.py, scripts/diagnostics/aurora_getfile_probe.py`.)

- [ ] **Step 3: Lint + optional live check**

Run: `uv run ruff check scripts/diagnostics/aurora_probe.py`
Expected: clean. (Optional, with the camera on Wi-Fi + Optomed Client closed: `uv run python scripts/diagnostics/aurora_probe.py` prints serial/status/filelist.)

- [ ] **Step 4: Commit**

```bash
git add scripts/diagnostics/aurora_probe.py
git commit -m "chore(diagnostics): consolidate aurora probes into one PictorClient-based tool"
```

---

## Task 11: gitignore + documentation

**Files:**
- Modify: `.gitignore`
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Ignore captured images**

Append to `.gitignore`:
```
# Patient fundus captures pulled from the Aurora -- never commit medical images.
fundus_images/
```

- [ ] **Step 2: Update CLAUDE.md**

- In the directory tree (§3), add under `src/arm101_hand/`: `│   ├── camera/   # device layer -- read-only Pictor/Aurora Wi-Fi client (discovery + file pull)` and note `hand/finger_io.py`, `hand/index_trigger.py`.
- In Common workflows (§4) Demos block, add:
  ```
  uv run python scripts/demos/grab_trigger_capture.py   # grab the camera, SPACE presses the shutter + auto-pulls the new fundus image to fundus_images/
  ```
- In the diagnostics block, add: `uv run python scripts/diagnostics/aurora_probe.py   # read-only Aurora reachability + filelist (Optomed Client must be closed)`.

- [ ] **Step 3: Update README.md**

Mirror the CLAUDE.md additions in the human-facing README demo/diagnostic listings (same two commands + a one-line note that the camera must be in Still + Quick-imaging mode with Optomed Client closed).

- [ ] **Step 4: Commit**

```bash
git add .gitignore CLAUDE.md README.md
git commit -m "docs: gitignore fundus_images; document grab_trigger_capture + aurora_probe"
```

---

## Task 12: Full verification

- [ ] **Step 1: Format + lint + type-check + tests**

```bash
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest -m "not hardware" -q
```
Expected: ruff clean; mypy clean (or only pre-existing notes — do not introduce new errors); all non-hardware tests pass, including the new `test_camera_protocol.py`, `test_camera_config.py`, `test_camera_client.py`, `test_camera_capture.py`, `test_finger_io.py`, `test_index_trigger.py`, and the unchanged `test_index_toggle.py` / `test_hand_motion.py`.

- [ ] **Step 2: Confirm the branch diff is coherent**

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```
Expected: the camera package, config, hand helpers, demo, diagnostic, docs — one coherent feature on `feat/grab-trigger-capture`.

- [ ] **Step 3 (when hardware is available): hardware smoke test**

Per Task 9 Step 3 — run the demo end-to-end and confirm a real capture lands in `fundus_images/` with a sidecar. This is the IL-aligned manual gate (no auto-moves beyond the staged grab; torque released on exit).

---

## Self-Review

**Spec coverage** (every §): camera package pure/client/capture → Tasks 1,2,4,5,6 ✓; reuse index_toggle + one-shot fire → Task 8 ✓; config schema+YAML → Task 3 ✓; the SPACE fire cycle (ensure_connected, snapshot, press/poll, dwell, release, wait-stable, get_file+validate, classify/warn, success) → Task 9 ✓; full arm+hand grab reuse via run_grab_demo, no grab_common change → Task 9 ✓; fundus_images naming + sidecar + gitignore → Tasks 5,11 ✓; single-connection fail-fast + verify/warn + write-race + no-customization-filter + no-surprise-movement + torque release → Tasks 4,5,9 ✓; tests (pure + hardware-gated) → every task + Task 12 ✓; diagnostics consolidation → Task 10 ✓; docs refresh + atomic branch → Tasks 11,12 ✓. DRY finger read/drive → Task 7 (added beyond the spec's file table to honor IL-7 — flagged).

**Placeholder scan:** none ("TBD"/"add error handling"/etc. absent). The one scaffolding stub (`_do_capture`) is explicitly called out for deletion in Task 9.

**Type consistency:** `PictorClient` ctor kwargs match across Tasks 4/9/10; `wait_for_new_files`/`save_capture`/`sidecar_dict`/`capture_filename` signatures match between definition (Tasks 2,5) and call sites (Task 9); `FileInfo.SIZE`/`.is_dir`/`.filename`/`.filesize` consistent; `press_base`/`apply_action`/`key_to_action`/`TriggerState` consistent between Task 8 and Task 9; `read_finger`/`drive_finger` consistent between Task 7 and Task 9.
