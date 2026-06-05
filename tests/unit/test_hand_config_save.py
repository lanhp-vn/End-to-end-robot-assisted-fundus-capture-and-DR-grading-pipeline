from pathlib import Path

from arm101_hand.config import (
    HandCalibration,
    PoseSpeeds,
    load_hand_calibration,
    save_hand_calibration,
)

SEED = Path("scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml")


def test_pose_speeds_default_when_absent():
    raw = {
        "schema_version": 2,
        "com_port": "COM18",
        "baudrate": 1000000,
        "timeout": 0.5,
        "speed": 4,
        "fingers": {
            "index": {
                "servo_1": {"id": 1, "middle_pos": 0},
                "servo_2": {"id": 2, "middle_pos": 0},
                "limits": {"base_min": -20, "base_max": 70, "side_min": -40, "side_max": 35},
            }
        },
    }
    cfg = HandCalibration.model_validate(raw)
    assert cfg.speeds == PoseSpeeds(open=5, close=3)


def test_calibration_round_trip(tmp_path):
    cfg = load_hand_calibration(SEED)
    out = tmp_path / "calib.yaml"
    save_hand_calibration(out, cfg)
    reloaded = load_hand_calibration(out)
    assert reloaded.model_dump() == cfg.model_dump()
