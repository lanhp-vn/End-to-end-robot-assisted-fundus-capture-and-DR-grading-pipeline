from arm101_hand.config.motor_ids import FINGER_NAMES, FINGER_SERVO_IDS


def test_finger_names_canonical_order():
    assert FINGER_NAMES == ("index", "middle", "ring", "thumb")


def test_servo_ids_cover_1_to_8_uniquely():
    flat = [i for pair in FINGER_SERVO_IDS.values() for i in pair]
    assert sorted(flat) == list(range(1, 9))


def test_each_finger_has_odd_then_even():
    for s1, s2 in FINGER_SERVO_IDS.values():
        assert s1 % 2 == 1 and s2 % 2 == 0 and s2 == s1 + 1


def test_keys_match_finger_names():
    assert tuple(FINGER_SERVO_IDS.keys()) == FINGER_NAMES
