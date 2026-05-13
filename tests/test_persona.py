from portfolio_lib.persona import render, DEFAULT_PERSONA, InvestorPersona


def test_render_contains_strategy():
    text = render(DEFAULT_PERSONA, include_full_text=False)
    assert "Buffett" in text or "quality" in text.lower()


def test_render_no_hardcoded_tickers():
    """Specific tickers belong in portfolio context, not the static persona."""
    text = render(DEFAULT_PERSONA, include_full_text=False)
    for ticker in ("NVDA", "VRT", "PLTR", "PANW", "NEE", "TGT"):
        assert ticker not in text


def test_render_contains_red_flags():
    text = render(DEFAULT_PERSONA, include_full_text=False)
    assert "Red Flag" in text or "red flag" in text.lower()


def test_render_custom_persona():
    persona = InvestorPersona(
        name="Test Investor",
        style="Value",
        time_horizon="5 years",
        red_flags=["rising debt"],
        response_structure="Table | Decision",
    )
    text = render(persona, include_full_text=False)
    assert "Test Investor" in text
    assert "Value" in text
    assert "rising debt" in text


def test_render_returns_string():
    result = render(DEFAULT_PERSONA, include_full_text=False)
    assert isinstance(result, str)
    assert len(result) > 50
