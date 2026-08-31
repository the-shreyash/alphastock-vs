"""Sprint D5.17 — the application universe is reference data that goes stale.

WHAT LIM-D5.16-2 ACTUALLY WAS
------------------------------
D5.16 found `TATAMOTORS` in `market_data.STOCK_UNIVERSE` and in no broker's
current master, and correctly declined to delete it: "do not silently delete
symbols merely because today's broker catalogues do not contain them." That is
the right rule. D5.17 supplied the evidence the rule asks for.

Tata Motors demerged. `TATAMOTORS` does not trade under that name at any venue.
Verified on 2026-08-31 against all five live published masters — none carries
the symbol, and each carries its two successors, `TMPV` (NSE 3456, the *old*
TATAMOTORS token renamed) and `TMCV` (NSE 759782) — and against Yahoo Finance,
which answers `TATAMOTORS.NS` with "No data found, symbol may be delisted"
while `TMPV.NS` and `TMCV.NS` both quote.

So the symbol was unpriceable by *every* source the platform has, on every
surface, for as long as it sat there: a permanently blank dashboard row, a
wasted resolution on every price cycle, and a sector exposure the universe
claimed to have and did not.

WHAT THIS FILE CAN AND CANNOT ASSERT
-------------------------------------
The hermetic tests below assert the invariants a universe row must satisfy and
the specific correction D5.17 made. They cannot assert that the universe is
*current* — that is a question only an exchange master can answer, and asking it
is a network call.

So the reconciliation is here too, skipped without an explicit
opt-in, and exempted from the hermetic network guard. That is the honest shape for the gap this sprint found and did not
close: **there is no reference-data refresh mechanism** (LIM-D5.17-1). Until
there is one, this is the check a human can run — `pytest -m live
tests/test_d517_reference_data.py` — instead of waiting for a user to notice a
price that stopped moving.
"""

import os

import pytest

from market_data import SECTORS, STOCK_UNIVERSE


# ==================================================================
# A. Universe invariants (hermetic)
# ==================================================================


def test_every_row_is_canonical_and_complete():
    """A universe row feeds the feed universe, the heatmap and the AI's context.
    A lowercase or padded symbol resolves at no broker and reads as a different
    instrument at every one of those three."""
    for row in STOCK_UNIVERSE:
        symbol = row["symbol"]
        assert symbol == symbol.strip().upper() and symbol, row
        assert row["name"].strip(), row
        assert row["sector"] in SECTORS, row


def test_no_symbol_appears_twice():
    symbols = [row["symbol"] for row in STOCK_UNIVERSE]
    assert len(symbols) == len(set(symbols))


def test_the_demerged_symbol_is_gone_and_both_successors_are_present():
    """Replaced, not dropped. The Auto exposure the old row represented still
    exists, and a universe that silently lost it is a different defect from the
    one being fixed."""
    symbols = {row["symbol"] for row in STOCK_UNIVERSE}

    assert "TATAMOTORS" not in symbols, (
        "a symbol that quotes at no broker and no baseline is a permanently "
        "blank row, not a placeholder"
    )
    assert {"TMPV", "TMCV"} <= symbols
    assert {row["sector"] for row in STOCK_UNIVERSE
            if row["symbol"] in ("TMPV", "TMCV")} == {"Auto"}


def test_the_delisted_symbol_no_longer_has_a_hardcoded_yahoo_ticker():
    """`resolve_yahoo_ticker` appends `.NS` to an unmapped NSE symbol, so the
    successors need no entry — but a *mapping* to a delisted ticker is a stated
    fact that is false, and would keep answering for a symbol nothing trades."""
    from services.real_market import YAHOO_TICKERS, resolve_yahoo_ticker

    assert "TATAMOTORS" not in YAHOO_TICKERS
    assert resolve_yahoo_ticker("TMPV") == "TMPV.NS"
    assert resolve_yahoo_ticker("TMCV") == "TMCV.NS"


def test_the_dashboard_feed_universe_follows_the_correction():
    """The universe is read, not restated — so a correction here reaches the
    instruments every account's broker feed is aimed at, with no second edit."""
    from services.brokers.feed_universe import dashboard_symbols

    symbols = set(dashboard_symbols())
    assert {"TMPV", "TMCV"} <= symbols
    assert "TATAMOTORS" not in symbols


# ==================================================================
# B. Reconciliation against a live exchange master (opt-in)
# ==================================================================

#: The one master used for reconciliation, and why only one.
#:
#: Kite's dump is a single unauthenticated CSV covering every exchange, so it is
#: the cheapest complete answer to "does this symbol still trade?". Five masters
#: would be five downloads to answer one question that is a property of the
#: exchange rather than of any broker.
KITE_INSTRUMENTS_URL = "https://api.kite.trade/instruments"


# `allow_network`: this test's entire purpose is to compare the platform's static
# universe against the exchange's live master, which cannot be done offline. It
# is gated behind an explicit environment opt-in as well, so the default run
# neither downloads anything nor depends on the network being reachable.
@pytest.mark.allow_network
@pytest.mark.skipif(
    os.environ.get("STOCKASSIST_LIVE_REFERENCE_CHECK", "").lower() not in ("1", "true", "yes"),
    reason="set STOCKASSIST_LIVE_REFERENCE_CHECK=1 to reconcile the universe "
           "against the live exchange master (downloads ~8 MB)",
)
def test_every_universe_symbol_still_trades():
    """The check that would have caught `TATAMOTORS` the day it stopped trading.

    Asserts against the *cash equity* index each adapter already builds, not
    against a raw grep of the file: a symbol that survives only as a derivative
    or an index row is not a tradable share and would satisfy a looser check
    while remaining unpriceable on every surface that reads this list.
    """
    import csv
    import io

    import httpx

    from services.brokers.catalogue import EQUITY_SEGMENT
    from services.brokers.registry import broker_registry

    response = httpx.get(KITE_INSTRUMENTS_URL, timeout=120.0)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(response.text)))

    index = type(broker_registry.get("zerodha")).build_catalogue_index(rows)
    tradable = {key[2] for key in index if key[0] == EQUITY_SEGMENT}

    missing = sorted(row["symbol"] for row in STOCK_UNIVERSE
                     if row["symbol"] not in tradable)
    assert not missing, (
        f"{missing} are in STOCK_UNIVERSE and in no exchange cash-equity row. "
        "Establish why before editing the list — a renamed symbol, a demerger "
        "and a suspension are three different corrections (see market_data.py)."
    )
