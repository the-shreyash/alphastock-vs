#!/usr/bin/env python3
"""Mock AI provider (Anthropic Messages API shape) for PH3.5 load testing.

WHY THIS EXISTS
---------------
The PH3.5 brief (§15) forbids sending benchmark traffic to a real AI provider,
and PH3.4 §11 had to mark AI latency **UNAVAILABLE** because no key is
configured in any measurement environment. Both facts point at the same gap: the
question PH3.5 actually needs answered is not "how fast is Claude" — that is the
provider's number, not StockAssist's. It is **"what happens to this process
while N requests are parked waiting on a slow AI call?"**

That question needs a provider that is slow *on demand*. Blanking the key does
not produce one: `services/ai_provider.py` falls through to a simulated offline
provider that answers instantly, so the AI routes would return in microseconds
and the waiting behaviour — event-loop occupancy, connection-pool pressure,
in-flight request growth — would never be exercised at all.

HOW IT IS WIRED
---------------
No application change is required. The `anthropic` SDK reads ``ANTHROPIC_BASE_URL``
from the environment (verified against anthropic 0.116.0), so the load
environment sets:

    ANTHROPIC_API_KEY=loadtest-mock-key-not-a-real-credential
    ANTHROPIC_BASE_URL=http://127.0.0.1:9030

`is_configured()` then returns True, the real `AsyncAnthropic` client is
constructed, and the real request path runs — against this server. This is the
same principle as the market-data origin override, and it is preferred over
monkeypatching for the same reason: a monkeypatch would measure a code path that
does not exist in production.

FAULT INJECTION (brief §15, §17)
--------------------------------
    POST /__control  {"latency_ms": 4000, "error_rate": 0.1,
                      "rate_limit_rate": 0.1, "timeout_rate": 0.05,
                      "response_tokens": 800}
    GET  /__control            → settings + counters
    POST /__control/reset      → defaults

Defaults model a *fast* AI response (900 ms), which is optimistic for a real
model and therefore the conservative choice: it understates rather than
overstates how long a request occupies the process.

``rate_limit_rate`` returns a genuine Anthropic-shaped 429 so the application's
own 429 handling (`services/claude_provider.py` maps it to a user-facing
message) is exercised, not bypassed.

STDLIB ONLY — see ``market_provider.py`` for why.

USAGE
-----
    python3 scripts/load/mocks/ai_provider.py --port 9030
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# A sentence pool used to build responses of a requested length. Real financial
# AI output is prose, and prose is what the application then stores, serializes
# and streams to the client — a 20-byte "ok" would remove all of that work from
# the measurement.
_SENTENCES = [
    "The position shows constructive momentum with the 14-period RSI at 58, "
    "which is firm without being extended.",
    "Support sits near the 20-day moving average; a close below it would "
    "invalidate the setup rather than merely weaken it.",
    "Volume on the advance is above the 30-session median, so the move is "
    "participation-backed rather than a thin drift.",
    "Risk is concentrated in a single sector, and correlation means the "
    "portfolio is less diversified than the position count suggests.",
    "MACD has crossed above its signal line, but the histogram is still narrow, "
    "so treat this as early confirmation rather than a completed signal.",
    "Watch next: the sector index relative strength, and whether the current "
    "level holds on a retest.",
]


class Control:
    """Mutable fault state shared across request threads (see market_provider)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.latency_ms = 900.0
        self.error_rate = 0.0
        self.rate_limit_rate = 0.0
        self.timeout_rate = 0.0
        self.timeout_sleep_s = 60.0
        self.response_tokens = 300
        self.requests = 0
        self.errors_injected = 0
        self.rate_limits_injected = 0
        self.timeouts_injected = 0
        self.concurrent = 0
        self.max_concurrent = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "latency_ms": self.latency_ms,
                "error_rate": self.error_rate,
                "rate_limit_rate": self.rate_limit_rate,
                "timeout_rate": self.timeout_rate,
                "timeout_sleep_s": self.timeout_sleep_s,
                "response_tokens": self.response_tokens,
                "requests": self.requests,
                "errors_injected": self.errors_injected,
                "rate_limits_injected": self.rate_limits_injected,
                "timeouts_injected": self.timeouts_injected,
                "concurrent": self.concurrent,
                "max_concurrent": self.max_concurrent,
            }

    def update(self, payload: dict) -> None:
        with self._lock:
            for f in ("latency_ms", "error_rate", "rate_limit_rate",
                      "timeout_rate", "timeout_sleep_s"):
                if f in payload:
                    setattr(self, f, float(payload[f]))
            if "response_tokens" in payload:
                self.response_tokens = int(payload["response_tokens"])

    def reset(self) -> None:
        with self._lock:
            self.latency_ms = 900.0
            self.error_rate = 0.0
            self.rate_limit_rate = 0.0
            self.timeout_rate = 0.0
            self.response_tokens = 300
            self.requests = 0
            self.errors_injected = 0
            self.rate_limits_injected = 0
            self.timeouts_injected = 0
            self.max_concurrent = 0

    def enter(self) -> None:
        with self._lock:
            self.requests += 1
            self.concurrent += 1
            # The single most useful number this mock reports: the high-water
            # mark of AI calls the application had in flight at once. It is the
            # direct answer to "how many workers can a slow provider tie up?"
            self.max_concurrent = max(self.max_concurrent, self.concurrent)

    def leave(self) -> None:
        with self._lock:
            self.concurrent -= 1


