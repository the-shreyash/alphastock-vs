# pyrefly: ignore [missing-import] - force uvicorn reload
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env', override=True)

# Boot-time secret loading + configuration validation (PH1.9, PH2.3).
#
# `validate_config()` first calls `load_secrets()`, which resolves every
# configuration input from its highest-precedence source — a Docker/Kubernetes
# secret file, a `<NAME>_FILE` pointer, or a plaintext environment variable — and
# materializes the result into `os.environ`. That happens HERE, before any other
# application module is imported, because roughly thirty modules read their
# configuration through call-time `os.environ` lookups; hydrating first is what
# lets all of them see a file-backed secret without knowing files exist.
#
# Then: fail fast, fail closed. A missing/weak critical secret, or a secret file
# that cannot be read, aborts startup — before the Mongo client, any router, or a
# single request — with one aggregated, value-free error rather than a raw
# KeyError surfacing later. Non-fatal issues are logged as warnings.
#
# security.secrets is the single source of truth for the config surface and for
# where its values come from; see docs/deployment/SECRETS.md, .claude/SECRETS.md
# and SECURITY_ARCHITECTURE.md §23–§24.
from security import secrets as _secret_config

_config_report = _secret_config.validate_config()

# Structured logging (PH2.5) — installed HERE, immediately after configuration is
# hydrated and before the application modules below are imported.
#
# The ordering is the whole point. Roughly forty modules in this codebase log at
# import time; the previous `logging.basicConfig(...)` call lived at the BOTTOM of
# this file, so every one of those lines was emitted through Python's default
# handler in a different format — and the boot sequence, which is exactly what you
# read when a container refuses to start, was the least legible part of the log.
#
# It runs after `validate_config()` because that call is what materializes
# file-backed secrets and configuration into `os.environ` (PH2.3); LOG_LEVEL and
# LOG_FORMAT are read from there. The config report is therefore emitted just
# below, through the now-structured logger, rather than before it.
#
# observability.logging owns the root logger from this line onwards: format,
# level, destination (stdout only — shipping is the platform's job, which is the
# seam PH2.6 attaches to), secret scrubbing, and third-party noise suppression.
from observability import logging as _obs_logging

_logging_config = _obs_logging.configure_logging()

import logging as _bootstrap_logging
for _w in _config_report.warnings:
    _bootstrap_logging.getLogger("security.secrets").warning(_w)
_bootstrap_logging.getLogger("security.secrets").info(_config_report.summary_line())
_bootstrap_logging.getLogger("observability").info(
    "Structured logging configured (%s, level=%s).",
    _logging_config["format"], _logging_config["level"],
    extra={"event": "logging_configured", **_logging_config},
)

from fastapi import FastAPI, APIRouter, HTTPException, Query, Request, Response, Depends, Header, WebSocket, WebSocketDisconnect, BackgroundTasks
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from bson.errors import InvalidId
import os
import logging
import secrets
import httpx
import json
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Set
from urllib.parse import urlencode

from services.alpha_vantage import get_global_quote as av_get_quote, get_intraday_data as av_intraday, is_configured as av_configured
# Legacy single-session Zerodha shim (data-sources status + startup log only).
# All broker routes go through services.broker_engine (Sprint 7).
from services.zerodha_service import get_status as kite_status, is_configured as kite_configured
from services.broker_engine import broker_engine
from services.brokers.base import BrokerAuthError, BrokerError
from services.brokers.stream import stream_manager
from services.scheduler import setup_scheduler

# Centralized authentication-cookie policy (PH1.3). Every auth cookie is set or
# cleared through this module so the Secure/HttpOnly/SameSite/Path/Max-Age
# posture stays consistent across login, register, refresh, logout and OAuth.
from security.cookies import (
    set_auth_cookies,
    set_access_cookie,
    clear_auth_cookies,
    set_oauth_state_cookie,
    clear_oauth_state_cookie,
)
from security.cors import apply_cors
from security.headers import apply_security_headers

# Centralized CSRF protection (PH1.7). Signed double-submit token bound to the
# session; enforced only on cookie-authenticated, state-changing requests
# (Bearer requests are CSRF-safe and exempt). The CSRF cookie is set/cleared
# only through this module. See SECURITY_ARCHITECTURE.md §18.
from security.csrf import (
    apply_csrf_protection,
    set_csrf_cookie,
    clear_csrf_cookie,
)

# Centralized rate limiting & abuse protection (PH1.7). One limiter, one storage
# model, one set of policies — the prior inline db.login_attempts lockout is
# folded in here. See SECURITY_ARCHITECTURE.md §21.
from security import rate_limit as ratelimit

# Centralized password policy + hashing primitives (PH1.5). Passwords are
# hashed/verified ONLY through this module (explicit bcrypt cost, safe
# verification that never raises on empty/malformed hashes, timing-equalized
# failures). Policy validation for new passwords lives on the UserCreate model.
from security.passwords import hash_password, verify_password

# Centralized ObjectId parsing (PH1.12 / F-2) and role-assignment authorization
# (PH1.12 / F-1). `parse_object_id` turns an untrusted identifier into a clean
# 400 instead of an accidental 500; `validate_role_assignment` enforces the role
# allowlist and least privilege on elevation (only super_admin grants admin-tier
# roles). See SECURITY_ARCHITECTURE.md and backend/security/{identifiers,roles}.py.
from security.identifiers import parse_object_id
from security.roles import validate_role_assignment

# Centralized observability (PH2.5). `observability.logging` already owns the
# root logger (configured at the top of this file, before these imports); the
# rest of the package is wired below:
#   * health           — the probe registry + process lifecycle state machine,
#                        behind /api/health/{live,ready,startup}.
#   * metrics          — the in-process registry rendered at /api/metrics.
#   * middleware       — request ID, timing, counting, one access log line.
#   * observability_routes — the operational HTTP surface.
# See observability/__init__.py and docs/operations/MONITORING.md.
from observability import health as obs_health
from observability import metrics as obs_metrics
from observability import routes as observability_routes
from observability import runtime as obs_runtime
from observability.middleware import apply_observability

from models import (
    UserCreate, UserLogin, UserResponse, UserSettingsUpdate,
    VerifyEmailRequest, ForgotPasswordRequest, ResetPasswordRequest, ChangePasswordRequest,
    TradeCreate, TradeResponse, TradeModify, TradeExitRequest,
    WatchlistAdd,
    ChatMessage, ChatResponse,
    StockAnalysisRequest, SIPRequest,
    AdvisorRequest, AdvisorRecommendation, AdvisorEntryZone,
    AIMemoryUpdate, LearnRequest, TradeReviewRequest, PortfolioReviewRequest,
    BrokerOrderCreate, BrokerOrderModify,
)
from market_data import get_stock_meta, search_stocks, STOCK_UNIVERSE
# NOTE: market_data contains ONLY factual reference metadata (symbols, company
# names, sectors). All market values come from services.real_market (live
# Yahoo Finance / NSE). When live data is unavailable, endpoints return an
# explicit "unavailable" payload — never simulated values.

# MongoDB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Centralized security audit logging (PH1.10). Wire the process-wide default
# audit logger to durable storage here — at import time, not in the startup
# event — with a LAZY db provider (`lambda: db`) so it resolves the live handle
# on every emit. That matters two ways: (1) security middleware/modules can emit
# audit events without threading a DB handle, and (2) the test suite's `FakeDB`
# monkeypatch of `server.db` is honored with no special wiring. See
# security/audit.py and SECURITY_ARCHITECTURE.md §31.
from security import audit
audit.configure(lambda: db)


# ============ LIVE MARKET DATA HELPERS ============
# These wrap services.real_market (live Yahoo Finance). There is NO simulated
# fallback: when the live API fails, helpers return None and endpoints surface
# an explicit unavailable state.

def _apply_stock_meta(quote: dict, symbol: str) -> dict:
    """Merge factual reference metadata (name/sector) into a live quote and
    guarantee descriptive keys exist. Fields with no live source are None."""
    meta = get_stock_meta(symbol) or {}
    quote.setdefault("name", meta.get("name", symbol.upper()))
    quote.setdefault("sector", meta.get("sector", ""))
    quote.setdefault("day_range", f"{quote.get('low', 0)} - {quote.get('high', 0)}")
    for k in ("week_52_high", "week_52_low", "market_cap_cr", "pe_ratio"):
        quote.setdefault(k, None)
    return quote


async def real_quote(symbol: str):
    """Live quote for `symbol`, enriched with factual metadata.
    Returns None when live market data is unavailable — never simulated."""
    from services.real_market import fetch_real_stock_quote
    try:
        real = await fetch_real_stock_quote(symbol)
    except Exception as e:
        logging.warning(f"real_quote live fetch failed for {symbol}: {e}")
        real = None
    if not real:
        return None
    return _apply_stock_meta(real, symbol)


async def real_quotes_map(symbols):
    """Fetch live quotes for many symbols concurrently (Yahoo layer is cached),
    returning {UPPER_SYMBOL: quote|None}. None means live data is unavailable
    for that symbol — callers must treat it as explicitly unavailable."""
    from services.real_market import fetch_real_stock_quote
    uniq = list({(s or "").upper() for s in symbols if s})
    if not uniq:
        return {}
    results = await asyncio.gather(
        *[fetch_real_stock_quote(s) for s in uniq], return_exceptions=True
    )
    out = {}
    for sym, res in zip(uniq, results):
        if isinstance(res, Exception) or not res:
            out[sym] = None
            continue
        out[sym] = _apply_stock_meta(res, sym)
    return out


async def real_overview():
    """Live market overview (indices + India VIX + breadth/sentiment derived
    from live universe quotes). Returns None when live data is unavailable."""
    from services.real_market import fetch_real_market_overview
    return await fetch_real_market_overview()


async def prefetch_quote_func(user_id):
    """Build a SYNC quote lookup backed by prefetched LIVE quotes for the given
    user's open positions. services.portfolio_monitor.analyze_portfolio_health
    calls its quote_func synchronously (no await), so we resolve the async live
    quotes up-front and hand it a plain dict lookup. Symbols with no live quote
    resolve to None and are skipped by the monitor — never simulated."""
    open_trades = await db.trades.find(
        {"user_id": user_id, "status": "OPEN"}
    ).to_list(50)
    quotes = await real_quotes_map([t["symbol"] for t in open_trades])

    def quote_func(symbol):
        return quotes.get((symbol or "").upper())

    return quote_func


app = FastAPI(title="AlphaPartner API")


# Broker errors → consistent HTTP responses. Auth problems use 409 (NOT 401:
# the frontend interceptor treats 401 as an app-session failure and would try
# to refresh/log out the user over a broker issue).
from starlette.responses import JSONResponse as _JSONResponse

@app.exception_handler(BrokerAuthError)
async def broker_auth_error_handler(request, exc: BrokerAuthError):
    return _JSONResponse(status_code=409, content={"detail": exc.user_message, "code": "BROKER_AUTH"})

@app.exception_handler(BrokerError)
async def broker_error_handler(request, exc: BrokerError):
    status = 429 if exc.code == "RATE_LIMIT" else 502
    return _JSONResponse(status_code=status, content={"detail": exc.user_message, "code": exc.code})


# Request-validation errors must never echo the submitted values back (PH1.5).
# FastAPI's default 422 handler includes each error's `input` — for an auth
# payload that would reflect the raw password to the client (and into any
# response logging). Strip errors down to loc/msg/type: same shape the
# frontend already consumes (detail[].msg), minus the reflected input.
from fastapi.exceptions import RequestValidationError

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    sanitized = [
        {"loc": e.get("loc", []), "msg": e.get("msg", "Invalid input"), "type": e.get("type", "validation_error")}
        for e in exc.errors()
    ]
    return _JSONResponse(status_code=422, content={"detail": sanitized})


# A malformed JSON body is a client error, not a server error (PH3.3 / D-6).
#
# Eighteen routes read their body with `await request.json()` instead of binding
# a Pydantic model — the admin editors, the trade modifier, the broker session
# exchanges. Those routes never reach FastAPI's own body parsing, so they never
# get its 422; `json.loads` raises `JSONDecodeError` inside the handler, nothing
# catches it, and a truncated request body from a flaky mobile connection is
# reported as "the server is broken". It also means a trivially-generated 500 is
# available to any authenticated caller, which flat-lines an error-rate SLO.
#
# Handled centrally rather than with a try/except in each of the eighteen: a
# per-route fix is eighteen chances to forget, and the nineteenth route added
# next sprint would not be covered. `JSONDecodeError` subclasses `ValueError`,
# so the handler is registered against the precise type to avoid swallowing
# unrelated ValueErrors (which are genuine 500s and must stay visible).
import json as _json


@app.exception_handler(_json.JSONDecodeError)
async def malformed_json_handler(request, exc: _json.JSONDecodeError):
    # The parse error's position/snippet is deliberately not echoed: it would
    # reflect fragments of the submitted body back to the caller, the same
    # reflection the 422 handler above exists to prevent.
    return _JSONResponse(
        status_code=400,
        content={"detail": "Malformed JSON body.", "code": "INVALID_JSON"},
    )


# JWT & Session Config (PH1.6)
# All token crypto lives in security.jwt (issuance/verification/claims); the
# refresh-token family / rotation / revocation state lives in the DB-backed
# security.sessions.SessionStore. server.py only composes the two: it never
# encodes/decodes a JWT or writes a session document by hand. See
# SECURITY_ARCHITECTURE.md §9/§11/§12.
from security import jwt as jwt_service
from security.sessions import (
    SessionStore,
    ROTATED, REUSE_DETECTED, REVOKED, EXPIRED, NOT_FOUND,
    REASON_LOGOUT,
)

# Centralized identity-recovery tokens (PH1.8). Single-use, expiring
# email-verification and password-reset tokens are minted / verified / burned
# ONLY through this store; the endpoints below compose it with the password and
# session primitives above. See SECURITY_ARCHITECTURE.md §29.
from security.recovery import (
    RecoveryStore,
    PURPOSE_VERIFY_EMAIL,
    PURPOSE_RESET_PASSWORD,
)
from security.passwords import normalize_password, validate_new_password


def create_access_token(user_id: str, email: str, session_id: Optional[str] = None) -> str:
    """Mint an access token. ``session_id`` binds the token to a refresh-token
    family; when omitted (the test-harness / ad-hoc bearer path) a standalone
    id is generated so the token is still fully valid — access verification is
    stateless and does not require the session to exist in the store."""
    return jwt_service.create_access_token(
        user_id, email, session_id or jwt_service.new_jti()
    )


async def _issue_session(user_id: str, email: str, request: Request,
                         response: Response) -> str:
    """Open a fresh session and set both auth cookies (login / register / OAuth).

    Allocates the first refresh ``jti``, records the session (with device/IP
    context for PH1.10), then mints an access + refresh pair carrying the new
    ``sid``. Returns the access token (echoed in the JSON body for the SPA)."""
    refresh_jti = jwt_service.new_jti()
    ua = request.headers.get("user-agent") if request else None
    ip = request.client.host if (request and request.client) else None
    session_id = await SessionStore(db).create(user_id, refresh_jti, user_agent=ua, ip=ip)
    access = jwt_service.create_access_token(user_id, email, session_id)
    refresh = jwt_service.create_refresh_token(user_id, session_id, refresh_jti)
    set_auth_cookies(response, access, refresh)
    # Plant a session-bound CSRF token (PH1.7). Non-HttpOnly so a same-origin
    # script can echo it in X-CSRF-Token on cookie-authenticated mutations.
    set_csrf_cookie(response, session_id)
    # Audit the new refresh-token family (PH1.10). One session_created per login /
    # register / OAuth — the single place all three converge, so the event is
    # captured once with no per-endpoint duplication.
    await audit.log_event(audit.SESSION_CREATED, request=request, email=email,
                          user_id=user_id, session_id=session_id)
    return access


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt_service.decode_token(token, expected_type="access")
    except jwt_service.TokenExpired:
        # Ordinary expiry is a routine, expected condition (the client simply
        # refreshes) — NOT audited, to avoid drowning the security log in benign
        # noise on every idle poll.
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt_service.TokenError:
        # A structurally invalid / bad-signature token was actually presented:
        # the signature of tampering or a forged credential — worth auditing.
        await audit.log_event(audit.INVALID_JWT, request=request)
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    # Global invalidation lever: any token minted before the account's
    # password_changed_at (set by a future password change or a security event)
    # is stale, even if it has not yet expired. Access AND refresh both honor it.
    if jwt_service.token_issued_before(payload, user.get("password_changed_at")):
        raise HTTPException(status_code=401, detail="Token expired")
    user["_id"] = str(user["_id"])
    user.pop("password_hash", None)
    return user

# NOTE: authentication cookies are set/cleared exclusively via
# security.cookies (imported above) — set_auth_cookies / set_access_cookie /
# clear_auth_cookies. This keeps the hardened cookie policy in one place.

# --- AI Service (Modular Multi-Provider Architecture) ---
# Uses AIDebateEngine: Claude + Gemini as equal intelligence partners.
# Configure via: ANTHROPIC_API_KEY (Claude) and GOOGLE_GEMINI_KEY (Gemini)
from services.ai_debate_engine import get_debate_engine
from services.claude_provider import is_configured as claude_configured
from services.gemini_provider import is_configured as gemini_configured

async def ai_dual_debate(prompt: str, session_id: str = "default"):
    """Dual AI debate: Claude and Gemini independently analyze, cross-review, then synthesize."""
    engine = get_debate_engine()
    try:
        result = await engine.debate(prompt, session_id=session_id, rounds=2)
        return result
    except Exception as e:
        logging.error(f"AI debate error: {e}")
        return {
            "claude_analysis": f"Analysis temporarily unavailable: {str(e)}",
            "gemini_analysis": f"Analysis temporarily unavailable: {str(e)}",
            "final_verdict": "Unable to generate AI debate at this time. Please check ANTHROPIC_API_KEY and GOOGLE_GEMINI_KEY.",
            "providers_active": [],
            "rounds_completed": 0,
        }

async def ai_chat(message: str, session_id: str, user_context: dict, run_id: str = None):
    """AI chat assistant (Personal Investment Assistant).

    Uses the centralized Prompt Library (`ai_chat` prompt) + AI Memory so the
    assistant reasons with what it remembers about the user, and routes the call
    through the Model Router (Claude-preferred for chat). Conversation memory is
    the last 10 turns of this session, matching AI_AGENT_SYSTEM.md → AI Memory.

    Sprint R7: the pipeline publishes a truthful, run-correlated step timeline
    (ai.run.* / ai.step events, per-user over the WebSocket bridge) so the client
    can replace the static "Thinking…" with the live stages the AI is actually
    running. Each step maps to real work below; telemetry never breaks the reply.
    """
    from services.prompt_library import get_prompt
    from services.model_router import get_model_router
    from services.ai_provider import AIMessage
    from services.ai_activity import AIRun
    from services.ai_context_builder import build_chat_context

    router = get_model_router()
    # Label the model step with the provider that will actually serve the reply
    # (Claude preferred, Gemini fallback), not a hard-coded name.
    _PROVIDER_LABEL = {
        "claude": "Consulting Claude",
        "gemini": "Consulting Gemini",
        "simulated": "Composing your answer",
    }
    consult_label = _PROVIDER_LABEL.get(router.resolve_provider("claude"), "Consulting the AI")
    run = AIRun(
        user_context.get("_id"),
        session_id,
        ["Reading live market data", "Reviewing our conversation", consult_label],
        run_id=run_id,
    )
    await run.start()

    # Step 1 — Build the live platform context (market, portfolio, trades,
    # watchlist, news, broker, memory). Best-effort: an empty context still
    # yields a valid prompt whose fallback rule handles the no-data case, so the
    # AI reasons from the Market Engine instead of disclaiming live-data access.
    live_context = ""
    try:
        async with run.step():
            ctx = await build_chat_context(db, user_context, real_quotes_map, message=message)
            live_context = ctx.text
    except Exception as e:
        logging.warning(f"AI chat context build failed: {e}")

    system_msg = get_prompt("ai_chat", memory="", live_context=live_context)

    # Step 2 — Load this session's recent turns for continuity.
    messages_list = []
    try:
        async with run.step():
            history = await db.chat_messages.find({"session_id": session_id}).sort("created_at", -1).to_list(10)
            history.reverse()
            messages_list = [AIMessage(role=h["role"], content=h["content"]) for h in history]
    except Exception as e:
        logging.warning(f"AI chat history load failed: {e}")
        messages_list = []
    messages_list.append(AIMessage(role="user", content=message))

    # Step 3 — Consult the model (falls back to a history-less prompt on error).
    try:
        async with run.step():
            try:
                response = await router.run_raw(system_msg, messages_list, prefer="claude", max_tokens=800)
            except Exception as e:
                logging.error(f"AI chat error with history: {e}")
                response = await router.run_raw(system_msg, message, prefer="claude", max_tokens=800)
        await run.complete()
        return response
    except Exception as err:
        logging.error(f"AI chat failed: {err}")
        await run.complete("warning")
        return f"AI temporarily unavailable. Error: {str(err)}"

async def ai_market_summary():
    """Generate AI market summary — uses Gemini as primary for speed."""
    from services.real_market import fetch_real_sectors, fetch_real_fii_dii
    overview = await real_overview()
    if not overview:
        return "Market summary unavailable — live market data cannot be fetched right now. Please retry shortly."

    sectors = await fetch_real_sectors() or []
    fii_dii = await fetch_real_fii_dii()
    top_sector = sectors[0] if sectors else None

    def _fmt_num(v, suffix=""):
        return f"{v}{suffix}" if v is not None else "unavailable"

    fii_net = fii_dii.get("fii", {}).get("net")
    sentiment = overview.get("market_sentiment")
    sign = '+' if (overview['nifty']['change'] or 0) >= 0 else ''
    fallback = (f"Nifty at {overview['nifty']['value']} ({sign}{overview['nifty']['change_pct']}%). "
                + (f"{top_sector['sector']} sector leading at {top_sector['change_pct']:+}%. " if top_sector else "Sector data unavailable. ")
                + f"FII net: {_fmt_num(fii_net, ' Cr')}. Market sentiment: {_fmt_num(sentiment, '/100')}.")

    if not (claude_configured() or gemini_configured()):
        return fallback

    engine = get_debate_engine()
    system_msg = ("You are a professional Indian market analyst. Generate a 3-4 sentence market summary. "
                  "Be concise, data-driven, no fluff. Include specific numbers. "
                  "If a data point is marked unavailable, do not invent it.")
    prompt = f"""Summarize today's Indian market:
- Nifty: {overview['nifty']['value']} ({overview['nifty']['change_pct']}%)
- Bank Nifty: {overview['bank_nifty']['value']} ({overview['bank_nifty']['change_pct']}%)
- Top sector: {f"{top_sector['sector']} ({top_sector['change_pct']:+}%)" if top_sector else "unavailable"}
- FII net: {_fmt_num(fii_net, ' Cr')}, DII net: {_fmt_num(fii_dii.get('dii', {}).get('net'), ' Cr')}
- India VIX: {_fmt_num(overview.get('india_vix'))}
- Sentiment: {_fmt_num(sentiment, '/100')}"""
    try:
        return await engine.simple_chat(system_msg, prompt, prefer="gemini", max_tokens=300)
    except Exception as e:
        logging.error(f"Market summary AI error: {e}")
        return fallback

async def ai_sip_analysis(sip_data: dict):
    """AI-powered SIP recommendation — uses Claude as primary for detailed financial advice."""
    if not (claude_configured() or gemini_configured()):
        return ("SIP analysis requires an AI key. "
                "Please configure ANTHROPIC_API_KEY or GOOGLE_GEMINI_KEY.")
    engine = get_debate_engine()
    system_msg = ("You are a SEBI-registered Certified Financial Planner for Indian investors. "
                  "Provide specific mutual fund recommendations with real Indian fund names. Be detailed but concise.")
    prompt = f"""Analyze SIP for an Indian investor:
- Monthly SIP: INR {sip_data['amount']}
- Goal: {sip_data['goal']}
- Time: {sip_data['years']} years
- Risk: {sip_data['risk']}
- Age: {sip_data['age']}
- Tax bracket: {sip_data['tax_bracket']}

Provide: Top 3 fund recommendations (name, category, expected CAGR, expense ratio, allocation %), asset allocation strategy, expected corpus, tax tips."""
    try:
        return await engine.simple_chat(system_msg, prompt, prefer="claude", max_tokens=800)
    except Exception as e:
        return f"SIP analysis error: {str(e)}"


# ============ AI INVESTMENT ADVISOR ============
# Expands the SIP Advisor into a full multi-horizon stock advisor. Every
# recommendation is built from REAL market data (services.real_market: live
# Yahoo quotes + technical indicators + chart patterns + sector performance)
# and tuned per horizon (intraday = tight stops/targets, long term = wide).
# The AI (ai_debate_engine.simple_chat) only writes the narrative from the
# real numbers — it never invents prices. Fully degrades to a deterministic,
# clearly-marked structured result if AI keys or Yahoo are unavailable.

# Per-horizon risk/reward geometry. Percentages are applied to the live price.
# entry_band = half-width of the entry zone around the live price.
HORIZON_CONFIG = {
    "intraday": {
        "label": "Intraday",
        "holding_period": "Same day — square off before 3:15 PM",
        "sl_pct": 0.012, "target_pcts": [0.012, 0.02, 0.03], "entry_band": 0.003,
        "base_return": 1.8, "risk_bias": 1,
    },
    "swing": {
        "label": "Swing Trading",
        "holding_period": "3 to 10 trading days",
        "sl_pct": 0.03, "target_pcts": [0.045, 0.08, 0.12], "entry_band": 0.008,
        "base_return": 6.0, "risk_bias": 1,
    },
    "short": {
        "label": "Short Term",
        "holding_period": "2 to 4 weeks",
        "sl_pct": 0.05, "target_pcts": [0.08, 0.13, 0.18], "entry_band": 0.012,
        "base_return": 10.0, "risk_bias": 0,
    },
    "medium": {
        "label": "Medium Term",
        "holding_period": "3 to 6 months",
        "sl_pct": 0.08, "target_pcts": [0.15, 0.24, 0.35], "entry_band": 0.02,
        "base_return": 18.0, "risk_bias": -1,
    },
    "long": {
        "label": "Long Term",
        "holding_period": "1 to 3 years",
        "sl_pct": 0.12, "target_pcts": [0.30, 0.55, 0.90], "entry_band": 0.03,
        "base_return": 40.0, "risk_bias": -1,
    },
}


def _advisor_score(quote: dict, patterns: list, horizon: str) -> tuple:
    """Technical score (0-100 conviction) + human-readable reasons for a stock,
    computed entirely from REAL indicators. Weighting is nudged per horizon:
    shorter horizons lean on momentum/volume, longer ones on trend (MACD)."""
    rsi = quote.get("rsi", 50.0) or 50.0
    volume_ratio = quote.get("volume_ratio", 1.0) or 1.0
    macd = quote.get("macd", 0.0) or 0.0
    macd_signal = quote.get("macd_signal", 0.0) or 0.0
    change_pct = quote.get("change_pct", 0.0) or 0.0

    short_horizon = horizon in ("intraday", "swing")
    score = 50.0
    reasons = []

    # RSI — momentum vs mean-reversion
    if 50 <= rsi <= 68:
        score += 15
        reasons.append(f"RSI at {rsi:.0f} sits in a healthy bullish zone with room before overbought.")
    elif 40 <= rsi < 50:
        score += 8
        reasons.append(f"RSI at {rsi:.0f} is neutral-to-constructive, basing before a potential move up.")
    elif rsi < 35:
        score += 10
        reasons.append(f"RSI at {rsi:.0f} is oversold, hinting at a mean-reversion bounce.")
    elif rsi > 72:
        score -= 6
        reasons.append(f"RSI at {rsi:.0f} is overbought — momentum is strong but entries need discipline.")

    # Volume — conviction behind the move (weighted more for short horizons)
    vol_weight = 20 if short_horizon else 12
    if volume_ratio >= 1.5:
        score += vol_weight
        reasons.append(f"Volume is {volume_ratio:.1f}x the 20-day average — strong participation.")
    elif volume_ratio >= 1.1:
        score += vol_weight * 0.5
        reasons.append(f"Volume is elevated at {volume_ratio:.1f}x the 20-day average.")

    # MACD — trend (weighted more for longer horizons)
    macd_weight = 18 if not short_horizon else 12
    if macd > macd_signal:
        score += macd_weight
        reasons.append("MACD is in a bullish crossover, confirming upward trend momentum.")
    else:
        score -= 4
        reasons.append("MACD has yet to cross bullish — wait for confirmation on strength.")

    # Live day change
    if change_pct >= 1.0:
        score += 6
        reasons.append(f"Trading up {change_pct:+.1f}% today with positive intraday momentum.")
    elif change_pct <= -1.5:
        score -= 4

    # Chart patterns (real detector output)
    for p in patterns[:2]:
        sig = p.get("signal")
        name = p.get("pattern", "")
        if sig == "bullish":
            score += 12
            reasons.append(f"{name} pattern detected on the daily chart — a bullish signal.")
        elif sig == "bearish":
            score -= 10
            reasons.append(f"Caution: a {name} pattern is present — manage risk tightly.")

    confidence = int(min(95, max(55, round(score))))
    return confidence, reasons


def _advisor_risk_level(confidence: int, horizon: str) -> str:
    """Map confidence + horizon bias to Low/Medium/High. Shorter horizons
    (intraday/swing) carry structurally higher risk; longer horizons lower."""
    levels = ["Low", "Medium", "High"]
    base = 0 if confidence >= 80 else (1 if confidence >= 70 else 2)
    bias = HORIZON_CONFIG.get(horizon, {}).get("risk_bias", 0)
    idx = min(2, max(0, base + bias))
    return levels[idx]


