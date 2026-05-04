"""Console-script entry: registers SO101FollowerNoGripper, then runs lerobot's calibrate."""

from lerobot.scripts.lerobot_calibrate import calibrate

from arm101_hand.robots import SO101FollowerNoGripper  # noqa: F401  (registers via decorator)


def main() -> None:
    # `calibrate` is draccus-wrapped; it reads sys.argv itself.
    calibrate()


if __name__ == "__main__":
    main()
