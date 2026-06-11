"""Optomed Aurora fundus camera device layer (read-only Pictor Wi-Fi client) + pure protocol.

Captures patient retinal images over the Pictor protocol. Distinct from the arm-mounted
USB observation camera in ``arm101_hand.system_camera`` (which films the Aurora's screen)."""

from .capture import pull_file, save_capture, snapshot_filenames, wait_for_new_files
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
    "pull_file",
    "recv_exact",
    "save_capture",
    "sidecar_dict",
    "snapshot_filenames",
    "wait_for_new_files",
]
