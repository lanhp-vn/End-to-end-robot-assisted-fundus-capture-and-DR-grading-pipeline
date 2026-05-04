"""Thin wrapper over lerobot's port-discovery utility.

Equivalent to running `uv run lerobot-find-port` directly — exposed here so
it sits next to the SO-ARM101 calibration runner for discoverability.
"""

from lerobot.scripts.lerobot_find_port import main

if __name__ == "__main__":
    main()
