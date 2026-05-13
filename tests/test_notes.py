from portfolio_lib.notes import get_holding_notes, get_watchlist_notes


def test_gme_intentional_hold_note():
    notes = get_holding_notes("GME")
    assert any("INTENTIONAL" in n.upper() for n in notes)
    assert any("Dec 2026" in n for n in notes)


def test_gmews_has_note():
    notes = get_holding_notes("GMEWS")
    assert len(notes) > 0


def test_unknown_ticker_returns_empty():
    assert get_holding_notes("NVDA") == []


def test_watchlist_missing_target_adds_note():
    notes = get_watchlist_notes("BRO", None)
    assert any("No price target" in n or "no target" in n.lower() for n in notes)


def test_watchlist_with_target_no_missing_note():
    notes = get_watchlist_notes("BRO", 65.0)
    assert not any("No price target" in n for n in notes)


def test_watchlist_gme_preserves_annotations():
    notes = get_watchlist_notes("GME", None)
    assert any("INTENTIONAL" in n.upper() for n in notes)
