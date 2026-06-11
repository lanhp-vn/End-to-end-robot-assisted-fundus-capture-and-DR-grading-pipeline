# tests/unit/test_camera_protocol.py
import struct

from arm101_hand.camera.protocol import (
    CODE_OK,
    CODE_REQUEST,
    DETECT_CAMERA,
    DIRECTORY,
    GET_FILELIST,
    CameraInfo,
    CameraStatus,
    FileInfo,
    MessageFail,
    decode_fat32_datetime,
    pack_header,
    unpack_header,
)


def test_header_roundtrip():
    raw = pack_header(GET_FILELIST, CODE_REQUEST, 7)
    assert raw == struct.pack("<III", GET_FILELIST, CODE_REQUEST, 7)
    assert unpack_header(raw) == (GET_FILELIST, CODE_REQUEST, 7)


def test_detect_camera_command_value():
    # On the wire little-endian this is the bytes 00 30 ac 16 used in discovery.
    assert struct.pack("<I", DETECT_CAMERA) == b"\x00\x30\xac\x16"


def test_camera_info_parse_real_reply():
    # Real CAMERA_DETECTED bytes captured on hardware (56 B).
    data = bytes.fromhex(
        "0130ac16"  # cmdId 0x16AC3001
        "02000000"  # interfaceLevel=2
        "64632d66332d31632d33662d32312d6130000000"  # mac "dc-f3-1c-3f-21-a0" + NUL pad (20 B)
        "01000000"  # cameraReserved=1
        "01000000"  # cameraCustomization=1
        "3131323535383130393334323200000000000000"  # serial "1125581093422" + NUL pad (20 B)
    )
    info = CameraInfo.parse(data)
    assert info.interface_level == 2
    assert info.mac == "dc-f3-1c-3f-21-a0"
    assert info.reserved == 1
    assert info.customization == 1
    assert info.serial == "1125581093422"


def test_camera_status_parse():
    payload = struct.pack("<I", 0) + b"3.3.7.11860".ljust(16, b"\x00") + b"1.3.0.2563".ljust(16, b"\x00")
    st = CameraStatus.parse(payload)
    assert st.client_subscribed == 0
    assert st.sw_version == "3.3.7.11860"
    assert st.wifi_version == "1.3.0.2563"


def test_file_info_parse_and_is_dir():
    rec = (
        struct.pack("<I", 1790736)  # filesize
        + struct.pack("<I", 0x20)  # fileType FILE
        + struct.pack("<H", 0)  # date
        + struct.pack("<H", 0)  # time
        + b"\\DCIM\\P0001\\IM0002EY.JPG".ljust(28, b"\x00")
    )
    info = FileInfo.parse(rec)
    assert info.filesize == 1790736
    assert info.filename == "\\DCIM\\P0001\\IM0002EY.JPG"
    assert info.is_dir is False
    dir_rec = struct.pack("<IIHH", 0, DIRECTORY, 0, 0) + b"\\DCIM\\P0001".ljust(28, b"\x00")
    assert FileInfo.parse(dir_rec).is_dir is True


def test_message_fail_parse():
    payload = struct.pack("<I", 0x16AC6005) + b"file not found".ljust(64, b"\x00")
    fail = MessageFail.parse(payload)
    assert fail.err_code == 0x16AC6005
    assert fail.message == "file not found"


def test_decode_fat32_datetime():
    # 2024-03-21 14:30:08 -> date bits, time bits
    date = ((2024 - 1980) << 9) | (3 << 5) | 21
    time = (14 << 11) | (30 << 5) | (8 // 2)
    dt = decode_fat32_datetime(date, time)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (2024, 3, 21, 14, 30, 8)
