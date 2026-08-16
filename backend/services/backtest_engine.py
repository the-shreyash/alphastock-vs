"""Backtesting Engine — simulates trading strategies on historical NSE data via yfinance."""
import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STRATEGIES = ["RSI_STRATEGY", "EMA_CROSSOVER", "VWAP_REVERSION", "MACD_SIGNAL"]

#: The shortest history any of these strategies can be evaluated over. Below it
#: the indicators return their neutral seed values (`_rsi` yields a flat 50,
#: `_ema` repeats the first price), so a "backtest" over fewer bars measures the
#: padding rather than the strategy. Refusing is the honest answer; the previous
#: code raised here too, but the raise was caught and answered with fabricated
#: performance.
_MIN_BARS = 10


# ─── Indicator helpers ────────────────────────────────────────────────────────

def _ema(prices, period):
    if len(prices) < period:
        return [prices[0]] * len(prices)
    mult = 2.0 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append((p - ema[-1]) * mult + ema[-1])
    return [ema[0]] * (period - 1) + ema


def _rsi(prices, period=14):
    if len(prices) < period + 1:
        return [50.0] * len(prices)
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    rsi_vals = [50.0] * period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs = avg_g / avg_l if avg_l else 100
        rsi_vals.append(100 - 100 / (1 + rs))
    return [50.0] + rsi_vals  # offset by 1 for alignment


