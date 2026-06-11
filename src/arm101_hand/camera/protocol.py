"""Pure framing/parsing for the Volk Pictor Prestige Camera-Client API (no sockets).

Testable core of the camera device layer (analogous to ``hand/kinematics.py``).
Verified against an Optomed Aurora (interface level 2) on 2026-06-10. Read paths
only -- POST_FILE / SUBSCRIBE / events are intentionally absent (YAGNI + safety).
All multi-byte fields are little-endian; char arrays are NUL-terminated ASCII.
"""

from __future__ import annotations

import re
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


_ILLEGAL_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def capture_filename(info: FileInfo, captured_at: datetime) -> str:
    """``<UTC-compact>_<sanitized-camera-path>`` -- always a valid local filename.

    The camera path is untrusted: backslashes become ``_``, then any remaining character unsafe
    in a local filename (Windows forbids ``< > : " / | ? *`` and control bytes; a corrupt reply
    can carry arbitrary bytes) is also replaced with ``_``. This guarantees the name can never
    trigger an OS write error -- a remote device must not be able to crash the local process.
    Falls back to ``unnamed`` if sanitizing leaves nothing.
    """
    flat = info.filename.lstrip("\\").replace("\\", "_")
    flat = _ILLEGAL_FILENAME_CHARS.sub("_", flat) or "unnamed"
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
