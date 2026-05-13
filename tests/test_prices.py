from unittest.mock import MagicMock, patch

from portfolio_lib.prices import get_price, clear_cache


def setup_function():
    clear_cache()


@patch("portfolio_lib.prices.yf")
def test_get_price_returns_float(mock_yf):
    mock_yf.Ticker.return_value.fast_info = {"last_price": 123.45}
    price = get_price("NVDA")
    assert price == 123.45


@patch("portfolio_lib.prices.yf")
def test_get_price_caches_result(mock_yf):
    mock_yf.Ticker.return_value.fast_info = {"last_price": 50.0}
    get_price("GME")
    get_price("GME")
    assert mock_yf.Ticker.call_count == 1


@patch("portfolio_lib.prices.yf")
def test_get_price_returns_none_on_error(mock_yf):
    mock_yf.Ticker.side_effect = Exception("network error")
    price = get_price("BROKEN")
    assert price is None


@patch("portfolio_lib.prices.yf")
def test_get_price_caches_none(mock_yf):
    mock_yf.Ticker.side_effect = Exception("fail")
    get_price("FAIL")
    get_price("FAIL")
    assert mock_yf.Ticker.call_count == 1