def _macd(prices):
    if len(prices) < 26:
        return [0] * len(prices), [0] * len(prices), [0] * len(prices)
    e12 = _ema(prices, 12)
    e26 = _ema(prices, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    signal = _ema(macd_line, 9)
    hist = [m - s for m, s in zip(macd_line, signal)]
    return macd_line, signal, hist


def _vwap(closes, highs, lows, volumes):
    vwaps = []
    for c, h, l, v in zip(closes, highs, lows, volumes):
        tp = (h + l + c) / 3
        vwaps.append(tp)  # simplified daily VWAP = typical price
    return vwaps


# ─── Strategy signal generators ──────────────────────────────────────────────

def _signals_rsi(closes, stop_pct, tgt_pct):
    rsi = _rsi(closes)
    signals = []
    in_trade = False
    for i in range(1, len(closes)):
        if not in_trade and rsi[i] < 30 and rsi[i - 1] >= 30:
            signals.append(("BUY", i))
            in_trade = True
        elif in_trade and (rsi[i] > 70 or rsi[i] > rsi[i - 1] + 20):
            signals.append(("SELL", i))
            in_trade = False
    return signals


def _signals_ema_crossover(closes):
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    signals = []
    for i in range(1, len(closes)):
        if ema9[i] > ema21[i] and ema9[i - 1] <= ema21[i - 1]:
            signals.append(("BUY", i))
        elif ema9[i] < ema21[i] and ema9[i - 1] >= ema21[i - 1]:
            signals.append(("SELL", i))
    return signals


def _signals_macd(closes):
    _, _, hist = _macd(closes)
    signals = []
    for i in range(1, len(closes)):
        if hist[i] > 0 and hist[i - 1] <= 0:
            signals.append(("BUY", i))
        elif hist[i] < 0 and hist[i - 1] >= 0:
            signals.append(("SELL", i))
    return signals


def _signals_vwap(closes, highs, lows, volumes):
    vwaps = _vwap(closes, highs, lows, volumes)
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    signals = []
    for i in range(1, len(closes)):
        dip = (vwaps[i] - closes[i]) / vwaps[i] * 100
        vol_spike = volumes[i] > avg_vol * 1.5
        if dip >= 1.5 and vol_spike:
            signals.append(("BUY", i))
        elif closes[i] >= vwaps[i]:
            signals.append(("SELL", i))
    return signals


# ─── Core backtest simulation ─────────────────────────────────────────────────

def _simulate(dates, opens, closes, highs, lows, volumes, strategy, stop_pct, tgt_pct, capital):
    if strategy == "RSI_STRATEGY":
        raw_signals = _signals_rsi(closes, stop_pct, tgt_pct)
    elif strategy == "EMA_CROSSOVER":
        raw_signals = _signals_ema_crossover(closes)
    elif strategy == "MACD_SIGNAL":
        raw_signals = _signals_macd(closes)
    else:  # VWAP_REVERSION
        raw_signals = _signals_vwap(closes, highs, lows, volumes)

    trades = []
    equity_curve = [{"date": str(dates[0])[:10], "capital": round(capital, 2)}]
    current_capital = capital
    position = None  # {"entry_price": float, "entry_idx": int, "shares": int, "entry_date": str}

    signal_map = {idx: action for action, idx in raw_signals}

    for i in range(len(closes)):
        date_str = str(dates[i])[:10]

        # Check stop-loss / target if in trade
        if position:
            entry = position["entry_price"]
            sl_price = entry * (1 - stop_pct / 100)
            tgt_price = entry * (1 + tgt_pct / 100)
            close = closes[i]

            hit_sl = close <= sl_price
            hit_tgt = close >= tgt_price
            sell_signal = signal_map.get(i) == "SELL"

            if hit_sl or hit_tgt or sell_signal:
                if hit_sl:
                    exit_price = sl_price
                    result = "LOSS"
                elif hit_tgt:
                    exit_price = tgt_price
                    result = "WIN"
                else:
                    exit_price = opens[i] if i + 1 < len(opens) else close
                    result = "WIN" if exit_price > entry else "LOSS"

                pnl = (exit_price - entry) * position["shares"]
                pnl_pct = ((exit_price - entry) / entry) * 100
                current_capital += pnl
                trades.append({
                    "entry_date": position["entry_date"],
                    "exit_date": date_str,
                    "entry_price": round(entry, 2),
                    "exit_price": round(exit_price, 2),
                    "shares": position["shares"],
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "result": result,
                    "type": "BUY→SELL",
                })
                position = None
                equity_curve.append({"date": date_str, "capital": round(current_capital, 2)})

        # Enter trade on BUY signal if not in position
        if not position and signal_map.get(i) == "BUY" and current_capital > 0:
            entry_price = opens[i + 1] if i + 1 < len(opens) else closes[i]
            shares = int(current_capital * 0.95 / entry_price)
            if shares > 0:
                position = {
                    "entry_price": entry_price,
                    "entry_idx": i,
                    "shares": shares,
                    "entry_date": date_str,
                }

    # Close any open position at end
    if position:
        exit_price = closes[-1]
        pnl = (exit_price - position["entry_price"]) * position["shares"]
        pnl_pct = ((exit_price - position["entry_price"]) / position["entry_price"]) * 100
        current_capital += pnl
        result = "WIN" if pnl > 0 else "LOSS"
        trades.append({
            "entry_date": position["entry_date"],
            "exit_date": str(dates[-1])[:10],
            "entry_price": round(position["entry_price"], 2),
            "exit_price": round(exit_price, 2),
            "shares": position["shares"],
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "result": result,
            "type": "BUY→SELL (open)",
        })
        equity_curve.append({"date": str(dates[-1])[:10], "capital": round(current_capital, 2)})

    return trades, equity_curve, round(current_capital, 2)


def _compute_metrics(trades, equity_curve, initial_capital, final_capital):
    if not trades:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0.0, "total_return_pct": 0.0, "max_drawdown_pct": 0.0,
            "best_trade_pct": 0.0, "worst_trade_pct": 0.0, "avg_trade_pct": 0.0,
            "sharpe_ratio": 0.0,
        }

    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    pnl_pcts = [t["pnl_pct"] for t in trades]

    # Max drawdown
    peak = initial_capital
    max_dd = 0.0
    running = initial_capital
    for t in trades:
        running += t["pnl"]
        if running > peak:
            peak = running
        dd = (peak - running) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualised daily returns approximation)
    n = len(pnl_pcts)
    if n > 1:
        avg = sum(pnl_pcts) / n
        variance = sum((x - avg) ** 2 for x in pnl_pcts) / (n - 1)
        std = math.sqrt(variance) if variance > 0 else 0.001
        sharpe = (avg / std) * math.sqrt(252 / max(n, 1))
    else:
        sharpe = 0.0

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_return_pct": round((final_capital - initial_capital) / initial_capital * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "best_trade_pct": round(max(pnl_pcts), 2),
        "worst_trade_pct": round(min(pnl_pcts), 2),
        "avg_trade_pct": round(sum(pnl_pcts) / n, 2),
        "sharpe_ratio": round(sharpe, 2),
    }