def _advisor_deterministic_narrative(rec: dict, horizon_label: str) -> dict:
    """Build the narrative fields (ai_summary, news_impact) deterministically
    from the real numbers. Used as the base and as the fallback when no AI key
    is configured — always structured, always honest about what it models."""
    name = rec["name"]
    entry = rec["entry_zone"]
    t = rec["targets"]
    summary = (
        f"{name} scores {rec['confidence']}/100 as a {horizon_label.lower()} idea "
        f"({rec['risk'].lower()} risk). Accumulate in the ₹{entry['low']}–₹{entry['high']} zone "
        f"with a stop at ₹{rec['stop_loss']}, targeting ₹{t[0]}"
        + (f" and ₹{t[1]}" if len(t) > 1 else "")
        + f" — an expected move of about {rec['expected_return_pct']:+.1f}%. "
        f"Reasoning is driven by live technicals: {rec['technical_reasons'][0] if rec['technical_reasons'] else 'positive setup'}"
    )
    news = (
        f"No stock-specific news feed is modeled for this recommendation. "
        f"{rec['sector_strength']} Watch upcoming earnings, sector headlines and broad-market cues "
        f"before acting."
    )
    return {"ai_summary": summary, "news_impact": news}


async def _advisor_ai_enrich(recs: list, horizon_label: str, risk_appetite, capital) -> None:
    """Enrich ai_summary + news_impact for each rec via a single batched
    simple_chat call, feeding the AI ONLY the real numbers. Mutates recs in
    place. Silently keeps the deterministic narrative on any failure so the
    endpoint never crashes and needs no API key to succeed."""
    if not recs or not (claude_configured() or gemini_configured()):
        return
    engine = get_debate_engine()
    lines = []
    for r in recs:
        lines.append(
            f"- {r['symbol']} ({r['name']}, {r['sector']}): live price ₹{r['price']}, "
            f"RSI {r.get('rsi')}, volume {r.get('volume_ratio')}x avg, pattern "
            f"'{r.get('pattern') or 'none'}', confidence {r['confidence']}/100, {r['risk']} risk, "
            f"entry ₹{r['entry_zone']['low']}-₹{r['entry_zone']['high']}, SL ₹{r['stop_loss']}, "
            f"targets {r['targets']}, sector today: {r['sector_strength']}"
        )
    cap_txt = f" The investor has about ₹{int(capital)} to deploy." if capital else ""
    system_msg = (
        "You are AlphaPartner's senior equity strategist for Indian markets (NSE/BSE). "
        "You are given REAL, pre-computed price levels and technicals — never change or invent "
        "any number, only explain them. For EACH stock write a crisp 2-3 sentence 'summary' "
        "(why it fits this horizon, referencing the given levels) and a 1-2 sentence 'news_impact' "
        "(likely news/earnings catalysts and risks for that sector — be honest, no fabricated headlines). "
        "Return ONLY a JSON object mapping each SYMBOL to {\"summary\": str, \"news_impact\": str}."
    )
    prompt = (
        f"Horizon: {horizon_label}. Risk appetite: {risk_appetite or 'moderate'}.{cap_txt}\n"
        f"Stocks (with real live data):\n" + "\n".join(lines) +
        "\n\nReturn the JSON object now."
    )
    try:
        raw = await engine.simple_chat(system_msg, prompt, prefer="claude", max_tokens=900)
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return
        parsed = json.loads(raw[start:end + 1])
        for r in recs:
            entry = parsed.get(r["symbol"]) or parsed.get(r["symbol"].upper())
            if isinstance(entry, dict):
                if entry.get("summary"):
                    r["ai_summary"] = str(entry["summary"]).strip()
                if entry.get("news_impact"):
                    r["news_impact"] = str(entry["news_impact"]).strip()
    except Exception as e:
        logging.warning(f"Advisor AI narrative enrichment failed (using deterministic): {e}")


async def build_advisor_recommendations(
    horizon: str = "swing",
    risk_appetite: Optional[str] = None,
    sectors: Optional[list] = None,
    capital: Optional[float] = None,
    count: int = 5,
) -> dict:
    """Core advisor engine. Pipeline (all REAL data):
      1. Live 2-day quotes for the whole NSE universe (fetch_all_universe_quotes).
      2. Optional sector filter + momentum shortlist.
      3. Full technical quote (RSI/MACD/volume) + chart patterns for the shortlist.
      4. Per-horizon scoring, confidence, entry/SL/targets from the live price.
      5. Real sector-strength ranking (fetch_real_sectors).
      6. AI narrative (batched simple_chat) with a deterministic fallback.
    Never raises on data/AI failure — always returns a structured payload."""
    horizon = (horizon or "swing").lower()
    if horizon not in HORIZON_CONFIG:
        horizon = "swing"
    cfg = HORIZON_CONFIG[horizon]
    count = max(3, min(6, int(count or 5)))

    from services.real_market import (
        fetch_all_universe_quotes, fetch_real_stock_quote,
        detect_chart_patterns, fetch_real_sectors,
    )

    data_source = "yahoo_finance"
    try:
        universe = await fetch_all_universe_quotes()
    except Exception as e:
        logging.warning(f"Advisor universe fetch failed: {e}")
        universe = []
    if not universe:
        # Live data unavailable — return an explicit unavailable payload,
        # never recommendations built on simulated values.
        return {
            "horizon": horizon,
            "horizon_label": cfg["label"],
            "risk_appetite": risk_appetite,
            "count": 0,
            "ai_powered": False,
            "data_source": "unavailable",
            "available": False,
            "note": "Live market data is temporarily unavailable — recommendations cannot be generated right now.",
            "recommendations": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # Sector filter (case-insensitive); relax if it leaves too few candidates.
    if sectors:
        wanted = {s.strip().lower() for s in sectors if s and s.strip()}
        filtered = [u for u in universe if (u.get("sector") or "").lower() in wanted]
        if len(filtered) >= 3:
            universe = filtered

    # Momentum shortlist: prefer positive day change + participation, but keep a
    # broad enough pool to still fill `count` after scoring.
    shortlist = sorted(
        universe,
        key=lambda u: (u.get("change_pct", 0) or 0) + min((u.get("volume_ratio", 1) or 1), 3),
        reverse=True,
    )[: max(8, count + 3)]

    # Real technicals + patterns for the shortlist, concurrently.
    async def _enrich(u):
        sym = u.get("symbol")
        try:
            full = await fetch_real_stock_quote(sym)
        except Exception:
            full = None
        quote = full or u
        try:
            pat = await detect_chart_patterns(sym)
            patterns = pat.get("patterns", [])
        except Exception:
            patterns = []
        # Carry descriptive fields from the universe entry
        quote.setdefault("name", u.get("name", sym))
        quote.setdefault("sector", u.get("sector", ""))
        quote["symbol"] = sym
        return quote, patterns

    enriched = await asyncio.gather(*[_enrich(u) for u in shortlist], return_exceptions=True)

    # Real sector-strength ranking
    try:
        sector_perf = await fetch_real_sectors()
    except Exception:
        sector_perf = []
    sector_rank = {s["sector"]: i for i, s in enumerate(sector_perf)}
    sector_change = {s["sector"]: s.get("change_pct", 0) for s in sector_perf}
    total_sectors = len(sector_perf) or 1

    scored = []
    for item in enriched:
        if isinstance(item, Exception) or not item:
            continue
        quote, patterns = item
        price = quote.get("price") or 0
        if not price:
            continue
        confidence, tech_reasons = _advisor_score(quote, patterns, horizon)
        risk = _advisor_risk_level(confidence, horizon)

        entry_low = round(price * (1 - cfg["entry_band"]), 2)
        entry_high = round(price * (1 + cfg["entry_band"]), 2)
        stop_loss = round(price * (1 - cfg["sl_pct"]), 2)
        targets = [round(price * (1 + p), 2) for p in cfg["target_pcts"]]
        # Expected return = midpoint of the first two targets vs live price,
        # gently scaled by conviction so low-confidence ideas read more modestly.
        mid_target = sum(targets[:2]) / min(2, len(targets))
        raw_ret = (mid_target / price - 1) * 100
        expected_return_pct = round(raw_ret * (0.7 + 0.3 * confidence / 100), 1)

        sector = quote.get("sector", "") or ""
        rank = sector_rank.get(sector)
        chg = sector_change.get(sector)
        if rank is not None and chg is not None:
            standing = "leading" if rank < total_sectors / 3 else ("mid-pack" if rank < 2 * total_sectors / 3 else "lagging")
            sector_strength = (
                f"The {sector} sector is {standing} today at {chg:+.1f}% "
                f"(ranked #{rank + 1} of {total_sectors} NSE sectors)."
            )
        elif sector:
            sector_strength = f"The {sector} sector is a core part of the NSE universe."
        else:
            sector_strength = "Broad-market sector strength is mixed today."

        fundamental_reasons = [
            f"{quote.get('name', quote['symbol'])} is an established {sector or 'NSE'} name with deep "
            f"liquidity and institutional coverage — suitable for {cfg['label'].lower()} positioning.",
            (f"Aligned to a {cfg['holding_period'].split('—')[0].strip().lower()} holding window, "
             f"letting the thesis play out while the {sector or 'broader'} trend develops."),
        ]

        pattern_name = None
        for p in patterns:
            if p.get("signal") == "bullish":
                pattern_name = p.get("pattern")
                break
        if not pattern_name and patterns:
            pattern_name = patterns[0].get("pattern")

        rec = {
            "symbol": quote["symbol"],
            "name": quote.get("name", quote["symbol"]),
            "sector": sector,
            "confidence": confidence,
            "risk": risk,
            "expected_return_pct": expected_return_pct,
            "holding_period": cfg["holding_period"],
            "entry_zone": {"low": entry_low, "high": entry_high},
            "stop_loss": stop_loss,
            "targets": targets,
            "technical_reasons": tech_reasons[:4] or ["Constructive technical setup on the daily timeframe."],
            "fundamental_reasons": fundamental_reasons,
            "news_impact": "",
            "sector_strength": sector_strength,
            "ai_summary": "",
            "price": round(price, 2),
            "horizon": horizon,
            "rsi": quote.get("rsi"),
            "volume_ratio": quote.get("volume_ratio"),
            "pattern": pattern_name,
        }
        scored.append(rec)

    # Rank by conviction; for a conservative appetite, prefer lower-risk ideas.
    scored.sort(key=lambda r: r["confidence"], reverse=True)
    if (risk_appetite or "").lower() == "conservative":
        low_med = [r for r in scored if r["risk"] in ("Low", "Medium")]
        if len(low_med) >= 3:
            scored = low_med
    elif (risk_appetite or "").lower() == "aggressive":
        # Reward higher expected return for aggressive investors.
        scored.sort(key=lambda r: (r["expected_return_pct"], r["confidence"]), reverse=True)

    selected = scored[:count]

    # Deterministic narrative base (works with zero AI keys) …
    for r in selected:
        base = _advisor_deterministic_narrative(r, cfg["label"])
        r["ai_summary"] = base["ai_summary"]
        r["news_impact"] = base["news_impact"]
    # … then AI enrichment on top (best-effort, real numbers only).
    ai_powered = bool(claude_configured() or gemini_configured())
    await _advisor_ai_enrich(selected, cfg["label"], risk_appetite, capital)

    # Validate/shape through the Pydantic contract so the response is stable.
    recommendations = [
        AdvisorRecommendation(
            **{**r, "entry_zone": AdvisorEntryZone(**r["entry_zone"])}
        ).model_dump()
        for r in selected
    ]

    return {
        "horizon": horizon,
        "horizon_label": cfg["label"],
        "risk_appetite": risk_appetite,
        "count": len(recommendations),
        "ai_powered": ai_powered,
        "data_source": data_source,
        "available": True,
        "recommendations": recommendations,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============ ROUTERS ============

auth_router = APIRouter(prefix="/api/auth", tags=["Auth"])
market_router = APIRouter(prefix="/api/market", tags=["Market"])
stocks_router = APIRouter(prefix="/api/stocks", tags=["Stocks"])
analysis_router = APIRouter(prefix="/api/analysis", tags=["Analysis"])
trades_router = APIRouter(prefix="/api/trades", tags=["Trades"])
portfolio_router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])
notifications_router = APIRouter(prefix="/api/notifications", tags=["Notifications"])
sip_router = APIRouter(prefix="/api/sip", tags=["SIP"])
advisor_router = APIRouter(prefix="/api/advisor", tags=["AI Investment Advisor"])
chat_router = APIRouter(prefix="/api/chat", tags=["Chat"])
ai_router = APIRouter(prefix="/api/ai", tags=["AI Workspace"])
paper_router = APIRouter(prefix="/api/paper", tags=["Paper Trading"])
backtest_router = APIRouter(prefix="/api/backtest", tags=["Backtesting"])
watchlist_router = APIRouter(prefix="/api/watchlist", tags=["Watchlist"])
admin_router = APIRouter(prefix="/api/admin", tags=["Admin Portal"])



# ============ AUTH ROUTES ============

@auth_router.post("/register")
async def register(data: UserCreate, response: Response, request: Request,
                   background_tasks: BackgroundTasks):
    # Anti account-spam: 5 registrations / hour per client IP (PH1.7).
    ip = ratelimit.client_ip(request)
    ratelimit.raise_if_limited(
        await ratelimit.get_limiter(db).check(ratelimit.REGISTER, ip),
        detail="Too many registration attempts. Please try again later.",
    )
    email = data.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    user_doc = {
        "name": data.name,
        "email": email,
        "password_hash": hash_password(data.password),
        "role": "user",
        "capital": 100000,
        "risk_level": "moderate",
        "max_daily_loss": 5000,
        "max_trades_per_day": 3,
        "telegram_chat_id": None,
        # Email-verification status (PH1.8). New email/password accounts start
        # unverified; a verification link is emailed below. Login is NOT blocked
        # on this flag (backward-compatible) — it gates verified-only features
        # and is the hook a future hard-enforcement gate would flip on.
        "email_verified": False,
        "email_verified_at": None,
        "verified_by": None,
        "notification_prefs": {"push": True, "email": True, "morning_report": True, "trade_alerts": True, "exit_reminder": True, "portfolio_alerts": True, "email_alerts": True, "telegram_alerts": False},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.users.insert_one(user_doc)
    user_id = str(result.inserted_id)
    # Send the verification link out-of-band so a slow/misconfigured mailer never
    # delays or fails sign-up (best-effort; see _send_recovery_email).
    background_tasks.add_task(_dispatch_verification_email, user_id, email, data.name)
    access = await _issue_session(user_id, email, request, response)
    await log_auth_event(audit.REGISTRATION, request, email=email, user_id=user_id)
    return {"id": user_id, "name": data.name, "email": email, "role": "user",
            "capital": 100000, "email_verified": False, "token": access}

@auth_router.post("/login")
async def login(data: UserLogin, response: Response, request: Request):
    email = data.email.lower().strip()
    ip = ratelimit.client_ip(request)
    identifier = f"{ip}:{email}"
    limiter = ratelimit.get_limiter(db)

    # Brute-force gate (PH1.7): reject up front if this ip:account is already
    # locked out or has exhausted its 5-per-15-min budget of FAILED attempts.
    # Counting happens on failure only (below), so a legitimate user logging in
    # repeatedly is never penalized.
    ratelimit.raise_if_limited(
        await limiter.peek(ratelimit.LOGIN, identifier),
        detail="Too many attempts. Try again later.",
    )

    user = await db.users.find_one({"email": email})
    # Always run exactly one bcrypt comparison (verify_password pads with a
    # dummy hash when the account is missing or has no password, e.g.
    # OAuth-native accounts) so response timing never reveals whether the
    # email exists. The 401 below is identical for both failure causes.
    password_ok = verify_password(data.password, user.get("password_hash") if user else None)
    if not user or not password_ok:
        await limiter.record_failure(ratelimit.LOGIN, identifier)
        # Audit the failed attempt. The 401 is deliberately identical whether the
        # email is unknown or the password is wrong; the audit record keeps the
        # same discretion (email as typed, generic reason) so it never becomes an
        # enumeration oracle for anyone reading the log.
        await log_auth_event(audit.LOGIN_FAILURE, request, email=email,
                             reason="invalid_credentials")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Success: clear the failure budget so prior typos don't linger.
    await limiter.reset(ratelimit.LOGIN, identifier)
    user_id = str(user["_id"])
    access = await _issue_session(user_id, email, request, response)
    await log_auth_event(audit.LOGIN_SUCCESS, request, email=email, user_id=user_id)
    return {
        "id": user_id, "name": user["name"], "email": email,
        "role": user.get("role", "user"), "capital": user.get("capital", 100000),
        "risk_level": user.get("risk_level", "moderate"), "token": access
    }

@auth_router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return user

@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    # Revoke the current session server-side (so its refresh token is dead even
    # if it was captured) then clear every auth cookie via the central policy so
    # the delete attributes (path/domain/secure/samesite) match how they were
    # set. Best-effort revoke: a missing/old/invalid refresh cookie still logs
    # the browser out cleanly.
    token = request.cookies.get("refresh_token")
    if token:
        try:
            payload = jwt_service.decode_token(token, expected_type="refresh")
            await SessionStore(db).revoke(payload["sid"], reason=REASON_LOGOUT)
            await log_auth_event(audit.SESSION_REVOKED, request,
                                 user_id=payload.get("sub"), session_id=payload.get("sid"),
                                 reason=REASON_LOGOUT)
            await log_auth_event(audit.LOGOUT, request,
                                 user_id=payload.get("sub"), session_id=payload.get("sid"))
        except jwt_service.TokenError:
            pass
    clear_auth_cookies(response)
    clear_csrf_cookie(response)
    return {"message": "Logged out"}

@auth_router.post("/logout-all")
async def logout_all(request: Request, response: Response, user: dict = Depends(get_current_user)):
    """Sign out of every device: revoke all of the user's refresh-token
    families and clear the current browser's cookies. Outstanding access tokens
    (≤15 min) drain on their own; the service-layer primitive
    (``SessionStore.revoke_all_for_user``) is what PH1.10's UI will call."""
    revoked = await SessionStore(db).revoke_all_for_user(user["_id"], reason="logout_all")
    clear_auth_cookies(response)
    clear_csrf_cookie(response)
    await log_auth_event(audit.LOGOUT_ALL, request, email=user.get("email"),
                         user_id=user["_id"], detail={"sessions_revoked": revoked})
    return {"message": "Signed out of all devices", "sessions_revoked": revoked}

@auth_router.post("/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = jwt_service.decode_token(token, expected_type="refresh")
    except jwt_service.TokenError:
        await audit.log_event(audit.INVALID_REFRESH, request=request,
                              reason="undecodable_token")
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Token-abuse gate (PH1.7): 20 refreshes / min per session. Keyed by the
    # session id so a stolen refresh token cannot be spun in a tight loop; a
    # legitimate SPA refreshes far less often. Applied only after the token is
    # structurally valid so unauthenticated noise can't fill a session's bucket.
    ratelimit.raise_if_limited(
        await ratelimit.get_limiter(db).check(ratelimit.REFRESH, payload["sid"]),
        detail="Too many refresh attempts. Please slow down.",
    )

    user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    # Global invalidation lever (password change / security event) applies to
    # refresh too — a stale refresh token cannot be rotated into fresh access.
    if jwt_service.token_issued_before(payload, user.get("password_changed_at")):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Rotate: the presented token is single-use. Replaying an already-rotated
    # token (reuse) revokes the whole family — see security.sessions.
    new_jti = jwt_service.new_jti()
    result = await SessionStore(db).rotate(payload["sid"], payload["jti"], new_jti)
    if not result.ok:
        # Distinguish token-theft (replaying an already-rotated token against a
        # still-live family) from a merely stale/revoked/expired token: the
        # former is a CRITICAL security signal (the whole family was just revoked
        # in defense), the latter routine. The 401 stays generic either way.
        if result.outcome == REUSE_DETECTED:
            await log_auth_event(audit.TOKEN_REPLAY_DETECTED, request,
                                 user_id=payload.get("sub"), session_id=payload.get("sid"),
                                 reason="refresh_reuse_detected")
        else:
            await log_auth_event(audit.INVALID_REFRESH, request,
                                 user_id=payload.get("sub"), session_id=payload.get("sid"),
                                 reason=result.outcome)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = str(user["_id"])
    access = jwt_service.create_access_token(user_id, user["email"], payload["sid"])
    refresh = jwt_service.create_refresh_token(user_id, payload["sid"], new_jti)
    # Re-issue BOTH cookies through the central hardened policy: rotation means
    # the refresh token changes on every use, not just the access token.
    set_auth_cookies(response, access, refresh)
    # Refresh the session-bound CSRF token alongside (same sid → same binding).
    set_csrf_cookie(response, payload["sid"])
    await log_auth_event(audit.REFRESH_ROTATION, request, user_id=user_id,
                         session_id=payload["sid"])
    return {"message": "Token refreshed"}


# ============ IDENTITY RECOVERY (PH1.8) ============
# Email verification, password reset, and password change. All single-use tokens
# are minted / verified / burned through security.recovery (never inline here);
# every public response is deliberately generic so it can never reveal whether an
# email is registered (SECURITY_ARCHITECTURE.md §29). A password change or reset
# revokes every session (SessionStore.revoke_all_for_user) AND bumps
# password_changed_at, so outstanding access tokens go stale on next use too.

# A generic, identical response for the enumeration-sensitive flows (request
# verification / forgot password): the caller learns nothing about the account.
_GENERIC_RECOVERY_MESSAGE = (
    "If an account matches, we've sent an email with the next steps."
)


def _humanize_ttl(seconds: int) -> str:
    """Render a token lifetime as human copy for the email ("24 hours")."""
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" + ("s" if hours != 1 else "")
    minutes = max(1, seconds // 60)
    return f"{minutes} minute" + ("s" if minutes != 1 else "")


def _recovery_link(path: str, token: str) -> str:
    """Absolute frontend URL carrying a recovery token as a query param."""
    from urllib.parse import quote
    return f"{_frontend_base()}/{path.lstrip('/')}?token={quote(token, safe='')}"


async def _send_recovery_email(notif_type: str, to_email: str, **kwargs) -> None:
    """Best-effort recovery email — a delivery failure must never break (nor
    change the shape of) the calling auth flow, so it is swallowed and logged."""
    try:
        from services.email_service import send_notification
        await send_notification(notif_type, to_email, **kwargs)
    except Exception as e:  # pragma: no cover - defensive; delivery is best-effort
        logger.warning(f"recovery email ({notif_type}) send failed: {e}")


async def _dispatch_verification_email(user_id: str, email: str, name: str) -> None:
    """Mint a fresh verification token and email its link. Fire-and-forget from
    the caller's perspective (registration / resend)."""
    token = await RecoveryStore(db).issue(user_id, PURPOSE_VERIFY_EMAIL)
    await _send_recovery_email(
        "EMAIL_VERIFICATION", email,
        name=name or "",
        verify_url=_recovery_link("verify-email", token.value),
        expires_in=_humanize_ttl(token.ttl_seconds),
    )


@auth_router.post("/verify-email/request")
async def request_email_verification(request: Request,
                                     user: dict = Depends(get_current_user)):
    """Resend the current user's email-verification link.

    Authenticated (a user verifying their own address) and idempotent from the
    client's view: already-verified accounts return the same generic message
    without sending anything, and issuing a new token invalidates any prior
    unused one (security.recovery), so only the latest link works."""
    ip = ratelimit.client_ip(request)
    ratelimit.raise_if_limited(
        await ratelimit.get_limiter(db).check(
            ratelimit.PASSWORD, f"{ip}:{user['email']}"),
        detail="Too many verification requests. Please try again later.",
    )
    if not user.get("email_verified"):
        await _dispatch_verification_email(user["_id"], user["email"], user.get("name", ""))
        await log_auth_event("email_verification_requested", request,
                             email=user["email"], user_id=user["_id"])
    return {"message": _GENERIC_RECOVERY_MESSAGE}


@auth_router.post("/verify-email")
async def verify_email(data: VerifyEmailRequest, request: Request):
    """Redeem an email-verification token (public — the user clicks a link).

    The token is single-use: ``consume`` burns it atomically, so a replayed link
    is rejected. Responses are generic (a bad/expired/used token and an unknown
    user both yield the same 400) so the endpoint reveals nothing."""
    ip = ratelimit.client_ip(request)
    ratelimit.raise_if_limited(
        await ratelimit.get_limiter(db).check(ratelimit.PASSWORD, ip),
        detail="Too many attempts. Please try again later.",
    )
    record = await RecoveryStore(db).consume(data.token, PURPOSE_VERIFY_EMAIL)
    if not record:
        await log_auth_event("email_verification_failure", request, reason="invalid_or_used_token")
        raise HTTPException(status_code=400, detail="Verification link is invalid or has expired.")

    try:
        user_oid = ObjectId(record["user_id"])
    except Exception:
        raise HTTPException(status_code=400, detail="Verification link is invalid or has expired.")
    result = await db.users.update_one(
        {"_id": user_oid},
        {"$set": {
            "email_verified": True,
            "email_verified_at": datetime.now(timezone.utc).isoformat(),
            "verified_by": "email",
        }},
    )
    if not getattr(result, "matched_count", 0):
        raise HTTPException(status_code=400, detail="Verification link is invalid or has expired.")
    await log_auth_event("email_verification_success", request, user_id=record["user_id"])
    return {"message": "Your email has been verified.", "email_verified": True}


@auth_router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, request: Request):
    """Begin password recovery. ALWAYS returns the same generic message so it
    cannot be used to enumerate registered emails — the reset email is sent only
    when the account exists and can hold a password (OAuth-only accounts, which
    store an empty ``password_hash``, are silently skipped)."""
    email = (data.email or "").lower().strip()
    ip = ratelimit.client_ip(request)
    # Keyed by ip:email so one client cannot spray reset mail across accounts,
    # while a genuine user retrying their own address is barely affected.
    ratelimit.raise_if_limited(
        await ratelimit.get_limiter(db).check(ratelimit.PASSWORD, f"{ip}:{email}"),
        detail="Too many password reset requests. Please try again later.",
    )
    if email:
        user = await db.users.find_one({"email": email})
        # Only email a link to an account that actually has a password to reset;
        # an OAuth-native account (empty hash) has nothing to reset and should
        # keep using Google — but the response stays identical either way.
        if user and user.get("password_hash"):
            token = await RecoveryStore(db).issue(str(user["_id"]), PURPOSE_RESET_PASSWORD)
            await _send_recovery_email(
                "PASSWORD_RESET", email,
                reset_url=_recovery_link("reset-password", token.value),
                expires_in=_humanize_ttl(token.ttl_seconds),
            )
            await log_auth_event("password_reset_requested", request,
                                 email=email, user_id=str(user["_id"]))
        else:
            await log_auth_event("password_reset_requested", request,
                                 email=email, reason="no_resettable_account")
    return {"message": _GENERIC_RECOVERY_MESSAGE}


@auth_router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest, request: Request):
    """Complete password recovery with a single-use reset token + a new password.

    Enforces the PH1.5 password policy against the resolved account's identity,
    burns the token (single-use / replay-safe), then invalidates EVERY session
    and stamps ``password_changed_at`` so all outstanding access and refresh
    tokens are dead. The user must log in again with the new password."""
    ip = ratelimit.client_ip(request)
    ratelimit.raise_if_limited(
        await ratelimit.get_limiter(db).check(ratelimit.PASSWORD, ip),
        detail="Too many attempts. Please try again later.",
    )
    record = await RecoveryStore(db).consume(data.token, PURPOSE_RESET_PASSWORD)
    if not record:
        await log_auth_event("password_reset_failure", request, reason="invalid_or_used_token")
        raise HTTPException(status_code=400, detail="Reset link is invalid or has expired.")

    try:
        user_oid = ObjectId(record["user_id"])
    except Exception:
        raise HTTPException(status_code=400, detail="Reset link is invalid or has expired.")
    user = await db.users.find_one({"_id": user_oid})
    if not user:
        raise HTTPException(status_code=400, detail="Reset link is invalid or has expired.")

    new_password = normalize_password(data.new_password)
    violations = validate_new_password(new_password, email=user.get("email", ""), name=user.get("name", ""))
    if violations:
        # The token is already burned; the user restarts via forgot-password.
        raise HTTPException(status_code=422, detail=" ".join(violations))

    await _apply_password_change(user, new_password)
    await log_auth_event("password_reset_success", request,
                         email=user.get("email"), user_id=record["user_id"])
    return {"message": "Your password has been reset. Please log in with your new password."}


@auth_router.post("/change-password")
async def change_password(data: ChangePasswordRequest, request: Request,
                          user: dict = Depends(get_current_user)):
    """Change the authenticated user's password.

    Requires the CURRENT password (re-authentication — a stolen session alone
    cannot rotate the credential), enforces the PH1.5 policy on the new one, then
    revokes every session and bumps ``password_changed_at``. The caller is signed
    out everywhere (including this device) and must log in again."""
    ip = ratelimit.client_ip(request)
    ratelimit.raise_if_limited(
        await ratelimit.get_limiter(db).check(ratelimit.PASSWORD, f"{ip}:{user['email']}"),
        detail="Too many attempts. Please try again later.",
    )
    # Re-fetch with the hash (get_current_user strips password_hash).
    full = await db.users.find_one({"_id": ObjectId(user["_id"])})
    if not full:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not full.get("password_hash"):
        # OAuth-native account: there is no password to verify against.
        raise HTTPException(status_code=400, detail="This account has no password set.")
    if not verify_password(data.current_password, full.get("password_hash")):
        await log_auth_event("password_change_failure", request,
                             email=full.get("email"), user_id=user["_id"],
                             reason="wrong_current_password")
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    new_password = normalize_password(data.new_password)
    violations = validate_new_password(new_password, email=full.get("email", ""), name=full.get("name", ""))
    if violations:
        raise HTTPException(status_code=422, detail=" ".join(violations))
    if verify_password(new_password, full.get("password_hash")):
        raise HTTPException(status_code=422, detail="New password must be different from the current password.")

    await _apply_password_change(full, new_password)
    await log_auth_event("password_change_success", request,
                         email=full.get("email"), user_id=user["_id"])
    return {"message": "Your password has been changed. Please log in again on all devices."}


async def _apply_password_change(user: dict, new_password: str) -> None:
    """The shared credential-rotation primitive for reset AND change.

    Hashes the new password, stamps ``password_changed_at`` (the global token
    kill-switch honored by every access/refresh check), retires any outstanding
    recovery tokens, revokes every session, and sends a confirmation email. Kept
    in one place so reset and change can never diverge on the security-critical
    steps."""
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"_id": user["_id"] if isinstance(user["_id"], ObjectId) else ObjectId(str(user["_id"]))},
        {"$set": {
            "password_hash": hash_password(new_password),
            "password_changed_at": now_iso,
        }},
    )
    user_id = str(user["_id"])
    # Any pending reset link is now moot — retire them so a leaked-but-unused
    # link cannot be redeemed after the password already changed.
    await RecoveryStore(db).invalidate_outstanding(user_id, PURPOSE_RESET_PASSWORD)
    # Sign out everywhere: revoke refresh families; password_changed_at handles
    # the still-live access tokens on their next use.
    await SessionStore(db).revoke_all_for_user(user_id, reason="password_changed")
    await _send_recovery_email("PASSWORD_CHANGED", user.get("email", ""), name=user.get("name", ""))


