#!/usr/bin/env python3
"""Mock market-data provider for PH3.5 load testing.

WHY THIS EXISTS
---------------
The PH3.5 brief (§14) forbids pointing load at a real market-data provider, and
that prohibition is not merely about politeness. PH3.4 §7 measured that **more
than 90% of the latency on every quote-enriched endpoint is provider transport**.
Driving 50 concurrent users at the real Yahoo Finance would therefore produce a
number that describes Yahoo's throttle and round-trip time, not StockAssist's
capacity — the one thing this sprint exists to establish. It would also send
thousands of unsolicited requests per minute to a third party.

So the origin is redirected (``MARKET_DATA_YAHOO_BASE``, see
``backend/services/real_market.py``) at this server, which speaks the subset of
the Yahoo Finance HTTP API that StockAssist actually consumes. Every line of
application code — the connection pool, the 8s/10s/12s timeouts, the 60s quote
cache, the RSI/MACD computation, the error containment — runs exactly as it does
in production. Only the bytes' origin changes.

FIDELITY, AND WHY IT MATTERS
----------------------------
The chart response is generated at **realistic size**, not as a minimal stub.
PH3.4 §14 measured a real ``range=3mo`` chart at ~7,130 bytes of daily OHLCV per
symbol, and the application parses every bar to compute a 14-period RSI and a
26-period MACD. A 200-byte stub would make JSON parsing and indicator maths
disappear from the measurement and quietly inflate the capacity finding. Bars
are therefore emitted at the true cardinality for the requested range.

Prices are **deterministic** — derived from a hash of the ticker — so two runs
of the same scenario produce identical payloads and identical indicator work.
A random walk would make run-to-run comparison meaningless.

FAULT INJECTION (brief §14, §17)
--------------------------------
Controlled, and controlled from outside the load script so a scenario file never
has to know about it:

    POST /__control  {"latency_ms": 250, "error_rate": 0.1, "timeout_rate": 0.05}
    GET  /__control            → current settings + request counters
    POST /__control/reset      → back to defaults

``timeout_rate`` sleeps past any caller timeout rather than closing the socket,
because that is the failure mode that actually ties up an application worker —
a refused connection returns immediately and tests nothing.

STDLIB ONLY, BY DESIGN
----------------------
No FastAPI, no httpx, no dependency on the backend's virtualenv. This is
host-side load-harness infrastructure (see ``scripts/README.md`` for that
boundary), and a mock that imports application dependencies is a mock that stops
working the day those dependencies are upgraded. ``ThreadingHTTPServer`` gives
one thread per request, which is what makes the injected latency concurrent
rather than a serialising queue — a single-threaded mock would itself become the
bottleneck and every measurement would be of this file.

USAGE
-----
    python3 scripts/load/mocks/market_provider.py --port 9020

Never run this in production. It serves fabricated prices, and
``ADR/CLAUDE.md`` forbids fabricated market data outside development.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------------------------------- #
# Bar counts per Yahoo `range` token.                                           #
#                                                                               #
# These are the real cardinalities (NSE trades ~250 sessions/year), because the #
# payload size and the indicator work both scale with them. `2d` is the default #
# for `fetch_yahoo_quote`; `3mo` is what the watchlist path actually requests.  #
# --------------------------------------------------------------------------- #
RANGE_BARS = {
    "1d": 1, "2d": 2, "5d": 5, "1mo": 22, "3mo": 65,
    "6mo": 125, "1y": 250, "2y": 500, "5y": 1250, "max": 2500,
}

# Intraday intervals are requested by the chart endpoint. Bars per range differ
# from the daily case, so they get their own table rather than a fudge factor.
INTRADAY_BARS = {"1m": 375, "5m": 75, "15m": 25, "30m": 13, "60m": 7, "1h": 7}

DAY_SECONDS = 86400


class Control:
    """Mutable fault-injection state, shared across request threads.

    Guarded by a lock even though CPython's GIL makes the individual reads
    atomic: the point is that a control update is applied as a *set*, so a
    request can never observe the new latency with the old error rate.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.latency_ms = 0.0
        self.error_rate = 0.0
        self.timeout_rate = 0.0
        self.timeout_sleep_s = 30.0
        self.requests = 0
        self.errors_injected = 0
        self.timeouts_injected = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "latency_ms": self.latency_ms,
                "error_rate": self.error_rate,
                "timeout_rate": self.timeout_rate,
                "timeout_sleep_s": self.timeout_sleep_s,
                "requests": self.requests,
                "errors_injected": self.errors_injected,
                "timeouts_injected": self.timeouts_injected,
            }

    def update(self, payload: dict) -> None:
        with self._lock:
            for field in ("latency_ms", "error_rate", "timeout_rate", "timeout_sleep_s"):
                if field in payload:
                    setattr(self, field, float(payload[field]))

    def reset(self) -> None:
        with self._lock:
            self.latency_ms = 0.0
            self.error_rate = 0.0
            self.timeout_rate = 0.0
            self.requests = 0
            self.errors_injected = 0
            self.timeouts_injected = 0

    def count(self) -> None:
        with self._lock:
            self.requests += 1


