"""Schema tests for ``arm101_hand.config.app_config``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from arm101_hand.config import AppConfig, load_app_config

REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDED_PATH = REPO_ROOT / "data" / "app_config.yaml"


def test_defaults_match_seeded_yaml() -> None:
    """Per ``02-code-style-python.md`` §6: pydantic defaults must match the YAML."""
    cfg_from_defaults = AppConfig()
    cfg_from_yaml = load_app_config(SEEDED_PATH)
    for section in ("arm", "safety"):
        assert getattr(cfg_from_defaults, section) == getattr(cfg_from_yaml, section), (
            f"section {section!r}: defaults diverge from seeded YAML"
        )


def test_seeded_yaml_loads_clean() -> None:
    """The seeded YAML must round-trip without warnings."""
    cfg = load_app_config(SEEDED_PATH)
    assert cfg.schema_version == 1, "schema_version of seeded YAML"
    assert cfg.arm.port == "COM20", "arm port matches BOM"
    assert cfg.safety.temp_warn_c == 50.0, "temp warn threshold read by scan.py"
    assert cfg.safety.safe_park.park_velocity_arm == 600, "gentle home velocity"


# | bad_payload | description                                  |
@pytest.mark.parametrize(
    "bad_payload,desc",
    [
        ({"safety": {"temp_warn_c": 200.0}}, "temp_warn_c above le=100 rejected"),
        ({"safety": {"voltage_warn_pct": -1.0}}, "voltage_warn_pct below ge=0 rejected"),
        ({"safety": {"safe_park": {"park_velocity_arm": 5000}}}, "park_velocity_arm > 4095 rejected"),
        ({"arm": {"unknown_key": 1}}, "extra=forbid rejects unknown keys"),
        ({"hand": {"port": "COM18"}}, "removed hand block now rejected as extra"),
        ({"window": {"width": 1280}}, "removed window block now rejected as extra"),
    ],
)
def test_invalid_payloads_rejected(bad_payload: dict[str, object], desc: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(bad_payload)


def test_validation_error_message_contains_offending_field(tmp_path: Path) -> None:
    """The error path should make it clear which field failed (per §6 fail-fast)."""
    bad = {"safety": {"safe_park": {"park_velocity_arm": 5000}}}
    bad_yaml = tmp_path / "bad_app.yaml"
    bad_yaml.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValidationError) as ei:
        load_app_config(bad_yaml)
    assert "park_velocity_arm" in str(ei.value), f"error string mentions park_velocity_arm; got: {ei.value!s}"
