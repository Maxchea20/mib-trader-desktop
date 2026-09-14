"""Hunt C gates."""
from src.brain.observation_hunt_c import structure_ok, HUNT_VERSION_C, slot_of, parent_open


def test_version():
    assert HUNT_VERSION_C == "OBSERVATION_HUNT_M5_C"


def test_slot_and_parent():
    # 15m open 0, 5m #3 open +600
    assert parent_open(1_700_000_600) == 1_700_000_600 - (1_700_000_600 % 900)
    assert slot_of(0) == 1
    assert slot_of(300) == 2
    assert slot_of(600) == 3


def test_structure_ok_choch():
    assert structure_ok({"event": "CHoCH", "hunt": {"event": "CHoCH"}})
    assert structure_ok({"event": "CHOCH", "bos_quality": {}})


def test_structure_ok_first_bos_only():
    assert structure_ok({
        "event": "BOS",
        "bos_quality": {"first_bos_after_choch": True},
        "hunt": {"event": "BOS", "first_bos_after_choch": True},
    })
    assert not structure_ok({
        "event": "BOS",
        "bos_quality": {"first_bos_after_choch": False},
        "hunt": {"event": "BOS"},
    })


def test_structure_ok_rejects_breakout():
    assert not structure_ok({
        "event": "BREAKOUT_DETECTED",
        "hunt": {"event": "BREAKOUT_DETECTED", "armed": True},
    })
