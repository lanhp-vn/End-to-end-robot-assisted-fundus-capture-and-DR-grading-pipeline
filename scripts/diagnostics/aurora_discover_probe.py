"""Throwaway probe: does the Optomed Aurora answer the Pictor Prestige
wireless discovery protocol (SPC70001873)?

Read-only. Sends one UDP DETECT_CAMERA datagram (0x16AC3000) to the camera
and waits for a CAMERA_DETECTED (0x16AC3001) reply, then does a light TCP
port sweep to see what the camera exposes. Stdlib only.

Usage:
    uv run python scripts/diagnostics/aurora_discover_probe.py [CAMERA_IP]
"""

from __future__ import annotations

import socket
import struct
import sys

CAMERA_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.12.16"
DISCOVERY_PORT = 3000
DETECT_CAMERA = bytes([0x00, 0x30, 0xAC, 0x16])  # 0x16AC3000, little-endian on wire
TIMEOUT_S = 5.0

# Likely camera-side service ports; 554/8554 = RTSP (would reopen the streaming
# question if open), 80/443/8080 = web, 2422/3000 = Pictor-family.
TCP_PORTS = [80, 443, 554, 2422, 3000, 5000, 8000, 8080, 8443, 8554, 37777]


def udp_discover() -> None:
    print(f"[UDP] sending DETECT_CAMERA ({DETECT_CAMERA.hex(' ')}) "
          f"to {CAMERA_IP}:{DISCOVERY_PORT} (+broadcast fallbacks)")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(TIMEOUT_S)
    for dest in (CAMERA_IP, "192.168.12.255", "255.255.255.255"):
        try:
            s.sendto(DETECT_CAMERA, (dest, DISCOVERY_PORT))
        except OSError as exc:
            print(f"      (send to {dest} failed: {exc})")

    got = False
    try:
        while True:
            data, addr = s.recvfrom(2048)
            got = True
            print(f"\n[UDP] REPLY from {addr}: {len(data)} bytes")
            print(f"      hex: {data.hex(' ')}")
            if len(data) >= 4:
                cmd = struct.unpack_from("<I", data, 0)[0]
                tag = " (CAMERA_DETECTED!)" if cmd == 0x16AC3001 else ""
                print(f"      cmdId = 0x{cmd:08X}{tag}")
            if len(data) >= 56:
                iface = struct.unpack_from("<I", data, 4)[0]
                mac = data[8:28].split(b"\x00")[0].decode("ascii", "replace")
                reserved = struct.unpack_from("<I", data, 28)[0]
                custom = struct.unpack_from("<I", data, 32)[0]
                serial = data[36:56].split(b"\x00")[0].decode("ascii", "replace")
                print(f"      interfaceLevel={iface}  mac={mac!r}  "
                      f"cameraReserved={reserved}  customization={custom}  "
                      f"serial={serial!r}")
    except socket.timeout:
        if not got:
            print(f"\n[UDP] no reply within {TIMEOUT_S:.0f}s "
                  "-> Aurora does NOT answer Pictor discovery (use Path C).")
    finally:
        s.close()


def tcp_sweep() -> None:
    print(f"\n[TCP] sweeping {CAMERA_IP} ...")
    for port in TCP_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.6)
        rc = s.connect_ex((CAMERA_IP, port))
        s.close()
        if rc == 0:
            label = " <- RTSP?" if port in (554, 8554) else ""
            print(f"      OPEN  : {port}{label}")
    print("[TCP] sweep done (only OPEN ports listed).")


if __name__ == "__main__":
    udp_discover()
    tcp_sweep()