CONTROL = Control()


def _seed_for(ticker: str) -> int:
    """A stable integer seed per ticker.

    ``hash()`` is deliberately not used: Python randomises string hashing per
    process (PYTHONHASHSEED), so it would give a different price series on every
    restart and make two load runs incomparable.
    """
    return int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8], 16)


def _series(ticker: str, bars: int, step_seconds: int) -> dict:
    """A deterministic OHLCV series of ``bars`` points for ``ticker``.

    Shaped like a plausible price path (a bounded sinusoid plus seeded noise)
    rather than a straight line, because RSI and MACD over a constant series
    degenerate — the application would compute them in a fraction of the time it
    spends on real data, and the CPU cost of the indicator maths would drop out
    of the measurement.
    """
    rng = random.Random(_seed_for(ticker))
    base = 100.0 + (_seed_for(ticker) % 3000) / 10.0  # ~100–400, stable per ticker
    now = int(time.time())
    # Align to the step so timestamps look like session boundaries rather than
    # an arbitrary offset from "now".
    end = now - (now % step_seconds)

    timestamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    price = base
    for i in range(bars):
        drift = math.sin(i / 9.0) * base * 0.01
        noise = (rng.random() - 0.5) * base * 0.012
        close = max(1.0, price + drift + noise)
        open_ = max(1.0, close - (rng.random() - 0.5) * base * 0.008)
        high = max(open_, close) * (1 + rng.random() * 0.004)
        low = min(open_, close) * (1 - rng.random() * 0.004)
        timestamps.append(end - (bars - 1 - i) * step_seconds)
        opens.append(round(open_, 2))
        highs.append(round(high, 2))
        lows.append(round(low, 2))
        closes.append(round(close, 2))
        volumes.append(rng.randint(100_000, 5_000_000))
        price = close

    return {
        "timestamp": timestamps,
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    }


