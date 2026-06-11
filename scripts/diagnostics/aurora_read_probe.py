"""Throwaway probe: full read-path check against the Aurora in one process.

Does UDP discovery and then IMMEDIATELY connects the TCP message socket
(the camera creates its listener in response to discovery, so the connect
must follow promptly). Then sends only read-only requests:
PING_CAMERA -> GET_CAMERA_STATUS -> GET_FILELIST(\DCIM).
Sends NO write commands (never POST_FILE). Stdlib only.

Usage:
    uv run python scripts/diagnostics/aurora_read_probe.py [CAMERA_IP] [PORT]
"""

from __future__ import annotations

import socket
import struct
import sys

CAMERA_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.12.16"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

DISCOVERY_PORT = 3000
DETECT_CAMERA = bytes([0x00, 0x30, 0xAC, 0x16])

PING_CAMERA = 0x16AC4010
GET_CAMERA_STATUS = 0x16AC4008
GET_FILELIST = 0x16AC4007
CODE_OK = 0x16AC5001
CODE_FAIL = 0x16AC5002
CODE_REQUEST = 0x16AC5003


def discover() -> int | None:
    """Returns cameraReserved flag, or None if no reply."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(4.0)
    s.sendto(DETECT_CAMERA, (CAMERA_IP, DISCOVERY_PORT))
    try:
        data, _ = s.recvfrom(2048)
    except socket.timeout:
        print("[UDP] no discovery reply")
        return None
    finally:
        s.close()
    reserved = struct.unpack_from("<I", data, 28)[0] if len(data) >= 56 else -1
    print(f"[UDP] CAMERA_DETECTED, cameraReserved={reserved}")
    return reserved


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"socket closed after {len(buf)}/{n} bytes")
        buf += chunk
    return buf


def read_header(sock: socket.socket, label: str) -> tuple[int, int, int]:
    cmd, code, seq = struct.unpack("<III", recv_exact(sock, 12))
    print(f"  [{label}] cmd=0x{cmd:08X} code=0x{code:08X} seq={seq}")
    if code == CODE_FAIL:
        payload = recv_exact(sock, 68)
        errcode = struct.unpack_from("<I", payload, 0)[0]
        errmsg = payload[4:].split(b"\x00")[0].decode("ascii", "replace")
        print(f"        CODE_FAIL errCode=0x{errcode:08X} msg={errmsg!r}")
    return cmd, code, seq


def connect_promptly() -> socket.socket | None:
    for attempt in range(1, 4):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4.0)
        try:
            s.connect((CAMERA_IP, PORT))
            print(f"[TCP] connected to {CAMERA_IP}:{PORT} (attempt {attempt})")
            return s
        except OSError as exc:
            print(f"[TCP] connect attempt {attempt} failed: {exc}")
            s.close()
    return None


def main() -> None:
    reserved = discover()
    if reserved == 1:
        print("[!] cameraReserved=1 -> another client (likely Optomed Client) "
              "may hold the only allowed connection.")

    sock = connect_promptly()
    if sock is None:
        print("[TCP] could not connect. If cameraReserved=1, close Optomed "
              "Client and any browser/app talking to the camera, then retry.")
        return

    try:
        sock.settimeout(8.0)

        print("[TCP] -> PING_CAMERA")
        sock.sendall(struct.pack("<III", PING_CAMERA, CODE_REQUEST, 1))
        read_header(sock, "PING")

        print("[TCP] -> GET_CAMERA_STATUS")
        sock.sendall(struct.pack("<III", GET_CAMERA_STATUS, CODE_REQUEST, 2))
        cmd, code, _ = read_header(sock, "STATUS")
        if code == CODE_OK:
            p = recv_exact(sock, 36)
            sub = struct.unpack_from("<I", p, 0)[0]
            sw = p[4:20].split(b"\x00")[0].decode("ascii", "replace")
            wifi = p[20:36].split(b"\x00")[0].decode("ascii", "replace")
            print(f"        clientSubscribed={sub} swVersion={sw!r} wifi={wifi!r}")

        print(r"[TCP] -> GET_FILELIST (\DCIM)")
        filepath = b"\\DCIM".ljust(64, b"\x00")  # dump shows 64-byte filepath field
        sock.sendall(struct.pack("<III", GET_FILELIST, CODE_REQUEST, 3) + filepath)
        cmd, code, _ = read_header(sock, "FILELIST")
        if code == CODE_OK:
            count = struct.unpack_from("<I", recv_exact(sock, 4), 0)[0]
            print(f"        file count = {count}")
            for i in range(min(count, 10)):
                rec = recv_exact(sock, 40)
                size, ftype = struct.unpack_from("<II", rec, 0)
                name = rec[12:40].split(b"\x00")[0].decode("ascii", "replace")
                print(f"        [{i}] {name!r}  size={size}  type=0x{ftype:X}")
            if count > 10:
                recv_exact(sock, (count - 10) * 40)  # drain remainder
                print(f"        ... (+{count - 10} more)")
    except (OSError, ConnectionError) as exc:
        print(f"[TCP] error during exchange: {exc}")
    finally:
        sock.close()
        print("[TCP] closed.")


if __name__ == "__main__":
    main()
