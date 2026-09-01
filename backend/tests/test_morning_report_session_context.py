"""The morning report must say when it was taken and what the market was doing (D5.19, D-9).

THE DEFECT
----------
The brief reports the Morning Report showing overnight text during an open
session. The data was not the problem — it was current when written. The
*narration* was, and this was the sentence in production on 2026-09-01:

    "Indian markets closed with Nifty at 24,058 (-0.09%) and Bank Nifty down
     0.95% ... Yesterday's top-performing sectors included Telecom (+2.69%)..."

That document was generated at **10:26 IST**, which is 71 minutes after NSE
opened. Nifty had not closed at 24,058; 24,058 was where it was trading at
10:26, and by 12:35 it was 24,112. The report described a live intraday level
as a settled close and a same-session sector move as yesterday's.

The cause is in the prompt, not the pipeline. `_generate_briefing` asked for a
"pre-market briefing" and handed the model a list of bare numbers with no
timestamp and no session state. Asked for a pre-market note, a model writes one
— and the only tense available for a number with no time attached is the past.
So the platform published a market event that did not happen, which is the one
thing CLAUDE.md's data rules and this sprint's brief both refuse outright.

The second half is age. A report generated at 10:26 and read at 12:35 is a
legitimate artefact — a morning report is a morning snapshot and regenerating
it continuously would make it something else. But it must carry the moment it
describes, or a reader cannot tell a two-hour-old level from a live one.

WHAT THESE TESTS PIN
--------------------
That the session state and observation time reach the prompt, that they are
recorded on the document, and — the falsifiable half — that the grounded
fallback never asserts a close while the market is open. None of this invents
data: it labels data the report already had.
"""
import asyncio

import pytest

from services import morning_report


def _run(coro):
    return asyncio.run(coro)


FACTS = {
    "nifty_str": "24,058",
    "sensex_str": "76,861",
    "nifty_chg": -0.09,
    "banknifty_chg": -0.95,
    "market_mood": "Neutral",
    "gift_nifty_str": "unavailable",
    "global_summary": "mixed",
    "fii_str": "₹4,589 Cr",
    "news_str": "bullish",
    "headlines_str": "unavailable",
    "events_str": "none scheduled",
    "sectors_str": "Telecom (+2.69%)",
    "picks_str": "SUNPHARMA, INFY",
    "picks_count": 2,
}


@pytest.fixture
def no_ai(monkeypatch):
    """Force the grounded fallback — no AI provider configured."""
    import server

    monkeypatch.setattr(server, "claude_configured", lambda: False)
    monkeypatch.setattr(server, "gemini_configured", lambda: False)


@pytest.fixture
def capture_prompt(monkeypatch):
    """Capture the context handed to the model instead of calling it."""
    seen = {}

    import server

    monkeypatch.setattr(server, "claude_configured", lambda: True)
    monkeypatch.setattr(server, "gemini_configured", lambda: True)

    class _Engine:
        async def simple_chat(self, system, context, **_k):
            seen["system"] = system
            seen["context"] = context
            return "A briefing."

    monkeypatch.setattr(server, "get_debate_engine", lambda: _Engine())
    return seen


def _facts(**over):
    merged = dict(FACTS)
    merged.update(over)
    return merged


# --------------------------------------------------------------------------- #
# The session reaches the model                                                #
# --------------------------------------------------------------------------- #

def test_the_prompt_states_that_the_market_is_open(capture_prompt):
    _run(morning_report._generate_briefing(_facts(market_is_open=True)))

    context = capture_prompt["context"].lower()
    assert "open" in context, "the model was not told the session state"


def test_the_prompt_states_that_the_market_is_closed(capture_prompt):
    _run(morning_report._generate_briefing(_facts(market_is_open=False)))

    assert "closed" in capture_prompt["context"].lower()


def test_the_prompt_carries_the_moment_the_numbers_were_observed(capture_prompt):
    _run(morning_report._generate_briefing(
        _facts(market_is_open=True, observed_at_str="10:26 IST")
    ))

    assert "10:26 IST" in capture_prompt["context"]


