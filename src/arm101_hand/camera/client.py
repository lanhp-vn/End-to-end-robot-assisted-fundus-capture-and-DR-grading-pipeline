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
            except TimeoutError:
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