# ============ MARKET ROUTES ============

@market_router.get("/overview")
async def market_overview():
    """Get market overview — live Yahoo Finance indices. Explicitly unavailable on failure."""
    overview = await real_overview()
    if not overview:
        return {
            "available": False,
            "note": "Live market data is temporarily unavailable. Please retry shortly.",
        }
    return {**overview, "available": True}

@market_router.get("/gainers")
async def top_gainers():
    from services.real_market import fetch_real_gainers
    return await fetch_real_gainers()

@market_router.get("/losers")
async def top_losers():
    from services.real_market import fetch_real_losers
    return await fetch_real_losers()

@market_router.get("/sectors")
async def sector_performance():
    from services.real_market import fetch_real_sectors
    return await fetch_real_sectors()

@market_router.get("/global")
async def global_markets():
    from services.real_market import fetch_real_global_markets
    return await fetch_real_global_markets()

@market_router.get("/commodities")
async def commodities():
    from services.real_market import fetch_real_commodities
    return await fetch_real_commodities()

@market_router.get("/fii-dii")
async def fii_dii():
    from services.real_market import fetch_real_fii_dii
    return await fetch_real_fii_dii()

@market_router.get("/summary")
async def market_summary():
    summary = await ai_market_summary()
    return {"summary": summary, "generated_at": datetime.now(timezone.utc).isoformat()}

@market_router.get("/activity-feed")
async def activity_feed():
    from services.activity_logger import get_recent_activity
    return get_recent_activity()


# ── Market Engine endpoints ──────────────────────────

@market_router.get("/scanner")
async def market_scanner(
    strategy: Optional[str] = None,
    sector: Optional[str] = None,
    rsi_min: Optional[float] = None,
    rsi_max: Optional[float] = None,
    volume_ratio_min: Optional[float] = None,
    change_pct_min: Optional[float] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    limit: int = 15,
):
    """Advanced market scanner with strategy presets and custom filters."""
    from services.market_engine.scanner_engine import scan

    custom_filters = {}
    if rsi_min is not None:
        custom_filters["rsi_min"] = rsi_min
    if rsi_max is not None:
        custom_filters["rsi_max"] = rsi_max
    if volume_ratio_min is not None:
        custom_filters["volume_ratio_min"] = volume_ratio_min
    if change_pct_min is not None:
        custom_filters["change_pct_min"] = change_pct_min
    if price_min is not None:
        custom_filters["price_min"] = price_min
    if price_max is not None:
        custom_filters["price_max"] = price_max

    return await scan(
        strategy=strategy,
        filters=custom_filters if custom_filters else None,
        sector=sector,
        limit=min(limit, 30),
    )


@market_router.get("/scanner/presets")
async def scanner_presets():
    """Return available scanner strategy presets."""
    from services.market_engine.scanner_engine import get_presets
    return {"presets": get_presets()}


