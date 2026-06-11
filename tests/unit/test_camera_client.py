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
    c = PictorClient(
        host="x",
        discovery_port=3000,
        message_port=8000,
        discover_timeout_s=1,
        connect_timeout_s=1,
        io_timeout_s=1,
    )
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
