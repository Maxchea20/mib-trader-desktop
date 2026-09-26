"""Learning Brain isolation + causal snapshot rules."""
from src.learning_brain.snapshot import row_kind
from src.learning_brain.features import extract, vector, FEATURE_KEYS
from src.learning_brain.isolation import fail_open


def test_row_kind_fire():
    assert row_kind({"action": "FIRE"}, "OPEN LONG") == "FIRE_SENT"
    assert row_kind({"action": "FIRE"}, "WEATHER_BLOCK") == "WEATHER_BLOCK"
    assert row_kind({"action": "WAIT", "timing_miss": True}) == "C_MISS"


def test_features_stable_keys():
    f = extract({"direction": "LONG", "event": "CHoCH", "timing": "S1"}, {"flag": "CHOP"}, 1700000000)
    assert list(f.keys()) == FEATURE_KEYS
    assert len(vector(f)) == len(FEATURE_KEYS)


def test_fail_open_swallows():
    @fail_open("SAFE")
    def boom():
        raise RuntimeError("no")
    assert boom() == "SAFE"
