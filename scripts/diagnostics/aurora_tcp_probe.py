"""Throwaway probe: confirm the Aurora services the Pictor TCP message socket.

Read-only. Connects to the message port (default 8000), sends PING_CAMERA then
GET_CAMERA_STATUS (both payload-free requests), and prints the responses.
Sends NO write commands (never POST_FILE). Stdlib only.

Usage:
    uv run python scripts/diagnostics/aurora_tcp_probe.py [CAMERA_IP] [PORT]
"""

from __future__ import annotations

import socket
import struct
import sys

CAMERA_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.12.16"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
TIMEOUT_S = 6.0

# cmdIds
PING_CAMERA = 0x16AC4010
GET_CAMERA_STATUS = 0x16AC4008
# codes
CODE_OK = 0x16AC5001
CODE_FAIL = 0x16AC5002
CODE_REQUEST = 0x16AC5003


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"socket closed after {len(buf)}/{n} bytes")
        buf += chunk
    return buf


def read_response(sock: socket.socket, label: str, ok_payload_len: int) -> bytes | None:
    hdr = recv_exact(sock, 12)
    cmd, code, seq = struct.unpack("<III", hdr)
    print(f"  [{label}] response: cmd=0x{cmd:08X} code=0x{code:08X} seq={seq}")
    if code == CODE_FAIL:
        payload = recv_exact(sock, 68)  # uint32 errCode + char[64] errMsg
        errcode = struct.unpack_from("<I", payload, 0)[0]
        errmsg = payload[4:].split(b"\x00")[0].decode("ascii", "replace")
        print(f"        CODE_FAIL errCode=0x{errcode:08X} msg={errmsg!r}")
        return None
    if code == CODE_OK and ok_payload_len:
        return recv_exact(sock, ok_payload_len)
    return b""


def main() -> None:
    print(f"[TCP] connecting to {CAMERA_IP}:{PORT} ...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_S)
    try:
        sock.connect((CAMERA_IP, PORT))
    except OSError as exc:
        print(f"[TCP] connect failed: {exc}")
        return
    print("[TCP] connected.")

    try:
        # 1) PING_CAMERA (no payload, OK has no payload)
        print("[TCP] -> PING_CAMERA")
        sock.sendall(struct.pack("<III", PING_CAMERA, CODE_REQUEST, 1))
        read_response(sock, "PING", ok_payload_len=0)

        # 2) GET_CAMERA_STATUS (no payload; OK payload = u32 + char[16] + char[16] = 36)
        print("[TCP] -> GET_CAMERA_STATUS")
        sock.sendall(struct.pack("<III", GET_CAMERA_STATUS, CODE_REQUEST, 2))
        payload = read_response(sock, "STATUS", ok_payload_len=36)
        if payload and len(payload) >= 36:
            subscribed = struct.unpack_from("<I", payload, 0)[0]
            sw = payload[4:20].split(b"\x00")[0].decode("ascii", "replace")
            wifi = payload[20:36].split(b"\x00")[0].decode("ascii", "replace")
            print(f"        clientSubscribed={subscribed}  "
                  f"cameraSwVersion={sw!r}  wifiSwVersion={wifi!r}")
    except (OSError, ConnectionError) as exc:
        print(f"[TCP] error during exchange: {exc}")
        print("      (if this stalled, a client may hold the single allowed "
              "connection -> close Optomed Client and retry)")
    finally:
        sock.close()
        print("[TCP] closed.")


if __name__ == "__main__":
    main()
