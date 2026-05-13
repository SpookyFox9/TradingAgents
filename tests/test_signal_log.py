from pathlib import Path
from datetime import date, timedelta
import json

import pytest

from portfolio_lib.signal_log import (
    record_decision,
    grade_open_signals,
    read_graded_signals,
    hit_rate_summary,
    render_track_record,
)


@pytest.fixture
def log_dir(tmp_path) -> Path:
    return tmp_path


def _backdated(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_record_creates_file(log_dir):
    record_decision(log_dir, "NVDA", "2026-05-05", "HOLD", 198.79)
    log = log_dir / "signal_log.jsonl"
    assert log.exists()


def test_record_contents(log_dir):
    record_decision(log_dir, "NVDA", "2026-05-05", "BUY", 150.0)
    row = json.loads((log_dir / "signal_log.jsonl").read_text())
    assert row["ticker"] == "NVDA"
    assert row["decision"] == "BUY"
    assert row["price_at_decision"] == 150.0
    assert row["grade"] is None


def test_record_deduplication(log_dir):
    record_decision(log_dir, "NVDA", "2026-05-05", "BUY", 150.0)
    record_decision(log_dir, "NVDA", "2026-05-05", "BUY", 155.0)  # duplicate
    lines = (log_dir / "signal_log.jsonl").read_text().splitlines()
    assert len([l for l in lines if l.strip()]) == 1


def test_grade_buy_correct(log_dir):
    record_decision(log_dir, "NVDA", _backdated(20), "BUY", 100.0)
    graded = grade_open_signals(log_dir, lambda t: 105.0)  # +5% → correct
    assert graded == 1
    rows = read_graded_signals(log_dir)
    assert rows[0]["grade"] == "Correct"


def test_grade_buy_wrong(log_dir):
    record_decision(log_dir, "NVDA", _backdated(20), "BUY", 100.0)
    grade_open_signals(log_dir, lambda t: 96.0)  # -4% → wrong
    rows = read_graded_signals(log_dir)
    assert rows[0]["grade"] == "Wrong"


def test_grade_sell_correct(log_dir):
    record_decision(log_dir, "GME", _backdated(20), "SELL", 25.0)
    grade_open_signals(log_dir, lambda t: 22.0)  # -12% → correct for SELL
    rows = read_graded_signals(log_dir)
    assert rows[0]["grade"] == "Correct"


def test_grade_hold_correct(log_dir):
    record_decision(log_dir, "NEE", _backdated(35), "HOLD", 95.0)
    grade_open_signals(log_dir, lambda t: 96.0)  # +1.1% → within 5% band → correct
    rows = read_graded_signals(log_dir)
    assert rows[0]["grade"] == "Correct"


def test_grade_not_yet_past_lookback(log_dir):
    record_decision(log_dir, "NVDA", _backdated(5), "BUY", 100.0)  # only 5 days old
    graded = grade_open_signals(log_dir, lambda t: 110.0)
    assert graded == 0  # too early


def test_grade_skips_missing_price(log_dir):
    record_decision(log_dir, "NVDA", _backdated(20), "BUY", 100.0)
    graded = grade_open_signals(log_dir, lambda t: None)
    assert graded == 0


def test_hit_rate_summary(log_dir):
    record_decision(log_dir, "NVDA", _backdated(20), "BUY", 100.0)
    record_decision(log_dir, "GME",  _backdated(20), "BUY", 50.0)
    grade_open_signals(log_dir, lambda t: 110.0)  # both +10% → correct
    stats = hit_rate_summary(log_dir)
    assert "BUY" in stats
    assert stats["BUY"]["hit_rate_pct"] == 100.0


def test_render_track_record_no_signals(log_dir):
    text = render_track_record(log_dir)
    assert "No graded signals" in text


def test_render_track_record_with_data(log_dir):
    # HOLD lookback is 30 days; use 35 to ensure it qualifies for grading
    record_decision(log_dir, "NVDA", _backdated(35), "HOLD", 198.0)
    graded = grade_open_signals(log_dir, lambda t: 199.0)
    assert graded == 1, "Signal should have been graded (35 days old, lookback=30)"
    text = render_track_record(log_dir)
    assert "HOLD" in text
    assert "%" in text