def chart_payload(ticker: str, interval: str, range_str: str) -> dict:
    """A Yahoo ``/v8/finance/chart`` response for ``ticker``."""
    if interval.endswith("m") or interval in ("1h", "60m"):
        bars = INTRADAY_BARS.get(interval, 75)
        step = 60 * int(interval[:-1]) if interval[:-1].isdigit() else 3600
    else:
        bars = RANGE_BARS.get(range_str, 22)
        step = DAY_SECONDS

    s = _series(ticker, bars, step)
    closes = s["close"]
    price = closes[-1]
    prev_close = closes[-2] if len(closes) > 1 else round(price * 0.995, 2)

    return {
        "chart": {
            "result": [{
                "meta": {
                    "currency": "INR",
                    "symbol": ticker,
                    "exchangeName": "NSI",
                    "regularMarketPrice": price,
                    "chartPreviousClose": prev_close,
                    "previousClose": prev_close,
                    "marketState": "REGULAR",
                    "regularMarketDayHigh": max(s["high"][-1], price),
                    "regularMarketDayLow": min(s["low"][-1], price),
                    "regularMarketVolume": s["volume"][-1],
                },
                "timestamp": s["timestamp"],
                "indicators": {
                    "quote": [{
                        "open": s["open"], "high": s["high"], "low": s["low"],
                        "close": s["close"], "volume": s["volume"],
                    }],
                    # Real Yahoo returns an adjusted-close series alongside the
                    # quote block. StockAssist never reads it — but it still
                    # travels the wire and still gets parsed by `resp.json()`,
                    # and omitting it would shrink the payload ~20% and quietly
                    # understate deserialization cost on the hottest path in the
                    # system. Fidelity here is measured in bytes, not fields.
                    "adjclose": [{"adjclose": s["close"]}],
                },
            }],
            "error": None,
        }
    }


def quote_summary_payload(ticker: str, modules: list) -> dict:
    """A Yahoo ``/v10/finance/quoteSummary`` response.

    Only the modules StockAssist actually reads are populated. An unrequested
    module is omitted rather than stubbed, so a route that starts depending on a
    new module fails loudly here instead of silently reading zeros.
    """
    rng = random.Random(_seed_for(ticker) + 7)
    price = 100.0 + (_seed_for(ticker) % 3000) / 10.0
    result: dict = {}

    if "summaryDetail" in modules:
        result["summaryDetail"] = {
            "trailingAnnualDividendRate": {"raw": round(rng.random() * 20, 2)},
            "dividendYield": {"raw": round(rng.random() * 0.04, 4)},
            "marketCap": {"raw": rng.randint(10**10, 10**13)},
            "trailingPE": {"raw": round(10 + rng.random() * 40, 2)},
            "fiftyTwoWeekHigh": {"raw": round(price * 1.3, 2)},
            "fiftyTwoWeekLow": {"raw": round(price * 0.7, 2)},
        }
    if "assetProfile" in modules:
        result["assetProfile"] = {
            "longBusinessSummary": "Synthetic load-test issuer. " * 12,
            "sector": "Technology", "industry": "Software", "country": "India",
            "fullTimeEmployees": rng.randint(1000, 200000), "website": "https://example.invalid",
        }
    if "defaultKeyStatistics" in modules:
        result["defaultKeyStatistics"] = {
            "trailingEps": {"raw": round(rng.random() * 100, 2)},
            "bookValue": {"raw": round(rng.random() * 500, 2)},
            "priceToBook": {"raw": round(rng.random() * 10, 2)},
            "beta": {"raw": round(0.5 + rng.random(), 2)},
        }
    if "financialData" in modules:
        result["financialData"] = {
            "currentPrice": {"raw": round(price, 2)},
            "targetMeanPrice": {"raw": round(price * 1.15, 2)},
            "recommendationKey": "buy",
            "returnOnEquity": {"raw": round(rng.random() * 0.3, 4)},
            "debtToEquity": {"raw": round(rng.random() * 100, 2)},
            "profitMargins": {"raw": round(rng.random() * 0.3, 4)},
        }

    return {"quoteSummary": {"result": [result] if result else [], "error": None}}


def search_payload(query: str) -> dict:
    rng = random.Random(_seed_for(query))
    quotes = [
        {
            "symbol": f"{query.upper()[:6]}{i}.NS",
            "shortname": f"Synthetic {query.title()} {i} Ltd",
            "longname": f"Synthetic {query.title()} {i} Limited",
            "quoteType": "EQUITY",
            "exchange": "NSI",
            "score": round(rng.random() * 100000, 2),
        }
        for i in range(1, 11)
    ]
    return {"quotes": quotes, "news": [], "count": len(quotes)}