# ─── Historical-data failure ──────────────────────────────────────────────────
#
# PH3.9 DELETED `_synthetic_backtest`, and this is the one mock removal in the
# sprint that is not an admin dashboard number — it is the most dangerous of the
# seventeen, because a fabricated backtest is investment advice built on noise.
#
# What it did: 20 invented trades with the win count drawn from `randint(10, 16)`
# — so the win rate was always between 50% and 80%, and **a losing strategy could
# not be represented**. Entry and exit dates were invented 2025 strings unrelated
# to the requested period. The Sharpe ratio, max drawdown and total return were
# then computed by the SAME `_compute_metrics` the real path uses, so they
# arrived looking exactly like measured statistics — and the frontend rendered
# them in the same cards.
#
# It was reached on ANY yfinance failure — a missing library, a network blip, a
# rate limit, a delisted symbol — not only when the library was absent. So the
# common case of "the data provider is briefly unavailable" produced a flattering
# fabricated result rather than an error.
#
# There is no honest fallback here. A backtest without historical prices is not a
# degraded backtest; it is not a backtest. The endpoint fails, loudly, with a
# reason the user can act on.


class HistoricalDataUnavailable(RuntimeError):
    """Historical price data could not be obtained, so no backtest was run.

    Carries the reason so the route can render something a user can act on
    ("this symbol returned no data" is a different problem from "the provider
    timed out"), while the underlying exception detail goes to the log.
    """

    def __init__(self, symbol: str, reason: str):
        self.symbol = symbol
        self.reason = reason
        super().__init__(f"No historical data for {symbol}: {reason}")


# ─── Public API ───────────────────────────────────────────────────────────────

async def run_backtest(
    symbol: str,
    start_date: str,
    end_date: str,
    strategy: str,
    stop_loss_pct: float,
    target_pct: float,
    initial_capital: float = 100_000.0,
) -> dict:
    """Run a full backtest over real historical bars, or raise.

    PH3.9: raises :class:`HistoricalDataUnavailable` where this previously fell
    back to a fabricated result. Every figure returned is now DERIVED from real
    OHLCV — there is no path through this function that invents one.
    """
    yahoo_symbol = symbol.upper()
    if not yahoo_symbol.endswith(".NS"):
        yahoo_symbol += ".NS"

    try:
        import yfinance as yf
    except ImportError as exc:
        logger.error("yfinance is not installed — backtesting is unavailable")
        raise HistoricalDataUnavailable(
            symbol.upper(),
            "The historical market-data library is not installed on this server.",
        ) from exc

    try:
        ticker = yf.Ticker(yahoo_symbol)
        hist = ticker.history(start=start_date, end=end_date)
    except Exception as exc:
        # The provider's own message can embed a URL or a key, so it goes to the
        # log in full while the caller gets the CLASS of failure. Note also that
        # this except no longer swallows anything: the old one caught every
        # exception and answered with invented performance, so a transient
        # network blip produced a flattering backtest instead of an error.
        logger.warning("backtest history fetch failed for %s: %s", yahoo_symbol, exc)
        raise HistoricalDataUnavailable(
            symbol.upper(),
            "The historical market-data provider could not be reached.",
        ) from exc

    if hist.empty:
        raise HistoricalDataUnavailable(
            symbol.upper(),
            f"No historical bars were returned for {yahoo_symbol} between "
            f"{start_date} and {end_date}. The symbol may be wrong or delisted, or "
            "the period may contain no trading days.")

    closes = list(hist["Close"])
    if len(closes) < _MIN_BARS:
        raise HistoricalDataUnavailable(
            symbol.upper(),
            f"Only {len(closes)} trading day(s) are available in this period; at least "
            f"{_MIN_BARS} are needed for the indicators these strategies use. Widen the "
            "date range.")

    trades, equity_curve, final_capital = _simulate(
        list(hist.index), list(hist["Open"]), closes,
        list(hist["High"]), list(hist["Low"]), list(hist["Volume"]),
        strategy, stop_loss_pct, target_pct, initial_capital,
    )
    metrics = _compute_metrics(trades, equity_curve, initial_capital, final_capital)

    return {
        "symbol": symbol.upper(),
        "strategy": strategy,
        "period": {"start": start_date, "end": end_date},
        "initial_capital": initial_capital,
        "final_capital": round(final_capital, 2),
        "data_source": "yfinance",
        "bars": len(closes),
        "provenance": "derived",
        # Every P&L in this product is gross (ANALYTICS.md §6.1), and a backtest
        # is where that matters most: it is the number somebody sizes a real
        # position from.
        "basis": "gross",
        "charges_note": ("Gross of brokerage, STT, exchange transaction charges, GST, "
                         "SEBI turnover fee and stamp duty — none of which this "
                         "simulation models. On Indian intraday equity these routinely "
                         "exceed the edge on a small trade, so a positive gross result "
                         "is not necessarily a positive net one."),
        "mock_metrics": [],
        **metrics,
        "trades": trades[-50:],  # cap to last 50 for response size
        "equity_curve": equity_curve,
    }
