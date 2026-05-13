"""Special-case annotations for specific tickers."""

_ANNOTATIONS: dict[str, list[str]] = {
    "GME": [
        "INTENTIONAL HOLD — do not act on sell signals.",
        "Strategy: hold for recovery or tax-loss harvest by year-end.",
    ],
    "GMEWS": [
        "Warrant with $0 entry — skipped from analysis.",
    ],
}

_MISSING_TARGET_NOTE = (
    "No price target set. Use agent decision as directional signal only."
)


def get_holding_notes(ticker: str) -> list[str]:
    return list(_ANNOTATIONS.get(ticker, []))


def get_watchlist_notes(ticker: str, target: float | None) -> list[str]:
    notes = list(_ANNOTATIONS.get(ticker, []))
    if target is None:
        notes.append(_MISSING_TARGET_NOTE)
    return notes