def timeseries_payload(ticker: str, types: list) -> dict:
    rng = random.Random(_seed_for(ticker) + 11)
    now = int(time.time())
    results = []
    for t in types:
        rows = [
            {"asOfDate": time.strftime("%Y-%m-%d", time.gmtime(now - y * 365 * DAY_SECONDS)),
             "reportedValue": {"raw": rng.randint(10**8, 10**12)}}
            for y in range(4)
        ]
        results.append({"meta": {"type": [t], "symbol": [ticker]}, t: rows})
    return {"timeseries": {"result": results, "error": None}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive, so the app's pool is exercised
    server_version = "StockAssistLoadMock/1.0"

    # BaseHTTPRequestHandler logs every request to stderr. At several thousand
    # requests per minute that is both noise and measurable I/O inside the
    # request path — the mock would be timing its own logging.
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        pass

    # ---------------------------------------------------------------- helpers #
    def _send(self, status: int, body: bytes, content_type="application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"))

    def _apply_faults(self) -> bool:
        """Apply configured latency/error/timeout. Returns False if handled."""
        cfg = CONTROL.snapshot()
        CONTROL.count()

        if cfg["timeout_rate"] and random.random() < cfg["timeout_rate"]:
            with CONTROL._lock:
                CONTROL.timeouts_injected += 1
            # Sleep past any caller timeout instead of closing the socket. A
            # refused connection returns instantly and would test nothing; the
            # failure that actually costs the application a worker is the one
            # where the response never comes.
            time.sleep(cfg["timeout_sleep_s"])
            return False

        if cfg["latency_ms"]:
            time.sleep(cfg["latency_ms"] / 1000.0)

        if cfg["error_rate"] and random.random() < cfg["error_rate"]:
            with CONTROL._lock:
                CONTROL.errors_injected += 1
            self._json({"error": "injected upstream failure"}, status=503)
            return False

        return True

    # ------------------------------------------------------------------ verbs #
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/__control":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                CONTROL.update(json.loads(raw or b"{}"))
            except (ValueError, TypeError) as e:
                self._json({"error": f"bad control payload: {e}"}, status=400)
                return
            self._json(CONTROL.snapshot())
            return
        if path == "/__control/reset":
            CONTROL.reset()
            self._json(CONTROL.snapshot())
            return
        self._json({"error": "not found"}, status=404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)

        # Control and health endpoints bypass fault injection on purpose: the
        # harness must be able to turn a fault OFF while that fault is active.
        if path == "/__control":
            self._json(CONTROL.snapshot())
            return
        if path == "/__health":
            self._json({"status": "ok", "service": "market-provider-mock"})
            return

        if not self._apply_faults():
            return

        if path.startswith("/v8/finance/chart/"):
            ticker = path.rsplit("/", 1)[-1]
            self._json(chart_payload(
                ticker,
                (qs.get("interval") or ["1d"])[0],
                (qs.get("range") or ["1mo"])[0],
            ))
            return

        if path.startswith("/v10/finance/quoteSummary/"):
            ticker = path.rsplit("/", 1)[-1]
            modules = (qs.get("modules") or [""])[0].split(",")
            self._json(quote_summary_payload(ticker, [m for m in modules if m]))
            return

        if path == "/v1/finance/search":
            self._json(search_payload((qs.get("q") or ["x"])[0]))
            return

        if path == "/v1/test/getcrumb":
            self._send(200, b"loadtest-crumb", content_type="text/plain")
            return

        if path.startswith("/ws/fundamentals-timeseries/"):
            ticker = path.rsplit("/", 1)[-1]
            types = (qs.get("type") or [""])[0].split(",")
            self._json(timeseries_payload(ticker, [t for t in types if t]))
            return

        # An unmapped path is a real signal: the application asked for something
        # this mock does not model, and a 200 with an empty body would hide it.
        self._json({"error": f"unmapped provider path: {path}"}, status=404)


def main() -> int:
    ap = argparse.ArgumentParser(description="Mock Yahoo Finance provider for PH3.5 load tests.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9020)
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"[market-mock] listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