def test_an_open_session_is_not_described_as_pre_market(capture_prompt):
    """Falsification of the exact instruction that produced the defect.

    "Write a pre-market briefing" over live intraday numbers is what made the
    model write "markets closed with Nifty at 24,058" at 10:26 on an open NSE.
    """
    _run(morning_report._generate_briefing(_facts(market_is_open=True)))

    assert "pre-market briefing" not in capture_prompt["context"].lower()


def test_the_model_is_told_not_to_call_a_live_level_a_close(capture_prompt):
    _run(morning_report._generate_briefing(_facts(market_is_open=True)))

    context = capture_prompt["context"].lower()
    assert "close" in context, (
        "the prompt must explicitly forbid narrating a live level as a close"
    )


# --------------------------------------------------------------------------- #
# The grounded fallback                                                        #
# --------------------------------------------------------------------------- #

def test_the_fallback_does_not_claim_a_close_during_an_open_session(no_ai):
    briefing = _run(morning_report._generate_briefing(_facts(market_is_open=True)))

    assert "closed" not in briefing.lower()


def test_the_fallback_labels_an_open_session_as_live(no_ai):
    briefing = _run(morning_report._generate_briefing(_facts(market_is_open=True)))

    assert "trading" in briefing.lower() or "live" in briefing.lower()


def test_the_fallback_still_restates_the_real_numbers(no_ai):
    """The label is added; the data is not replaced."""
    briefing = _run(morning_report._generate_briefing(_facts(market_is_open=True)))

    assert "24,058" in briefing


def test_the_fallback_is_unchanged_in_shape_when_the_market_is_closed(no_ai):
    briefing = _run(morning_report._generate_briefing(_facts(market_is_open=False)))

    assert "24,058" in briefing
    assert briefing.strip()


# --------------------------------------------------------------------------- #
# The document records it                                                      #
# --------------------------------------------------------------------------- #

def test_session_context_is_recorded_on_the_report(monkeypatch):
    """A reader at 12:35 must be able to tell a 10:26 level from a live one."""
    monkeypatch.setattr(morning_report, "_market_is_open", lambda: True)

    context = morning_report.build_session_context()

    assert context["market_open"] is True
    assert context["observed_at"], "the report must carry when it was taken"


def test_session_context_reports_a_closed_market_as_closed(monkeypatch):
    monkeypatch.setattr(morning_report, "_market_is_open", lambda: False)

    assert morning_report.build_session_context()["market_open"] is False


def test_the_clock_reads_the_platform_validator(monkeypatch):
    """Drive the real `_market_is_open`, not a stand-in for it.

    Falsification M23 replaced this function's body with `return False` and
    every other test in this file still passed, because they all monkeypatch
    `_market_is_open` itself. A helper that nothing exercises is a helper that
    can be replaced by a constant without anyone noticing.
    """
    from services.market_engine import validator

    monkeypatch.setattr(validator, "is_market_hours", lambda: True)
    assert morning_report._market_is_open() is True

    monkeypatch.setattr(validator, "is_market_hours", lambda: False)
    assert morning_report._market_is_open() is False


def test_an_unanswerable_clock_reports_closed(monkeypatch):
    """D5.18's D-1 rule: a market the platform cannot vouch for is not open."""
    from services.market_engine import validator

    def _boom():
        raise RuntimeError("clock unavailable")

    monkeypatch.setattr(validator, "is_market_hours", _boom)
    assert morning_report._market_is_open() is False


def test_session_context_uses_the_platform_clock_not_a_provider_field(monkeypatch):
    """D5.18's D-1 rule, applied here.

    Whether NSE is open is a fact about the exchange and the clock. Sourcing it
    from a vendor's `marketState` is what rendered "MARKET CLOSED" over ticking
    prices; this report must read the same clock the badge does.
    """
    calls = []

    def _clock():
        calls.append(1)
        return True

    monkeypatch.setattr(morning_report, "_market_is_open", _clock)
    morning_report.build_session_context()

    assert calls, "the platform clock was not consulted"
