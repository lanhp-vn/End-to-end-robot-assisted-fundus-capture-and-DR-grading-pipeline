"""Throwaway probe: pull one image via GET_FILE and save it (to OS temp, not
the repo, since these are patient fundus images).

Read-only. discover -> connect :8000 -> GET_FILE(<path>) -> save bytes.
Stdlib only.

Usage:
    uv run python scripts/diagnostics/aurora_getfile_probe.py [CAMERA_IP] [PORT] [FILEPATH]
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import tempfile

CAMERA_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.12.16"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
FILEPATH = sys.argv[3] if len(sys.argv) > 3 else r"\DCIM\P0001\IM0002EY.JPG"

DISCOVERY_PORT = 3000
DETECT_CAMERA = bytes([0x00, 0x30, 0xAC, 0x16])
GET_FILE = 0x16AC4004
CODE_OK = 0x16AC5001
CODE_FAIL = 0x16AC5002
CODE_REQUEST = 0x16AC5003


def discover_free() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(4.0)
    s.sendto(DETECT_CAMERA, (CAMERA_IP, DISCOVERY_PORT))
    try:
        data, _ = s.recvfrom(2048)
    except socket.timeout:
        print("[UDP] no discovery reply")
        return False
    finally:
        s.close()
    reserved = struct.unpack_from("<I", data, 28)[0] if len(data) >= 56 else -1
    print(f"[UDP] CAMERA_DETECTED, cameraReserved={reserved}")
    return True


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError(f"socket closed after {len(buf)}/{n} bytes")
        buf += chunk
    return bytes(buf)


def main() -> None:
    discover_free()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(20.0)
    try:
        sock.connect((CAMERA_IP, PORT))
        print(f"[TCP] connected to {CAMERA_IP}:{PORT}")

        print(f"[TCP] -> GET_FILE {FILEPATH!r}")
        filepath = FILEPATH.encode("ascii").ljust(64, b"\x00")
        sock.sendall(struct.pack("<III", GET_FILE, CODE_REQUEST, 1) + filepath)

        cmd, code, seq = struct.unpack("<III", recv_exact(sock, 12))
        print(f"  [GET_FILE] cmd=0x{cmd:08X} code=0x{code:08X} seq={seq}")
        if code == CODE_FAIL:
            p = recv_exact(sock, 68)
            errcode = struct.unpack_from("<I", p, 0)[0]
            errmsg = p[4:].split(b"\x00")[0].decode("ascii", "replace")
            print(f"        CODE_FAIL errCode=0x{errcode:08X} msg={errmsg!r}")
            return

        # getFileResponse: fileInfo_t (40 bytes) then filesize bytes of data
        info = recv_exact(sock, 40)
        filesize, ftype = struct.unpack_from("<II", info, 0)
        name = info[12:40].split(b"\x00")[0].decode("ascii", "replace")
        print(f"        fileInfo: name={name!r} size={filesize} type=0x{ftype:X}")

        data = recv_exact(sock, filesize)
        magic = data[:3].hex(" ")
        is_jpeg = data[:3] == b"\xff\xd8\xff"
        out = os.path.join(tempfile.gettempdir(),
                           "aurora_" + os.path.basename(FILEPATH).replace("\\", "_"))
        with open(out, "wb") as fh:
            fh.write(data)
        print(f"        received {len(data)} bytes (first3={magic}, "
              f"JPEG={is_jpeg})")
        print(f"        saved -> {out}")
    except (OSError, ConnectionError) as exc:
        print(f"[TCP] error: {exc}")
    finally:
        sock.close()
        print("[TCP] closed.")


if __name__ == "__main__":
    main()
