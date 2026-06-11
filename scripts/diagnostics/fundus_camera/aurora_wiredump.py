"""Read-only wire dump of the Aurora's GET_FILE exchange (and the GET_FILELIST before it).

Diagnostic for the corrupted image pull seen in grab_trigger_capture: the camera returned a
107-byte non-JPEG with a garbled filename. This pulls an EXISTING file (no capture needed) and
hex-dumps every byte sent + received, plus a non-destructive MSG_PEEK check for leftover bytes
after each response. That single capture discriminates the three open hypotheses:

  * GET_FILE returns garbage on a plain existing file  -> response framing/layout is wrong
    (reproducible anytime). Compare the raw header+fileinfo bytes to the assumed layout:
    [12B header][40B FileInfo][filesize bytes].
  * Leftover bytes appear after GET_FILELIST           -> the filelist response carries trailing
    bytes the client never consumes, which then desyncs the following GET_FILE.
  * GET_FILE is clean here but failed after a capture  -> the corruption is specific to the
    post-capture "busy" window; instrument the live demo next.

Never writes to the camera (read commands only). Run with Optomed Client CLOSED.

Usage:
  uv run python scripts/diagnostics/fundus_camera/aurora_wiredump.py [--host IP] [--file CAMERA_PATH] [--root \\DCIM]
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_fundus_config  # noqa: E402
from arm101_hand.fundus_camera import CameraError, FileInfo, PictorClient  # noqa: E402
from arm101_hand.fundus_camera.protocol import (  # noqa: E402
    CODE_EVENT,
    CODE_FAIL,
    CODE_OK,
    CODE_REQUEST,
    MessageFail,
    unpack_header,
)

_CONFIG = _REPO_ROOT / "src" / "arm101_hand" / "data" / "fundus_config.yaml"
_CODE_NAMES = {
    CODE_OK: "CODE_OK",
    CODE_FAIL: "CODE_FAIL",
    CODE_REQUEST: "CODE_REQUEST",
    CODE_EVENT: "CODE_EVENT",
}


def _hexdump(data: bytes, max_bytes: int) -> str:
    shown = data[:max_bytes]
    lines = []
    for off in range(0, len(shown), 16):
        chunk = shown[off : off + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"      {off:04x}  {hexpart:<47}  {asciipart}")
    if len(data) > max_bytes:
        lines.append(f"      ... (+{len(data) - max_bytes} more bytes not shown)")
    return "\n".join(lines)


class Tap:
    """Wire tap passed to PictorClient: prints a labeled hex dump per send/recv when enabled."""

    def __init__(self, max_bytes: int):
        self.enabled = False
        self.max_bytes = max_bytes

    def __call__(self, label: str, data: bytes) -> None:
        if not self.enabled:
            return
        print(f"  {label} ({len(data)} bytes)")
        print(_hexdump(data, self.max_bytes))
        if label == "RECV header" and len(data) >= 12:
            cmd, code, seq = unpack_header(data)
            print(f"      -> cmdId=0x{cmd:X} code=0x{code:X} ({_CODE_NAMES.get(code, '??')}) seq={seq}")
        elif label == "RECV fail-payload" and len(data) >= 68:
            fail = MessageFail.parse(data)
            print(f"      -> errCode=0x{fail.err_code:X} msg={fail.message!r}")
        elif label == "RECV fileinfo" and len(data) >= FileInfo.SIZE:
            fi = FileInfo.parse(data)
            print(
                f"      -> filesize={fi.filesize} file_type=0x{fi.file_type:X} "
                f"filename={fi.filename!r} (printable_ascii={_is_clean(fi.filename)})"
            )


def _is_clean(name: str) -> bool:
    return bool(name) and all(32 <= ord(c) < 127 for c in name)


def _is_phantom(buf: bytes) -> bool:
    """True if ``buf`` begins with a spurious CODE_FAIL frame (seqId=0) -- the harmless trailing
    phantom the client's seqId skip-loop consumes on the next request (not a desync)."""
    if len(buf) < 12:
        return False
    _cmd, code, seq = unpack_header(buf)
    return code == CODE_FAIL and seq == 0


def _leftover_note(buf: bytes) -> str:
    if not buf:
        return ""
    if _is_phantom(buf):
        return " (harmless trailing phantom -- the next request's skip-loop consumes it)"
    return " <-- UNEXPECTED leftover (not a phantom); inspect the dump below"


def _peek_leftover(sock: socket.socket, *, max_n: int = 4096, timeout_s: float = 0.5) -> bytes:
    """Non-destructively peek at any bytes still buffered after a complete response.

    MSG_PEEK leaves the bytes in place (read-only). After a clean request/response there should
    be NONE; anything here means the last response framing consumed the wrong byte count.
    """
    prev = sock.gettimeout()
    sock.settimeout(timeout_s)
    try:
        return sock.recv(max_n, socket.MSG_PEEK)
    except (TimeoutError, OSError):
        return b""
    finally:
        sock.settimeout(prev)


def _prefer_jpg(files: list[FileInfo]) -> FileInfo:
    for f in files:
        if f.filename.lower().endswith((".jpg", ".jpeg")):
            return f
    return files[0]


