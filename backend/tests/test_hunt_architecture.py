from types import SimpleNamespace

from src.brain import hunt_s1, hunt_slot3, hunt_brain


def _event(kind, ts, direction="LONG", level=100.0):
    return SimpleNamespace(
        event_type=kind,
        timestamp=ts,
        detection_timestamp=ts,
        direction=direction,
        reference_price=level,
        price=level,
    )


def test_s1_current_forming_event_rejects_previous_15m(monkeypatch):
    forming = [{"ts": 1800}, {"ts": 2700}]
    monkeypatch.setattr(hunt_s1, "forming_15m", lambda *_: forming)
    monkeypatch.setattr(
        hunt_s1,
        "obs_structure",
        lambda *_args, **_kwargs: SimpleNamespace(
            history=[_event("BOS", 1800), _event("BOS", 2700)]
        ),
    )
    got = hunt_s1.current_forming_15m_event(
        [{"ts": 0}],
        [{"ts": 2700}],
    )
    assert got.timestamp == 2700


def test_s1_current_forming_event_returns_none_when_only_old_event(monkeypatch):
    forming = [{"ts": 1800}, {"ts": 2700}]
    monkeypatch.setattr(hunt_s1, "forming_15m", lambda *_: forming)
    monkeypatch.setattr(
        hunt_s1,
        "obs_structure",
        lambda *_args, **_kwargs: SimpleNamespace(
            history=[_event("BOS", 1800)]
        ),
    )
    assert hunt_s1.current_forming_15m_event([{"ts": 0}], [{"ts": 2700}]) is None


def test_slot3_only_enters_next_15m():
    ev = _event("BOS", 2700)
    assert hunt_slot3.next_candle_ready(ev, 3599) is False
    assert hunt_slot3.next_candle_ready(ev, 3600) is True
    assert hunt_slot3.next_candle_ready(ev, 4499) is True
    assert hunt_slot3.next_candle_ready(ev, 4500) is False


def test_s1_engine_is_only_a_compatibility_facade():
    import inspect
    from src.brain import s1_engine

    source = inspect.getsource(s1_engine)
    assert "last_15m_break" not in source
    assert "fresh_m5_event_s1" not in source


def test_legacy_s1_lookback_is_removed():
    import inspect
    import src.brain.s1_detect as detect

    source = inspect.getsource(detect)
    assert "S1_LOOKBACK_BARS" not in source
    assert "last_15m_break" not in source
    assert "fresh_m5_event_s1" not in source


def test_hunt_state_has_one_ticket_authority():
    hunt_brain.reset_hunt_state()
    assert hunt_brain._STATE["fired"] is False
    assert "thesis_id" in hunt_brain._STATE
    assert "objective_15m_ts" in hunt_brain._STATE
