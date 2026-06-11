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
