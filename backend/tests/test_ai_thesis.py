"""Unit tests for backend/src/ai_thesis.py.

No network access needed — _call_openai() is never exercised here (it's a
thin wrapper around the openai SDK with nothing to unit test beyond "does it
raise without a key", covered below). Everything else is pure functions.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ai_thesis  # noqa: E402


def test_build_prompt_includes_all_snapshot_fields():
    snap = {
        "timeframe": "15m", "price": 76892.8,
        "recent_ohlcv": "O:76800.0 H:76820.0 L:76790.0 C:76810.0 V:1000",
        "support_resistance": "Support (5x, str 25) @ 76833.1",
        "fibonacci": "Fib 61% @ 77485.4",
        "fair_value_gaps": "none",
        "agent_status_strip": "trend: SHORT (79%)",
        "hunt_action": "WAIT", "hunt_why": ["no CHoCH"],
        "weather_flag": "CHOP", "market_regime": "NEUTRAL",
    }
    prompt = ai_thesis._build_prompt(snap)
    assert "WAIT" in prompt
    assert "CHOP" in prompt
    assert "NEUTRAL" in prompt
    assert "76892.8" in prompt
    assert "Support (5x, str 25)" in prompt
    assert "trend: SHORT (79%)" in prompt


def test_prompt_instructs_independent_read_and_honest_disagreement():
    """Regression guard for the explicit design requirement: the AI must not
    be steered toward agreeing with the Brain by default."""
    prompt = ai_thesis._build_prompt({
        "timeframe": "15m", "price": 1, "recent_ohlcv": "", "hunt_action": "WAIT",
        "hunt_why": [], "weather_flag": None, "market_regime": None,
        "support_resistance": "none", "fibonacci": "none",
        "fair_value_gaps": "none", "agent_status_strip": "none",
    })
    lowered = prompt.lower()
    assert "own" in lowered and "read" in lowered
    assert "disagree" in lowered


def test_recent_ohlcv_summary_includes_full_bar_not_just_close():
    candles = [{"ts": 1, "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 500}]
    out = ai_thesis._recent_ohlcv_summary(candles)
    assert "O:100.0" in out
    assert "H:105.0" in out
    assert "L:98.0" in out
    assert "C:102.0" in out
    assert "V:500" in out


def test_levels_summary_pulls_same_data_key_levels_panel_uses():
    agents = [{"agent": "support_resistance", "key_levels": [
        {"label": "Support (5x, str 25)", "price": 76833.1},
        {"label": "Resistance (2x, str 18)", "price": 77001.4},
    ]}]
    out = ai_thesis._levels_summary(agents, "support_resistance")
    assert "Support (5x, str 25) @ 76833.1" in out
    assert "Resistance (2x, str 18) @ 77001.4" in out


def test_levels_summary_missing_agent_returns_none_string():
    assert ai_thesis._levels_summary([], "support_resistance") == "none"


def test_status_strip_summary_formats_all_five_agents():
    agents = [
        {"agent": "breakout", "direction": "NEUTRAL", "confidence": 34},
        {"agent": "momentum", "direction": "SHORT", "confidence": 53},
        {"agent": "volume", "direction": "SHORT", "confidence": 59},
        {"agent": "trend", "direction": "SHORT", "confidence": 79},
        {"agent": "pattern", "direction": "LONG", "confidence": 55},
    ]
    out = ai_thesis._status_strip_summary(agents)
    assert "trend: SHORT (79%)" in out
    assert "pattern: LONG (55%)" in out


def test_parse_response_disagree_case():
    raw = "ALIGNED: no\nTHESIS: Structure looks like it's coiling for a breakout the Brain hasn't caught."
    parsed = ai_thesis._parse_response(raw)
    assert parsed["aligned"] is False
    assert "coiling" in parsed["thesis"]


def test_parse_response_agree_case():
    raw = "ALIGNED: yes\nTHESIS: Range-bound chop, agrees with the Brain sitting out."
    parsed = ai_thesis._parse_response(raw)
    assert parsed["aligned"] is True


def test_parse_response_missing_aligned_line_defaults_to_none():
    raw = "THESIS: Some read with no ALIGNED line at all."
    parsed = ai_thesis._parse_response(raw)
    assert parsed["aligned"] is None
    assert "Some read" in parsed["thesis"]


def test_call_openai_raises_cleanly_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        ai_thesis._call_openai("test prompt")
        raised = False
    except RuntimeError as e:
        raised = True
        msg = str(e)
    assert raised is True
    assert "OPENAI_API_KEY" in msg


def test_run_once_disabled_is_a_no_op():
    ai_thesis.CONFIG["enabled"] = False
    ai_thesis.STATE["thesis"] = None
    try:
        ai_thesis.run_once()
        assert ai_thesis.STATE["thesis"] is None
        assert ai_thesis.STATE["processing"] is False
    finally:
        ai_thesis.CONFIG["enabled"] = True


def test_status_shape():
    s = ai_thesis.status()
    assert "config" in s and "state" in s
    assert "thesis" in s["state"]
    assert "processing" in s["state"]
    assert "aligned_with_brain" in s["state"]


def test_module_has_no_write_path():
    """Structural guard for the core safety claim: this module must never
    call anything that can place, modify, or cancel an order, or write
    autotrader config. Checks actual call sites (module docstring is
    exempt — it exists to describe this exact guarantee in prose)."""
    import inspect
    src = inspect.getsource(ai_thesis)
    # Strip the module docstring (the first triple-quoted block) before
    # scanning, since it legitimately names these functions to explain
    # they are NOT called.
    body = src.split('"""', 2)[-1] if src.count('"""') >= 2 else src
    forbidden = ["paper_trading.open_trade(", "paper_trading.close_trade(",
                 "mexc_private.submit_order(", "mexc_private.cancel_orders(",
                 "autotrader.update("]
    for term in forbidden:
        assert term not in body, f"found forbidden write-path call: {term}"