CONTROL = Control()


def _body(tokens: int) -> str:
    """Prose of roughly ``tokens`` tokens (~4 chars/token is the usual rule)."""
    target_chars = max(40, tokens * 4)
    out: list[str] = []
    size = 0
    i = 0
    while size < target_chars:
        s = _SENTENCES[i % len(_SENTENCES)]
        out.append(s)
        size += len(s) + 1
        i += 1
    return " ".join(out)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "StockAssistAIMock/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _json(self, payload: dict, status: int = 200, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/__control":
            self._json(CONTROL.snapshot())
            return
        if path == "/__health":
            self._json({"status": "ok", "service": "ai-provider-mock"})
            return
        self._json({"type": "error", "error": {"type": "not_found_error",
                                               "message": "unmapped path"}}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"

        if path == "/__control":
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

        if not path.endswith("/v1/messages"):
            self._json({"type": "error", "error": {"type": "not_found_error",
                                                   "message": f"unmapped path {path}"}},
                       status=404)
            return

        cfg = CONTROL.snapshot()
        CONTROL.enter()
        try:
            if cfg["timeout_rate"] and random.random() < cfg["timeout_rate"]:
                with CONTROL._lock:
                    CONTROL.timeouts_injected += 1
                time.sleep(cfg["timeout_sleep_s"])
                return

            if cfg["rate_limit_rate"] and random.random() < cfg["rate_limit_rate"]:
                with CONTROL._lock:
                    CONTROL.rate_limits_injected += 1
                self._json(
                    {"type": "error", "error": {"type": "rate_limit_error",
                                                "message": "Number of requests has exceeded your rate limit."}},
                    status=429, headers={"retry-after": "5"},
                )
                return

            if cfg["latency_ms"]:
                time.sleep(cfg["latency_ms"] / 1000.0)

            if cfg["error_rate"] and random.random() < cfg["error_rate"]:
                with CONTROL._lock:
                    CONTROL.errors_injected += 1
                self._json({"type": "error", "error": {"type": "api_error",
                                                       "message": "Injected provider failure."}},
                           status=500)
                return

            try:
                req = json.loads(raw or b"{}")
            except ValueError:
                req = {}

            text = _body(int(cfg["response_tokens"]))
            self._json({
                "id": "msg_loadtest_0000000000",
                "type": "message",
                "role": "assistant",
                "model": req.get("model", "claude-3-haiku-20240307"),
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 512, "output_tokens": int(cfg["response_tokens"])},
            })
        finally:
            CONTROL.leave()


def main() -> int:
    ap = argparse.ArgumentParser(description="Mock Anthropic Messages API for PH3.5 load tests.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9030)
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"[ai-mock] listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