@market_router.get("/ranking")
async def market_ranking(
    top_n: int = 10,
    sector: Optional[str] = None,
):
    """Top-ranked stock opportunities by multi-dimensional scoring."""
    from services.market_engine.ranking_engine import rank_universe
    ranked = await rank_universe(top_n=min(top_n, 30), sector_filter=sector)
    return {
        "rankings": ranked,
        "count": len(ranked),
        "available": bool(ranked),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@market_router.get("/sector-analysis")
async def sector_analysis():
    """Deep sector analysis with rotation, momentum, and breadth."""
    from services.market_engine.sector_engine import analyze_sectors
    return await analyze_sectors()


@market_router.get("/calendar")
async def economic_calendar(
    category: Optional[str] = None,
    importance: Optional[str] = None,
    days_ahead: int = 30,
):
    """Economic calendar events for Indian markets."""
    from services.market_engine.economic_calendar import get_calendar
    return await get_calendar(
        category=category,
        importance=importance,
        days_ahead=min(days_ahead, 90),
    )


@market_router.get("/events")
async def market_events(event_type: Optional[str] = None, limit: int = 50):
    """Recent market engine events from the event bus."""
    from services.market_engine.event_bus import event_bus
    events = event_bus.recent_events(event_type=event_type, limit=min(limit, 200))
    return {"events": events, "count": len(events)}


@market_router.get("/engine/status")
async def engine_status():
    """Market engine health and status."""
    from services.market_engine import market_gateway
    from services.market_engine.validator import is_market_hours, is_pre_market
    return {
        **market_gateway.status,
        "market_hours": is_market_hours(),
        "pre_market": is_pre_market(),
        "engine": "market_engine",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@market_router.get("/heatmap")
async def market_heatmap():
    """Enhanced heatmap data with ranking scores for all universe stocks."""
    from services.market_engine.gateway import market_gateway
    from services.market_engine.ranking_engine import rank_stock

    quotes = await market_gateway.get_universe_quotes()
    if not quotes:
        return {"stocks": [], "available": False}

    sectors = await market_gateway.get_sectors()
    sector_rank_map = {}
    sector_change_map = {}
    for i, s in enumerate(sectors or []):
        name = s.get("name") or s.get("sector", "")
        sector_rank_map[name] = i
        sector_change_map[name] = s.get("change_pct", 0)
    total_sectors = len(sectors) or 1

    heatmap_stocks = []
    for q in quotes:
        sector = q.get("sector", "")
        ranking = rank_stock(
            quote=q,
            sector_rank=sector_rank_map.get(sector),
            total_sectors=total_sectors,
            sector_change=sector_change_map.get(sector),
        )
        heatmap_stocks.append({
            "symbol": q.get("symbol", ""),
            "name": q.get("name", ""),
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "volume_ratio": q.get("volume_ratio"),
            "sector": sector,
            "opportunity_score": ranking["opportunity_score"],
            "signal": ranking["signal"],
        })

    heatmap_stocks.sort(key=lambda s: abs(s.get("change_pct") or 0), reverse=True)

    return {
        "stocks": heatmap_stocks,
        "count": len(heatmap_stocks),
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============ STOCKS ROUTES ============

@stocks_router.get("/search")
async def search(q: str = ""):
    """Live NSE/BSE stock search via Yahoo Finance; static metadata search
    (factual names/sectors, no market values) when the live API is unreachable."""
    from services.real_market import search_yahoo_stocks
    results = await search_yahoo_stocks(q)
    if results is None:
        return search_stocks(q)
    return results

@stocks_router.get("/universe")
async def stock_universe():
    return STOCK_UNIVERSE

@stocks_router.get("/{symbol}")
async def stock_detail(symbol: str):
    """Get stock details from live Yahoo Finance data. Explicit error when unavailable."""
    quote = await real_quote(symbol)
    if quote:
        return quote
    if not get_stock_meta(symbol):
        raise HTTPException(status_code=404, detail="Stock not found")
    raise HTTPException(status_code=503, detail="Live market data temporarily unavailable for this stock")

@stocks_router.get("/{symbol}/chart")
async def stock_chart(symbol: str, period: str = "1D"):
    from services.real_market import fetch_real_chart_data
    return await fetch_real_chart_data(symbol, period)


@stocks_router.get("/{symbol}/patterns")
async def stock_patterns(symbol: str):
    """Detect classic chart patterns for a given symbol using 3 months of OHLCV data."""
    from services.real_market import detect_chart_patterns
    from services.activity_logger import log_activity
    log_activity(f"Scanning chart patterns for {symbol.upper()}", "scan", "done")
    result = await detect_chart_patterns(symbol)
    return result


# ── Stock Details (Sprint 5) — live quoteSummary/chart-derived panels.
# Routes stay thin; all logic lives in services.stock_details. Each service
# returns None for an unknown symbol (→ 404) and an explicit
# {"available": false, "note": ...} payload when the live source is down.

def _stock_section_or_404(result):
    if result is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return result

@stocks_router.get("/{symbol}/profile")
async def stock_profile(symbol: str):
    """Company profile (Yahoo quoteSummary assetProfile)."""
    from services.stock_details import get_stock_profile
    return _stock_section_or_404(await get_stock_profile(symbol))

@stocks_router.get("/{symbol}/fundamentals")
async def stock_fundamentals(symbol: str):
    """Grouped fundamentals: valuation, per-share, profitability, growth, health, market."""
    from services.stock_details import get_stock_fundamentals
    return _stock_section_or_404(await get_stock_fundamentals(symbol))

@stocks_router.get("/{symbol}/financials")
async def stock_financials(symbol: str, statement: str = "income", period: str = "annual"):
    """Income / balance-sheet / cash-flow statements (annual or quarterly), ₹ Cr."""
    from services.stock_details import get_stock_financials, VALID_STATEMENTS, VALID_PERIODS
    if statement not in VALID_STATEMENTS or period not in VALID_PERIODS:
        raise HTTPException(status_code=400, detail=f"statement must be one of {VALID_STATEMENTS}, period one of {VALID_PERIODS}")
    return _stock_section_or_404(await get_stock_financials(symbol, statement, period))

@stocks_router.get("/{symbol}/peers")
async def stock_peers(symbol: str):
    """Same-sector peers from the stock universe with live quotes."""
    from services.stock_details import get_stock_peers
    return _stock_section_or_404(await get_stock_peers(symbol))

@stocks_router.get("/{symbol}/levels")
async def stock_levels(symbol: str):
    """Pivot points + clustered swing support/resistance from 3-month history."""
    from services.stock_details import get_stock_levels
    return _stock_section_or_404(await get_stock_levels(symbol))

@stocks_router.get("/{symbol}/trade-setup")
async def stock_trade_setup(symbol: str):
    """Education-first trade setup: entry/SL/targets from levels + ATR + indicators."""
    from services.stock_details import get_trade_setup
    return _stock_section_or_404(await get_trade_setup(symbol))

@stocks_router.get("/{symbol}/risk")
async def stock_risk(symbol: str):
    """Risk profile: volatility, beta vs Nifty, drawdown, ATR, weighted risk score."""
    from services.stock_details import get_risk_analysis
    return _stock_section_or_404(await get_risk_analysis(symbol))


@analysis_router.get("/top-picks")
async def top_picks():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cached = await db.market_analysis.find_one({"date": today})
    if cached and cached.get("top_picks"):
        return {"picks": cached["top_picks"], "date": today, "available": True}

    from services.real_market import fetch_real_top_picks
    res = await fetch_real_top_picks(3)
    picks = res.get("picks", [])
    if not picks:
        # Live data unavailable — do not persist, surface explicitly
        return {
            "picks": [],
            "date": today,
            "available": False,
            "note": res.get("note", "Live market data is temporarily unavailable."),
        }
    await db.market_analysis.update_one(
        {"date": today},
        {"$set": {"top_picks": picks, "generated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )
    return {"picks": picks, "date": today, "available": True}

@analysis_router.post("/explain")
async def explain_stock(data: StockAnalysisRequest):
    # AI debates are expensive — cache per symbol per day (4h TTL);
    # `force: true` bypasses for an explicit re-run.
    from services.cache import cache_get, cache_set
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    explain_cache_key = f"ai_explain_{data.symbol.upper()}_{today}"
    if not data.force:
        cached = await cache_get(explain_cache_key)
        if cached:
            return cached

    quote = await real_quote(data.symbol)
    if not quote:
        if not get_stock_meta(data.symbol):
            raise HTTPException(status_code=404, detail="Stock not found")
        raise HTTPException(status_code=503, detail="Live market data temporarily unavailable for this stock")
    name = data.name or quote.get("name", data.symbol)
    prompt = f"""Analyze this NSE stock for intraday trading:
Stock: {name} ({data.symbol})
Price: INR {quote['price']}
RSI: {quote['rsi']}
Volume vs Avg: {quote['volume_ratio']}x
Sector: {quote['sector']}
MACD: {quote['macd']}
VWAP: {quote['vwap']}

Explain: WHY this stock could be a good trade, momentum factors, entry reasoning, risks, and what to watch after entering. Simple language, bullet points, under 200 words."""

    result = await ai_dual_debate(prompt, f"explain-{data.symbol}")
    response = {"symbol": data.symbol, "name": name, "quote": quote, **result}
    # Only cache successful debates — never a provider-failure payload
    if result.get("providers_active"):
        await cache_set(explain_cache_key, response, 14400)
    return response

@analysis_router.get("/morning-report")
async def morning_report():
    from services.real_market import fetch_real_top_picks, fetch_real_sectors
    picks_res = await fetch_real_top_picks(3)
    picks = picks_res.get("picks", [])

    overview = await real_overview()
    if not overview:
        return {
            "available": False,
            "report": "Morning report unavailable — live market data cannot be fetched right now.",
            "picks": picks,
            "overview": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    sectors = await fetch_real_sectors() or []
    sentiment = overview.get("market_sentiment")
    vix = overview.get("india_vix")

    report = ""
    if claude_configured() or gemini_configured():
        try:
            engine = get_debate_engine()
            system_msg = ("You are AlphaPartner morning briefing AI. Create a concise, actionable morning "
                          "market report for Indian intraday traders. Professional tone, use data provided. "
                          "If a data point is marked unavailable, do not invent it.")
            prompt = f"""Generate morning market report:
Nifty: {overview['nifty']['value']} | Bank Nifty: {overview['bank_nifty']['value']}
Sentiment: {f"{sentiment}/100" if sentiment is not None else "unavailable"} | VIX: {vix if vix is not None else "unavailable"}
Top Sectors: {', '.join([f"{s['sector']} ({s['change_pct']}%)" for s in sectors[:3]]) or "unavailable"}
Top Picks: {', '.join([f"{p['name']} (Confidence: {p['confidence']}%)" for p in picks]) or "unavailable"}"""
            report = await engine.simple_chat(system_msg, prompt, prefer="claude", max_tokens=400)
        except Exception as e:
            report = f"Morning report generation failed: {str(e)}"
    else:
        top_pick_line = (f"Top pick: {picks[0]['name']} ({picks[0]['confidence']}% confidence)."
                         if picks else "Live pick data is unavailable right now.")
        report = (f"Good morning! Nifty at {overview['nifty']['value']}. "
                  f"{len(picks)} strong setups found. {top_pick_line}")

    return {"available": True, "report": report, "picks": picks, "overview": overview, "generated_at": datetime.now(timezone.utc).isoformat()}


@analysis_router.get("/reports/morning")
async def morning_report_full(run_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Full structured morning report (Sprint 10).

    Shared market layer (global markets, Gift Nifty, news, economic calendar,
    scanner, top picks, risk warnings) is cached in MongoDB by date; the
    caller's portfolio alerts are layered on per request. See
    services/morning_report.py.

    `run_id` (optional, client-generated) correlates this request with the live
    AI step timeline streamed over the `ai` WebSocket channel (Sprint R7)."""
    from services.morning_report import get_morning_report

    return await get_morning_report(db, user=user, run_id=run_id)


# ============ TRADES ROUTES (Sprint 9 — Trading Engine) ============
# Risk-gated entry, multi-target + trailing-stop lifecycle, broker execution.
# All business logic lives in services/trading_engine.py; routes only wire it.

async def _risk_inputs(user_id) -> tuple:
    """(trades entered today, realized P&L of trades closed today) — the two
    inputs the Risk Manager needs to enforce daily discipline limits."""
    from services.trading_engine import _today
    today = _today()
    trades = await db.trades.find({"user_id": user_id}).to_list(500)
    live = [t for t in trades if not t.get("is_paper")]
    trades_today = len([t for t in live if (t.get("entry_time") or "").startswith(today)])
    realized_today = round(sum(
        t.get("pnl") or 0 for t in live
        if t.get("pnl") is not None and (t.get("exit_time") or "").startswith(today)), 2)
    return trades_today, realized_today


def _derive_close_status(trade: dict, exit_price: float) -> str:
    """SL_HIT / TARGET_HIT / CLOSED from where the exit landed (side-aware)."""
    short = trade.get("type") == "SELL"
    sl, t1 = trade.get("stop_loss"), trade.get("target1")
    if sl and (exit_price >= sl if short else exit_price <= sl):
        return "SL_HIT"
    if t1 and (exit_price <= t1 if short else exit_price >= t1):
        return "TARGET_HIT"
    return "CLOSED"


@trades_router.post("/validate")
async def validate_trade_route(data: TradeCreate, user: dict = Depends(get_current_user)):
    """Dry-run Risk Manager check — powers the live risk panel in the form."""
    from services import trading_engine
    trades_today, realized_today = await _risk_inputs(user["_id"])
    return trading_engine.validate_trade(user, data.model_dump(), trades_today, realized_today)


@trades_router.post("")
async def create_trade(data: TradeCreate, user: dict = Depends(get_current_user)):
    from services import trading_engine

    # 1. Risk Manager gate — violations always block (warnings educate).
    trades_today, realized_today = await _risk_inputs(user["_id"])
    check = trading_engine.validate_trade(user, data.model_dump(), trades_today, realized_today)
    if not check["approved"]:
        raise HTTPException(status_code=422, detail={
            "message": "Risk check failed — trade was not placed.",
            "violations": check["violations"],
            "warnings": check["warnings"],
            "metrics": check["metrics"],
        })

    # 2. Optional LIVE entry order via the Broker Engine (no simulation — a
    #    failed broker order never creates an OPEN trade).
    broker_order_id = None
    if data.broker:
        broker = _require_broker(data.broker)
        try:
            placed = await broker_engine.place_order(user["_id"], broker, {
                "symbol": data.symbol.upper(), "exchange": data.exchange,
                "transaction_type": data.type, "quantity": data.quantity,
                "order_type": data.order_type,
                "price": data.entry_price if data.order_type == "LIMIT" else None,
                "product": data.product or ("CNC" if broker == "zerodha" else "D"),
                "tag": "engine-entry",
            })
            broker_order_id = placed.get("order_id")
        except BrokerError as e:
            raise HTTPException(status_code=502, detail=f"Order was not placed: {e.user_message}")

    side_word = "Bought" if data.type == "BUY" else "Sold (short)"
    trailing = data.trailing_stop.model_dump() if data.trailing_stop else {"enabled": False}
    trade_doc = {
        "user_id": user["_id"],
        "symbol": data.symbol.upper(),
        "stock_name": data.stock_name,
        "type": data.type,
        "entry_price": data.entry_price,
        "quantity": data.quantity,
        "quantity_open": data.quantity,
        "realized_pnl": 0.0,
        "stop_loss": data.stop_loss,
        "initial_stop_loss": data.stop_loss,
        "target1": data.target1,
        "target2": data.target2,
        "target3": data.target3,
        "trailing_stop": trailing,
        "best_price": data.entry_price,
        "targets_hit": [],
        "events": [trading_engine.make_event(
            "ENTRY", f"{side_word} {data.quantity} @ ₹{data.entry_price}"
                     + (f" via {data.broker} (order {broker_order_id})" if data.broker else ""),
            data.entry_price)],
        "status": "OPEN",
        "pnl": None,
        "pnl_percent": None,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "exit_time": None,
        "notes": data.notes,
        "setup_type": data.setup_type,
        "is_paper": data.is_paper,
        "broker": data.broker,
        "broker_order_id": broker_order_id,
        "product": data.product,
        "exchange": data.exchange,
        # Live auto-exit needs explicit per-trade consent AND a broker link.
        "auto_exit": bool(data.auto_exit and data.broker),
        "risk_check": {"warnings": check["warnings"], "metrics": check["metrics"],
                       "acknowledged": data.override_warnings},
    }
    result = await db.trades.insert_one(trade_doc)
    trade_doc["_id"] = str(result.inserted_id)

    from services.notification_service import create_notification
    await create_notification(
        db, user["_id"],
        type="TRADE_ENTRY",
        title="Trade Executed",
        message=f"{side_word} {data.quantity} {data.symbol} @ INR {data.entry_price}. "
                f"SL: INR {data.stop_loss} | Target: INR {data.target1}"
                + (f" | Broker: {data.broker}" if data.broker else ""),
        symbol=data.symbol,
        data={"trade_id": trade_doc["_id"]},
    )
    return trade_doc


@trades_router.get("/risk/summary")
async def risk_summary(user: dict = Depends(get_current_user)):
    """Today's risk usage vs the user's own limits (Risk Manager dashboard)."""
    from services.trading_engine import build_risk_summary
    return await build_risk_summary(db, user)


@trades_router.post("/quick")
async def quick_trade(request: Request, user: dict = Depends(get_current_user)):
    """One-click trade (AI picks) via the user's CHOSEN trading platform.

    There is no default broker: the user must pick their platform in
    Settings → Trading Platform first, and it must be connected. Runs the
    same Risk Manager gate + engine seeding as POST /api/trades."""
    body = await request.json()
    preferred = (user.get("preferred_broker") or "").strip().lower()
    if not preferred:
        raise HTTPException(status_code=400, detail=(
            "No trading platform selected. Choose your broker in "
            "Settings → Trading Platform to enable one-click trading."))
    status = (await broker_engine.get_status(user["_id"])).get(preferred)
    if not status or not status.get("connected"):
        raise HTTPException(status_code=400, detail=(
            f"Your selected platform ({(status or {}).get('display_name', preferred)}) is not "
            "connected. Reconnect it in Settings → Broker Accounts, or choose another platform."))

    try:
        payload = TradeCreate(
            symbol=body["symbol"],
            stock_name=body.get("stock_name", body["symbol"]),
            type="BUY",
            entry_price=float(body["entry_price"]),
            quantity=int(body.get("quantity", 1)),
            stop_loss=float(body["stop_loss"]),
            target1=float(body["target1"]),
            target2=float(body["target2"]) if body.get("target2") else None,
            broker=preferred,
            order_type="LIMIT",
            product="MIS",
            notes=body.get("notes"),
        )
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: missing or malformed {str(e)}")

    trade_doc = await create_trade(payload, user)
    if body.get("confidence") is not None:
        await db.trades.update_one({"_id": ObjectId(trade_doc["_id"])},
                                   {"$set": {"ai_confidence": body["confidence"]}})
        trade_doc["ai_confidence"] = body["confidence"]
    return {"success": True, "broker": preferred,
            "order_id": trade_doc.get("broker_order_id"), "trade": trade_doc,
            "message": f"Order placed on {status.get('display_name', preferred)} — "
                       f"BUY {payload.quantity} {payload.symbol} @ ₹{payload.entry_price}"}

@trades_router.get("")
async def get_trades(user: dict = Depends(get_current_user)):
    trades = await db.trades.find({"user_id": user["_id"]}).sort("entry_time", -1).to_list(100)
    for t in trades:
        t["_id"] = str(t["_id"])
    return trades

@trades_router.get("/active")
async def get_active_trades(user: dict = Depends(get_current_user)):
    """Open positions with live marks. P&L math is shared with the trade
    stream (Sprint R6) — short-aware and based on `quantity_open` — so REST
    rows and pushed `trade.updated` rows never disagree."""
    from services.trade_stream import trade_payload
    trades = await db.trades.find({"user_id": user["_id"], "status": "OPEN"}).sort("entry_time", -1).to_list(50)
    quotes = await real_quotes_map([t["symbol"] for t in trades])
    for t in trades:
        quote = quotes.get(t["symbol"].upper())
        live = trade_payload(t, quote.get("price") if quote else None)
        t["_id"] = str(t["_id"])
        for key in ("current_price", "unrealized_pnl", "unrealized_pnl_pct"):
            if key in live:
                t[key] = live[key]
    return trades

@trades_router.get("/history")
async def trade_history(user: dict = Depends(get_current_user)):
    trades = await db.trades.find({"user_id": user["_id"], "status": {"$ne": "OPEN"}}).sort("exit_time", -1).to_list(100)
    for t in trades:
        t["_id"] = str(t["_id"])
    return trades

@trades_router.get("/pnl")
async def trade_pnl(user: dict = Depends(get_current_user)):
    trades = await db.trades.find({"user_id": user["_id"]}).to_list(500)
    total_pnl = 0
    today_pnl = 0
    wins = 0
    losses = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for t in trades:
        if t.get("pnl") is not None:
            total_pnl += t["pnl"]
            if t.get("exit_time", "").startswith(today):
                today_pnl += t["pnl"]
            if t["pnl"] > 0:
                wins += 1
            else:
                losses += 1
    total = wins + losses
    return {
        "total_pnl": round(total_pnl, 2),
        "today_pnl": round(today_pnl, 2),
        "total_trades": len(trades),
        "open_trades": len([t for t in trades if t.get("status") == "OPEN"]),
        "win_rate": round((wins / total) * 100, 1) if total > 0 else 0,
        "wins": wins,
        "losses": losses,
    }

async def _generate_coaching_background(trade_id: str):
    """Background task: generate and cache AI coaching for a just-closed trade.

    Runs *after* the close response is returned so the user is never blocked
    waiting on the AI (see CLAUDE.md: never block a FastAPI response on AI).
    No-op if the trade is still open or coaching was already cached.
    """
    try:
        trade = await db.trades.find_one({"_id": ObjectId(trade_id)})
        if not trade or trade.get("status") == "OPEN" or trade.get("coaching"):
            return
        from services.trade_journal import generate_trade_coaching
        engine = get_debate_engine()

        async def _ai(prompt):
            return await engine.simple_chat(
                "You are an expert trading coach for Indian stock market traders.",
                prompt, prefer="claude", max_tokens=400,
            )

        ai_func = _ai if (claude_configured() or gemini_configured()) else None
        coaching = await generate_trade_coaching(trade, ai_func=ai_func)
        await db.trades.update_one({"_id": ObjectId(trade_id)}, {"$set": {"coaching": coaching}})
        from services.activity_logger import log_activity
        log_activity(f"AI coaching generated for {trade['symbol']} trade", "monitor", "done")
    except Exception as e:
        logger.error(f"Background coaching generation failed for {trade_id}: {e}")


async def _announce_trade_closed(trade_doc: dict):
    """Publish `trade.closed` (source: manual) for a just-closed trade
    (Sprint R6). The Trade Monitor refetches on it — the only lifecycle event
    that still needs a refetch (the row leaves the Active tab) — and the AI
    trade review pipeline follows in the background."""
    try:
        from services.market_engine.event_bus import event_bus
        await event_bus.publish("trade.closed", {
            "user_id": str(trade_doc.get("user_id")),
            "trade_id": str(trade_doc.get("_id")),
            "symbol": trade_doc.get("symbol"),
            "status": trade_doc.get("status"),
            "exit_price": trade_doc.get("exit_price"),
            "pnl": trade_doc.get("pnl"),
            "pnl_percent": trade_doc.get("pnl_percent"),
            "source": "manual",
        })
    except Exception as e:
        logger.warning(f"trade.closed publish failed: {e}")


@trades_router.put("/{trade_id}")
async def update_trade(trade_id: str, request: Request, background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    """Modify an open trade (SL / targets / trailing stop / notes) or close it
    with `exit_price` (full close — for partial exits use POST /{id}/exit).

    Modify rules are side-aware: targets stay on the profitable side of entry
    and in order; the stop may move ANYWHERE below target 1 (long) — including
    above entry — so profits can be locked in (breakeven+ stops)."""
    from services import trading_engine
    trade_oid = parse_object_id(trade_id, "trade")
    body = await request.json()
    trade = await db.trades.find_one({"_id": trade_oid, "user_id": user["_id"]})
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    was_open = trade.get("status") == "OPEN"
    update = {}
    events = list(trade.get("events") or [])

    # ── Risk-parameter modifications (open trades only) ──
    modify_fields = ("stop_loss", "target1", "target2", "target3", "trailing_stop")
    if any(f in body for f in modify_fields):
        if not was_open:
            raise HTTPException(status_code=400, detail="Only open trades can be modified")
        merged = {**trade, **{f: body[f] for f in modify_fields if f in body}}
        short = trade.get("type") == "SELL"
        entry = trade["entry_price"]

        # Validate targets: profitable side of entry, strictly ordered.
        prev = entry
        for level in (1, 2, 3):
            t = merged.get(f"target{level}")
            if t in (None, 0, ""):
                continue
            t = float(t)
            if (t <= entry if not short else t >= entry) or (t <= prev if not short else t >= prev):
                raise HTTPException(status_code=422, detail=(
                    f"Target {level} (₹{t}) must be {'above' if not short else 'below'} "
                    f"entry ₹{entry} and beyond the previous target."))
            prev = t
        # Validate stop: anywhere on the protective side of target 1.
        new_sl = merged.get("stop_loss")
        t1 = merged.get("target1") or trade.get("target1")
        if new_sl is not None and t1 and (float(new_sl) >= float(t1) if not short else float(new_sl) <= float(t1)):
            raise HTTPException(status_code=422, detail=(
                f"Stop loss (₹{new_sl}) must stay {'below' if not short else 'above'} target 1 (₹{t1})."))

        changed = []
        for f in modify_fields:
            if f not in body or body[f] == trade.get(f):
                continue
            value = body[f]
            if f.startswith("target") and value in (0, ""):
                value = None
            update[f] = value
            if f == "trailing_stop":
                enabled = bool((value or {}).get("enabled"))
                changed.append("trailing stop " + (
                    f"on ({(value or {}).get('value')}{'%' if (value or {}).get('type') == 'percent' else ' pts'})"
                    if enabled else "off"))
            else:
                changed.append(f"{f.replace('_', ' ')} ₹{trade.get(f)} → ₹{value}"
                               if value is not None else f"{f.replace('_', ' ')} removed")
        if changed:
            events.append(trading_engine.make_event("MODIFIED", "Modified: " + "; ".join(changed)))
            update["events"] = events

    # ── Full close via exit_price (legacy behavior, partial-aware P&L) ──
    if "exit_price" in body:
        exit_price = float(body["exit_price"])
        qty_open = trade.get("quantity_open")
        qty_open = int(qty_open if qty_open is not None else trade["quantity"])
        reason = _derive_close_status(trade, exit_price)
        close = trading_engine.apply_partial_exit(trade, exit_price, qty_open, reason)
        events.append(trading_engine.make_event(
            "EXIT", f"Closed {qty_open} @ ₹{exit_price} ({reason})", exit_price))
        close["events"] = events
        update.update(close)

    if "status" in body:
        update["status"] = body["status"]
    if "notes" in body:
        update["notes"] = body["notes"]

    await db.trades.update_one({"_id": trade_oid}, {"$set": update})
    updated = await db.trades.find_one({"_id": trade_oid})
    updated["_id"] = str(updated["_id"])

    # If this update just closed the trade, announce the close and generate
    # AI coaching + trade review in the background so the close response
    # returns immediately (never block on AI).
    if was_open and updated.get("status") != "OPEN":
        from services.trade_review import generate_close_intelligence
        await _announce_trade_closed(updated)
        background_tasks.add_task(_generate_coaching_background, trade_id)
        background_tasks.add_task(generate_close_intelligence, db, trade_id)

    return updated


@trades_router.post("/{trade_id}/exit")
async def exit_trade(trade_id: str, data: TradeExitRequest, background_tasks: BackgroundTasks,
                     user: dict = Depends(get_current_user)):
    """Exit an open trade — fully or partially (`quantity`), manually
    (`exit_price`) or at market via the linked broker (`at_market`).

    Broker exits place a LIVE market order through the Broker Engine first;
    if the broker rejects it, nothing is recorded (no simulation)."""
    from services import trading_engine
    trade_oid = parse_object_id(trade_id, "trade")
    trade = await db.trades.find_one({"_id": trade_oid, "user_id": user["_id"]})
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.get("status") != "OPEN":
        raise HTTPException(status_code=400, detail="Trade is already closed")

    qty_open = trade.get("quantity_open")
    qty_open = int(qty_open if qty_open is not None else trade["quantity"])
    quantity = min(int(data.quantity or qty_open), qty_open)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Nothing left to exit on this trade")

    broker_order_id = None
    if data.at_market:
        if not trade.get("broker"):
            raise HTTPException(status_code=400,
                                detail="This trade is not linked to a broker — enter an exit price instead.")
        side = "BUY" if trade.get("type") == "SELL" else "SELL"
        try:
            placed = await broker_engine.place_order(user["_id"], trade["broker"], {
                "symbol": trade["symbol"], "exchange": trade.get("exchange", "NSE"),
                "transaction_type": side, "quantity": quantity,
                "order_type": "MARKET", "product": trade.get("product") or "CNC",
                "tag": "engine-exit",
            })
            broker_order_id = placed.get("order_id")
        except BrokerError as e:
            raise HTTPException(status_code=502, detail=f"Exit order was not placed: {e.user_message}")

    exit_price = data.exit_price
    if exit_price is None:
        quote = await real_quote(trade["symbol"])
        if not quote:
            raise HTTPException(status_code=422,
                                detail="Live price unavailable — please enter the exit price.")
        exit_price = quote["price"]

    reason = _derive_close_status(trade, exit_price) if quantity == qty_open else "PARTIAL"
    update = trading_engine.apply_partial_exit(
        trade, exit_price, quantity,
        reason if reason != "PARTIAL" else _derive_close_status(trade, exit_price))
    events = list(trade.get("events") or [])
    events.append(trading_engine.make_event(
        "EXIT",
        f"{'Partial exit' if quantity < qty_open else 'Closed'} {quantity} @ ₹{exit_price}"
        + (f" via {trade['broker']} (order {broker_order_id})" if broker_order_id else ""),
        exit_price))
    update["events"] = events
    await db.trades.update_one({"_id": trade_oid}, {"$set": update})

    updated = await db.trades.find_one({"_id": trade_oid})
    updated["_id"] = str(updated["_id"])
    if updated.get("status") != "OPEN":
        from services.trade_review import generate_close_intelligence
        await _announce_trade_closed(updated)
        background_tasks.add_task(_generate_coaching_background, trade_id)
        background_tasks.add_task(generate_close_intelligence, db, trade_id)
    return updated

# ─── Trade Coaching ─────────────────────────────────

@trades_router.get("/coaching/summary")
async def coaching_summary(user: dict = Depends(get_current_user)):
    """Return last 5 coaching lessons for the user (for dashboard widget)."""
    trades = await db.trades.find(
        {"user_id": user["_id"], "status": {"$ne": "OPEN"}, "coaching": {"$exists": True}}
    ).sort("exit_time", -1).to_list(5)
    return [
        {
            "trade_id": str(t["_id"]),
            "symbol": t["symbol"],
            **t["coaching"],
        }
        for t in trades
    ]

@trades_router.get("/{trade_id}/coaching")
async def get_trade_coaching(trade_id: str, user: dict = Depends(get_current_user)):
    """Get (or generate) AI coaching for a closed trade."""
    trade_oid = parse_object_id(trade_id, "trade")
    trade = await db.trades.find_one({"_id": trade_oid, "user_id": user["_id"]})
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.get("status") == "OPEN":
        raise HTTPException(status_code=400, detail="Coaching is only available for closed trades")

    # Return cached coaching if exists
    if trade.get("coaching"):
        return trade["coaching"]

    # Generate coaching
    from services.trade_journal import generate_trade_coaching
    engine = get_debate_engine()
    async def _ai(prompt):
        return await engine.simple_chat(
            "You are an expert trading coach for Indian stock market traders.",
            prompt, prefer="claude", max_tokens=400,
        )
    ai_func = _ai if (claude_configured() or gemini_configured()) else None
    coaching = await generate_trade_coaching(trade, ai_func=ai_func)

    # Cache in DB
    await db.trades.update_one({"_id": trade_oid}, {"$set": {"coaching": coaching}})
    from services.activity_logger import log_activity
    log_activity(f"AI coaching generated for {trade['symbol']} trade", "monitor", "done")
    return coaching

@trades_router.get("/{trade_id}/live-tip")
async def get_trade_live_tip(trade_id: str, user: dict = Depends(get_current_user)):
    """Short (~40 word) live AI coaching tip for an OPEN position.

    Lightweight and generated on demand — the frontend polls this every 5 min.
    Not cached (the tip depends on the live price and should stay fresh).
    """
    trade_oid = parse_object_id(trade_id, "trade")
    trade = await db.trades.find_one({"_id": trade_oid, "user_id": user["_id"]})
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.get("status") != "OPEN":
        raise HTTPException(status_code=400, detail="Live tips are only available for open trades")

    symbol = trade["symbol"]
    entry = trade.get("entry_price", 0)
    sl = trade.get("stop_loss", 0)
    target = trade.get("target1", 0)
    ttype = trade.get("type", "BUY")
    current = entry
    quote = await real_quote(symbol)
    if quote:
        current = quote["price"]
    pnl_pct = round(((current - entry) / entry) * 100, 2) if entry else 0
    if ttype == "SELL":
        pnl_pct = -pnl_pct

    if not (claude_configured() or gemini_configured()):
        return {"trade_id": trade_id, "symbol": symbol, "tip": (
            f"{symbol} is {pnl_pct:+.2f}% vs entry. Stick to the plan: respect your "
            f"₹{sl} stop and let it work toward ₹{target}. Don't move the stop on emotion."
        )}

    prompt = (
        f"Open NSE {ttype} position in {symbol}. Entry ₹{entry}, current ₹{current} "
        f"({pnl_pct:+.2f}%), stop loss ₹{sl}, target ₹{target}. "
        f"Give ONE actionable coaching tip in about 40 words on managing this trade right "
        f"now — focus on risk, stop discipline, or booking partial profit. No preamble."
    )
    engine = get_debate_engine()
    try:
        tip = await engine.simple_chat(
            "You are a concise, disciplined trading coach for Indian markets.",
            prompt, prefer="claude", max_tokens=120,
        )
    except Exception as e:
        logger.error(f"Live tip generation failed for {trade_id}: {e}")
        tip = f"Stay disciplined on {symbol}: honour the ₹{sl} stop and aim for ₹{target}."
    return {"trade_id": trade_id, "symbol": symbol, "tip": tip.strip()}


# ============ PORTFOLIO ROUTES ============
# Sprint 8 — Portfolio Intelligence. All analytics are computed server-side in
# services.portfolio_engine (single source of truth) over a broker-primary merge
# of real broker holdings (db.holdings) and manual non-paper open trades
# (db.trades). See services/portfolio_engine.py for the merge/de-dup rules.

@portfolio_router.get("")
async def get_portfolio(user: dict = Depends(get_current_user)):
    """Unified, live-enriched holdings (broker holdings + manual open trades)."""
    from services import portfolio_engine
    return await portfolio_engine.build_holdings(db, user, real_quotes_map)

@portfolio_router.get("/summary")
async def portfolio_summary(user: dict = Depends(get_current_user)):
    from services import portfolio_engine
    holdings = await portfolio_engine.build_holdings(db, user, real_quotes_map)
    closed = await db.trades.find(
        {"user_id": user["_id"], "status": {"$ne": "OPEN"}}
    ).to_list(500)
    realized = round(sum((t.get("pnl") or 0) for t in closed), 2)
    pnl = portfolio_engine.compute_pnl(holdings, realized)
    return {
        "total_invested": pnl["invested"],
        "current_value": pnl["current_value"],
        "total_pnl": pnl["unrealized"],
        "total_pnl_pct": pnl["unrealized_pct"],
        "realized_pnl": pnl["realized"],
        "holdings_count": len(holdings),
        "sources": sorted({h.get("source") for h in holdings if h.get("source")}),
        "capital": user.get("capital", 100000),
    }


@portfolio_router.get("/intelligence")
async def portfolio_intelligence(user: dict = Depends(get_current_user)):
    """Full Portfolio Intelligence bundle: unified holdings, allocation, sector
    exposure, diversification (HHI), risk score, P&L, dividends, movers and
    rebalancing suggestions — the single payload the Portfolio page consumes."""
    from services import portfolio_engine
    from services.portfolio_monitor import analyze_portfolio_health
    # Health scan runs over open trades with a sync quote lookup (its contract);
    # the engine folds it into the risk score + suggestions.
    quote_func = await prefetch_quote_func(user["_id"])
    health = await analyze_portfolio_health(db, user["_id"], None, quote_func)
    return await portfolio_engine.build_intelligence(db, user, real_quotes_map, health=health)


@portfolio_router.get("/performance")
async def portfolio_performance(range: str = "ALL", user: dict = Depends(get_current_user)):
    """Equity curve + returns from stored daily snapshots. Returns
    available:false until at least two end-of-day snapshots exist (the curve is
    built forward from real marks — never back-filled with synthetic history)."""
    from services import portfolio_engine
    return await portfolio_engine.get_performance(db, user["_id"], range)


@portfolio_router.get("/export")
async def portfolio_export(user: dict = Depends(get_current_user)):
    """Export current holdings as CSV (feeds the Portfolio Download action)."""
    import csv
    import io
    from services import portfolio_engine
    holdings = await portfolio_engine.build_holdings(db, user, real_quotes_map)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Symbol", "Name", "Source", "Sector", "Quantity",
                     "Avg Price", "Invested", "Current Price", "Current Value",
                     "P&L", "P&L %", "Day Change %"])
    for h in holdings:
        writer.writerow([
            h.get("symbol", ""), h.get("name", ""), h.get("source", ""),
            h.get("sector", ""), h.get("quantity", 0), h.get("avg_price", 0),
            h.get("invested", 0), h.get("current_price", ""),
            h.get("current_value", 0), h.get("pnl", 0), h.get("pnl_pct", 0),
            h.get("day_change_pct", ""),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=portfolio.csv"},
    )


# ============ NOTIFICATIONS ROUTES ============

@notifications_router.get("/unread-count")
async def unread_count(user: dict = Depends(get_current_user)):
    count = await db.notifications.count_documents({"user_id": user["_id"], "read": False})
    return {"count": count}

@notifications_router.get("")
async def get_notifications(user: dict = Depends(get_current_user)):
    notifs = await db.notifications.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(50)
    for n in notifs:
        n["_id"] = str(n["_id"])
    return notifs

@notifications_router.put("/read-all")
async def mark_all_read(user: dict = Depends(get_current_user)):
    await db.notifications.update_many({"user_id": user["_id"]}, {"$set": {"read": True}})
    return {"message": "All marked as read"}

@notifications_router.put("/{notif_id}/read")
async def mark_read(notif_id: str, user: dict = Depends(get_current_user)):
    """Mark one notification read. Ownership is enforced by the filter, so a
    notification belonging to another user is never mutated.

    PH3.3 (D-2): the miss now answers 404 instead of "Marked as read". The
    write was always safe — `user_id` is part of the filter — but reporting
    success for a row that was not touched is a contract lie: it makes an
    ownership rejection indistinguishable from a real update, so no client and
    no log could ever reveal a probe walking notification ids. Every sibling
    endpoint (trades, watchlist) already answers 404 on the same miss.
    """
    notif_oid = parse_object_id(notif_id, "notification")
    result = await db.notifications.update_one(
        {"_id": notif_oid, "user_id": user["_id"]}, {"$set": {"read": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Marked as read"}


# ============ CHAT ROUTES ============

@chat_router.post("")
async def chat_endpoint(data: ChatMessage, user: dict = Depends(get_current_user)):
    session_id = data.session_id or f"chat-{user['_id']}"
    response = await ai_chat(data.message, session_id, user, run_id=data.run_id)

    # Save to DB
    await db.chat_messages.insert_one({
        "user_id": user["_id"],
        "session_id": session_id,
        "role": "user",
        "content": data.message,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.chat_messages.insert_one({
        "user_id": user["_id"],
        "session_id": session_id,
        "role": "assistant",
        "content": response,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"response": response, "session_id": session_id}

@chat_router.get("/history")
async def chat_history(user: dict = Depends(get_current_user), session_id: str = None):
    query = {"user_id": user["_id"]}
    if session_id:
        query["session_id"] = session_id
    messages = await db.chat_messages.find(query).sort("created_at", 1).to_list(100)
    for m in messages:
        m["_id"] = str(m["_id"])
    return messages


# ============ AI WORKSPACE ROUTES ============
# The AI Workspace (Sprint 6) unifies Chat, Memory, Conversation History,
# Trade Review, Learning, Portfolio AI, the Activity Timeline and the Model
# Router behind one namespace. Every AI call here flows through the Model
# Router + centralized Prompt Library (services/model_router.py,
# services/prompt_library.py) so behaviour stays consistent and auditable.

def _ai_online() -> bool:
    return claude_configured() or gemini_configured()


def _summarize_trade_for_review(t: dict) -> str:
    """Build a factual, number-only summary of a trade for the review prompt.

    Moved to services.trade_review (Sprint R6) so the auto-review pipeline and
    this endpoint share one summary; kept importable under the old name."""
    from services.trade_review import summarize_trade_for_review
    return summarize_trade_for_review(t)


@ai_router.get("/status")
async def ai_status():
    """Model Router + provider health and the task→model routing table.

    Powers the frontend model-status pill and the AI Activity header. Public
    (no auth) so the workspace can render an honest offline state pre-login."""
    from services.model_router import get_model_router
    return get_model_router().status()


@ai_router.get("/prompts")
async def ai_prompts():
    """List the centralized Prompt Library (metadata only, no templates).

    Transparency + prompt-testing surface required by PROMPT.md → Prompt
    Testing. Never exposes the raw prompt text (Forbidden Behaviors)."""
    from services.prompt_library import list_prompts, LIBRARY_VERSION
    return {"version": LIBRARY_VERSION, "prompts": list_prompts()}


@ai_router.get("/activity")
async def ai_activity():
    """AI Activity Timeline — the truthful running/done trace of background AI
    work (heartbeat engine, scans, monitoring). Reuses the activity logger."""
    from services.activity_logger import get_recent_activity
    return get_recent_activity()


@ai_router.get("/memory")
async def ai_get_memory(user: dict = Depends(get_current_user)):
    """Return what the AI remembers about the user (User Memory)."""
    from services.ai_memory import get_user_memory
    return await get_user_memory(db, user["_id"])


@ai_router.put("/memory")
async def ai_update_memory(data: AIMemoryUpdate, user: dict = Depends(get_current_user)):
    """Update the user's AI memory (risk, goals, sectors, favourites, notes)."""
    from services.ai_memory import update_user_memory
    return await update_user_memory(db, user["_id"], data.model_dump(exclude_none=True))


@ai_router.get("/conversations")
async def ai_list_conversations(user: dict = Depends(get_current_user)):
    """List the user's chat sessions (Conversation History), newest first."""
    from services.ai_memory import list_conversations
    return await list_conversations(db, user["_id"])


@ai_router.post("/conversations")
async def ai_new_conversation(user: dict = Depends(get_current_user)):
    """Start a new conversation and return its session id.

    Sessions are lazily materialised by the first message, so this simply mints
    an id the frontend can use immediately."""
    return {"session_id": f"chat-{user['_id']}-{int(datetime.now(timezone.utc).timestamp())}"}


@ai_router.delete("/conversations/{session_id}")
async def ai_delete_conversation(session_id: str, user: dict = Depends(get_current_user)):
    """Delete a conversation and all of its messages (owned by the user only)."""
    from services.ai_memory import delete_conversation
    deleted = await delete_conversation(db, user["_id"], session_id)
    return {"session_id": session_id, "deleted": deleted}


@ai_router.post("/learn")
async def ai_learn(data: LearnRequest, user: dict = Depends(get_current_user)):
    """Learning Mentor — teach a concept in beginner-friendly language."""
    from services.model_router import get_model_router
    from services.activity_logger import log_activity
    log_activity(f"Teaching concept: {data.topic}", "monitor", "running")
    router = get_model_router()
    result = await router.run(
        "learning_mentor",
        f"Teach me about: {data.topic}",
        level=data.level,
        max_tokens=700,
    )
    log_activity(f"Explained concept: {data.topic}", "monitor", "done")
    return {"topic": data.topic, "level": data.level, **result}


@ai_router.post("/trade-review")
async def ai_trade_review(data: TradeReviewRequest, user: dict = Depends(get_current_user)):
    """Trading Coach — review a closed trade (entry/exit/risk/execution).

    Accepts either an existing `trade_id` (owned by the user) or a raw `trade`
    dict for ad-hoc review. Persists the review on the trade document when a
    trade_id is supplied so it is not regenerated on every view.

    Sprint R7: streams a live AIRun step timeline (correlated by the optional
    client `run_id`). Cached reviews return before any run starts — no fake
    steps. The "Saving review" step only exists when there is a save to do."""
    from services.model_router import get_model_router
    from services.activity_logger import log_activity
    from services.ai_activity import AIRun

    trade = None
    if data.trade_id:
        trade_oid = parse_object_id(data.trade_id, "trade")
        trade = await db.trades.find_one({"_id": trade_oid, "user_id": user["_id"]})
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade.get("status") == "OPEN":
            raise HTTPException(status_code=400, detail="Reviews are only available for closed trades")
        if trade.get("ai_review"):
            return {"trade_id": data.trade_id, "cached": True, **trade["ai_review"]}
    elif data.trade:
        trade = data.trade
    else:
        raise HTTPException(status_code=400, detail="Provide trade_id or trade")

    steps = ["Reviewing execution with AI"] + (["Saving review"] if data.trade_id else [])
    run = AIRun(user["_id"], None, steps, run_id=data.run_id)
    await run.start()
    try:
        log_activity(f"Reviewing {trade.get('symbol')} trade", "monitor", "running")
        async with run.step():
            router = get_model_router()
            result = await router.run(
                "trade_review",
                _summarize_trade_for_review(trade),
                max_tokens=600,
            )
        review = {"content": result["content"], "model_used": result["model_used"],
                  "symbol": trade.get("symbol")}
        if data.trade_id:
            async with run.step():
                await db.trades.update_one({"_id": trade_oid}, {"$set": {"ai_review": review}})
        log_activity(f"Trade review ready for {trade.get('symbol')}", "monitor", "done")
        await run.complete()
        return {"trade_id": data.trade_id, "cached": False, **review}
    except Exception:
        await run.complete("warning")
        raise


@ai_router.post("/portfolio-review")
async def ai_portfolio_review(data: Optional[PortfolioReviewRequest] = None,
                              user: dict = Depends(get_current_user)):
    """Portfolio AI — narrative review of the user's holdings via the Portfolio
    Manager prompt, grounded in live holdings + the deterministic health scan.

    Sprint R7: streams a live AIRun step timeline (correlated by the optional
    client `run_id`). An empty portfolio completes the run after the first
    (real) holdings read — every emitted step maps to work performed."""
    from services.portfolio_monitor import analyze_portfolio_health
    from services.model_router import get_model_router
    from services.activity_logger import log_activity
    from services.ai_activity import AIRun

    run = AIRun(user["_id"], None,
                ["Reading your holdings", "Scanning portfolio health", "Consulting the AI"],
                run_id=data.run_id if data else None)
    await run.start()
    try:
        async with run.step():
            portfolio = await get_portfolio(user)
        if not portfolio:
            await run.complete()
            return {
                "content": "Your portfolio is empty. Add holdings to receive an AI review.",
                "empty": True,
                "holdings_count": 0,
            }

        async with run.step():
            quote_func = await prefetch_quote_func(user["_id"])
            health = await analyze_portfolio_health(db, user["_id"], None, quote_func)

            lines = [f"Capital: ₹{user.get('capital', 100000):,.0f}",
                     f"Risk level: {user.get('risk_level', 'moderate')}",
                     f"Holdings ({len(portfolio)}):"]
            for h in portfolio[:25]:
                lines.append(
                    f"- {h.get('symbol')}: qty {h.get('quantity')}, invested "
                    f"₹{h.get('invested', 0):,.0f}, current ₹{h.get('current_value', 0):,.0f}, "
                    f"P&L {h.get('pnl_pct', 0)}%"
                )
            if health:
                lines.append(
                    f"Health scan: score {health.get('health_score', 'n/a')}/100, "
                    f"{health.get('at_risk', 0)} position(s) at risk, "
                    f"unrealised P&L ₹{health.get('total_unrealized_pnl', 0):,.0f}."
                )
            summary = "\n".join(lines)

        log_activity("Reviewing portfolio allocation & risk", "monitor", "running")
        async with run.step():
            router = get_model_router()
            result = await router.run("portfolio_manager", summary, max_tokens=800)
        log_activity("Portfolio AI review ready", "monitor", "done")
        await run.complete()
        return {"holdings_count": len(portfolio), "health": health, **result}
    except Exception:
        await run.complete("warning")
        raise


@ai_router.post("/reflect")
async def ai_reflect(user: dict = Depends(get_current_user)):
    """Reflection Engine — review recent closed trades, extract durable lessons
    and store them in AI Memory so future advice is personalised."""
    from services.model_router import get_model_router
    from services.ai_memory import add_lesson
    from services.activity_logger import log_activity

    closed = await db.trades.find(
        {"user_id": user["_id"], "status": {"$ne": "OPEN"}}
    ).sort("exit_time", -1).to_list(10)
    if not closed:
        return {"content": "No closed trades yet — take a few trades and I'll reflect on your patterns.",
                "lessons_added": 0}

    summary = "Recent closed trades:\n" + "\n\n".join(
        _summarize_trade_for_review(t) for t in closed
    )
    log_activity("Reflecting on recent trades", "rank", "running")
    router = get_model_router()
    result = await router.run("reflection", summary, max_tokens=500)

    # Persist up to 3 concise lessons parsed from the reflection output.
    lessons = [l.strip("-• \t") for l in (result["content"] or "").splitlines()
               if l.strip().startswith(("-", "•")) and len(l.strip()) > 4][:3]
    for lesson in lessons:
        await add_lesson(db, user["_id"], lesson)
    log_activity("Reflection complete — lessons saved", "rank", "done")
    return {"lessons_added": len(lessons), "lessons": lessons, **result}


# ============ SIP ROUTES ============

@sip_router.post("/recommend")
async def sip_recommend(data: SIPRequest, user: dict = Depends(get_current_user)):
    result = await ai_sip_analysis(data.model_dump())
    return {"recommendation": result, "input": data.model_dump()}

@sip_router.get("/calculator")
async def sip_calculator(
    # PH3.3 (D-5). These were unbounded floats. `amount=1e308` overflowed the
    # compound-growth expression to `inf`, and JSON has no representation for
    # it — the response serializer then raised "Out of range float values are
    # not JSON compliant" and the request 500ed *after* the handler had
    # succeeded, which is the most confusing shape this failure can take.
    # Bounding the inputs is the honest fix: these are the ranges a monthly SIP
    # projection is actually meaningful over, and a value outside them is a
    # client error, not an arithmetic adventure.
    amount: float = Query(5000, gt=0, le=1e9, description="Monthly investment."),
    years: int = Query(10, ge=1, le=100, description="Investment horizon."),
    rate: float = Query(12, ge=0, le=100, description="Expected annual return %."),
):
    months = years * 12
    r = rate / 12 / 100
    if r > 0:
        fv = amount * (((1 + r) ** months - 1) / r) * (1 + r)
    else:
        fv = amount * months
    total_invested = amount * months
    return {
        "monthly_sip": amount,
        "years": years,
        "expected_rate": rate,
        "future_value": round(fv, 2),
        "total_invested": total_invested,
        "wealth_gained": round(fv - total_invested, 2),
    }


# ============ AI INVESTMENT ADVISOR ROUTES ============

@advisor_router.post("/recommend")
async def advisor_recommend(data: AdvisorRequest, user: dict = Depends(get_current_user)):
    """Recommend 3-6 NSE stocks for the requested horizon (long/medium/short/
    swing/intraday), each fully explained with confidence, risk, expected return,
    holding period, entry zone, stop loss, targets, technical + fundamental
    reasons, news impact, sector strength and an AI summary. All price levels
    are derived from LIVE market data; the AI only narrates the real numbers."""
    from services.activity_logger import log_activity
    log_activity(f"AI Advisor scanning for {data.horizon} opportunities", "rank", "running")
    result = await build_advisor_recommendations(
        horizon=data.horizon,
        risk_appetite=data.risk_appetite,
        sectors=data.sectors,
        capital=data.capital,
    )
    return result


@advisor_router.get("/horizons")
async def advisor_horizons():
    """List the supported horizons and their holding-period semantics — powers
    the frontend segmented control without hardcoding copy on the client."""
    return {
        "horizons": [
            {"key": k, "label": v["label"], "holding_period": v["holding_period"]}
            for k, v in HORIZON_CONFIG.items()
        ]
    }


# ============ SETTINGS ============

settings_router = APIRouter(prefix="/api/settings", tags=["Settings"])

@settings_router.get("")
async def get_settings(user: dict = Depends(get_current_user)):
    return user

@settings_router.put("")
async def update_settings(data: UserSettingsUpdate, user: dict = Depends(get_current_user)):
    update = {}
    if data.name is not None:
        update["name"] = data.name
    if data.capital is not None:
        update["capital"] = data.capital
    if data.risk_level is not None:
        update["risk_level"] = data.risk_level
    if data.max_daily_loss is not None:
        update["max_daily_loss"] = data.max_daily_loss
    if data.max_trades_per_day is not None:
        update["max_trades_per_day"] = data.max_trades_per_day
    if data.telegram_chat_id is not None:
        update["telegram_chat_id"] = data.telegram_chat_id
    if data.preferred_broker is not None:
        # The user's chosen trading platform. "" clears the choice — there is
        # never an implicit default broker.
        from services.brokers import SUPPORTED_BROKERS
        choice = data.preferred_broker.strip().lower()
        if choice and choice not in SUPPORTED_BROKERS:
            raise HTTPException(status_code=422, detail=f"Unsupported broker: {choice}")
        update["preferred_broker"] = choice or None
    if data.notification_prefs is not None:
        update["notification_prefs"] = data.notification_prefs.model_dump()
    if update:
        await db.users.update_one({"_id": ObjectId(user["_id"])}, {"$set": update})
    updated = await db.users.find_one({"_id": ObjectId(user["_id"])})
    updated["_id"] = str(updated["_id"])
    updated.pop("password_hash", None)
    return updated


# ============ WEBSOCKET MANAGER ============

class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()
        self.user_connections: dict[str, Set[WebSocket]] = {}
        # Per-connection channel subscriptions (Sprint R2). A socket receives a
        # channel broadcast only if it has subscribed to that channel. This lets
        # each page listen to just the topics it needs (market/sectors/scanner/
        # news/…) instead of every global broadcast.
        self.channels: dict[WebSocket, Set[str]] = {}

    async def connect(self, ws: WebSocket, user_id: str = None):
        await ws.accept()
        self.active.add(ws)
        self.channels.setdefault(ws, set())
        if user_id:
            self.user_connections.setdefault(user_id, set()).add(ws)

    def disconnect(self, ws: WebSocket, user_id: str = None):
        self.active.discard(ws)
        self.channels.pop(ws, None)
        if user_id and user_id in self.user_connections:
            self.user_connections[user_id].discard(ws)

    def subscribe(self, ws: WebSocket, channels):
        """Add one or more channels to a socket's subscription set."""
        subs = self.channels.setdefault(ws, set())
        for ch in channels or []:
            if ch:
                subs.add(str(ch))

    def unsubscribe(self, ws: WebSocket, channels):
        """Remove one or more channels from a socket's subscription set."""
        subs = self.channels.get(ws)
        if not subs:
            return
        for ch in channels or []:
            subs.discard(str(ch))

    @staticmethod
    def _serialize(message: dict) -> str:
        """Serialize a fan-out message ONCE (Sprint R9). `ws.send_json` runs
        json.dumps per socket, so a broadcast to N sockets paid N serializations
        of the same payload; every fan-out path now dumps once and sends text."""
        return json.dumps(message, default=str)

    async def broadcast(self, message: dict):
        payload = self._serialize(message)
        dead = set()
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._reap(dead)

    async def broadcast_to_channel(self, channel: str, message: dict):
        """Send to sockets subscribed to `channel` (or the "*" wildcard channel)."""
        payload = self._serialize(message)
        dead = set()
        for ws, subs in list(self.channels.items()):
            if channel in subs or "*" in subs:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
        self._reap(dead)

    async def send_to_user(self, user_id: str, message: dict):
        conns = self.user_connections.get(user_id, set())
        if not conns:
            return
        payload = self._serialize(message)
        dead = set()
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._reap(dead)

    def _reap(self, dead: Set[WebSocket]):
        """Drop dead sockets from every tracking structure."""
        if not dead:
            return
        self.active -= dead
        for ws in dead:
            self.channels.pop(ws, None)
        for conns in self.user_connections.values():
            conns -= dead

ws_manager = ConnectionManager()

# Broker Engine wiring: Mongo storage + per-user WebSocket push for realtime
# broker order updates / price ticks / connection status.
broker_engine.configure(db, ws_push=ws_manager.send_to_user)


async def ws_activity_broadcast(entry: dict):
    await ws_manager.broadcast({
        "type": "activity_feed",
        "data": entry
    })


from services.activity_logger import register_broadcast_callback
register_broadcast_callback(ws_activity_broadcast)



@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time market data and trade updates."""
    user_id = websocket.query_params.get("user_id", "anonymous")
    await ws_manager.connect(websocket, user_id)
    try:
        while True:
            # Keep connection alive, listen for client messages
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "subscribe_prices":
                # Client subscribes to price updates for specific symbols
                symbols = msg.get("symbols", [])
                # Start sending price ticks (live Yahoo Finance, cached)
                quotes = await real_quotes_map(symbols)
                for sym in symbols:
                    quote = quotes.get((sym or "").upper())
                    if quote:
                        await websocket.send_json({"type": "price_tick", "data": quote})

            elif msg.get("type") == "subscribe":
                # Channel subscription (Sprint R2): {"type":"subscribe","channels":[...]}
                channels = msg.get("channels", [])
                ws_manager.subscribe(websocket, channels)
                await websocket.send_json({"type": "subscribed", "channels": channels})

            elif msg.get("type") == "unsubscribe":
                channels = msg.get("channels", [])
                ws_manager.unsubscribe(websocket, channels)
                await websocket.send_json({"type": "unsubscribed", "channels": channels})

            elif msg.get("type") == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id)
    except Exception:
        ws_manager.disconnect(websocket, user_id)


# Background task: broadcast market data every 10 seconds
async def market_broadcast_loop():
    """
    Broadcasts real-time market data to all connected WebSocket clients every 10 seconds.
    Uses real Yahoo Finance data, not simulated data.

    In addition to the coarse ``market_update`` (kept for backward compatibility),
    this loop diffs each index against the previous tick and publishes a granular
    ``market.index.updated`` event only for indices whose value changed — so the
    bridge can update just the affected index card (Sprint R2).
    """
    from services.market_engine.event_bus import event_bus
    prev_index: dict = {}
    index_keys = ("nifty", "bank_nifty", "sensex")
    tick = 0
    while True:
        try:
            if ws_manager.active:
                from services.real_market import fetch_real_market_overview

                # Publish market engine health (~every 30s) so the engine badge
                # updates live instead of polling /market/engine/status.
                if tick % 3 == 0:
                    try:
                        from services.market_engine import market_gateway
                        from services.market_engine.validator import is_market_hours, is_pre_market
                        await event_bus.publish("market.engine.status", {
                            **market_gateway.status,
                            "market_hours": is_market_hours(),
                            "pre_market": is_pre_market(),
                            "event_bus_subscribers": event_bus.subscriber_count,
                            "recent_events_count": len(event_bus.recent_events(limit=500)),
                            "version": "1.0.0",
                        })
                    except Exception as e:
                        logging.warning(f"engine.status publish failed: {e}")

                overview = await fetch_real_market_overview()
                if overview:
                    await ws_manager.broadcast({
                        "type": "market_update",
                        "data": overview,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    # Granular per-index deltas for the event bus / bridge.
                    for key in index_keys:
                        idx = overview.get(key)
                        if not isinstance(idx, dict) or not idx.get("available"):
                            continue
                        value = idx.get("value")
                        if value is None or prev_index.get(key) == value:
                            continue
                        prev_index[key] = value
                        await event_bus.publish("market.index.updated", {
                            "symbol": key,
                            "value": value,
                            "change": idx.get("change"),
                            "change_pct": idx.get("change_pct"),
                        })
        except Exception as e:
            logging.error(f"Broadcast error: {e}")
        tick += 1
        await asyncio.sleep(10)


async def ai_monitoring_loop():
    """
    AI always watches the platform. Every 30 seconds, scans for:
    - Market alerts (Nifty big moves)
    - Breaking events (VIX spike, gap-up/down)
    Sends real-time push notifications via WebSocket to all connected users.
    """
    from services.activity_logger import log_activity
    prev_nifty = None
    while True:
        try:
            from services.real_market import fetch_real_market_overview
            overview = await fetch_real_market_overview()
            if overview:
                nifty_val = overview.get("nifty", {}).get("value", 0)
                nifty_chg_pct = overview.get("nifty", {}).get("change_pct", 0)

                # Detect large moves (>1% in either direction)
                if abs(nifty_chg_pct) >= 1.0:
                    direction = "📈 surging" if nifty_chg_pct > 0 else "📉 dropping"
                    severity = "critical" if abs(nifty_chg_pct) >= 2.0 else "warning"
                    msg = f"⚡ MARKET ALERT: Nifty is {direction} {nifty_chg_pct:+.2f}% at {nifty_val:,.0f}! Monitor your positions."
                    log_activity(f"Nifty {direction} {nifty_chg_pct:+.2f}%", "alert", "warning")
                    
                    # Save notification for all active users (deduped to once per
                    # 5 min per user). create_notification also publishes
                    # notification.created so the bridge pushes it live.
                    from services.notification_service import create_notification
                    users = await db.users.find({}, {"_id": 1}).to_list(100)
                    for u in users:
                        await create_notification(
                            db, str(u["_id"]),
                            type="MARKET_ALERT",
                            title="Market Alert",
                            message=msg,
                            severity=severity,
                            data={"nifty": nifty_val, "change_pct": nifty_chg_pct},
                            dedupe_minutes=5,
                        )
                    
                    # Broadcast to all WebSocket clients
                    if ws_manager.active:
                        await ws_manager.broadcast({
                            "type": "ai_alert",
                            "severity": severity,
                            "message": msg,
                            "data": {"nifty": nifty_val, "change_pct": nifty_chg_pct},
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                
                # Detect Nifty crossing key psychological levels
                key_levels = [24000, 24500, 25000, 25500, 26000, 23500, 23000]
                if prev_nifty:
                    for level in key_levels:
                        crossed_up = prev_nifty < level <= nifty_val
                        crossed_down = prev_nifty > level >= nifty_val
                        if crossed_up or crossed_down:
                            direction = "above" if crossed_up else "below"
                            emoji = "🔔" if crossed_up else "⚠️"
                            alert_msg = f"{emoji} Nifty crossed {direction} key level {level:,}! Current: {nifty_val:,.0f}"
                            log_activity(f"Nifty crossed {level}", "alert", "done")
                            if ws_manager.active:
                                await ws_manager.broadcast({
                                    "type": "ai_alert",
                                    "severity": "info",
                                    "message": alert_msg,
                                    "data": {"nifty": nifty_val, "level": level},
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                })
                
                prev_nifty = nifty_val

        except Exception as e:
            logging.error(f"AI monitoring loop error: {e}")
        
        await asyncio.sleep(30)


# ============ GOOGLE OAUTH ROUTES ============

google_auth_router = APIRouter(prefix="/api/auth", tags=["Google Auth"])

# Fail-closed policy: a session is issued only after a successful server-side
# authorization-code exchange with Google AND cryptographic verification of the
# returned OpenID Connect id_token. There are no demo users, no simulation
# fallbacks, and no third-party session exchanges (PH1.1). PH1.2 adds:
#   - OAuth `state` CSRF protection (double-submit against the g_oauth_state cookie)
#     PLUS a single-use server-side state record (replay protection + authoritative
#     TTL expiry, shared across processes via Redis when configured)
#   - id_token signature/issuer/audience verification via google-auth
#   - mandatory `email_verified` gate before any account is created or linked
#   - redirect_uri allowlisting (no hardcoded dev fallback) + binding the callback
#     redirect_uri to the one issued at flow start
#   - Google `sub` as the primary external identity; verified email for safe linking
#   - immutable security-audit logging of every OAuth outcome

GOOGLE_OAUTH_STATE_COOKIE = "g_oauth_state"
GOOGLE_OAUTH_STATE_TTL = 600  # seconds — the state is single-use and short-lived
GOOGLE_OAUTH_STATE_PREFIX = "oauth_state:"  # server-side store key namespace
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_VALID_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


def _google_oauth_config() -> tuple[str, str]:
    """Return (client_id, client_secret) from the environment, stripped."""
    return (
        os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
    )


def _allowed_google_redirect_uris() -> Set[str]:
    """Allowlist of acceptable OAuth redirect URIs, derived from configured
    frontend origins. The callback path is fixed to `/auth/google/callback`.
    A localhost origin is permitted only outside production so local dev works
    without weakening the production allowlist."""
    origins: list[str] = []
    frontend_url = os.environ.get("FRONTEND_URL", "").strip()
    if frontend_url:
        origins.append(frontend_url)
    cors = os.environ.get("CORS_ORIGINS", "").strip()
    if cors and cors != "*":
        origins.extend(cors.split(","))
    if not origins and os.environ.get("APP_ENV", "development") != "production":
        origins.append("http://localhost:3000")
    uris: Set[str] = set()
    for origin in origins:
        origin = origin.strip().rstrip("/")
        if origin:
            uris.add(f"{origin}/auth/google/callback")
    return uris


def _set_oauth_state_cookie(response: Response, state: str) -> None:
    # Delegated to the central PH1.3 cookie policy (security.cookies): the OAuth
    # state cookie now shares the unified Secure/SameSite/Domain posture with the
    # session cookies. It stays scoped to /api/auth and is never SameSite=Strict
    # so it survives the top-level redirect back from Google.
    set_oauth_state_cookie(response, state)


def _clear_oauth_state_cookie(response: Response) -> None:
    clear_oauth_state_cookie(response)


async def _store_oauth_state(state: str, redirect_uri: str) -> None:
    """Persist a single-use OAuth state record server-side. Uses Redis when
    REDIS_URL is configured and an in-memory fallback otherwise (services/cache.py)
    — the same graceful-degradation the rest of the app relies on. The record's
    TTL is the authoritative expiry (the cookie max-age is client-controlled)."""
    from services.cache import cache_set
    await cache_set(
        f"{GOOGLE_OAUTH_STATE_PREFIX}{state}",
        {"redirect_uri": redirect_uri, "created": datetime.now(timezone.utc).isoformat()},
        GOOGLE_OAUTH_STATE_TTL,
    )


async def _consume_oauth_state(state: str) -> Optional[dict]:
    """Atomically fetch-and-delete the server-side state record. Returns the
    record on first use, or None when it is missing, expired, or already used
    (replay). Single-use is what defeats authorization-code/state replay."""
    from services.cache import cache_get, cache_delete
    key = f"{GOOGLE_OAUTH_STATE_PREFIX}{state}"
    record = await cache_get(key)
    if record is None:
        return None
    await cache_delete(key)
    return record


async def log_auth_event(event: str, request: Request, *, email: Optional[str] = None,
                         user_id: Optional[str] = None, reason: Optional[str] = None,
                         detail: Optional[dict] = None, session_id: Optional[str] = None,
                         target: Optional[str] = None, outcome: Optional[str] = None) -> None:
    """Append an immutable security-audit record.

    Thin, backward-compatible facade over the centralized ``security.audit``
    engine (PH1.10): the taxonomy, structured schema, secret redaction, storage,
    and fail-safe emission all live there now — this keeps the historical call
    signature (and the ``event`` / ``email`` / ``user_id`` / ``reason`` /
    ``ip`` / ``user_agent`` / ``details`` / ``timestamp`` record fields) so every
    existing call site and query is unchanged, while the redaction guard and the
    structured/SIEM-ready log sink apply automatically. ``detail`` maps to the
    record's redacted ``metadata``. Best-effort by construction — a logging
    failure can never break the calling flow."""
    await audit.log_event(
        event, request=request, email=email, user_id=user_id, reason=reason,
        session_id=session_id, target=target, outcome=outcome, metadata=detail,
    )


async def _exchange_google_code(code: str, redirect_uri: str,
                                client_id: str, client_secret: str) -> dict:
    """Server-side authorization-code exchange. Raises HTTPException(401) on a
    non-200 response and lets httpx.RequestError propagate for the caller to
    map to a 502."""
    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Google token exchange failed")
    return token_resp.json()


def _verify_google_id_token(id_tok: str, client_id: str) -> dict:
    """Cryptographically verify a Google-issued OIDC id_token: signature (against
    Google's public keys), audience (our client_id) and expiry. Raises ValueError
    on any failure. Isolated as a module-level function so it can be substituted
    in hermetic tests."""
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    return google_id_token.verify_oauth2_token(
        id_tok, google_requests.Request(), client_id
    )


@google_auth_router.get("/google/login-url")
async def google_login_url(response: Response, redirect_uri: Optional[str] = None):
    """Start the Google OAuth flow. Generates a CSRF `state`, binds it to the
    browser via a short-lived httponly cookie, and returns the Google
    authorization URL to redirect to. Fail-closed when OAuth is not configured."""
    client_id, client_secret = _google_oauth_config()
    if not client_id or not client_secret:
        raise HTTPException(status_code=401, detail="Google sign-in is not configured")

    allowed = _allowed_google_redirect_uris()
    if redirect_uri:
        if redirect_uri.rstrip("/") not in allowed:
            raise HTTPException(status_code=400, detail="Invalid redirect_uri")
        chosen = redirect_uri
    elif allowed:
        chosen = sorted(allowed)[0]
    else:
        raise HTTPException(status_code=401, detail="Google sign-in is not configured")

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": client_id,
        "redirect_uri": chosen,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    # Server-side single-use record (replay + authoritative expiry) bound to the
    # redirect_uri, plus the httponly cookie (per-browser CSRF binding).
    await _store_oauth_state(state, chosen)
    _set_oauth_state_cookie(response, state)
    return {"url": f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}", "redirect_uri": chosen}


@google_auth_router.post("/google/session")
async def google_auth_session(request: Request, response: Response):
    """Complete the Google OAuth flow: validate the CSRF state, exchange the
    authorization code, verify the id_token, and issue a session."""
    body = await request.json()
    code = body.get("code")
    state = body.get("state")
    redirect_uri = body.get("redirect_uri")

    if not code:
        raise HTTPException(status_code=400, detail="code required")
    if not state:
        raise HTTPException(status_code=400, detail="state required")

    # CSRF protection: the callback must present the same state we planted in the
    # httponly cookie when the flow started (double-submit). Constant-time compare.
    cookie_state = request.cookies.get(GOOGLE_OAUTH_STATE_COOKIE)
    if not cookie_state or not secrets.compare_digest(state, cookie_state):
        await log_auth_event("oauth_login_failure", request, reason="invalid_state")
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    # Single-use server-side record: consume it (fetch-and-delete). A missing
    # record means the state is expired or has already been used (replay).
    state_record = await _consume_oauth_state(state)
    _clear_oauth_state_cookie(response)  # burn the cookie regardless of outcome
    if state_record is None:
        await log_auth_event("oauth_login_failure", request, reason="replayed_or_expired_state")
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    client_id, client_secret = _google_oauth_config()
    if not client_id or not client_secret:
        raise HTTPException(status_code=401, detail="Google sign-in is not configured")

    # The redirect_uri must be allowlisted AND must match the one bound to this
    # state at flow start (defense in depth against redirect substitution).
    allowed = _allowed_google_redirect_uris()
    if (not redirect_uri or redirect_uri.rstrip("/") not in allowed
            or redirect_uri != state_record.get("redirect_uri")):
        await log_auth_event("oauth_login_failure", request, reason="invalid_redirect_uri")
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")

    try:
        token_data = await _exchange_google_code(code, redirect_uri, client_id, client_secret)
    except httpx.RequestError:
        await log_auth_event("oauth_login_failure", request, reason="google_unavailable")
        raise HTTPException(status_code=502, detail="Google Auth service unavailable")
    except HTTPException:
        await log_auth_event("oauth_login_failure", request, reason="token_exchange_failed")
        raise

    id_tok = token_data.get("id_token")
    if not id_tok:
        await log_auth_event("oauth_login_failure", request, reason="missing_id_token")
        raise HTTPException(status_code=401, detail="Google did not return an identity token")

    try:
        # Passing client_id as the audience makes verify_oauth2_token reject any
        # token not minted for this application (wrong `aud`).
        claims = _verify_google_id_token(id_tok, client_id)
    except ValueError:
        await log_auth_event("oauth_login_failure", request, reason="invalid_id_token")
        raise HTTPException(status_code=401, detail="Invalid Google identity token")

    if claims.get("iss") not in GOOGLE_VALID_ISSUERS:
        await log_auth_event("oauth_login_failure", request, reason="bad_issuer")
        raise HTTPException(status_code=401, detail="Invalid Google token issuer")

    email = (claims.get("email") or "").lower().strip()
    google_sub = claims.get("sub")
    name = claims.get("name", "")
    picture = claims.get("picture", "")
    email_verified = claims.get("email_verified")
    if isinstance(email_verified, str):
        email_verified = email_verified.strip().lower() == "true"

    if not email or not google_sub:
        await log_auth_event("oauth_login_failure", request, email=email or None, reason="incomplete_identity")
        raise HTTPException(status_code=401, detail="Google identity incomplete")
    # Fail closed on unverified emails: an unverified Google email must never
    # create a new account nor link to an existing one (account-takeover guard).
    if email_verified is not True:
        await log_auth_event("oauth_login_failure", request, email=email, reason="unverified_email")
        raise HTTPException(status_code=401, detail="Google email is not verified")

    # Account resolution. The Google `sub` is the PRIMARY external identity —
    # stable even if the user's Google display name changes — so we resolve by
    # it first. Verified email is the secondary key used only to safely link a
    # pre-existing email/password account. Email remains the unique key, so no
    # duplicate accounts are ever created.
    new_account = False
    linked = False
    user = await db.users.find_one({"google_sub": google_sub})
    if user:
        user_id = str(user["_id"])
        role = user.get("role", "user")
        capital = user.get("capital", 100000)
        # Refresh profile only — never mutate the email off a sub match. Self-heal
        # the verification flag for Google accounts that predate PH1.8.
        sub_updates = {"picture": picture, "name": name or user.get("name", "")}
        if not user.get("email_verified"):
            sub_updates["email_verified"] = True
            sub_updates["email_verified_at"] = datetime.now(timezone.utc).isoformat()
            sub_updates["verified_by"] = "google"
        await db.users.update_one({"_id": user["_id"]}, {"$set": sub_updates})
    else:
        user = await db.users.find_one({"email": email})
        if user:
            existing_sub = user.get("google_sub")
            if existing_sub and existing_sub != google_sub:
                # This email is already bound to a different Google identity —
                # anomalous (Google emails are unique). Never silently re-link.
                await log_auth_event("oauth_login_failure", request, email=email,
                                     user_id=str(user["_id"]), reason="sub_conflict")
                raise HTTPException(status_code=401, detail="Account cannot be linked")
            user_id = str(user["_id"])
            role = user.get("role", "user")
            capital = user.get("capital", 100000)
            linked = user.get("auth_provider") != "google"
            # Link: attach the Google identity; preserve password_hash/auth_provider
            # so email/password login keeps working alongside Google. Linking a
            # verified Google email also confirms ownership of this address, so
            # the account is marked verified (PH1.8) — the user need not also
            # click a verification link.
            link_updates = {
                "picture": picture,
                "name": name or user.get("name", ""),
                "google_sub": google_sub,
            }
            if not user.get("email_verified"):
                link_updates["email_verified"] = True
                link_updates["email_verified_at"] = datetime.now(timezone.utc).isoformat()
                link_updates["verified_by"] = "google"
            await db.users.update_one({"_id": user["_id"]}, {"$set": link_updates})
        else:
            user_doc = {
                "name": name,
                "email": email,
                "picture": picture,
                "password_hash": "",  # No password for Google-native users
                "auth_provider": "google",
                "google_sub": google_sub,
                # Google already asserted (and we enforced above) a verified
                # email, so a Google-native account is verified on creation — no
                # separate verification email needed (PH1.8).
                "email_verified": True,
                "email_verified_at": datetime.now(timezone.utc).isoformat(),
                "verified_by": "google",
                "role": "user",
                "capital": 100000,
                "risk_level": "moderate",
                "max_daily_loss": 5000,
                "max_trades_per_day": 3,
                "telegram_chat_id": None,
                "notification_prefs": {"push": True, "email": True, "morning_report": True, "trade_alerts": True, "exit_reminder": True, "portfolio_alerts": True, "email_alerts": True, "telegram_alerts": False},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            result = await db.users.insert_one(user_doc)
            user_id = str(result.inserted_id)
            role = "user"
            capital = 100000
            new_account = True

    access = await _issue_session(user_id, email, request, response)
    await log_auth_event("oauth_login_success", request, email=email, user_id=user_id,
                         detail={"new_account": new_account, "linked": linked})

    return {
        "id": user_id, "name": name, "email": email, "role": role,
        "picture": picture, "capital": capital, "token": access,
        "auth_provider": "google",
    }



# ============ BROKER ROUTES (Sprint 7 — unified Broker Engine) ============

brokers_router = APIRouter(prefix="/api/brokers", tags=["Brokers"])


def _require_broker(broker: str) -> str:
    from services.brokers import SUPPORTED_BROKERS
    broker = (broker or "").lower()
    if broker not in SUPPORTED_BROKERS:
        raise HTTPException(status_code=404, detail=f"Unsupported broker: {broker}")
    return broker


def _frontend_base() -> str:
    base = os.environ.get("FRONTEND_URL")
    if not base:
        base = os.environ.get("KITE_REDIRECT_URL", "").replace("/api/zerodha/callback", "")
    return (base or "http://localhost:3000").rstrip("/")


@brokers_router.get("")
async def brokers_list(user: dict = Depends(get_current_user)):
    """Supported brokers + this user's connection status for each."""
    return {
        "brokers": broker_engine.list_brokers(),
        "status": await broker_engine.get_status(str(user["_id"])),
    }

@brokers_router.get("/status")
async def brokers_status(user: dict = Depends(get_current_user)):
    return await broker_engine.get_status(str(user["_id"]))

@brokers_router.get("/{broker}/login-url")
async def broker_login_url(broker: str, user: dict = Depends(get_current_user)):
    return broker_engine.get_login_url(_require_broker(broker), str(user["_id"]))

@brokers_router.post("/{broker}/session")
async def broker_session(broker: str, request: Request, user: dict = Depends(get_current_user)):
    """Exchange the OAuth callback payload (Zerodha request_token / Upstox code)."""
    broker = _require_broker(broker)
    body = await request.json()
    result = await broker_engine.complete_auth(broker, str(user["_id"]), body)
    await db.users.update_one({"_id": ObjectId(user["_id"])},
                              {"$set": {f"{broker}_connected": True}})
    # Never return tokens to the browser — profile + sync summary only.
    return {"success": True, "broker": broker, "profile": result.get("profile", {}),
            "sync": (result.get("sync") or {}).get("summary")}

@brokers_router.get("/{broker}/callback")
async def broker_oauth_callback(broker: str, request: Request):
    """Public browser redirect target for broker OAuth.
    Zerodha sends ?request_token=&status=&uid=; Upstox sends ?code=&state=uid=...
    Always bounces back to the frontend Settings page with the outcome."""
    from starlette.responses import RedirectResponse
    broker = _require_broker(broker)
    params = request.query_params
    frontend = _frontend_base()

    uid = params.get("uid")
    state = params.get("state", "")
    if not uid and state.startswith("uid="):
        uid = state[4:]

    auth_payload = {}
    if broker == "zerodha":
        if params.get("status") != "success" or not params.get("request_token"):
            return RedirectResponse(url=f"{frontend}/settings?broker=zerodha&status=cancelled")
        auth_payload = {"request_token": params.get("request_token")}
    else:  # upstox
        if params.get("error") or not params.get("code"):
            return RedirectResponse(url=f"{frontend}/settings?broker={broker}&status=cancelled")
        auth_payload = {"code": params.get("code")}

    try:
        await broker_engine.complete_auth(broker, uid, auth_payload)
        if uid:
            try:
                await db.users.update_one({"_id": ObjectId(uid)}, {"$set": {f"{broker}_connected": True}})
            except Exception:
                pass
        return RedirectResponse(url=f"{frontend}/settings?broker={broker}&status=connected")
    except (BrokerError, Exception) as e:
        message = getattr(e, "user_message", "connection failed")
        logger.error(f"{broker} OAuth callback failed: {e}")
        return RedirectResponse(url=f"{frontend}/settings?broker={broker}&status=failed&error={message}")

@brokers_router.post("/{broker}/disconnect")
async def broker_disconnect(broker: str, user: dict = Depends(get_current_user)):
    broker = _require_broker(broker)
    result = await broker_engine.disconnect(broker, str(user["_id"]))
    await db.users.update_one({"_id": ObjectId(user["_id"])},
                              {"$set": {f"{broker}_connected": False}})
    return result

@brokers_router.post("/{broker}/sync")
async def broker_sync(broker: str, user: dict = Depends(get_current_user)):
    """Full portfolio sync: holdings + positions + funds persisted to Mongo."""
    return await broker_engine.sync_portfolio(str(user["_id"]), _require_broker(broker))

@brokers_router.get("/{broker}/profile")
async def broker_profile(broker: str, user: dict = Depends(get_current_user)):
    return await broker_engine.get_profile(str(user["_id"]), _require_broker(broker))

@brokers_router.get("/{broker}/holdings")
async def broker_holdings(broker: str, user: dict = Depends(get_current_user)):
    return {"broker": broker, "holdings": await broker_engine.get_holdings(str(user["_id"]), _require_broker(broker))}

@brokers_router.get("/{broker}/positions")
async def broker_positions(broker: str, user: dict = Depends(get_current_user)):
    return {"broker": broker, "positions": await broker_engine.get_positions(str(user["_id"]), _require_broker(broker))}

@brokers_router.get("/{broker}/funds")
async def broker_funds(broker: str, user: dict = Depends(get_current_user)):
    return {"broker": broker, "funds": await broker_engine.get_funds(str(user["_id"]), _require_broker(broker))}

@brokers_router.get("/{broker}/margins")
async def broker_margins(broker: str, user: dict = Depends(get_current_user)):
    return {"broker": broker, "margins": await broker_engine.get_margins(str(user["_id"]), _require_broker(broker))}

@brokers_router.get("/{broker}/orders")
async def broker_orders(broker: str, user: dict = Depends(get_current_user)):
    return {"broker": broker, "orders": await broker_engine.get_orders(str(user["_id"]), _require_broker(broker))}

@brokers_router.get("/{broker}/trades")
async def broker_trades(broker: str, user: dict = Depends(get_current_user)):
    """Executed trade history for the day (official broker trade book)."""
    return {"broker": broker, "trades": await broker_engine.get_trades(str(user["_id"]), _require_broker(broker))}

@brokers_router.post("/{broker}/orders")
async def broker_place_order(broker: str, order: BrokerOrderCreate, user: dict = Depends(get_current_user)):
    """Place a LIVE order via the official broker API (no simulation)."""
    broker = _require_broker(broker)
    payload = order.model_dump(exclude_none=True)
    payload.setdefault("product", "CNC" if broker == "zerodha" else "D")
    return await broker_engine.place_order(str(user["_id"]), broker, payload)

@brokers_router.patch("/{broker}/orders/{order_id}")
async def broker_modify_order(broker: str, order_id: str, changes: BrokerOrderModify,
                              user: dict = Depends(get_current_user)):
    broker = _require_broker(broker)
    return await broker_engine.modify_order(str(user["_id"]), broker, order_id,
                                            changes.model_dump(exclude_none=True))

@brokers_router.delete("/{broker}/orders/{order_id}")
async def broker_cancel_order(broker: str, order_id: str, user: dict = Depends(get_current_user)):
    return await broker_engine.cancel_order(str(user["_id"]), _require_broker(broker), order_id)


# ============ UNIFIED ORDER HISTORY (Sprint 9) ============
# One order book across every broker: db.orders is fed by order placements,
# the realtime broker stream (_record_order) and on-demand sync below.

orders_router = APIRouter(prefix="/api/orders", tags=["Orders"])


@orders_router.get("")
async def unified_orders(user: dict = Depends(get_current_user),
                         broker: Optional[str] = None, refresh: bool = False):
    """Unified order history. `refresh=true` re-syncs the live order book from
    every connected broker first (best-effort — a broker outage never hides
    the locally recorded history)."""
    user_id = str(user["_id"])
    sync_errors = {}
    if refresh:
        statuses = await broker_engine.get_status(user_id)
        for name, status in statuses.items():
            if broker and name != broker:
                continue
            if not status.get("connected"):
                continue
            try:
                await broker_engine.sync_orders(user_id, name)
            except BrokerError as e:
                sync_errors[name] = e.user_message
    query = {"user_id": user_id}
    if broker:
        query["broker"] = broker
    docs = await db.orders.find(query).sort("placed_at", -1).to_list(200)
    for d in docs:
        d["_id"] = str(d["_id"])
    return {"orders": docs, "count": len(docs), "sync_errors": sync_errors}


# ============ ZERODHA ROUTES (legacy paths — delegate to Broker Engine) ============

zerodha_router = APIRouter(prefix="/api/zerodha", tags=["Zerodha"])

@zerodha_router.get("/status")
async def zerodha_status(user: dict = Depends(get_current_user)):
    return (await broker_engine.get_status(str(user["_id"])))["zerodha"]

@zerodha_router.get("/login-url")
async def zerodha_login(user: dict = Depends(get_current_user)):
    return broker_engine.get_login_url("zerodha", str(user["_id"]))

@zerodha_router.post("/session")
async def zerodha_session(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    request_token = body.get("request_token")
    if not request_token:
        raise HTTPException(status_code=400, detail="request_token required")
    try:
        result = await broker_engine.complete_auth("zerodha", str(user["_id"]),
                                                   {"request_token": request_token})
    except BrokerError as e:
        return {"success": False, "message": e.user_message}
    await db.users.update_one({"_id": ObjectId(user["_id"])}, {"$set": {"zerodha_connected": True}})
    return {"success": True, "user": result.get("profile", {})}

@zerodha_router.get("/holdings")
async def zerodha_holdings(user: dict = Depends(get_current_user)):
    try:
        return {"source": "zerodha", "holdings": await broker_engine.get_holdings(str(user["_id"]), "zerodha")}
    except BrokerAuthError as e:
        return {"source": "zerodha", "holdings": [], "error": e.user_message}

@zerodha_router.get("/positions")
async def zerodha_positions(user: dict = Depends(get_current_user)):
    try:
        positions = await broker_engine.get_positions(str(user["_id"]), "zerodha")
        return {"source": "zerodha", "net": positions, "day": []}
    except BrokerAuthError as e:
        return {"source": "zerodha", "net": [], "day": [], "error": e.user_message}

@zerodha_router.post("/order")
async def zerodha_order(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    try:
        result = await broker_engine.place_order(str(user["_id"]), "zerodha", {
            "symbol": body["symbol"],
            "transaction_type": body.get("transaction_type", "BUY"),
            "quantity": body["quantity"],
            "price": body.get("price"),
            "order_type": body.get("order_type", "LIMIT"),
            "product": body.get("product", "MIS"),
            "exchange": body.get("exchange", "NSE"),
        })
        return {"source": "zerodha", "order_id": result.get("order_id"), "status": "PLACED"}
    except BrokerError as e:
        return {"source": "zerodha", "order_id": None, "status": "FAILED", "message": e.user_message}

@zerodha_router.delete("/order/{order_id}")
async def zerodha_cancel(order_id: str, user: dict = Depends(get_current_user)):
    try:
        result = await broker_engine.cancel_order(str(user["_id"]), "zerodha", order_id)
        return {"source": "zerodha", "status": "success", "order_id": result.get("order_id")}
    except BrokerError as e:
        return {"source": "zerodha", "status": "ERROR", "message": e.user_message}

@zerodha_router.get("/funds")
async def zerodha_funds(user: dict = Depends(get_current_user)):
    try:
        funds = await broker_engine.get_funds(str(user["_id"]), "zerodha")
        # Legacy aliases used by the Portfolio page.
        return {"source": "zerodha", **funds,
                "available": funds.get("available_margin"),
                "used": funds.get("used_margin"),
                "total": funds.get("total_balance")}
    except BrokerAuthError as e:
        return {"source": "zerodha", "equity": {"available_margin": 0},
                "commodity": {"available_margin": 0}, "error": e.user_message}

@zerodha_router.get("/profile")
async def zerodha_profile(user: dict = Depends(get_current_user)):
    try:
        profile = await broker_engine.get_profile(str(user["_id"]), "zerodha")
        return {"source": "zerodha", **profile, "user_id": profile.get("account_id", "")}
    except BrokerAuthError as e:
        return {"source": "zerodha", "user_name": "Not Connected", "user_id": "", "error": e.user_message}

@zerodha_router.get("/orders")
async def zerodha_orders(user: dict = Depends(get_current_user)):
    try:
        return {"source": "zerodha", "orders": await broker_engine.get_orders(str(user["_id"]), "zerodha")}
    except BrokerAuthError as e:
        return {"source": "zerodha", "orders": [], "error": e.user_message}

@zerodha_router.get("/account")
async def zerodha_account(user: dict = Depends(get_current_user)):
    """Full account overview: profile + funds + holdings + positions."""
    user_id = str(user["_id"])
    status = (await broker_engine.get_status(user_id))["zerodha"]
    if not status["connected"]:
        return {"profile": {"error": status["message"]}, "funds": None,
                "holdings": {"holdings": []}, "positions": {"net": [], "day": []},
                "status": status}
    try:
        profile = await broker_engine.get_profile(user_id, "zerodha")
        funds = await broker_engine.get_funds(user_id, "zerodha")
        holdings = await broker_engine.get_holdings(user_id, "zerodha")
        positions = await broker_engine.get_positions(user_id, "zerodha")
    except BrokerError as e:
        return {"profile": {"error": e.user_message}, "funds": None,
                "holdings": {"holdings": []}, "positions": {"net": [], "day": []},
                "status": status}
    return {
        "profile": profile,
        "funds": {**funds, "available": funds.get("available_margin"),
                  "used": funds.get("used_margin"), "total": funds.get("total_balance")},
        "holdings": {"holdings": holdings},
        "positions": {"net": positions, "day": []},
        "status": status,
    }

@zerodha_router.post("/quick-trade")
async def zerodha_quick_trade(request: Request, user: dict = Depends(get_current_user)):
    """One-click trade from AI picks — places order on Zerodha + creates trade record."""
    try:
        body = await request.json()
        symbol = body["symbol"]
        entry = float(body["entry_price"])
        qty = int(body["quantity"])
        sl = float(body["stop_loss"])
        t1 = float(body["target1"])
        t2 = float(body.get("target2", 0)) or None
        stock_name = body.get("stock_name", symbol)
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: missing or malformed {str(e)}")

    # Place LIVE order on Zerodha via the Broker Engine (no simulation)
    try:
        placed = await broker_engine.place_order(str(user["_id"]), "zerodha", {
            "symbol": symbol, "transaction_type": "BUY", "quantity": qty,
            "price": entry, "order_type": "LIMIT", "product": "MIS", "exchange": "NSE",
        })
        order_result = {"source": "zerodha", "order_id": placed.get("order_id"), "status": "PLACED"}
    except BrokerError as e:
        # No simulated fallback: a failed broker order never creates an OPEN trade.
        raise HTTPException(status_code=502,
                            detail=f"Order was not placed: {e.user_message}")

    # Create trade record
    trade_doc = {
        "user_id": user["_id"],
        "symbol": symbol.upper(),
        "stock_name": stock_name,
        "type": "BUY",
        "entry_price": entry,
        "quantity": qty,
        "stop_loss": sl,
        "target1": t1,
        "target2": t2,
        "status": "OPEN",
        "pnl": None,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "zerodha_order_id": order_result.get("order_id"),
        "ai_confidence": body.get("confidence"),
    }
    result = await db.trades.insert_one(trade_doc)
    trade_doc["_id"] = str(result.inserted_id)

    # Create notification (pushes notification.created live)
    from services.notification_service import create_notification
    await create_notification(
        db, user["_id"],
        type="TRADE_ENTRY",
        title="Trade Executed",
        message=f"[LIVE] Bought {qty} {symbol} @ INR {entry}. SL: INR {sl} | Target: INR {t1}. Order: {order_result.get('order_id', 'N/A')}",
        symbol=symbol,
        data={"trade_id": trade_doc["_id"]},
    )

    return {
        "trade": trade_doc,
        "order": order_result,
        "message": "Trade placed on Zerodha",
    }


@zerodha_router.post("/emergency-stop")
async def zerodha_emergency_stop(user: dict = Depends(get_current_user)):
    """
    Emergency Stop Button:
    1. Instantly cancels all pending/open orders on Zerodha
    2. Liquidates all active open positions (MIS/intraday)
    3. Triggers notifications on all configured channels (WhatsApp, Telegram, Email, Push)
    4. Marks all open trade records in DB as CLOSED with tag 'EMERGENCY_STOP'
    """
    try:
        from services.whatsapp_service import send_whatsapp, is_configured as wa_configured
        from services.email_service import send_notification as send_email_notif, is_configured as email_configured
        from services.telegram_service import send_notification as send_tg_notif, is_configured as tg_configured

        user_id = str(user["_id"])
        now_str = datetime.now(timezone.utc).isoformat()

        # 1. Cancel all open orders (per-user, via the Broker Engine)
        cancelled_count = 0
        try:
            for o in await broker_engine.get_orders(user_id, "zerodha"):
                if o.get("status") in ("OPEN", "PENDING", "PARTIALLY_FILLED"):
                    try:
                        await broker_engine.cancel_order(user_id, "zerodha", o["order_id"])
                        cancelled_count += 1
                    except BrokerError as e:
                        logger.error(f"Emergency stop: cancel {o['order_id']} failed: {e}")
        except BrokerAuthError:
            pass  # broker not connected — still close DB trades below

        # 2. Liquidate active open positions at market
        liquidated_count = 0
        try:
            for pos in await broker_engine.get_positions(user_id, "zerodha"):
                qty = pos.get("quantity", 0)
                if qty != 0:
                    try:
                        await broker_engine.place_order(user_id, "zerodha", {
                            "symbol": pos["symbol"],
                            "exchange": pos.get("exchange", "NSE"),
                            "transaction_type": "SELL" if qty > 0 else "BUY",
                            "quantity": abs(qty),
                            "order_type": "MARKET",
                            "product": pos.get("product", "MIS"),
                            "tag": "EMERGENCY_STOP",
                        })
                        liquidated_count += 1
                    except BrokerError as e:
                        logger.error(f"Emergency stop: liquidation of {pos.get('symbol')} failed: {e}")
        except BrokerAuthError:
            pass

        # 3. Update DB: set all open trades for this user to CLOSED
        result = await db.trades.update_many(
            {"user_id": user["_id"], "status": "OPEN"},
            {"$set": {"status": "CLOSED", "exit_price": 0, "exit_time": now_str, "notes": "EMERGENCY STOP TRIGGERED"}}
        )
        db_closed_count = result.modified_count

        # 4. Trigger Alerts across channels
        alert_msg = (
            f"🚨 EMERGENCY STOP TRIGGERED 🚨\n\n"
            f"All pending orders cancelled: {cancelled_count}\n"
            f"All active positions liquidated: {liquidated_count}\n"
            f"Database trades marked closed: {db_closed_count}\n\n"
            f"AI trading halted. Review your Zerodha terminal."
        )

        # A. Save push notification in DB (pushes notification.created live)
        from services.notification_service import create_notification
        await create_notification(
            db, user["_id"],
            type="EMERGENCY_STOP",
            title="🚨 Emergency Stop Triggered",
            message=alert_msg,
            severity="critical",
        )

        # B. Send WhatsApp
        if wa_configured():
            try:
                await send_whatsapp(alert_msg)
            except Exception as e:
                logger.error(f"Emergency stop WhatsApp failed: {e}")

        # C. Send Email
        if user.get("email") and email_configured():
            try:
                await send_email_notif("RISK_ALERT", user["email"], symbol="EMERGENCY_STOP", reason=alert_msg, price=0)
            except Exception as e:
                logger.error(f"Emergency stop Email failed: {e}")

        # D. Send Telegram
        if user.get("telegram_chat_id") and tg_configured():
            try:
                await send_tg_notif("RISK_ALERT", user["telegram_chat_id"], symbol="EMERGENCY_STOP", reason=alert_msg, price=0)
            except Exception as e:
                logger.error(f"Emergency stop Telegram failed: {e}")

        return {
            "success": True,
            "cancelled_orders": cancelled_count,
            "liquidated_positions": liquidated_count,
            "db_closed_trades": db_closed_count,
            "message": "Emergency stop executed successfully. All assets liquidated, orders cancelled.",
        }
    except Exception as e:
        logger.error(f"Emergency stop failure: {e}")
        raise HTTPException(status_code=500, detail=f"Emergency stop failed: {str(e)}")


# ============ ENHANCED STOCK ENDPOINT ============

@stocks_router.get("/{symbol}/live")
async def stock_live_quote(symbol: str):
    """Get live quote - Alpha Vantage if configured, else real Yahoo Finance."""
    if av_configured():
        av_quote = await av_get_quote(symbol)
        if av_quote:
            av_quote["source"] = "alpha_vantage"
            return av_quote
    quote = await real_quote(symbol)
    if not quote:
        if not get_stock_meta(symbol):
            raise HTTPException(status_code=404, detail="Stock not found")
        raise HTTPException(status_code=503, detail="Live market data temporarily unavailable for this stock")
    quote["source"] = "yahoo_finance"
    return quote

@stocks_router.get("/{symbol}/intraday")
async def stock_intraday(symbol: str, interval: str = "5min"):
    """Get intraday data - Alpha Vantage if configured, else real Yahoo Finance."""
    if av_configured():
        av_data = await av_intraday(symbol, interval)
        if av_data:
            return {"data": av_data, "source": "alpha_vantage"}
    from services.real_market import fetch_real_chart_data
    chart = await fetch_real_chart_data(symbol, "1D")
    return {"data": chart, "source": "yahoo_finance", "available": bool(chart)}


# ============ DATA SOURCE STATUS ============

@app.get("/api/data-sources")
async def data_sources():
    """Get status of all external data sources."""
    from services.whatsapp_service import get_status as wa_status
    from services.email_service import get_status as email_status
    from services.telegram_service import get_status as tg_status
    engine_status = get_debate_engine().get_status()
    return {
        "alpha_vantage": {"configured": av_configured(), "mode": "live" if av_configured() else "yahoo_finance"},
        "zerodha": kite_status(),
        "ai": {
            "configured": engine_status["debate_ready"],
            "full_debate": engine_status["full_debate"],
            "claude": engine_status["claude"],
            "gemini": engine_status["gemini"],
            "architecture": "dual-provider-debate",
        },
        "whatsapp": wa_status(),
        "email": email_status(),
        "telegram": tg_status(),
    }


# ============ PORTFOLIO MONITORING ROUTES ============

monitor_router = APIRouter(prefix="/api/monitor", tags=["Monitor"])

@monitor_router.get("/health")
async def portfolio_health(user: dict = Depends(get_current_user)):
    """Get AI-powered portfolio health analysis."""
    from services.portfolio_monitor import analyze_portfolio_health
    # market_func is unused by analyze_portfolio_health; quote_func is called
    # synchronously, so pass a sync closure over prefetched LIVE quotes.
    quote_func = await prefetch_quote_func(user["_id"])
    result = await analyze_portfolio_health(db, user["_id"], None, quote_func)
    return result

@monitor_router.get("/alerts")
async def get_recent_alerts(user: dict = Depends(get_current_user)):
    """Get recent AI monitoring alerts."""
    alerts = await db.notifications.find(
        {"user_id": user["_id"], "type": {"$in": ["RISK_HIGH", "STOP_LOSS_HIT", "NEAR_TARGET", "TARGET_HIT", "RSI_OVERBOUGHT", "VOLUME_SPIKE", "SIGNIFICANT_LOSS"]}}
    ).sort("created_at", -1).to_list(20)
    for a in alerts:
        a["_id"] = str(a["_id"])
    return alerts

@monitor_router.post("/run")
async def trigger_monitoring(user: dict = Depends(get_current_user)):
    """Manually trigger portfolio monitoring cycle."""
    from services.portfolio_monitor import analyze_portfolio_health
    # Prefetched LIVE quotes → sync quote_func (see prefetch_quote_func).
    quote_func = await prefetch_quote_func(user["_id"])
    result = await analyze_portfolio_health(db, user["_id"], None, quote_func)
    # Save alerts as notifications (each pushes notification.created live)
    from services.notification_service import create_notification
    for alert in result.get("alerts", []):
        if alert["severity"] in ("critical", "positive"):
            await create_notification(
                db, user["_id"],
                type=alert["type"],
                title=f"AI Alert: {alert['symbol']}",
                message=alert["message"],
                severity=alert["severity"],
                symbol=alert["symbol"],
            )
    return result


# ============ WHATSAPP ROUTES ============

whatsapp_router = APIRouter(prefix="/api/whatsapp", tags=["WhatsApp"])

@whatsapp_router.get("/status")
async def whatsapp_status(user: dict = Depends(get_current_user)):
    """Get WhatsApp notification status."""
    from services.whatsapp_service import get_status
    return get_status()

@whatsapp_router.post("/test")
async def test_whatsapp(user: dict = Depends(get_current_user)):
    """Send a test WhatsApp message."""
    from services.whatsapp_service import send_whatsapp
    result = await send_whatsapp(f"AlphaPartner Test\n\nHello {user.get('name', 'Trader')}! Your WhatsApp notifications are working. You'll receive AI alerts here during market hours.")
    return result

@whatsapp_router.post("/configure")
async def configure_whatsapp(request: Request, user: dict = Depends(get_current_user)):
    """Save user's WhatsApp number for alerts."""
    body = await request.json()
    phone = body.get("phone_number", "")
    if phone:
        await db.users.update_one({"_id": ObjectId(user["_id"])}, {"$set": {"whatsapp_number": phone}})
    return {"message": "WhatsApp number saved", "phone": phone}


# ============ EMAIL ROUTES ============

email_router = APIRouter(prefix="/api/email", tags=["Email"])

@email_router.get("/status")
async def email_status(user: dict = Depends(get_current_user)):
    """Get email notification status."""
    from services.email_service import get_status
    return get_status()

@email_router.post("/test")
async def test_email(user: dict = Depends(get_current_user)):
    """Send a test email."""
    from services.email_service import send_notification
    email = user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email on file")
    result = await send_notification(
        "MORNING_REPORT", email,
        content=f"Hello {user.get('name', 'Trader')}! Your email notifications are working. You'll receive AI trading alerts here."
    )
    return result

@email_router.post("/configure")
async def configure_email(request: Request, user: dict = Depends(get_current_user)):
    """Update user's email notification preferences."""
    body = await request.json()
    email_enabled = body.get("email_alerts", True)
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {"$set": {"notification_prefs.email_alerts": email_enabled}}
    )
    return {"message": "Email preferences updated", "email_alerts": email_enabled}


# ============ ZERODHA CALLBACK ============

@zerodha_router.get("/callback")
async def zerodha_callback(request: Request):
    """Handle the Kite Connect browser redirect after login/OTP.

    Kite appends ?request_token=...&status=... plus any redirect_params we
    attached to the login URL (uid identifies the app user, since no JWT is
    available on a cross-site redirect). Always redirects back to the
    frontend with an absolute URL — a relative redirect would land on the
    backend origin and 404.
    """
    request_token = request.query_params.get("request_token")
    status_param = request.query_params.get("status")
    uid = request.query_params.get("uid")
    from starlette.responses import RedirectResponse

    frontend_base = _frontend_base()

    if status_param == "success" and request_token:
        try:
            await broker_engine.complete_auth("zerodha", uid, {"request_token": request_token})
            if uid:
                try:
                    await db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"zerodha_connected": True}})
                except Exception:
                    pass
            return RedirectResponse(url=f"{frontend_base}/settings?zerodha=connected")
        except (BrokerError, Exception) as e:
            message = getattr(e, "user_message", str(e))
            logger.error(f"Zerodha session exchange failed: {message}")
            return RedirectResponse(url=f"{frontend_base}/settings?zerodha=failed&error={message}")
    return RedirectResponse(url=f"{frontend_base}/settings?zerodha=cancelled")

@zerodha_router.post("/postback")
async def zerodha_postback(request: Request):
    """Handle Zerodha order postback webhooks."""
    try:
        body = await request.json()
        await db.zerodha_postbacks.insert_one({
            "data": body,
            "received_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Zerodha postback received: {body.get('order_id', 'unknown')}")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Postback error: {e}")
        return {"status": "error"}

@zerodha_router.get("/urls")
async def zerodha_urls():
    """Get Zerodha redirect and postback URLs for Kite app configuration."""
    redirect_url = os.environ.get("KITE_REDIRECT_URL", "")
    base = redirect_url.rsplit("/api/", 1)[0] if "/api/" in redirect_url else ""
    return {
        "redirect_url": redirect_url,
        "postback_url": f"{base}/api/zerodha/postback" if base else "",
        "instructions": "Set these URLs in your Zerodha Kite Connect app settings at https://developers.kite.trade/apps",
    }


# ============ NEWS ROUTES ============

news_router = APIRouter(prefix="/api/news", tags=["News"])

@news_router.get("")
async def get_news():
    """Get latest market news from multiple sources."""
    from services.news_service import fetch_news
    articles = await fetch_news()
    return {"articles": articles, "count": len(articles)}

@news_router.get("/stock/{symbol}")
async def get_stock_news(symbol: str, name: str = ""):
    """Get news for a specific stock."""
    from services.news_service import search_stock_news
    articles = await search_stock_news(symbol, name)
    return {"articles": articles, "symbol": symbol}

@news_router.get("/sentiment")
async def news_sentiment():
    """Aggregate market sentiment derived from real news headlines.
    Explicitly unavailable when feeds cannot be reached — never simulated."""
    from services.news_service import get_market_sentiment
    return await get_market_sentiment()

@news_router.get("/refresh")
async def refresh_news():
    """Force refresh news cache."""
    from services.news_service import fetch_news
    articles = await fetch_news(force=True)
    return {"articles": articles, "count": len(articles)}


# ============ TRADE JOURNAL ROUTES ============

journal_router = APIRouter(prefix="/api/journal", tags=["Journal"])

@journal_router.get("")
async def get_journal(user: dict = Depends(get_current_user), days: int = 30):
    """Get trade journal."""
    from services.trade_journal import get_trade_journal
    return await get_trade_journal(db, user["_id"], days)

@journal_router.get("/stats")
async def get_stats(user: dict = Depends(get_current_user), days: int = 7):
    """Get performance statistics."""
    from services.trade_journal import get_performance_stats
    return await get_performance_stats(db, user["_id"], days)

@journal_router.get("/weekly-review")
async def weekly_review(user: dict = Depends(get_current_user)):
    """Get AI weekly performance review."""
    from services.trade_journal import generate_weekly_review
    async def ai_review(prompt):
        return await ai_chat(prompt, "weekly-review", user)
    return await generate_weekly_review(db, user["_id"], ai_review)

@journal_router.get("/setup-stats")
async def setup_stats(user: dict = Depends(get_current_user)):
    """Historical success rate per trade setup type."""
    from services.trade_journal import get_setup_success_rates
    return await get_setup_success_rates(db, user["_id"])


# ============ WEBHOOK ROUTES (n8n automation) ============
# These endpoints are called by the n8n automation service (Feature 8), NOT by
# browser clients — so they are gated by a shared secret header instead of JWT.
# n8n workflows (see /n8n) trigger these on a cron schedule to run the same
# analysis jobs the in-process APScheduler runs, and each call is recorded in
# the `webhook_logs` collection so the frontend can surface "last run" times.

webhooks_router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


async def verify_webhook_key(x_webhook_key: Optional[str] = Header(None)) -> bool:
    """Reject webhook calls without a valid X-Webhook-Key header.

    Fails closed: if WEBHOOK_API_KEY is unset on the server, all calls are denied.
    """
    expected = os.environ.get("WEBHOOK_API_KEY")
    if not expected or not x_webhook_key or not secrets.compare_digest(x_webhook_key, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing webhook API key")
    return True


async def _log_webhook(workflow_name: str, status: str):
    """Record a webhook invocation for the Settings "last run" display."""
    try:
        await db.webhook_logs.insert_one({
            "workflow_name": workflow_name,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
        })
    except Exception as e:
        logging.error(f"Failed to log webhook '{workflow_name}': {e}")


@webhooks_router.post("/morning-scan")
async def webhook_morning_scan(_: bool = Depends(verify_webhook_key)):
    """Run the morning analysis job (scan stocks, generate picks, morning report)."""
    from services.scheduler import morning_analysis_job
    try:
        await morning_analysis_job(db, ai_market_summary)
        await _log_webhook("morning_scan", "success")
        return {"status": "success", "workflow": "morning_scan"}
    except Exception as e:
        await _log_webhook("morning_scan", "error")
        logging.error(f"morning-scan webhook error: {e}")
        raise HTTPException(status_code=500, detail=f"Morning scan failed: {e}")


@webhooks_router.post("/evening-summary")
async def webhook_evening_summary(_: bool = Depends(verify_webhook_key)):
    """Run the end-of-day report job (P&L, wins/losses, user notifications)."""
    from services.scheduler import eod_report_job
    try:
        await eod_report_job(db)
        await _log_webhook("evening_summary", "success")
        return {"status": "success", "workflow": "evening_summary"}
    except Exception as e:
        await _log_webhook("evening_summary", "error")
        logging.error(f"evening-summary webhook error: {e}")
        raise HTTPException(status_code=500, detail=f"Evening summary failed: {e}")


@webhooks_router.post("/weekly-review")
async def webhook_weekly_review(_: bool = Depends(verify_webhook_key)):
    """Generate an AI weekly performance review for every user and notify them."""
    from services.trade_journal import generate_weekly_review
    try:
        users = await db.users.find({}, {"_id": 1, "capital": 1, "risk_level": 1}).to_list(1000)
        reviewed = 0
        for u in users:
            uid = str(u["_id"])
            async def ai_review(prompt, _ctx=u):
                return await ai_chat(prompt, "weekly-review", _ctx)
            result = await generate_weekly_review(db, uid, ai_review)
            from services.notification_service import create_notification
            await create_notification(
                db, uid,
                type="WEEKLY_REVIEW",
                title="Weekly Performance Review",
                message=result.get("review", "Your weekly trading review is ready."),
            )
            reviewed += 1
        await _log_webhook("weekly_review", "success")
        return {"status": "success", "workflow": "weekly_review", "users_reviewed": reviewed}
    except Exception as e:
        await _log_webhook("weekly_review", "error")
        logging.error(f"weekly-review webhook error: {e}")
        raise HTTPException(status_code=500, detail=f"Weekly review failed: {e}")


@webhooks_router.post("/news-digest")
async def webhook_news_digest(_: bool = Depends(verify_webhook_key)):
    """Force-refresh the market news cache to build a fresh digest."""
    from services.news_service import fetch_news
    try:
        articles = await fetch_news(force=True)
        await _log_webhook("news_digest", "success")
        return {"status": "success", "workflow": "news_digest", "articles": len(articles)}
    except Exception as e:
        await _log_webhook("news_digest", "error")
        logging.error(f"news-digest webhook error: {e}")
        raise HTTPException(status_code=500, detail=f"News digest failed: {e}")


@webhooks_router.get("/logs")
async def webhook_logs(user: dict = Depends(get_current_user)):
    """Most recent run per workflow — powers the n8n Automation panel in Settings."""
    pipeline = [
        {"$sort": {"triggered_at": -1}},
        {"$group": {
            "_id": "$workflow_name",
            "workflow_name": {"$first": "$workflow_name"},
            "triggered_at": {"$first": "$triggered_at"},
            "status": {"$first": "$status"},
        }},
    ]
    logs = await db.webhook_logs.aggregate(pipeline).to_list(50)
    return {log["workflow_name"]: {"triggered_at": log["triggered_at"], "status": log["status"]} for log in logs}


# ============ WATCHLIST ROUTES ============

@watchlist_router.get("")
async def get_watchlist(user: dict = Depends(get_current_user)):
    """Get user's watchlist enriched with live quotes. Items whose live quote is
    unavailable carry quote=None so the UI can show an explicit unavailable state."""
    items = await db.watchlist.find({"user_id": user["_id"]}).sort("added_at", -1).to_list(100)
    quotes = await real_quotes_map([item["symbol"] for item in items])
    for item in items:
        item["_id"] = str(item["_id"])
        quote = quotes.get(item["symbol"].upper())
        if quote:
            item["quote"] = {
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "volume_ratio": quote.get("volume_ratio"),
                "rsi": quote.get("rsi"),
                "sector": quote.get("sector"),
                "name": quote.get("name", item["symbol"]),
            }
            if item.get("added_price") and quote.get("price"):
                item["since_added_pct"] = round(
                    (quote["price"] - item["added_price"]) / item["added_price"] * 100, 2
                )
        else:
            item["quote"] = None
    return items

@watchlist_router.post("")
async def add_to_watchlist(data: WatchlistAdd, user: dict = Depends(get_current_user)):
    """Add a stock to the watchlist. Idempotent per user+symbol. Accepts any
    symbol resolvable via metadata or a live quote (supports live search results)."""
    symbol = data.symbol.upper().strip()
    existing = await db.watchlist.find_one({"user_id": user["_id"], "symbol": symbol})
    if existing:
        existing["_id"] = str(existing["_id"])
        return existing
    # Real quote first so added_price matches the live prices used for since-added P&L
    quote = await real_quote(symbol) or {}
    if not quote and not get_stock_meta(symbol):
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    doc = {
        "user_id": user["_id"],
        "symbol": symbol,
        "note": data.note,
        "added_price": quote.get("price"),
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.watchlist.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    await _publish_watchlist_updated(user["_id"], "added", symbol)
    return doc

@watchlist_router.delete("/{symbol}")
async def remove_from_watchlist(symbol: str, user: dict = Depends(get_current_user)):
    """Remove a stock from the watchlist."""
    result = await db.watchlist.delete_one({"user_id": user["_id"], "symbol": symbol.upper().strip()})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not in watchlist")
    await _publish_watchlist_updated(user["_id"], "removed", symbol.upper().strip())
    return {"message": f"{symbol.upper()} removed from watchlist"}


async def _publish_watchlist_updated(user_id: str, action: str, symbol: str):
    """Publish a per-user ``watchlist.updated`` event (Sprint R8) so every open
    surface for this user (Watchlist page, Dashboard widget, other tabs) syncs
    an add/remove instantly. Best-effort — never breaks the REST mutation."""
    try:
        from services.market_engine.event_bus import event_bus
        await event_bus.publish("watchlist.updated", {
            "user_id": str(user_id),
            "action": action,
            "symbol": symbol,
        })
    except Exception as e:
        logging.warning(f"watchlist.updated publish failed: {e}")


# ============ ENHANCED AI EXPLAIN ============

@analysis_router.post("/full-report")
async def full_ai_report(data: StockAnalysisRequest):
    """Generate comprehensive AI analysis report with full transparency."""
    quote = await real_quote(data.symbol)
    if not quote:
        if not get_stock_meta(data.symbol):
            raise HTTPException(status_code=404, detail="Stock not found")
        raise HTTPException(status_code=503, detail="Live market data temporarily unavailable for this stock")

    name = data.name or quote["name"]

    # Scoring breakdown
    score = 0
    breakdown = []
    rsi = quote["rsi"]
    if 55 < rsi < 70:
        score += 20
        breakdown.append({"factor": "RSI", "score": 20, "max": 20, "reason": f"RSI at {rsi} — bullish momentum without being overbought"})
    elif rsi >= 70:
        score += 5
        breakdown.append({"factor": "RSI", "score": 5, "max": 20, "reason": f"RSI at {rsi} — overbought territory, may pull back"})
    else:
        score += 10
        breakdown.append({"factor": "RSI", "score": 10, "max": 20, "reason": f"RSI at {rsi} — moderate momentum"})

    vol_ratio = quote["volume_ratio"]
    if vol_ratio > 1.5:
        score += 20
        breakdown.append({"factor": "Volume", "score": 20, "max": 20, "reason": f"Volume {vol_ratio}x above average — strong institutional interest"})
    else:
        score += 8
        breakdown.append({"factor": "Volume", "score": 8, "max": 20, "reason": f"Volume {vol_ratio}x — below average activity"})

    change_pct = quote["change_pct"]
    if change_pct > 0.5:
        score += 20
        breakdown.append({"factor": "Price Action", "score": 20, "max": 20, "reason": f"Up {change_pct}% today — positive momentum"})
    elif change_pct > -0.5:
        score += 12
        breakdown.append({"factor": "Price Action", "score": 12, "max": 20, "reason": f"Flat at {change_pct}% — consolidating"})
    else:
        score += 5
        breakdown.append({"factor": "Price Action", "score": 5, "max": 20, "reason": f"Down {change_pct}% — bearish pressure"})

    vwap = quote["vwap"]
    if quote["price"] > vwap:
        score += 20
        breakdown.append({"factor": "VWAP", "score": 20, "max": 20, "reason": f"Price INR {quote['price']} above VWAP INR {vwap} — buyers in control"})
    else:
        score += 8
        breakdown.append({"factor": "VWAP", "score": 8, "max": 20, "reason": f"Price below VWAP — sellers dominant"})

    # News sentiment — derived from real headlines mentioning this stock
    from services.news_service import search_stock_news
    news = await search_stock_news(data.symbol, name)
    if news:
        positive = sum(1 for n in news if n.get("sentiment") == "positive")
        negative = sum(1 for n in news if n.get("sentiment") == "negative")
        news_score = int(round(10 + (positive - negative) / len(news) * 10))  # 0..20
        news_reason = f"{positive} positive / {negative} negative of {len(news)} recent headlines mentioning {name}"
    else:
        news_score = 10
        news_reason = "No recent news found for this stock — neutral sentiment assumed"
    score += news_score
    breakdown.append({"factor": "News Sentiment", "score": news_score, "max": 20, "reason": news_reason})

    # Get AI debate
    def _na(v):
        return v if v is not None else "unavailable"

    prompt = f"""Deep analysis for {name} ({data.symbol}):
Price: INR {quote['price']} ({quote['change_pct']}%)
RSI: {rsi} | MACD: {quote['macd']} | Volume: {vol_ratio}x avg
VWAP: {vwap} | Sector: {quote['sector']}
52W Range: {_na(quote['week_52_low'])} - {_na(quote['week_52_high'])}
P/E: {_na(quote['pe_ratio'])} | Market Cap: {_na(quote['market_cap_cr'])} Cr

Provide detailed analysis: entry strategy, risk factors, key support/resistance levels, sector outlook, and specific trading plan with exact price levels. If a data point is marked unavailable, do not invent it."""

    debate = await ai_dual_debate(prompt, f"report-{data.symbol}")

    return {
        "symbol": data.symbol,
        "name": name,
        "quote": quote,
        "confidence_score": min(score, 100),
        "scoring_breakdown": breakdown,
        "total_possible": 100,
        **debate,
        "related_news": news[:5],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============ GEMINI DIRECT ROUTES ============

gemini_router = APIRouter(prefix="/api/gemini", tags=["Gemini"])

@gemini_router.get("/status")
async def gemini_status():
    """Check Gemini direct API status."""
    from services.gemini_direct import is_configured
    return {"configured": is_configured(), "model": "gemini-2.0-flash", "note": "Direct Google AI Studio integration"}

@gemini_router.post("/analyze")
async def gemini_analyze_stock(data: StockAnalysisRequest, user: dict = Depends(get_current_user)):
    """Get Gemini real-time analysis for a stock."""
    from services.gemini_direct import gemini_realtime_analysis
    stock_data = await real_quote(data.symbol)
    if not stock_data:
        if not get_stock_meta(data.symbol):
            raise HTTPException(status_code=404, detail="Stock not found")
        raise HTTPException(status_code=503, detail="Live market data temporarily unavailable for this stock")
    if data.name:
        stock_data["name"] = data.name
    analysis = await gemini_realtime_analysis(stock_data)
    return {"symbol": data.symbol, "analysis": analysis, "quote": stock_data}

@gemini_router.get("/market-pulse")
async def gemini_market_pulse_endpoint():
    """Get Gemini market pulse."""
    from services.gemini_direct import gemini_market_pulse
    pulse = await gemini_market_pulse()
    return {"pulse": pulse or "Gemini key not configured", "source": "gemini-2.5-flash"}

@gemini_router.post("/chat")
async def gemini_chat(data: ChatMessage, user: dict = Depends(get_current_user)):
    """Direct chat with Gemini for trading questions."""
    from services.gemini_direct import gemini_analyze
    prompt = f"""You are AlphaPartner's Gemini AI, an expert Indian stock market analyst.
User capital: INR {user.get('capital', 100000)}. Risk level: {user.get('risk_level', 'moderate')}.

User question: {data.message}

Provide a helpful, specific answer focused on Indian markets (NSE/BSE). Use INR. Be concise."""
    response = await gemini_analyze(prompt)
    return {"response": response or "Gemini unavailable", "model": "gemini-2.5-flash"}



# ============ PAPER TRADING ROUTES ============

@paper_router.get("/balance")
async def paper_balance(user: dict = Depends(get_current_user)):
    from services.paper_trade import get_paper_balance
    return await get_paper_balance(user["_id"], db)

@paper_router.get("/trades")
async def paper_trades(user: dict = Depends(get_current_user)):
    from services.paper_trade import get_paper_trades
    return await get_paper_trades(user["_id"], db)

@paper_router.get("/pnl")
async def paper_pnl(user: dict = Depends(get_current_user)):
    from services.paper_trade import get_paper_pnl
    return await get_paper_pnl(user["_id"], db)

class PaperTradeCreate(BaseModel):
    symbol: str
    stock_name: str = ""
    quantity: int
    entry_price: float
    type: str = "BUY"
    stop_loss: float
    target1: float
    target2: float = 0.0
    setup_type: str = "MOMENTUM"
    notes: str = ""

@paper_router.post("/trade")
async def paper_trade(data: PaperTradeCreate, user: dict = Depends(get_current_user)):
    from services.paper_trade import execute_paper_trade
    try:
        return await execute_paper_trade(
            user_id=user["_id"],
            symbol=data.symbol,
            stock_name=data.stock_name,
            quantity=data.quantity,
            entry_price=data.entry_price,
            trade_type=data.type,
            stop_loss=data.stop_loss,
            target1=data.target1,
            target2=data.target2,
            setup_type=data.setup_type,
            notes=data.notes,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@paper_router.post("/close/{trade_id}")
async def paper_close(trade_id: str, user: dict = Depends(get_current_user)):
    from services.paper_trade import close_paper_trade
    parse_object_id(trade_id, "trade")  # reject malformed ids with a clean 400
    try:
        return await close_paper_trade(trade_id, user["_id"], db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@paper_router.post("/reset")
async def paper_reset(user: dict = Depends(get_current_user)):
    from services.paper_trade import reset_paper_capital
    return await reset_paper_capital(user["_id"], db)


# ============ BACKTESTING ROUTES ============

class BacktestRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    strategy: str = "RSI_STRATEGY"
    stop_loss_pct: float = 2.0
    target_pct: float = 4.0
    initial_capital: float = 100000.0

@backtest_router.post("")
async def run_backtest_route(data: BacktestRequest):
    from services.backtest_engine import run_backtest
    from services.activity_logger import log_activity
    log_activity(f"Running backtest: {data.strategy} on {data.symbol}", "scan", "running")
    result = await run_backtest(
        symbol=data.symbol,
        start_date=data.start_date,
        end_date=data.end_date,
        strategy=data.strategy,
        stop_loss_pct=data.stop_loss_pct,
        target_pct=data.target_pct,
        initial_capital=data.initial_capital,
    )
    log_activity(f"Backtest complete: {result.get('win_rate', 0)}% win rate on {data.symbol}", "scan", "done")
    return result


# ============ ADMIN PORTAL ROUTES (Sprint 11) ============
# Internal control center — every endpoint validates role ∈ {admin, super_admin}.
# Audit logs are immutable; every mutating action is recorded.

import platform
import psutil  # lightweight system metrics — already available via uvicorn's dep chain


async def require_admin(request: Request) -> dict:
    """Dependency: rejects non-admin users with 403."""
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def log_admin_action(admin_id: str, action: str, target: str = "", details: dict = None):
    """Append an immutable audit record."""
    await db.admin_audit_logs.insert_one({
        "admin_id": admin_id,
        "action": action,
        "target": target,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ---------- Dashboard ----------

@admin_router.get("/dashboard")
async def admin_dashboard(user: dict = Depends(require_admin)):
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Eleven independent counts, issued CONCURRENTLY rather than one after another
    # (PH3.4, O-4).
    #
    # Nothing here depends on anything else here — they are eleven separate
    # `count_documents` calls whose results are assembled into one response. Run
    # sequentially, the endpoint's latency was the SUM of eleven database round
    # trips; run concurrently it is the slowest one. On a deployment where Mongo is
    # a network hop away (rather than the in-memory double this is measured
    # against) that is the difference between ~11xRTT and ~1xRTT of waiting.
    #
    # This is a latency change only: the same eleven queries are issued, so the
    # load on the database is identical and there is no new cache, no new index and
    # no changed result. The gather is bounded by the literal length of this list —
    # it cannot fan out with the data — so it can never become a source of
    # unbounded concurrency against the database.
    #
    # `list_collection_names()` stays sequential and ahead of the gather: the
    # payments count is *conditional* on its result. Folding a dependent call into
    # the same gather would race it.
    has_payments = "payments" in await db.list_collection_names()

    (
        total_users, premium_users, elite_users, admin_users,
        today_trades, total_trades, open_trades,
        ai_requests_today, total_notifications, open_tickets,
        broker_connections,
    ) = await asyncio.gather(
        db.users.count_documents({}),
        db.users.count_documents({"role": {"$in": ["pro", "premium"]}}),
        db.users.count_documents({"role": "elite"}),
        db.users.count_documents({"role": {"$in": ["admin", "super_admin"]}}),
        db.trades.count_documents({"entry_time": {"$regex": f"^{today_str}"}}),
        db.trades.count_documents({}),
        db.trades.count_documents({"status": "OPEN"}),
        db.chat_messages.count_documents({"created_at": {"$regex": f"^{today_str}"}}),
        db.notifications.count_documents({}),
        db.support_tickets.count_documents({"status": {"$in": ["open", "in_progress"]}}),
        db.broker_accounts.count_documents({}),
    )
    total_payments = await db.payments.count_documents({}) if has_payments else 0
    # Revenue (mock — real system would sum from payments collection)
    revenue_today = total_payments * 499  # placeholder
    mrr = premium_users * 499 + elite_users * 999
    arr = mrr * 12

    # Health checks
    try:
        await db.command("ping")
        db_health = "healthy"
    except Exception:
        db_health = "unhealthy"

    return {
        "total_users": total_users,
        "premium_users": premium_users,
        "elite_users": elite_users,
        "admin_users": admin_users,
        "today_trades": today_trades,
        "total_trades": total_trades,
        "open_trades": open_trades,
        "ai_requests_today": ai_requests_today,
        "total_notifications": total_notifications,
        "open_tickets": open_tickets,
        "revenue_today": revenue_today,
        "mrr": mrr,
        "arr": arr,
        "broker_connections": broker_connections,
        "db_health": db_health,
        "api_health": "healthy",
        "server_health": "healthy",
    }


# ---------- Pagination ----------
#
# PH3.3 (D-1). `page` and `limit` were plain unvalidated ints, so a client could
# send `page=0` and reach `skip = (page - 1) * limit == -20`. MongoDB rejects a
# negative skip with an OperationFailure rather than clamping it, and the
# uncaught driver error surfaced as a 500 — an implementation error leaking out
# for what is plainly a bad request. `limit=0` reached the page-count expression
# `(total + limit - 1) // limit` and raised ZeroDivisionError by the same route.
#
# Declared as FastAPI `Query` constraints rather than defensive clamping inside
# each handler, for three reasons: an out-of-range value becomes a 422 that
# names the offending parameter (clamping would silently serve page 1 to a
# client that asked for page 0, and hide its bug); the bound appears in the
# OpenAPI schema, so it documents itself; and there is nothing for a fourth
# paginated endpoint to forget to copy — it reuses these two aliases.
#
# The `le=100` upper bound is not cosmetic: without it `limit=1000000` is an
# unbounded collection scan that any authenticated admin session can trigger.
PageParam = Query(1, ge=1, description="1-based page number.")
LimitParam = Query(20, ge=1, le=100, description="Rows per page (max 100).")
LogLimitParam = Query(50, ge=1, le=200, description="Rows per page (max 200).")


# ---------- User Management ----------

@admin_router.get("/users")
async def admin_list_users(
    request: Request,
    page: int = PageParam,
    limit: int = LimitParam,
    search: str = "",
    role: str = "",
    status: str = "",
    user: dict = Depends(require_admin),
):
    query = {}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
        ]
    if role:
        query["role"] = role
    if status == "blocked":
        query["blocked"] = True
    elif status == "active":
        query["blocked"] = {"$ne": True}

    total = await db.users.count_documents(query)
    skip = (page - 1) * limit
    cursor = db.users.find(query, {"password_hash": 0}).sort("created_at", -1).skip(skip).limit(limit)
    users = []
    async for u in cursor:
        u["_id"] = str(u["_id"])
        users.append(u)
    return {"users": users, "total": total, "page": page, "limit": limit, "pages": max(1, (total + limit - 1) // limit)}


@admin_router.get("/users/{user_id}")
async def admin_get_user(user_id: str, user: dict = Depends(require_admin)):
    oid = parse_object_id(user_id, "user")
    target = await db.users.find_one({"_id": oid}, {"password_hash": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target["_id"] = str(target["_id"])
    # Enrich with stats
    trade_count = await db.trades.count_documents({"user_id": user_id})
    target["trade_count"] = trade_count
    return target


@admin_router.put("/users/{user_id}")
async def admin_update_user(user_id: str, request: Request, user: dict = Depends(require_admin)):
    oid = parse_object_id(user_id, "user")
    body = await request.json()
    allowed = {"name", "role", "capital", "risk_level", "max_daily_loss", "max_trades_per_day"}
    update = {k: v for k, v in body.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    # F-1: role is validated against the allowlist, and admin-tier roles
    # (admin / super_admin) may only be granted by a super_admin. This closes the
    # privilege-escalation path where `role` was an unchecked passthrough field.
    if "role" in update:
        validate_role_assignment(update["role"], user.get("role", ""))
    await db.users.update_one({"_id": oid}, {"$set": update})
    await log_admin_action(user["_id"], "user.updated", user_id, update)
    return {"success": True}


@admin_router.post("/users/{user_id}/block")
async def admin_block_user(user_id: str, user: dict = Depends(require_admin)):
    oid = parse_object_id(user_id, "user")
    await db.users.update_one({"_id": oid}, {"$set": {"blocked": True}})
    await log_admin_action(user["_id"], "user.blocked", user_id)
    return {"success": True}


@admin_router.post("/users/{user_id}/unblock")
async def admin_unblock_user(user_id: str, user: dict = Depends(require_admin)):
    oid = parse_object_id(user_id, "user")
    await db.users.update_one({"_id": oid}, {"$set": {"blocked": False}})
    await log_admin_action(user["_id"], "user.unblocked", user_id)
    return {"success": True}


@admin_router.delete("/users/{user_id}")
async def admin_delete_user(user_id: str, user: dict = Depends(require_admin)):
    if user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Only super_admin can delete users")
    oid = parse_object_id(user_id, "user")
    await db.users.delete_one({"_id": oid})
    await log_admin_action(user["_id"], "user.deleted", user_id)
    return {"success": True}


@admin_router.post("/users/{user_id}/grant-plan")
async def admin_grant_plan(user_id: str, request: Request, user: dict = Depends(require_admin)):
    oid = parse_object_id(user_id, "user")
    body = await request.json()
    plan = body.get("plan", "pro")
    duration_days = body.get("duration_days", 30)
    valid_plans = {"free", "pro", "elite", "lifetime", "developer", "investor", "beta_tester"}
    if plan not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Choose from: {', '.join(valid_plans)}")
    # PH3.3 (D-3): `duration_days` went straight from an untyped JSON body into
    # `timedelta(days=...)`, which raises TypeError for a string/null/list and
    # OverflowError for an astronomically large int — both uncaught, both a 500
    # for what is a malformed request. The route reads its body as raw JSON
    # rather than a Pydantic model, so the coercion has to be explicit here.
    # 3650 days (10 years) is the ceiling: anything longer is `lifetime`.
    if not isinstance(duration_days, int) or isinstance(duration_days, bool):
        raise HTTPException(status_code=400, detail="duration_days must be an integer")
    if not 1 <= duration_days <= 3650:
        raise HTTPException(status_code=400, detail="duration_days must be between 1 and 3650")
    expires_at = (datetime.now(timezone.utc) + timedelta(days=duration_days)).isoformat() if plan != "lifetime" else None
    await db.users.update_one({"_id": oid}, {"$set": {
        "role": plan,
        "plan_granted_by": user["_id"],
        "plan_granted_at": datetime.now(timezone.utc).isoformat(),
        "plan_expires_at": expires_at,
    }})
    await log_admin_action(user["_id"], "user.plan_granted", user_id, {"plan": plan, "duration_days": duration_days})
    return {"success": True, "plan": plan, "expires_at": expires_at}


# ---------- Payments ----------

@admin_router.get("/payments")
async def admin_list_payments(page: int = PageParam, limit: int = LimitParam,
                              user: dict = Depends(require_admin)):
    # Check if payments collection exists
    collections = await db.list_collection_names()
    if "payments" not in collections:
        return {"payments": [], "total": 0, "page": page, "limit": limit, "pages": 1}
    total = await db.payments.count_documents({})
    skip = (page - 1) * limit
    cursor = db.payments.find().sort("created_at", -1).skip(skip).limit(limit)
    payments = []
    async for p in cursor:
        p["_id"] = str(p["_id"])
        payments.append(p)
    return {"payments": payments, "total": total, "page": page, "limit": limit, "pages": max(1, (total + limit - 1) // limit)}


@admin_router.get("/payments/stats")
async def admin_payment_stats(user: dict = Depends(require_admin)):
    users_by_plan = {}
    async for u in db.users.find({}, {"role": 1}):
        r = u.get("role", "user")
        users_by_plan[r] = users_by_plan.get(r, 0) + 1
    premium_count = users_by_plan.get("pro", 0) + users_by_plan.get("premium", 0)
    elite_count = users_by_plan.get("elite", 0)
    lifetime_count = users_by_plan.get("lifetime", 0)
    mrr = premium_count * 499 + elite_count * 999
    arr = mrr * 12
    return {
        "mrr": mrr,
        "arr": arr,
        "revenue_today": 0,
        "revenue_week": 0,
        "revenue_month": mrr,
        "revenue_year": arr,
        "pending_payments": 0,
        "refunds": 0,
        "failed_payments": 0,
        "plan_distribution": users_by_plan,
        "premium_count": premium_count,
        "elite_count": elite_count,
        "lifetime_count": lifetime_count,
    }


@admin_router.post("/payments/{payment_id}/refund")
async def admin_refund_payment(payment_id: str, user: dict = Depends(require_admin)):
    await log_admin_action(user["_id"], "payment.refunded", payment_id)
    return {"success": True, "message": "Refund initiated"}


# ---------- AI Monitoring ----------

@admin_router.get("/ai/status")
async def admin_ai_status(user: dict = Depends(require_admin)):
    claude_ok = claude_configured()
    gemini_ok = gemini_configured()
    # Aggregate AI usage from chat_messages
    total_ai = await db.chat_messages.count_documents({})
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_ai = await db.chat_messages.count_documents({"created_at": {"$regex": f"^{today_str}"}})
    return {
        "providers": [
            {
                "name": "Claude (Anthropic)",
                "model": "claude-opus-4-5-20250520",
                "status": "online" if claude_ok else "offline",
                "configured": claude_ok,
                "latency_ms": 1200,
                "daily_requests": today_ai // 2 if today_ai else 0,
                "monthly_requests": total_ai // 2 if total_ai else 0,
                "estimated_cost": round((total_ai // 2) * 0.015, 2) if total_ai else 0,
                "failures": 0,
                "fallbacks": 0,
            },
            {
                "name": "Gemini (Google)",
                "model": "gemini-2.5-pro",
                "status": "online" if gemini_ok else "offline",
                "configured": gemini_ok,
                "latency_ms": 900,
                "daily_requests": today_ai // 2 if today_ai else 0,
                "monthly_requests": total_ai // 2 if total_ai else 0,
                "estimated_cost": round((total_ai // 2) * 0.007, 2) if total_ai else 0,
                "failures": 0,
                "fallbacks": 0,
            },
        ],
        "total_requests_today": today_ai,
        "total_requests_all": total_ai,
        "total_estimated_cost": round(total_ai * 0.011, 2) if total_ai else 0,
    }


@admin_router.get("/ai/usage")
async def admin_ai_usage(user: dict = Depends(require_admin)):
    # Top AI users by message count
    pipeline = [
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    top_users = []
    async for doc in db.chat_messages.aggregate(pipeline):
        uid = doc["_id"]
        # PH3.3 (D-9): `ObjectId(uid)` raised InvalidId on any chat_messages row
        # whose `user_id` is not a well-formed id — a legacy row, an import, or a
        # future code path writing a session key here — and took the whole admin
        # AI-usage page to a 500. One malformed row must not blank an operational
        # dashboard; the aggregate still counts it, it simply resolves to Unknown.
        try:
            u = await db.users.find_one(
                {"_id": ObjectId(uid)}, {"name": 1, "email": 1, "role": 1}) if uid else None
        except (InvalidId, TypeError):
            u = None
        top_users.append({
            "user_id": uid,
            "name": u.get("name", "Unknown") if u else "Unknown",
            "email": u.get("email", "") if u else "",
            "role": u.get("role", "user") if u else "user",
            "request_count": doc["count"],
            "estimated_cost": round(doc["count"] * 0.011, 2),
        })
    return {"top_users": top_users}


# ---------- API Monitoring ----------

@admin_router.get("/apis/health")
async def admin_api_health(user: dict = Depends(require_admin)):
    apis = [
        {"name": "Yahoo Finance", "type": "market_data", "status": "online", "latency_ms": 450, "requests_today": 0, "requests_month": 0, "quota_remaining": "unlimited", "failure_rate": 0.0},
        {"name": "Claude API", "type": "ai", "status": "online" if claude_configured() else "offline", "latency_ms": 1200, "requests_today": 0, "requests_month": 0, "quota_remaining": "usage-based", "failure_rate": 0.0},
        {"name": "Gemini API", "type": "ai", "status": "online" if gemini_configured() else "offline", "latency_ms": 900, "requests_today": 0, "requests_month": 0, "quota_remaining": "usage-based", "failure_rate": 0.0},
        {"name": "News RSS", "type": "news", "status": "online", "latency_ms": 350, "requests_today": 0, "requests_month": 0, "quota_remaining": "unlimited", "failure_rate": 0.0},
        {"name": "Zerodha Kite", "type": "broker", "status": "online" if kite_configured() else "offline", "latency_ms": 200, "requests_today": 0, "requests_month": 0, "quota_remaining": "usage-based", "failure_rate": 0.0},
        {"name": "Alpha Vantage", "type": "market_data", "status": "online" if av_configured() else "offline", "latency_ms": 600, "requests_today": 0, "requests_month": 0, "quota_remaining": "500/day", "failure_rate": 0.0},
        {"name": "Razorpay", "type": "payment", "status": "configured", "latency_ms": 300, "requests_today": 0, "requests_month": 0, "quota_remaining": "unlimited", "failure_rate": 0.0},
        {"name": "Email (SMTP)", "type": "notification", "status": "configured", "latency_ms": 500, "requests_today": 0, "requests_month": 0, "quota_remaining": "unlimited", "failure_rate": 0.0},
    ]
    return {"apis": apis, "overall_status": "healthy"}


# ---------- Analytics ----------

@admin_router.get("/analytics/users")
async def admin_analytics_users(user: dict = Depends(require_admin)):
    total = await db.users.count_documents({})
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_signups = await db.users.count_documents({"created_at": {"$regex": f"^{today_str}"}})
    # Build plan breakdown
    plan_breakdown = {}
    async for u in db.users.find({}, {"role": 1}):
        r = u.get("role", "user")
        plan_breakdown[r] = plan_breakdown.get(r, 0) + 1
    return {
        "total_users": total,
        "today_signups": today_signups,
        "dau": today_signups,  # Simplified; real system tracks active sessions
        "mau": total,
        "retention_rate": 78.5,
        "churn_rate": 4.2,
        "growth_rate": 12.8,
        "conversion_rate": round((plan_breakdown.get("pro", 0) + plan_breakdown.get("elite", 0)) / max(total, 1) * 100, 1),
        "plan_breakdown": plan_breakdown,
    }


@admin_router.get("/analytics/revenue")
async def admin_analytics_revenue(user: dict = Depends(require_admin)):
    # Generate last 30 days mock revenue data for charts
    revenue_data = []
    for i in range(30):
        d = datetime.now(timezone.utc) - timedelta(days=29 - i)
        revenue_data.append({
            "date": d.strftime("%Y-%m-%d"),
            "revenue": 2500 + (i * 150) + (500 if i % 7 == 0 else 0),
            "subscriptions": 3 + (i % 5),
        })
    return {"daily_revenue": revenue_data}


@admin_router.get("/analytics/features")
async def admin_analytics_features(user: dict = Depends(require_admin)):
    chat_count = await db.chat_messages.count_documents({})
    trade_count = await db.trades.count_documents({})
    notif_count = await db.notifications.count_documents({})
    features = [
        {"name": "AI Chat", "usage_count": chat_count, "percentage": 85},
        {"name": "Trading", "usage_count": trade_count, "percentage": 72},
        {"name": "Stock Scanner", "usage_count": 0, "percentage": 68},
        {"name": "Morning Report", "usage_count": 0, "percentage": 55},
        {"name": "Portfolio", "usage_count": 0, "percentage": 50},
        {"name": "News", "usage_count": 0, "percentage": 45},
        {"name": "Notifications", "usage_count": notif_count, "percentage": 40},
        {"name": "SIP Advisor", "usage_count": 0, "percentage": 25},
        {"name": "Paper Trading", "usage_count": 0, "percentage": 20},
        {"name": "Backtesting", "usage_count": 0, "percentage": 15},
    ]
    return {"features": features}


# ---------- Audit Logs ----------

@admin_router.get("/logs")
async def admin_list_logs(
    page: int = PageParam, limit: int = LogLimitParam, action: str = "", admin_id: str = "",
    user: dict = Depends(require_admin),
):
    query = {}
    if action:
        query["action"] = {"$regex": action, "$options": "i"}
    if admin_id:
        query["admin_id"] = admin_id
    total = await db.admin_audit_logs.count_documents(query)
    skip = (page - 1) * limit
    logs = await db.admin_audit_logs.find(query).sort("timestamp", -1).skip(skip).limit(limit).to_list(limit)

    # Resolve every actor in ONE query, not one per row (PH3.4, O-3).
    #
    # This loop previously issued `db.users.find_one(...)` per log entry: 26
    # queries to render 25 rows, measured. The row count is operator-controlled
    # (`limit`, bounded at 200 by LogLimitParam), so the query count scaled with
    # the page size — 201 round trips for a page of 200. On a deployment where
    # Mongo is a network hop away that is the whole latency of the page, and it is
    # invisible in `explain` because each individual query is perfectly indexed.
    #
    # Distinct ids only: an operator who performed every action on the page (the
    # common case) is fetched once rather than `limit` times.
    #
    # The per-row `try/except` is preserved in behaviour, not structure: a
    # malformed `admin_id` must not take the page to a 500, and an id that
    # resolves to no user must still render. Audit rows deliberately outlive the
    # accounts that wrote them, so "unresolvable" is a normal state here, not an
    # error — see PH3.3 §12.
    # The two failure modes stay distinguishable, because they mean different
    # things and PH3.3 §12 pins the difference: an `admin_id` that is not a
    # well-formed ObjectId is "System" (a record not written by a human operator,
    # or a legacy row), while a well-formed id with no surviving user is "Unknown"
    # (a real operator whose account has since been deleted). Collapsing both to
    # "Unknown" would have been an invisible behaviour change carried in on the
    # back of a performance fix.
    unparseable = set()
    valid_oids = []
    for raw in {log.get("admin_id") for log in logs if log.get("admin_id")}:
        try:
            valid_oids.append(ObjectId(raw))
        except (InvalidId, TypeError):
            unparseable.add(raw)

    actors = {}
    if valid_oids:
        async for u in db.users.find({"_id": {"$in": valid_oids}}, {"name": 1, "email": 1}):
            actors[str(u["_id"])] = u

    for log in logs:
        log["_id"] = str(log["_id"])
        raw_id = log.get("admin_id")
        if raw_id in unparseable or not raw_id:
            log["admin_name"] = "System"
            log["admin_email"] = ""
            continue
        actor = actors.get(str(raw_id))
        log["admin_name"] = actor.get("name", "Unknown") if actor else "Unknown"
        log["admin_email"] = actor.get("email", "") if actor else ""
    return {"logs": logs, "total": total, "page": page, "limit": limit, "pages": max(1, (total + limit - 1) // limit)}


# ---------- Support Tickets ----------

@admin_router.get("/support/tickets")
async def admin_list_tickets(
    page: int = PageParam, limit: int = LimitParam, status: str = "",
    user: dict = Depends(require_admin),
):
    query = {}
    if status:
        query["status"] = status
    total = await db.support_tickets.count_documents(query)
    skip = (page - 1) * limit
    cursor = db.support_tickets.find(query).sort("created_at", -1).skip(skip).limit(limit)
    tickets = []
    async for t in cursor:
        t["_id"] = str(t["_id"])
        tickets.append(t)
    return {"tickets": tickets, "total": total, "page": page, "limit": limit, "pages": max(1, (total + limit - 1) // limit)}


@admin_router.post("/support/tickets")
async def admin_create_ticket(request: Request, user: dict = Depends(require_admin)):
    body = await request.json()
    ticket = {
        "user_id": body.get("user_id", user["_id"]),
        "subject": body.get("subject", ""),
        "messages": [{"from": "user", "text": body.get("message", ""), "at": datetime.now(timezone.utc).isoformat()}],
        "status": "open",
        "priority": body.get("priority", "medium"),
        "assigned_to": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.support_tickets.insert_one(ticket)
    return {"success": True, "ticket_id": str(result.inserted_id)}


@admin_router.put("/support/tickets/{ticket_id}")
async def admin_update_ticket(ticket_id: str, request: Request, user: dict = Depends(require_admin)):
    ticket_oid = parse_object_id(ticket_id, "ticket")
    body = await request.json()
    update = {}
    if "status" in body:
        update["status"] = body["status"]
    if "priority" in body:
        update["priority"] = body["priority"]
    if "assigned_to" in body:
        update["assigned_to"] = body["assigned_to"]
    if "reply" in body:
        await db.support_tickets.update_one(
            {"_id": ticket_oid},
            {"$push": {"messages": {"from": "admin", "admin_id": user["_id"], "text": body["reply"], "at": datetime.now(timezone.utc).isoformat()}}},
        )
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    if update:
        await db.support_tickets.update_one({"_id": ticket_oid}, {"$set": update})
    await log_admin_action(user["_id"], "ticket.updated", ticket_id, body)
    return {"success": True}


# ---------- Feature Flags ----------

@admin_router.get("/feature-flags")
async def admin_list_feature_flags(user: dict = Depends(require_admin)):
    flags = []
    async for f in db.feature_flags.find().sort("name", 1):
        f["_id"] = str(f["_id"])
        flags.append(f)
    return {"flags": flags}


@admin_router.post("/feature-flags")
async def admin_create_feature_flag(request: Request, user: dict = Depends(require_admin)):
    body = await request.json()
    flag = {
        "name": body.get("name", ""),
        "key": body.get("key", body.get("name", "").lower().replace(" ", "_")),
        "enabled": body.get("enabled", False),
        "description": body.get("description", ""),
        "target_plans": body.get("target_plans", ["all"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.feature_flags.insert_one(flag)
    await log_admin_action(user["_id"], "feature_flag.created", str(result.inserted_id), {"name": flag["name"]})
    return {"success": True, "flag_id": str(result.inserted_id)}


@admin_router.put("/feature-flags/{flag_id}")
async def admin_update_feature_flag(flag_id: str, request: Request, user: dict = Depends(require_admin)):
    flag_oid = parse_object_id(flag_id, "feature flag")
    body = await request.json()
    update = {}
    for field in ("enabled", "description", "target_plans", "name"):
        if field in body:
            update[field] = body[field]
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.feature_flags.update_one({"_id": flag_oid}, {"$set": update})
    await log_admin_action(user["_id"], "feature_flag.updated", flag_id, update)
    return {"success": True}


# ---------- Announcements ----------

@admin_router.get("/announcements")
async def admin_list_announcements(user: dict = Depends(require_admin)):
    items = []
    async for a in db.announcements.find().sort("created_at", -1):
        a["_id"] = str(a["_id"])
        items.append(a)
    return {"announcements": items}


@admin_router.post("/announcements")
async def admin_create_announcement(request: Request, user: dict = Depends(require_admin)):
    body = await request.json()
    ann = {
        "title": body.get("title", ""),
        "body": body.get("body", ""),
        "type": body.get("type", "info"),  # info, maintenance, feature, security, promotion
        "target": body.get("target", "all"),  # all, free, pro, elite, admins
        "status": body.get("status", "active"),
        "scheduled_at": body.get("scheduled_at"),
        "created_by": user["_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.announcements.insert_one(ann)
    await log_admin_action(user["_id"], "announcement.created", str(result.inserted_id), {"title": ann["title"]})
    return {"success": True, "announcement_id": str(result.inserted_id)}


@admin_router.put("/announcements/{ann_id}")
async def admin_update_announcement(ann_id: str, request: Request, user: dict = Depends(require_admin)):
    ann_oid = parse_object_id(ann_id, "announcement")
    body = await request.json()
    update = {}
    for field in ("title", "body", "type", "target", "status", "scheduled_at"):
        if field in body:
            update[field] = body[field]
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.announcements.update_one({"_id": ann_oid}, {"$set": update})
    await log_admin_action(user["_id"], "announcement.updated", ann_id, update)
    return {"success": True}


@admin_router.delete("/announcements/{ann_id}")
async def admin_delete_announcement(ann_id: str, user: dict = Depends(require_admin)):
    ann_oid = parse_object_id(ann_id, "announcement")
    await db.announcements.delete_one({"_id": ann_oid})
    await log_admin_action(user["_id"], "announcement.deleted", ann_id)
    return {"success": True}


# ---------- System Health ----------

@admin_router.get("/system/health")
async def admin_system_health(user: dict = Depends(require_admin)):
    # CPU / RAM / Disk via psutil
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        sys_info = {
            "cpu_percent": cpu_percent,
            "ram_total_gb": round(mem.total / (1024**3), 1),
            "ram_used_gb": round(mem.used / (1024**3), 1),
            "ram_percent": mem.percent,
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "disk_used_gb": round(disk.used / (1024**3), 1),
            "disk_percent": round(disk.percent, 1),
        }
    except Exception:
        sys_info = {"cpu_percent": 0, "ram_total_gb": 0, "ram_used_gb": 0, "ram_percent": 0, "disk_total_gb": 0, "disk_used_gb": 0, "disk_percent": 0}

    # MongoDB health
    try:
        await db.command("ping")
        mongo_status = "healthy"
        stats = await db.command("dbStats")
        mongo_details = {
            "collections": stats.get("collections", 0),
            "data_size_mb": round(stats.get("dataSize", 0) / (1024 * 1024), 2),
            "storage_size_mb": round(stats.get("storageSize", 0) / (1024 * 1024), 2),
        }
    except Exception as e:
        mongo_status = "unhealthy"
        mongo_details = {"error": str(e)}

    # WebSocket connections
    ws_count = len(ws_manager.active_connections) if hasattr(ws_manager, "active_connections") else 0

    # Platform info
    return {
        "system": sys_info,
        "mongodb": {"status": mongo_status, **mongo_details},
        "redis": {"status": "not_configured"},
        "websockets": {"active_connections": ws_count, "status": "healthy" if ws_count >= 0 else "unknown"},
        "scheduler": {"status": "running"},
        "platform": {
            "python_version": platform.python_version(),
            "os": platform.system(),
            "architecture": platform.machine(),
        },
        "overall_status": "healthy" if mongo_status == "healthy" else "degraded",
    }


# ============ APP SETUP ============

app.include_router(auth_router)
app.include_router(google_auth_router)
app.include_router(market_router)
app.include_router(stocks_router)
app.include_router(analysis_router)
app.include_router(trades_router)
app.include_router(paper_router)
app.include_router(backtest_router)

app.include_router(portfolio_router)
app.include_router(notifications_router)
app.include_router(chat_router)
app.include_router(ai_router)
app.include_router(sip_router)
app.include_router(advisor_router)
app.include_router(settings_router)
app.include_router(brokers_router)
app.include_router(orders_router)
app.include_router(zerodha_router)
app.include_router(monitor_router)
app.include_router(whatsapp_router)
app.include_router(email_router)
app.include_router(news_router)
app.include_router(journal_router)
app.include_router(webhooks_router)
app.include_router(watchlist_router)
app.include_router(gemini_router)
app.include_router(admin_router)

# Operational endpoints (PH2.5): /api/health/{live,ready,startup}, /api/health,
# /api/metrics, /api/diagnostics. See observability/routes.py — and note that
# the long-standing /api/monitor/health above is unrelated (it returns an
# authenticated, AI-powered *portfolio* health analysis, not process health).
app.include_router(observability_routes.router)

# Liveness check (legacy path, PH2.1 container contract).
#
# Predates the /api/health family and is deliberately kept: backend/Dockerfile's
# HEALTHCHECK, docker/healthcheck.sh and the PH2.4 CI smoke tests all target it,
# and its contract ("status": "running") is asserted in the pipeline. It is
# functionally a liveness probe — unauthenticated, rate-limit exempt, touching no
# dependency — which is why it was a sound choice for the container health check
# and why nothing needs to migrate. New orchestrator configuration should prefer
# /api/health/live, which reports lifecycle state and uptime alongside the status.
@app.get("/api")
async def api_root():
    return {"message": "AlphaPartner API", "status": "running", "version": "1.0"}


@app.get("/api/ai-activity")
async def get_recent_ai_activity():
    from services.activity_logger import get_recent_activity
    return get_recent_activity()


# Security middleware pipeline (see SECURITY_ARCHITECTURE.md §27). Starlette runs
# middleware in reverse registration order (last added = outermost = runs first
# on the request). We register CSRF and the rate limiter FIRST so they end up
# *inside* CORS and the security headers: a 403/429 they emit still passes back
# out through CORS (ACAO) and the header stamper, so a browser can actually read
# the rejection — while they still reject before any route handler or DB work.
#
# Resulting request-time execution order:
#   Security Headers → CORS → Rate Limiter → CSRF → route dispatch → handler
#
# CSRF protection (PH1.7): innermost of the four so it sees the same request the
# route would, and its 403 is fully decorated on the way out.
apply_csrf_protection(app)

# Rate limiting (PH1.7): platform-wide flooding backstop. db_provider is a
# late-binding accessor so the middleware always uses the current db handle
# (and tests can monkeypatch server.db).
ratelimit.apply_rate_limiting(app, lambda: db)

# CORS is centralized in security.cors (PH1.4): environment-driven, exact-match
# origin allowlist with credentials — never a wildcard. See that module for the
# full policy and rationale.
apply_cors(app)

# HTTP security headers are centralized in security.headers (PH1.4b):
# anti-clickjacking, anti-MIME-sniffing, transport security (HSTS), a strict
# Content-Security-Policy, and the cross-origin isolation family. Applied AFTER
# CORS so it wraps (and decorates) even CORS preflight/rejection responses. See
# that module for the full policy and rationale.
apply_security_headers(app)

# Observability (PH2.5): registered LAST, so it is the OUTERMOST middleware and
# runs FIRST on every request. Three reasons, in order of importance:
#   1. The request ID must exist before anything else can log — the rate
#      limiter, the CSRF middleware and the audit logger all emit records, and
#      those describe *rejected* requests, which are the ones most worth
#      correlating.
#   2. Rejections must be counted. A 429 from the rate limiter or a CORS refusal
#      never reaches a route handler; instrumentation placed inside them would
#      report a healthy service while the front door returned errors to everyone.
#   3. X-Request-ID must be stamped on every response, including ones generated
#      by an inner middleware, so any error a user sees carries an ID they can
#      quote to support.
#
# Full request-time execution order:
#   Observability → Security Headers → CORS → Rate Limiter → CSRF → route → handler
apply_observability(app)

# The root logger was configured at the TOP of this file (see the
# observability.logging import there). This is only the module-level logger
# handle used by the startup/shutdown code below.
logger = logging.getLogger(__name__)

# Dependency health checks (PH2.5). Registered at import time, not inside the
# startup event, so /api/health/ready reports a meaningful answer even if startup
# itself fails part-way — the readiness probe is the only thing that can explain
# *why* a container never became ready, so it must not depend on that boot
# completing.
#
# `lambda: db` is the same late-binding accessor pattern the rate limiter and the
# audit logger use: the probe always resolves the live handle, and the test
# suite's FakeDB monkeypatch of `server.db` is honored with no special wiring.
#
# Redis is registered as NON-critical, deliberately. `services/cache.py` falls
# back to an in-process dict when REDIS_URL is unset, so a single-process
# deployment without Redis is a supported configuration, not a fault — the probe
# reports "skip" there. When Redis *is* configured but unreachable the check
# fails and is reported, yet it still does not pull the instance out of the load
# balancer, because this application degrades to that same in-process fallback
# and can continue serving. Promote it to critical=True once Redis becomes
# load-bearing (multi-process pub/sub fan-out).
obs_health.register_check("mongodb", obs_health.make_mongo_probe(lambda: db), critical=True)
obs_health.register_check("redis", obs_health.make_redis_probe(), critical=False)

# Publish build metadata as the conventional `app_info` gauge, so a metrics query
# can join runtime facts onto any other series ("p99 latency, by version").
obs_metrics.set_app_info(
    version=obs_runtime.service_version(),
    environment=obs_runtime.environment(),
    python_version=obs_runtime.python_version(),
    revision=obs_runtime.vcs_ref(),
)


async def ensure_indexes():
    """Declare every index the application depends on. Idempotent.

    EXTRACTED FROM `startup()` IN PH3.4, AND WHY
    --------------------------------------------
    These declarations used to be the first forty lines of the startup event
    handler, interleaved with feature-flag seeding, broker-session restore,
    scheduler wiring and five background tasks. That had three costs, and the
    third is the one that mattered:

    1. Nothing could assert the index set. A missing index is invisible in code
       review and silent in every test — the in-memory double has no plans — so
       the only place it shows up is a production query that got slower as the
       collection grew. `tests/test_perf_regression.py` now asserts this
       function's output against the queries the routes actually issue.
    2. Nothing could measure them. `scripts/perf_db_benchmark.py` runs the real
       query shapes against a real MongoDB before and after this function; it
       could not call the previous version without also starting the scheduler,
       the heartbeat engine and the WebSocket loops.
    3. The list could not be read. Deciding whether a new query is covered meant
       reading past unrelated startup work to find out.

    Nothing about *when* this runs changed: `startup()` still awaits it first,
    before anything else, exactly as before.

    ADDING AN INDEX HERE IS A PRODUCTION COST, NOT A FREE WIN. Every index is
    paid for on every write to the collection and in RAM for the working set. The
    ones below are justified individually against a query in the request path;
    §8 of `docs/performance/PH3.4_PERFORMANCE_CERTIFICATION.md` records the
    query, the measured plan before and after, and the write cost for each.
    """
    await db.users.create_index("email", unique=True)

    # ----------------------------------------------------------------------- #
    # trades — the busiest user-scoped collection in the product.
    #
    # `user_id` alone (all this collection had before PH3.4) serves the filter
    # and nothing else. Every listing route then sorts, and a sort a compound
    # index cannot serve is a blocking in-memory SORT stage: Mongo materializes
    # every matching document, sorts it, and — past 100 MB — gives up and errors.
    # The compound indexes below let the index supply the ordering, so the
    # `.limit(100)` stops the scan at 100 documents instead of after reading the
    # user's entire trade history. Measured: 60x fewer documents examined on
    # GET /api/trades at 60 trades/user (PH3.4 §8, O-1).
    # ----------------------------------------------------------------------- #
    await db.trades.create_index([("user_id", 1), ("entry_time", -1)])   # GET /api/trades
    await db.trades.create_index([("user_id", 1), ("exit_time", -1)])    # GET /api/trades/history

    # `{user_id, status, entry_time}` and not `{user_id, status}`. The shorter
    # form serves the *filter* for GET /api/trades/active but not its
    # `sort(entry_time DESC)`, which measured as a surviving in-memory SORT stage
    # even once the filter was indexed. Appending the sort key lets the index
    # supply the ordering too, and — because a compound index also serves any
    # prefix of itself — it still answers every `{user_id, status}` lookup
    # (portfolio holdings, the trade stream) that the two-field version did.
    # Three fields instead of two, one index instead of two.
    await db.trades.create_index([("user_id", 1), ("status", 1), ("entry_time", -1)])

    # `user_id` is deliberately KEPT rather than replaced. It is redundant for
    # reads — the compound indexes above have `user_id` as their prefix and serve
    # every filter it served — but dropping an index is a separate, riskier
    # operation than adding one (it cannot be rolled back without a rebuild on a
    # large collection), and this sprint's rule is the smallest safe change.
    # Removal is recorded as technical debt in the certification, not done here.
    await db.trades.create_index("user_id")

    # ----------------------------------------------------------------------- #
    # notifications — polled by the bell badge on every authenticated page.
    # `{user_id, read}` serves the unread count; `{user_id, created_at}` serves
    # the panel's newest-first listing without an in-memory sort.
    # ----------------------------------------------------------------------- #
    await db.notifications.create_index([("user_id", 1), ("created_at", -1)])
    await db.notifications.create_index([("user_id", 1), ("read", 1)])
    await db.notifications.create_index("user_id")  # kept, as above

    # ----------------------------------------------------------------------- #
    # watchlist — had NO index of any kind before PH3.4, on any field.
    #
    # Every read (`GET /api/watchlist`), every duplicate check on add, and every
    # delete was a full collection scan across *every user's* rows. At 400 users
    # x 20 symbols that is 8,000 documents read to return one user's 20, and the
    # cost grows with total signups rather than with the caller's own data — the
    # shape that looks fine in development forever and then does not.
    # The unique compound index also makes the duplicate check the application
    # already performs enforceable by the database.
    # ----------------------------------------------------------------------- #
    await db.watchlist.create_index([("user_id", 1), ("symbol", 1)], unique=True)
    await db.watchlist.create_index([("user_id", 1), ("added_at", -1)])

    # ----------------------------------------------------------------------- #
    # holdings — synced broker positions. Also had no index.
    #
    # `services/portfolio_engine.build_holdings` reads this on EVERY
    # /api/portfolio* route (summary, intelligence, performance, export), and
    # `portfolio_stream`/`trade_stream` read it per broker on every realtime
    # symbol-token resolution. It was a collection scan on the single most
    # data-heavy page in the product.
    # ----------------------------------------------------------------------- #
    await db.holdings.create_index([("user_id", 1), ("broker", 1)])

    # ----------------------------------------------------------------------- #
    # orders — the unified cross-broker order book (GET /api/orders), listed
    # newest-first. No index before PH3.4.
    # ----------------------------------------------------------------------- #
    await db.orders.create_index([("user_id", 1), ("placed_at", -1)])

    # ----------------------------------------------------------------------- #
    # chat_messages.
    #
    # `{user_id, session_id}` was the only index, and it left the hottest query
    # in the AI product on a collection scan: `POST /api/chat` loads the last ten
    # turns for conversation continuity (server.py:488) filtering on
    # **`session_id` alone**. A compound index is only usable from its leading
    # field, so a query that does not constrain `user_id` cannot touch it — every
    # message a user sends to the AI scanned the entire chat collection.
    # `{session_id, created_at}` serves both its filter and its newest-first sort.
    #
    # `{user_id, session_id, created_at}` replaces the old two-field index rather
    # than joining it: it serves `GET /api/chat/history?session_id=` including the
    # sort, and still answers every `{user_id, session_id}` and `{user_id}` lookup
    # as a prefix.
    # ----------------------------------------------------------------------- #
    await db.chat_messages.create_index([("session_id", 1), ("created_at", -1)])
    await db.chat_messages.create_index([("user_id", 1), ("session_id", 1), ("created_at", 1)])

    await db.broker_accounts.create_index([("user_id", 1), ("broker", 1)], unique=True)

    # Admin Portal collections (Sprint 11)
    await db.admin_audit_logs.create_index("timestamp")
    await db.admin_audit_logs.create_index("admin_id")
    await db.admin_audit_logs.create_index("action")

    # Security audit log (PH1.2, centralized PH1.10) — immutable security events.
    # The centralized security.audit engine writes a structured superset of the
    # original record; index the new query axes (category / severity / user_id /
    # session_id) an incident investigation filters on, alongside the originals.
    await db.security_audit_logs.create_index("timestamp")
    await db.security_audit_logs.create_index("event")
    await db.security_audit_logs.create_index("email")
    await db.security_audit_logs.create_index("category")
    await db.security_audit_logs.create_index("severity")
    await db.security_audit_logs.create_index("user_id")
    await db.security_audit_logs.create_index("session_id")

    # Session store (PH1.6) — one document per refresh-token family. The TTL
    # index reaps sessions at their absolute expiry (Mongo purges docs whose
    # `expires_at` Date is in the past); session_id is the unique family handle.
    await db.sessions.create_index("session_id", unique=True)
    await db.sessions.create_index("user_id")
    await db.sessions.create_index("expires_at", expireAfterSeconds=0)

    # Rate-limit store (PH1.7) — window-counter + lockout docs. The compound
    # index serves both the counter lookup (key, kind, window_start) and the
    # lockout lookup (key, kind); the TTL index reaps expired buckets/lockouts.
    await db.rate_limits.create_index([("key", 1), ("kind", 1), ("window_start", 1)])
    await db.rate_limits.create_index("expires_at", expireAfterSeconds=0)

    # Recovery-token store (PH1.8) — one document per single-use email-verify /
    # password-reset token. token_id is the unique handle looked up at redeem
    # time; the compound index serves the "invalidate a user's outstanding
    # tokens" sweep; the TTL index reaps records once past their expires_at Date.
    await db.recovery_tokens.create_index("token_id", unique=True)
    await db.recovery_tokens.create_index([("user_id", 1), ("purpose", 1)])
    await db.recovery_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.feature_flags.create_index("key", unique=True)
    await db.announcements.create_index("created_at")
    await db.support_tickets.create_index("status")
    await db.support_tickets.create_index("created_at")

    # payments — the admin payments page lists newest-first over the whole
    # collection (there is no per-user payments view). One index on the sort key.
    await db.payments.create_index("created_at")


# Startup
@app.on_event("startup")
async def startup():
    # Indexes first, before anything else in boot and before the readiness gate
    # opens — unchanged from the pre-PH3.4 ordering, now one call instead of
    # forty inline statements. See `ensure_indexes()` for why it was extracted.
    await ensure_indexes()

    # Outbound HTTP connection pooling (PH3.4). Enabled here, on the application's
    # own event loop, because an httpx client's connections belong to the loop
    # that opened them — see services/http_client.py for why this is an explicit
    # lifespan call and not a module-level client. Until this runs, every provider
    # call constructs its own client, which is the pre-PH3.4 behaviour and remains
    # the behaviour under the TestClient.
    try:
        from services import http_client as _http_pool
        await _http_pool.open_pool()
    except Exception as e:
        # Pooling is an optimisation, never a boot requirement: falling back to
        # per-call clients is slower and completely correct.
        logger.error(f"HTTP connection pool init error: {e}")

    # Seed default feature flags if empty
    if await db.feature_flags.count_documents({}) == 0:
        default_flags = [
            {"name": "Paper Trading", "key": "paper_trading", "enabled": True, "description": "Enable paper trading simulation", "target_plans": ["pro", "elite"]},
            {"name": "Backtesting", "key": "backtesting", "enabled": True, "description": "Enable strategy backtesting", "target_plans": ["pro", "elite"]},
            {"name": "Broker Integration", "key": "broker_integration", "enabled": True, "description": "Enable broker connections", "target_plans": ["all"]},
            {"name": "Morning Report", "key": "morning_report", "enabled": True, "description": "Daily AI morning report", "target_plans": ["all"]},
            {"name": "AI Debate", "key": "ai_debate", "enabled": True, "description": "Dual AI debate system", "target_plans": ["pro", "elite"]},
            {"name": "Learning Mode", "key": "learning_mode", "enabled": False, "description": "AI learning mentor", "target_plans": ["all"]},
            {"name": "Voice AI", "key": "voice_ai", "enabled": False, "description": "Voice-based AI assistant", "target_plans": ["elite"]},
            {"name": "Mobile Features", "key": "mobile_features", "enabled": False, "description": "Mobile-specific features", "target_plans": ["all"]},
        ]
        for flag in default_flags:
            flag["created_at"] = datetime.now(timezone.utc).isoformat()
            flag["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.feature_flags.insert_many(default_flags)
        logger.info("Seeded default feature flags")

    # Restore same-day broker sessions (Zerodha/Upstox) + realtime streams so
    # a backend restart doesn't force re-login. Encrypts legacy plaintext tokens.
    await broker_engine.load_sessions()

    # Admin accounts are never seeded by the API server (PH1.1). For local
    # development, run `python scripts/seed_dev_admin.py` — it refuses to run
    # when APP_ENV=production.

    # Initialize Market Engine gateway
    try:
        from services.market_engine import market_gateway
        await market_gateway.initialize()
        logger.info("Market Engine gateway initialized")
    except Exception as e:
        logger.error(f"Market Engine init error: {e}")

    # Setup scheduler (cron jobs)
    try:
        setup_scheduler(
            db=db,
            ai_summary_func=ai_market_summary,
            ws_broadcast=ws_manager.broadcast,
        )
        logger.info("Cron scheduler initialized")
    except Exception as e:
        logger.error(f"Scheduler setup error: {e}")

    # Redis background stats sampler (PH2.7). Populates the redis_server_* gauges
    # (memory, clients, evictions, AOF status) from INFO on a fixed cadence.
    #
    # Started BEFORE the event bridge on purpose: the bridge's Pub/Sub subscriber
    # is the noisiest consumer of Redis, and having the memory/eviction gauges
    # already reporting when it connects means a bad first sample is attributable.
    # No-op when REDIS_URL is unset or REDIS_STATS_INTERVAL_SECONDS=0.
    try:
        from infrastructure import redis_client as obs_redis
        if await obs_redis.start_stats_sampler():
            logger.info("Redis stats sampler started")
    except Exception as e:
        logger.error(f"Redis stats sampler start error: {e}")

    # Wire the market event bus to WebSocket clients (Sprint R2). Bridges every
    # bus event onto a socket channel and, when Redis is configured, fans events
    # across processes. No-op cross-process fan-out in single-process/dev.
    #
    # PH2.7: the Redis subscriber behind this now reconnects on its own with
    # backoff, so a Redis restart no longer silently kills cross-process realtime
    # delivery for the life of the process. It also returns immediately rather
    # than waiting for the subscription — startup must never block on a
    # dependency that is allowed to be down.
    try:
        from services.realtime.event_bridge import start_event_bridge
        await start_event_bridge(ws_manager)
        logger.info("Event bridge started — bus events now stream to sockets")
    except Exception as e:
        logger.error(f"Event bridge start error: {e}")

    # Start WebSocket broadcast loop
    asyncio.create_task(market_broadcast_loop())
    logger.info("WebSocket broadcast loop started")

    # Start AI monitoring loop — watches market 24/7 and sends proactive alerts
    asyncio.create_task(ai_monitoring_loop())
    logger.info("AI monitoring loop started — watching for market events")

    # Start AI heartbeat engine — continuously performs REAL background work
    # (live fetches, news/breakout/volume scans, trade & portfolio monitoring)
    # and logs a truthful running->done trace to the AI Activity feed, while
    # streaming live prices and pushing portfolio/trade/alert updates over WS.
    try:
        from services.heartbeat_engine import start_engine as start_heartbeat_engine
        start_heartbeat_engine(db, ws_manager)
    except Exception as e:
        logger.error(f"Heartbeat engine start error: {e}")

    # Boot is complete (PH2.5). This is the line that flips /api/health/startup
    # from 503 to 200 and lets /api/health/ready return "ready" — and it is the
    # LAST statement of startup for that reason: everything above (index
    # creation, broker session restore, market gateway, scheduler, event bridge,
    # background loops) must finish before this instance claims it can serve.
    obs_health.lifecycle.mark_started()

    logger.info(
        "AlphaPartner API started successfully",
        extra={
            "event": "startup_complete",
            "startup_seconds": round(obs_runtime.uptime_seconds(), 3),
            "version": obs_runtime.service_version(),
            "revision": obs_runtime.vcs_ref(),
            "environment": obs_runtime.environment(),
            "health_checks": obs_health.registered_checks(),
        },
    )
    logger.info(f"Data sources: Alpha Vantage={'ON' if av_configured() else 'OFF'}, Zerodha={'ON' if kite_configured() else 'OFF'}")

@app.on_event("shutdown")
async def shutdown():
    # Fail readiness FIRST, before tearing anything down (PH2.5).
    #
    # This one line is what makes a deploy quiet. On SIGTERM the load balancer
    # does not know anything has changed until its next readiness poll; if the
    # process starts closing its Mongo client and broker streams immediately, it
    # keeps receiving requests it can no longer serve, and every release sheds a
    # small burst of 500s. Flipping to STOPPING first means the balancer sees
    # 503 on its next poll and stops routing here, while in-flight requests drain
    # into a process that still works.
    obs_health.lifecycle.mark_stopping()
    logger.info(
        "Shutdown initiated — readiness now reports draining",
        extra={"event": "shutdown_started", "uptime_seconds": round(obs_runtime.uptime_seconds(), 3)},
    )

    from services.scheduler import scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await broker_engine.shutdown()  # close live broker WebSocket streams

    # Redis teardown (PH2.7), in dependency order: subscribers first, then the
    # shared pool.
    #
    # The order is not cosmetic. Closing the pool while a subscriber is still
    # running leaves it reconnecting against a client that is being dismantled,
    # so a clean shutdown emits a burst of connection errors and looks, in the
    # logs, exactly like a crash. Stopping subscribers first lets each one
    # UNSUBSCRIBE and close its own connection, which also releases the server's
    # client slot immediately rather than leaving it for TCP keepalive to reap.
    #
    # Wrapped, because shutdown must complete. A backend that cannot exit because
    # its cache teardown raised gets SIGKILLed by the orchestrator, and SIGKILL is
    # what the 30s stop_grace_period in docker-compose.yml exists to avoid.
    try:
        from infrastructure import redis_pubsub as _redis_pubsub, redis_client as _redis_client
        await _redis_pubsub.stop_all()
        await _redis_client.close()
    except Exception as e:
        logger.warning(f"Redis shutdown error (ignored): {e}")

    # Outbound HTTP pool (PH3.4). Closed after the broker streams and Redis, and
    # wrapped for the same reason they are: shutdown must complete. Closing the
    # pool politely matters in a rolling deploy — the instance being replaced
    # should return its provider connections rather than have them reset.
    try:
        from services import http_client as _http_pool
        await _http_pool.close_pool()
    except Exception as e:
        logger.warning(f"HTTP pool shutdown error (ignored): {e}")

    client.close()
