"""The day's change must be the day's change, at every requested range (D5.19).

THE DEFECT
----------
`fetch_yahoo_quote` derived `prev_close` from Yahoo's `meta.chartPreviousClose`,
which is *the close before the requested window starts* — not the previous
session's close. It is therefore a function of `range_str`, and every caller
that asked for history to compute indicators silently got a change measured
over that whole history.

Measured live on 2026-09-01 against the real vendor, one symbol, four ranges:

    RELIANCE  2d   prev_close=1277.0  chg=+2.62%
    RELIANCE  5d   prev_close=1298.0  chg=+0.96%
    RELIANCE  1mo  prev_close=1307.8  chg=+0.21%
    RELIANCE  3mo  prev_close=1320.0  chg=-0.72%

Four different answers to "what did RELIANCE do today", from one price.

`fetch_real_stock_quote` asks for `3mo` (it needs 26+ bars for MACD), so
`GET /api/stocks/RELIANCE` — the stock detail page — reported **-0.72%** while
`/api/market/ranking` and `/api/market/overview`, which fetch `2d`, reported
**+2.62%** for the same instrument at the same instant. The detail page was
showing a three-month change under a "today" label.

This is the D5.18 D-1 defect's shape exactly: two surfaces of one product
disagreeing about one fact. D-1 was the market clock; this is the day's change.
It blocks D5.19's D-3, because making an index card navigable sends a user from
a card reading NIFTY +0.13% to a page reading NIFTY +3.11%.

THE INVARIANT
-------------
The previous close is the close of the bar before the one the live price
belongs to. That is a property of the series, so it is the same number at every
range — which is exactly what these tests assert, and what makes the assertion
falsifiable: a fix that merely hardcodes `2d` somewhere passes the equality but
fails `test_prev_close_ignores_chart_previous_close`.
"""
import asyncio

import pytest

from services import real_market


def _run(coro):
    """The suite's convention for driving a coroutine — see test_market_gateway."""
    return asyncio.run(coro)


def _payload(closes, *, chart_prev, price, timestamps=None):
    """A Yahoo chart response carrying `closes` as its daily bars.

    `chart_prev` is deliberately set to a value that appears nowhere in the
    series, so any test that passes cannot be reading it by accident.
    """
    n = len(closes)
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "chartPreviousClose": chart_prev,
                        "marketState": "REGULAR",
                        "exchangeName": "NSE",
                        "currency": "INR",
                    },
                    "timestamp": timestamps or list(range(n)),
                    "indicators": {
                        "quote": [
                            {
                                "open": list(closes),
                                "high": [c + 1 for c in closes],
                                "low": [c - 1 for c in closes],
                                "close": list(closes),
                                "volume": [1000 + i for i in range(n)],
                            }
                        ]
                    },
                }
            ]
        }
    }


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def yahoo(monkeypatch):
    """Serve a scripted chart payload per requested range, with no cache."""
    sent = {}

    async def _cache_get(_key):
        return None

    async def _cache_set(*_a, **_k):
        return None

    monkeypatch.setattr(real_market, "cache_get", _cache_get)
    monkeypatch.setattr(real_market, "cache_set", _cache_set)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **_k):
            rng = url.split("range=")[1]
            sent["range"] = rng
            return _Resp(sent["payloads"][rng])

    monkeypatch.setattr(
        real_market.http_client, "client_for", lambda **_k: _Client()
    )
    return sent


# The same instrument, the same live price, three windows onto one series.
# Yesterday's close is 100.0 in every one of them; `chartPreviousClose` is a
# different, window-dependent number in each, and is never 100.0.
_PRICE = 110.0
_YESTERDAY = 100.0
_SERIES = {
    "2d": ([_YESTERDAY, _PRICE], 91.0),
    "5d": ([70.0, 80.0, 90.0, _YESTERDAY, _PRICE], 61.0),
    "3mo": ([float(50 + i) for i in range(49)] + [_YESTERDAY, _PRICE], 41.0),
}


@pytest.mark.parametrize("range_str", sorted(_SERIES))
def test_prev_close_is_the_previous_session_at_every_range(yahoo, range_str):
    """One price, one series, one answer — whatever window was requested."""
    yahoo["payloads"] = {
        r: _payload(closes, chart_prev=prev, price=_PRICE)
        for r, (closes, prev) in _SERIES.items()
    }

    quote = _run(real_market.fetch_yahoo_quote("TESTSYM", range_str=range_str))

    assert quote is not None
    assert quote["prev_close"] == _YESTERDAY
    assert quote["change"] == pytest.approx(10.0)
    assert quote["change_pct"] == pytest.approx(10.0)


def test_the_days_change_does_not_depend_on_the_requested_range(yahoo):
    """The regression this file exists for, stated as one assertion.

    Before the fix these three ranges returned +10.0%, +57.1% and +168.3% for
    an identical instrument at an identical price.
    """
    yahoo["payloads"] = {
        r: _payload(closes, chart_prev=prev, price=_PRICE)
        for r, (closes, prev) in _SERIES.items()
    }

    answers = {}
    for range_str in sorted(_SERIES):
        quote = _run(real_market.fetch_yahoo_quote("TESTSYM", range_str=range_str))
        answers[range_str] = (quote["prev_close"], quote["change_pct"])

    assert len(set(answers.values())) == 1, (
        f"the day's change differs by range: {answers}"
    )


def test_prev_close_ignores_chart_previous_close(yahoo):
    """The vendor field is not consulted when the series can answer.

    A fix that special-cased `range_str == "2d"` would pass the equality tests
    above; it fails here, because this asserts on the *source* of the number
    rather than on its value at one range.
    """
    closes = [100.0, 110.0]
    yahoo["payloads"] = {
        "2d": _payload(closes, chart_prev=999.0, price=110.0),
    }

    quote = _run(real_market.fetch_yahoo_quote("TESTSYM", range_str="2d"))

    assert quote["prev_close"] == 100.0
    assert quote["prev_close"] != 999.0


def test_single_bar_falls_back_to_the_vendor_field(yahoo):
    """With one bar the series cannot answer, and the vendor's is the best there is.

    Absence of a second bar is not a reason to report a zero change: that would
    render "unchanged" over a price that may have moved several percent, which
    is the fabrication `applyLivePrices` and the tick contract both refuse.
    """
    yahoo["payloads"] = {"1d": _payload([110.0], chart_prev=100.0, price=110.0)}

    quote = _run(real_market.fetch_yahoo_quote("TESTSYM", range_str="1d"))

    assert quote["prev_close"] == 100.0
    assert quote["change_pct"] == pytest.approx(10.0)


def test_no_usable_previous_close_reports_no_change_rather_than_a_wrong_one(yahoo):
    """Neither a series nor a vendor field: the change is unknown.

    The pre-existing contract returns 0 here, and this pins it rather than
    changing it — but it pins the *reason*: `prev_close` is falsy, so there is
    nothing to subtract from. A future improvement should make this `None`;
    doing so inside this sprint would change a field's type for every consumer.
    """
    yahoo["payloads"] = {"1d": _payload([110.0], chart_prev=0, price=110.0)}

    quote = _run(real_market.fetch_yahoo_quote("TESTSYM", range_str="1d"))

    assert quote["change"] == 0
    assert quote["change_pct"] == 0