def _find_a_file(camera: PictorClient, root: str) -> FileInfo | None:
    """First non-dir file under ``root`` (recursing one level into subdirectories)."""
    entries = camera.get_filelist(root)
    files = [e for e in entries if not e.is_dir]
    if files:
        return _prefer_jpg(files)
    for d in (e for e in entries if e.is_dir):
        sub = [e for e in camera.get_filelist(d.filename) if not e.is_dir]
        if sub:
            return _prefer_jpg(sub)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None, help="static camera IP (default: discover by broadcast)")
    ap.add_argument("--file", default=None, help="camera path to pull (default: auto-find an existing file)")
    ap.add_argument("--root", default="\\DCIM", help="filelist root to search (default: \\DCIM)")
    ap.add_argument("--max-bytes", type=int, default=128, help="bytes to hex-dump per message (default: 128)")
    args = ap.parse_args()

    conn = load_fundus_config(_CONFIG).connection
    tap = Tap(args.max_bytes)
    camera = PictorClient(
        host=args.host or conn.host,
        discovery_port=conn.discovery_port,
        message_port=conn.message_port,
        discover_timeout_s=conn.discover_timeout_s,
        connect_timeout_s=conn.connect_timeout_s,
        io_timeout_s=conn.io_timeout_s,
        trace=tap,
    )

    print("Connecting (Optomed Client must be CLOSED) ...")
    try:
        camera.ensure_connected()
        status = camera.get_status()
    except CameraError as e:
        print(f"camera not ready: {e}", file=sys.stderr)
        return 1
    info = camera.info
    print(f"CAMERA serial={info.serial if info else '?'} sw={status.sw_version} wifi={status.wifi_version}")

    # --- find the target file (trace OFF: this is just navigation) ---
    target = args.file
    if target is None:
        print(f"Searching {args.root} for an existing file to pull ...")
        found = _find_a_file(camera, args.root)
        if found is None:
            print(f"no file found under {args.root} -- pass --file explicitly", file=sys.stderr)
            return 1
        target = found.filename
    print(f"Target file: {target!r}")
    sock = camera._sock  # diagnostic: read the raw socket for the leftover-byte peek
    assert sock is not None

    # --- the GET_FILELIST immediately before the pull (trace ON) ---
    print(f"\n=== GET_FILELIST {Path(target).parent.as_posix()!r} (the call right before the pull) ===")
    tap.enabled = True
    parent = str(Path(target).parent)
    try:
        listing = camera.get_filelist(parent)
        print(f"  parsed {len(listing)} record(s)")
    except (CameraError, OSError) as e:
        print(f"  get_filelist error: {e}")
    leftover = _peek_leftover(sock)
    print(f"  LEFTOVER after GET_FILELIST: {len(leftover)} byte(s)" + _leftover_note(leftover))
    if leftover and not _is_phantom(leftover):
        print(_hexdump(leftover, args.max_bytes))

    # --- the GET_FILE pull itself (trace ON) ---
    print(f"\n=== GET_FILE {target!r} ===")
    try:
        fi, data = camera.get_file(target)
        leftover2 = _peek_leftover(sock)
        is_jpeg = data[:3] == b"\xff\xd8\xff"
        print("\n  --- summary ---")
        print(f"  returned filename : {fi.filename!r} (printable_ascii={_is_clean(fi.filename)})")
        print(f"  claimed filesize  : {fi.filesize}   bytes received: {len(data)}")
        print(f"  starts with JPEG  : {is_jpeg} (first 8 bytes: {data[:8].hex()})")
        print(f"  LEFTOVER after GET_FILE: {len(leftover2)} byte(s)" + _leftover_note(leftover2))
        if leftover2 and not _is_phantom(leftover2):
            print(_hexdump(leftover2, args.max_bytes))
        _verdict(_is_clean(fi.filename), is_jpeg, fi.filesize == len(data), leftover2)
    except CameraError as e:
        print(f"\n  GET_FILE raised CameraError: {e}")
        print("  (the camera refused the pull outright -- not a framing desync; retry / check it is idle)")
    except OSError as e:
        print(f"\n  GET_FILE socket error: {e}")
    finally:
        camera.close()
        print("\nCamera connection closed.")
    return 0


def _verdict(clean_name: bool, is_jpeg: bool, size_ok: bool, leftover: bytes) -> None:
    print("\n  --- verdict (read the bytes above to confirm) ---")
    if clean_name and is_jpeg and size_ok:
        print("  SUCCESS: GET_FILE returned a clean filename + valid JPEG magic + matching size --")
        print("  the seqId skip-loop pulled the real image past the camera's spurious phantom frames.")
        if _is_phantom(leftover):
            print("  The trailing leftover is just a phantom (CODE_FAIL seq=0); the NEXT request skips")
            print("  it harmlessly -- proven here, since this pull ran right after a filelist leftover.")
        elif leftover:
            print("  NOTE: trailing leftover is NOT a phantom -- inspect the dump above.")
    elif not clean_name or not is_jpeg:
        print("  GET_FILE returned garbage -> the response layout assumed by client.get_file")
        print("  ([12B header][40B fileInfo][filesize data]) does not match the wire. Compare the")
        print("  'RECV header' + 'RECV fileinfo' dumps above against that assumption.")
    else:
        print("  Mixed signal -- inspect the dumps above by hand.")


if __name__ == "__main__":
    sys.exit(main())
