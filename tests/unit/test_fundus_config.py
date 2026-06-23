from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import FundusConfig, load_fundus_config

_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "fundus_config.yaml"


def test_defaults():
    cfg = FundusConfig()
    assert cfg.connection.message_port == 8000  # Pictor protocol constant
    assert cfg.connection.discovery_port == 3000  # Pictor protocol constant
    assert cfg.capture.hold_seconds > 0  # shutter-hold timing is tunable -- sanity only
    assert cfg.capture.dcim_root == "\\DCIM"
    assert cfg.capture.fundus_dir == "media_outputs/fundus_images"


def test_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        FundusConfig.model_validate({"connection": {"bogus": 1}})


def test_data_yaml_loads():
    cfg = load_fundus_config(_DATA)
    assert cfg.connection.message_port == 8000
    assert cfg.capture.stable_polls >= 1
    assert cfg.capture.fundus_dir == "media_outputs/fundus_images"
