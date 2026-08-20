# StockAssist AI
## Architecture & Product Decisions

Version: 1.2

Status: Active Development

---

# Purpose

This document records all major engineering, architecture, infrastructure, AI, product, and business decisions made during the development of StockAssist AI.

Every significant decision must be documented before implementation.

The objective is to ensure future developers understand:

• Why a decision was made

• Alternatives considered

• Trade-offs

• Long-term impact

This document acts as the project's Architectural Decision Record (ADR).

---

# Decision Template

Every decision should follow this structure.

Decision ID

Date

Status

Context

Decision

Alternatives Considered

Pros

Cons

Consequences

Review Date

---

# ADR-001

Title

Frontend Framework

Status

Accepted

Context

The platform requires a scalable frontend capable of handling dashboards, charts, AI workspaces, responsive layouts, animations, and real-time updates.

Decision

Use

React

TypeScript

Vite

Tailwind CSS

shadcn/ui

Reason

Fast development

Excellent ecosystem

Type safety

Reusable components

Large community

Future scalability

Alternatives

Vue

Angular

Next.js

Rejected Because

Current architecture is SPA-focused and does not require SSR initially.

---

# ADR-002

Title

Backend Framework

Status

Accepted

Decision

Node.js

Express

TypeScript

Reason

Unified JavaScript ecosystem

Large package ecosystem

Real-time support

Easy broker integration

---

# ADR-003

Title

Database

Status

Accepted

Decision

MongoDB Atlas

Reason

Flexible schema

Excellent for evolving products

Good TypeScript support

Easy scaling

Alternatives

PostgreSQL

MySQL

Firebase

---

# ADR-004

Title

Caching

Decision

Redis

Reason

Fast

Reliable

Supports BullMQ

Excellent caching

Session storage

Rate limiting

---

# ADR-005

Title

Background Jobs

Decision

BullMQ

Redis

Reason

Reliable

Retry support

Scheduling

Independent workers

---

# ADR-006

Title

Authentication

Decision

JWT

Refresh Tokens

Reason

Stateless

Scalable

API Friendly

---

# ADR-007

Title

Broker Architecture

Decision

Broker Adapter Pattern

Reason

Support multiple brokers

Minimal code duplication

Easy future expansion

Supported Brokers

Zerodha

Upstox

Future

Angel One

Groww

Interactive Brokers

---

# ADR-008

Title

Market Data

Decision

Provider Abstraction Layer

Reason

Never tie business logic directly to one provider.

Benefits

Easy replacement

Fallback providers

Testing

Caching

Status

Extended by ADR-026 (Provider-Independent Market Data Architecture).

---

# ADR-009

Title

AI Architecture

Decision

Multi-Agent System

Reason

Single AI becomes difficult to scale.

Multiple specialized agents provide:

Better reasoning

Parallel execution

Cleaner architecture

---

# ADR-010

Title

Primary AI Models

Decision

Claude

Gemini

Reason

Claude

Reasoning

Architecture

Code

Planning

Gemini

Fast responses

Large context

Multimodal

Future

Model abstraction layer.

Never depend on one provider.

---

# ADR-011

Title

Real-Time Updates

Decision

WebSockets

Fallback

Polling

Reason

Fast updates

Lower latency

Better UX

---

# ADR-012

Title

Deployment

Decision

Frontend

Vercel

Backend

Railway

Future

AWS

Reason

Fast deployment

Low maintenance

Simple scaling

---

# ADR-013

Title

Event Driven Architecture

Decision

Publish business events.

Examples

trade.created

portfolio.updated

subscription.upgraded

Reason

Loose coupling

Scalable

Future microservices

---

# ADR-014

Title

UI Framework

Decision

Glassmorphism

Minimalism

Motion Design

Reason

Premium appearance

High readability

Modern financial UI

---

# ADR-015

Title

Design Language

Inspired By

Apple

Linear

Stripe

TradingView

Bloomberg

Reason

Trust

Professionalism

Simplicity

---

# ADR-016

Title

State Management

Decision

React Query

Context

Reason

Separate server state from UI state.

Avoid unnecessary global state.

---

# ADR-017

Title

Validation

Decision

Zod

Reason

Type-safe validation

Frontend and backend consistency

---

# ADR-018

Title

API Design

Decision

REST

Versioned APIs

/api/v1

Future

GraphQL

---

# ADR-019

Title

Documentation First

Decision

Create documentation before implementation.

Reason

AI-assisted development

Clear requirements

Better consistency

Lower technical debt

---

# ADR-020

Title

Development Workflow

Decision

Documentation

↓

Design

↓

Implementation

↓

Testing

↓

Deployment

Reason

Predictable development

Higher quality

---

# ADR-021

Title

No Mock Data in Production

Decision

Every production feature must use live APIs.

Reason

Platform trust

Accurate AI

Reliable analytics

---

# ADR-022

Title

Security First

Decision

Every feature undergoes security review.

Reason

Financial platform

Sensitive data

Broker integrations

Payments

---

# ADR-023

Title

AI Transparency

Decision

AI should explain its reasoning.

Users should understand

Why a recommendation exists

Confidence level

Supporting evidence

Risks

AI must not behave as an unexplained black box.

---

# ADR-024

Title

Subscription Model

Decision

Freemium SaaS

Free

Pro

Elite

Future Enterprise

Reason

Accessible for beginners

Sustainable business model

---

# ADR-025

Title

Admin Portal

Decision

Separate internal application.

Reason

Never expose administrative functionality to regular users.

RBAC required.

---

# ADR-026

Title

Provider-Independent Market Data Architecture

Date

2026-07-16

Decision

StockAssist AI is provider-independent. All market data enters the platform through a Market Gateway abstraction with per-provider adapters, governed by a Source Manager that selects the best provider per user.

Provider priority:

1. Connected Broker WebSocket (Zerodha, Upstox, Angel One, Fyers, Dhan)

2. Licensed Exchange Feed (future)

3. Yahoo Finance (always-available baseline)

Every provider produces one normalized market event model. The Market Engine, AI, and Frontend consume only normalized events and never know the provider — downstream provenance is limited to a source tier (streaming / delayed).

Connecting a broker automatically upgrades the user's feed to the broker's streaming WebSocket at no subscription cost — the broker already owns the user's data entitlement.

Premium never sells market data; it sells AI intelligence.

Reason

Yahoo Finance (polling) was the platform's real latency bottleneck, not the internal event-driven architecture. Depending on any single provider is a business and technical risk. Broker feeds give professional streaming data with zero data cost.

Consequences

• Adding a provider = one adapter + one normalizer + registry entry; nothing else changes.
• Never bypass the Market Gateway or Source Manager.
• Frontend and AI must never contain provider-specific logic.
• Failover is automatic and silent (broker → licensed → Yahoo → cached data with banner).

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---

# ADR-027

Title

Feature Freeze & Production Hardening Program (PH1–PH3)

Date

2026-07-17

Status

Accepted

Context

The MVP is feature complete (Phase 1 Sprints 1–12; Phase 2 Releases R1–R9). The Sprint 12 Production Readiness Audit (PRODUCTION_READINESS_REPORT.md) returned a verdict of NOT READY FOR PRODUCTION: two critical authentication backdoors enabled by default, wildcard CORS with credentialed requests, insecure auth cookies, broken Docker packaging, no CI/CD, no rate limiting, fabricated admin analytics data (ADR-021 violation), a non-hermetic backend test suite, and zero frontend tests. The audit also confirmed a structural documentation/code mismatch: DEPLOYMENT.md and ADR-002 describe a Node.js + Express + TypeScript backend and Vite frontend, while the actual system is Python + FastAPI (`backend/server.py`) with a CRA/craco JavaScript frontend.

Decision

1. Immediate feature freeze. No new product features merge until Production Certification.

2. A three-phase Production Hardening program is inserted between the completed MVP and product Phases 3–9:
   • PH1 — Production Security Hardening (12 sprints)
   • PH2 — Production Infrastructure & DevOps (12 sprints)
   • PH3 — Production Quality Assurance (12 sprints)

3. Two permanent documents govern the program: PRODUCTION_HARDENING.md (strategy, risk, certification, Definition of Production Ready) and PRODUCTION_ROADMAP.md (36 sprint definitions, sequencing, dependency graph).

4. Security removals (auth backdoors, OAuth fallbacks) are permanent — never rolled back; broken flows are fixed forward.

5. The FastAPI + CRA stack is acknowledged as the as-built system of record. ADR-002 is superseded in practice; DEPLOYMENT.md and related documents will be reconciled to the actual stack in sprint PH3.10 (either documenting the FastAPI stack as final or recording an explicit migration ADR — one of the two must be chosen there).

6. Launch requires the three phase certifications (PH1.12, PH2.12, PH3.12) and a re-scored production readiness of ≥ 9.0 with no category below 8.0.

Alternatives Considered

• Fix only the six critical blockers and launch — rejected: leaves no pipeline to keep them fixed, no tests to catch regressions, and unmeasured recovery capability.

• Continue feature development in parallel with hardening — rejected: every new feature widens the attack/regression surface being certified.

Consequences

• Product Phases 3–9 in ROADMAP.md are blocked until PH3.12.
• All PH work is tracked in TASKS.md under "Production Hardening Program".
• Documentation version bumped to 1.2; standalone CHANGELOG.md introduced.
• Estimated program duration: ~5–6 calendar weeks with parallel tracks.

Review Date

At PH3.12 (Production Certification go/no-go).

---

# ADR-028

Title

Market Gateway Foundation — Provider Abstraction Scope for Phase D1

Date

2026-08-19

Status

Accepted

Context

ADR-026 approved the provider-independent architecture; MARKET_DATA_ARCHITECTURE.md specified it. Sprint D1 is the first implementation increment. Inspection of the codebase before D1 found the target architecture partially present and partially contradicted:

• `market_engine/gateway.py` existed, but imported `services/real_market.py` (Yahoo) directly and passed a hardcoded `provider="yahoo"` to the normalizer — the gateway was itself the platform's largest piece of Yahoo-specific code.
• `normalizer.py` stamped `provider: "yahoo"` onto every normalized quote, leaking provider identity downstream in violation of Developer Rules 4 and 5.
• The gateway's index normalization was a silent no-op: the provider's index sub-dicts carry no `name`, a nameless index fails `validate_index_quote`, so the raw payload passed through untouched on every request.
• `services/ai_context_builder.py` — the AI's only door to market data — called `real_market.*` directly.
• `server.py` and five service modules bypassed the gateway entirely.
• `real_market.py` also contains non-provider concerns: derived analytics (RSI/MACD/VWAP, market breadth, sentiment scoring, gainer/loser ranking) and a second provider (NSE India, for FII/DII).

Three conflicts between the target architecture and the as-built system required a decision rather than a mechanical migration.

Decision

1. **Adapters wrap the hardened provider client; they do not replace it.** `real_market.py` becomes the Yahoo adapter's provider client. Its pooled HTTP (PH3.4), Redis caching, batched cache warm (Sprint R9), load-test origin override (PH3.5) and error containment stay on the production path untouched.

2. **The D1 adapter contract omits the streaming push surface.** MARKET_DATA_ARCHITECTURE.md's contract includes `subscribe`/`unsubscribe`/`on_raw`. D1 ships one polling provider and no consumer able to receive pushed ticks, so that surface would be code nothing implements and nothing calls. D1 defines lifecycle, capabilities, health, and a capability-gated fetch surface. The push surface lands in D3 with the first broker WebSocket adapter and its consumer. `ProviderKind` already separates the two families, so nothing above the adapter is rewritten when it arrives.

3. **`source_tier` is added; the legacy `source` field is retained.** Normalized events now carry `source_tier` (`streaming`/`delayed`) and `ingested_at`, and no provider name. The `source: "yahoo_finance"` string remains in REST payloads (`/api/stocks/{symbol}/live`, `/api/stocks/{symbol}/intraday`, the advisor's `data_source`) because removing it is a breaking API change with a frontend consumer that branches on it.

4. **The gateway supplies index names at the normalization boundary,** which makes index normalization actually run. Normalized fields are merged *over* the raw sub-dict so provider-supplied keys — notably `available`, which every overview consumer branches on — survive. Behaviour is additive: no existing field changes value.

5. **D1 migrates the Market Engine and the AI layer only.** The remaining gateway bypasses are frozen in an enforced register (`KNOWN_GATEWAY_BYPASSES` in `tests/test_market_gateway.py`) that may only shrink. A new bypass fails CI.

Alternatives Considered

• **Migrate every caller in D1** — rejected. `/api/market/sectors` returns the provider's `{"sector": …}` while the gateway returns the canonical `{"name": …}`; rerouting the routes silently breaks the API contract and the frontend that reads it. The reconciliation is real work, not a rename, and belongs with the D2 tier-indicator cutover that touches the same components.

• **Extract derived analytics out of `real_market.py` in D1** — rejected. Wide, high-regression-surface, and not blocking: a second provider can be added without it.

• **Remove `source` from API responses now** — rejected as an undocumented breaking change. Sequenced instead: D1 adds `source_tier`, D2 moves consumers onto it, a later sprint removes `source`.

Consequences

• Adding a provider is one adapter + one normalizer family + one `provider_registry.register()` call. Proven executable: the D1 suite registers a fake streaming provider at broker priority and the gateway serves from it, selecting the matching normalizer and stamping the streaming tier, with no gateway edit.
• Failover is a property of health bookkeeping rather than switching code: a provider crossing into DOWN stops being resolved and the tier below takes over automatically. Recovery is symmetric.
• Provider failure degrades to no data, never to fabricated data. Verified at the gateway boundary.
• Two debts are recorded rather than hidden: the remaining gateway bypasses, and `source` in the REST contract. Both are D2 scope with tests holding the line meanwhile.

Requires Approval

Consequence 3 (retaining `source: "yahoo_finance"` in the public REST contract until D2) leaves a provider name on a public surface, which Developer Rule 4 forbids. It is retained deliberately for API compatibility and is the one D1 outcome that needs an explicit accept-or-accelerate decision.

Review Date

At D2 (Source Manager completion and frontend tier indicator).

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---

# ADR-029

Title

Source Manager Completion — Resolution Semantics for Phase D2

Date

2026-08-20

Status

Accepted

Context

D1 (ADR-028) delivered the Provider Adapter contract, the Provider Registry, a Source Manager with capability-based resolution and health-based exclusion, and the Yahoo migration. D2's brief is to complete the Source Manager: capability resolution, registry integration, provider priority, provider health, capability+health resolution, a user-context foundation, a failover foundation, and verified Market Engine / AI decoupling.

Inspecting D1 against that brief found the foundation sound and four seams that were left deliberately inert, each of which stops being defensible the moment a second provider exists:

• `resolve()` returned a single provider. A request whose preferred provider raised returned nothing, even with a healthy baseline one tier below. The baseline only took over after the provider accumulated `DOWN_AFTER_FAILURES` consecutive failures — eight whole requests, each of which served a user an empty dashboard for an outage the platform could already route around.

• `resolve()` returned `None` with no reason. "Nothing is registered", "this user is entitled to nothing", "no provider serves order-book depth" and "every provider is in outage" reached the gateway as the same silence, and the gateway degraded all four into the caller's empty default — which a caller also could not distinguish from "the provider answered and there was nothing to report".

• `user_id` was accepted and ignored. A D3 broker adapter registered against the global registry would have been resolved for every user, consuming one user's broker entitlement on behalf of another — the exact thing Category 2 of MARKET_DATA_ARCHITECTURE.md forbids.

• Every provider started at `ProviderState.UP`. A provider registered one millisecond ago and one with ten thousand clean requests behind it reported identically on the diagnostics surface whose only job is telling them apart.

Decision

1. **Resolution returns an ordered failover chain, and the gateway walks it inside one request.** `SourceManager.resolve_feed()` returns a `Resolution` carrying the selected provider plus the ordered alternatives; `MarketGateway._serve_with_provider` tries the next eligible provider when one raises. Health counters keep their role — a DOWN provider is dropped from resolution entirely, so later requests never pay for its timeout — but the chain closes the window before they trip. Only an exception advances the chain: an empty result is an answer, and failing over on it would double every provider call on a quiet market to produce the same empty list.

2. **A fourth health state, `UNKNOWN`, is the initial state — and it ranks alongside `UP`, not below it.** The ranking is load-bearing, not cosmetic. Ranking `UNKNOWN` below `UP` deadlocks the Provider Priority Algorithm: a freshly registered priority-1 broker feed leaves `UNKNOWN` only by being called and is called only by being selected, so it would sit behind a healthy priority-3 Yahoo forever and the platform's headline feature would never engage. `DEGRADED` is different in kind — evidence of failure rather than absence of evidence — and continues to demote a provider below a healthy lower tier.

3. **`user_id` is replaced by a `ResolutionContext`, and entitlement is enforced in the provider.** The context carries `user_id`, `symbol` and `exchange`; `MarketDataProvider.owner_user_id` declares whose entitlement a provider is served under and `is_eligible_for(context)` is the filter. A provider bound to a user cannot be resolved for anybody else, and a request with no user attached (a scheduled refresh, a scanner sweep) never reaches one. The gateway supplies the symbol at every call site that has one, so MARKET_DATA_ARCHITECTURE.md's per-symbol rule — "a broker feed covering NSE equities does not disqualify Yahoo from serving a US index the broker doesn't carry" — becomes implementable in D3 entirely inside a provider's `is_eligible_for`, with no call-site change.

   A bare `user_id` string was rejected: entitlement is per user but coverage is per instrument, and threading a fifth keyword argument through eleven gateway methods later is a wide, merge-conflict-heavy change. `user_id=` remains accepted everywhere as a shorthand, so no existing caller changed.

4. **Unavailability is explicit.** `UnavailableReason` names the four cases (`no_providers_registered`, `not_entitled`, `capability_unsupported`, `all_providers_down`). It travels on `provider.status` and on `MarketGateway.status["last_unavailable"]`, and it describes the *feed*, never the providers behind it, so it does not breach Developer Rule 4.

5. **Public API shapes are unchanged.** Gateway methods still return their empty defaults when the feed is genuinely unavailable, and still re-raise when a call fails — the difference is that they now re-raise only after every eligible provider has been tried. No route contract, no frontend payload, and no existing call site changed.

Alternatives Considered

• **Add probation windows and periodic re-probing now** — rejected. MARKET_DATA_ARCHITECTURE.md assigns them to Phase 5, the D2 brief scopes out "sophisticated automatic failover policies", and a re-probe needs a clock source and a background sweeper D2 has no other use for. The consequence is recorded below rather than hidden.

• **Cache resolution per user session** — rejected for D2. The cache would need invalidating on broker connect, broker disconnect, token refresh, every health transition and every registry mutation: five invalidation paths guarding a sorted traversal of a one-element list. It becomes worth its invalidation surface in D3, when a per-user session actually exists to hang it on.

• **Fail over on an empty result as well as on an exception** — rejected. An empty gainers list at 3am is correct, and the policy would double every provider call on a quiet market.

Consequences

• A user whose preferred provider dies mid-session keeps their feed within the same request, at a lower freshness tier. D1 recovered only after eight failed requests.

• Cross-user entitlement leakage is impossible by construction rather than by every future call site remembering to check.

• An operator reading a log line can tell a startup registration bug from an expired broker token from an unimplemented capability from a total outage.

• **A demoted provider has no self-recovery path in D2.** It is last in the chain, the chain stops at the first provider that answers, and health only improves on a successful call — so a provider that blips past `DEGRADED` stays on the lower tier until an external `record_success`, a process restart, or the Phase 5 re-probe. D3's broker adapter is the natural first caller: a reconnected WebSocket knows it recovered without anyone polling it. Pinned by `test_a_demoted_provider_has_no_self_recovery_path_in_d2` so it cannot regress silently and so D5 has a red-to-green target.

• DD-5 (provider names on two live UI surfaces) is closed. DD-1, DD-2, DD-3 and DD-4 are **not** closed by D2 and are re-sequenced in TASK.md; DD-2 in particular remains blocked on the ADR-028 approval item below.

Requires Approval

ADR-028's open approval item is unchanged and now overdue: `source: "yahoo_finance"` remains in the public REST contract, and `InvestmentAdvisor.jsx` and `Markets.jsx` still branch on it, so a broker feed would render as "Fallback data". D2 did not move those consumers onto `source_tier` because the endpoints that serve them bypass the gateway (DD-1) and therefore do not emit `source_tier` yet; closing DD-2 requires closing DD-1's shape reconciliation first. This needs an explicit accept-or-accelerate decision before D3 makes a streaming feed visible to users.

Review Date

At D3 (first broker WebSocket adapter).

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---

# ADR-030

Title

Public REST Contract Reconciliation — Retiring Provider Identity from Market Data Responses (DD-1 / DD-2)

Date

2026-08-20

Status

Accepted — supersedes the open approval item in ADR-028

Context

ADR-028 (D1) retained `source: "yahoo_finance"` in the public REST contract for API compatibility and flagged it as the one deliberate violation of Developer Rule 4 needing an explicit accept-or-accelerate decision. ADR-029 (D2) recorded it as overdue. It was accelerated.

Two debts were entangled and had to be resolved together:

• **DD-1** — the public market routes bypassed the Market Gateway, blocked on a shape reconciliation: `/api/market/sectors` returned the provider's `{"sector": …}` while the gateway returns the canonical `{"name": …}`, and the frontend read the provider's key. Routing the endpoint at the gateway without reconciling that would have silently blanked every sector label in the UI.

• **DD-2** — `source: "yahoo_finance"` (and the advisor's `data_source`) named a provider on a public surface. Consumers could not be moved onto `source_tier` while the endpoints serving them bypassed the gateway and therefore never emitted it. DD-2 could not close before DD-1.

Decision

1. **Provider identity is removed from the market-data contract, not retained behind a deprecation window.** Every market-data response now carries `source_tier` (`"delayed"` / `"streaming"`) and no provider name. `services/real_market.py` no longer writes a provider name into its own payloads.

   Retention was rejected because it was not actually backward compatible. `source` was a hardcoded literal, so the day a broker feed serves a quote the field keeps reporting `"yahoo_finance"` — it would not merely leak provenance, it would report the *wrong* provenance. `InvestmentAdvisor.jsx` branched on precisely that value to choose between "Live market data" and "Fallback data", so D3's headline feature would have rendered a live streaming feed to the user as "Fallback data". A field that is about to start lying is worse than a field that is removed.

2. **The value comes from the Source Manager, never from a literal.** `MarketGateway.source_tier(capability)` reads the active tier, so the contract tracks reality without any route learning who serves it.

3. **The sector shape is reconciled by emitting both keys.** The route returns the canonical `name` and keeps `sector` as a deprecated alias of the same value. The alias lives in the route rather than in `normalizer.py`, so the canonical model does not acquire a legacy key that every future provider's normalizer would inherit. `Dashboard.jsx` and `Markets.jsx` read `name`; the alias is removable once no consumer reads `sector`.

4. **Compatibility is preserved where it is meaningful:** every other field, every payload shape, every status code, and the sector alias. What changed is one field name on one family of endpoints, plus the two frontend branches that read it — both migrated in the same change.

5. **Diagnostics surfaces keep provider detail.** `/api/data-sources`, `Settings.jsx` and `AdminAPIs.jsx` still name providers, which MARKET_DATA_ARCHITECTURE.md explicitly permits for settings and diagnostics surfaces and forbids only on live data surfaces.

Alternatives Considered

• **Keep `source` and add `source_tier` alongside it** — rejected per Decision 1: the retained field becomes actively wrong under D3 rather than merely redundant.

• **Keep `source` but derive its value from the resolved provider** — rejected. It would be honest but still publishes a provider name, which is the rule being enforced.

• **Migrate every gateway bypass in the same change** — rejected as unrelated scope. The remaining bypasses (`heartbeat_engine`, `scheduler`, `paper_trade`, `portfolio_stream`, `portfolio_engine`, `stock_details`, `morning_report`) do not emit provider identity to any public surface; they are Developer Rule 2 debt and stay in the enforced register.

• **Route `/api/stocks/{symbol}/live` through the gateway too** — rejected for now (DD-1b). The canonical StockQuote drops `currency`, `market_state` and the `historical_*` series that endpoint returns, so it would be a silent breaking change dressed as a refactor.

Consequences

• No provider name reaches any market-data client. Guarded by `TestPublicContractCarriesNoProviderIdentity`, which sweeps every public market endpoint's response and includes a control proving the sweep can actually observe a leak — planted on a passthrough endpoint, because a leak planted on the sectors route is stripped by the normalizer and would prove nothing.

• The frontend tier indicator now has real data behind it: `Markets.jsx` renders Live/Delayed from `source_tier`, which is DD-7's groundwork.

• **A latent 500 was found and fixed.** `MarketGateway.get_sectors` iterated whatever the provider returned; a provider answering with a dict where a list belongs was iterated into its *keys*, and `normalize_sector_data("some_key")` raised `AttributeError`. It surfaced only because the route migration made the path reachable. Malformed payloads are now logged and dropped, as MARKET_DATA_ARCHITECTURE.md requires.

• Three pieces of residue are recorded rather than hidden: DD-1a (Alpha Vantage selected in a route handler), DD-1b (the live-quote endpoint's richer shape), DD-1c (`backtest_engine`'s `data_source: "yfinance"`, deliberately untouched because it is a PH3.9 anti-fabrication control on a different surface).

• ADR-028's open approval item is closed. No decision remains pending on the market-data contract.

Review Date

At D3 (first broker WebSocket adapter), when `source_tier` first takes the value `"streaming"` in production.

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---

# ADR-031

Title

Broker Provider Framework — Capability-Based, Provider-Independent Broker Integration (Sprint D3)

Date

2026-08-20

Status

Accepted — implemented

Context

Phase D's plan (ROADMAP.md, ADR-028) listed D3 as "the Zerodha Kite WebSocket adapter — the streaming push surface, make-before-break switching, failover to Yahoo", the market-data headline feature. Inspection before starting found a broker layer that already existed and was already load-bearing — `services/brokers/` with a `BrokerAdapter` ABC, Zerodha and Upstox adapters, encrypted per-user sessions, portfolio sync, live order streaming — and that was nonetheless not a framework:

• **No registry.** The broker set was a module literal, `SUPPORTED_BROKERS = {"zerodha": …, "upstox": …}`, and `create_adapter()` built a fresh instance per call. Nothing could accumulate a broker's API health across requests, which is why BROKER_INTEGRATION.md's Admin Portal health monitoring had no data source.

• **No capability model.** Every account-data and order method was `@abstractmethod`, so a broker missing an endpoint could only be integrated by writing stub methods that lie — raising from a method that claims to exist, or returning `[]` from one that claims to have looked.

• **No gateway.** `BrokerEngine` held adapters and called them directly, so capability enforcement, response shape, error normalization and health had no single place to live.

• **Canonical shapes in a docstring.** The normalized Holding/Position/Order/Trade/Funds shapes were documented at the top of `base.py` and enforced nowhere. Zerodha's `get_funds` returned Kite's whole `equity`/`commodity` margin tree under a `raw` key, straight through to core services.

• **Broker names in core code.** `server.py` chose an order product with `"CNC" if broker == "zerodha" else "D"` (twice) and branched on the broker name to parse its OAuth callback; `BrokerEngine.start_stream` branched on `if broker == "zerodha":` and read `KITE_API_KEY` by name; `stream.py` dispatched its run loop on the broker name.

Building the streaming feed first would have hung the platform's headline feature on all of that, and every item would have had to be unpicked afterwards with a live feature sitting on top.

Decision

**D3 is re-scoped from "first WebSocket adapter" to "Broker Provider Framework". Broker market-data streaming moves to D4.**

The framework, all under `services/brokers/`:

1. **`capabilities.py`** — `BrokerCapability`, and `CAPABILITY_METHODS` binding each capability to the adapter method that serves it, which is what makes the model *verifiable* rather than decorative.

2. **`registry.py`** — `BrokerRegistry`, one long-lived adapter per broker, with registration-time validation: an adapter declaring a capability it has not implemented fails at import, not at 09:15 on a Monday. A `@capability_stub` mark distinguishes a default that only raises (declaring it is a defect) from one that genuinely works — `get_margins` delegating to `get_funds` — which identity comparison against the base class could not.

3. **`gateway.py`** — `BrokerGateway`, the single choke point. Four guarantees on every call: capability enforcement before the adapter is reached, canonical shapes, one error family, health bookkeeping.

4. **`contracts.py`** — the canonical shapes as dataclasses that know how to build themselves from an adapter payload. Coercion is lenient (a missing optional field becomes its zero value) and validation is narrow (an order with no id is rejected, because an untrackable order in the order book is worse than an error). `BrokerOrderAck` is deliberately separate from `BrokerOrder`: `place_order` persists `{**request, **ack}`, so a full-order ack would overwrite the request's real quantity and price with default zeros.

5. **`errors.py`** — one error vocabulary. The existing wire codes are adopted verbatim rather than tidied, because they are already on the public contract; what is added is that retry policy and recovery hints are *derived* from the code instead of re-decided per call site, and `normalize_broker_error` guarantees no `httpx`, `KeyError` or `struct.error` crosses the gateway.

6. **`health.py`** — broker API health, distinct from a user's session. **An auth failure never counts against it.** Kite invalidates every access token daily at 06:00 IST, so at 06:01 every connected user's next call raises `BrokerAuthError`; counting those would drive Zerodha to DOWN every morning while its API was perfectly available.

7. **`credentials.py`** — the authentication/configuration boundary. Adapters *declare* which environment variables carry their credentials and never read them, which is what lets `BrokerEngine` open a broker stream without naming a single secret.

8. **`BrokerConnection`** — the canonical user → broker association, carrying no token material so it is safe on events, in logs and in AI context.

Consequences

• **Adding a broker is one adapter plus one registry entry.** Proven, not asserted: `tests/test_broker_framework.py` defines `AcmeBrokerAdapter` — a fictional, deliberately *partial* broker with its own product code ("DELIVERY", neither Zerodha's nor Upstox's) — built from nothing but the public contract, and exercises it end to end with no core module changed. Structural tests assert that the Trading Engine, Portfolio Engine, portfolio/trade streams, AI Context Builder, paper trading and the Broker Engine name no broker in executable code.

• **Three broker-name leaks in core are closed.** The order-product default now comes from the adapter (the old `else` branch silently handed Upstox's product code to every future broker); OAuth callback parsing moved to the adapter (the old `else` assumed every future broker speaks Upstox's dialect); the stream transport dispatches on a declared `stream_protocol`, so two brokers on one vendor API share a transport and a new protocol adds a table entry rather than a branch.

• **Two defects were found and fixed on the way.** `_request` logged the full broker URL on 401/403, and Kite's logout endpoint carries the access token *in the query string* — so a rejected logout, which is exactly what an already-dead token produces, wrote a live broker access token into the application log. URLs are now stripped of their query before logging. Separately, `BROKER_FORCE_IPV4` was evaluated once at import, so a deployment setting it after the process read its environment silently kept the import-time answer.

• **Kite's `raw` margin tree no longer reaches core services.** Nothing read it; any consumer that had started to would have been reading a shape only one broker produces.

• **The Source Manager can finally do its job.** `broker.connected` / `broker.disconnected` were documented in BROKER_INTEGRATION.md and published by nothing, so MARKET_DATA_ARCHITECTURE.md's Source Manager responsibility 1 was unimplementable. The Broker Gateway publishes them with the broker's capabilities attached; the Source Manager subscribes and maintains the per-user connected-broker registry. The two subsystems meet only on the Event Bus — the Market Engine imports no broker module and the broker layer imports no Market Engine module.

• **D3 deliberately does NOT register a broker as a market-data provider.** Doing so would have meant either a fabricated `streaming` tier (forbidden outright by CLAUDE.md's data rules) or a REST-polled provider silently taking a connected user's quotes away from the Yahoo baseline with none of the make-before-break machinery that makes such a switch safe. Pinned by `test_d3_does_not_register_a_broker_as_a_market_data_provider` so D4 has a red-to-green target.

• `SUPPORTED_BROKERS` and `create_adapter()` survive as deprecated views *derived from* the registry, so they cannot drift from what is actually registered.

• Nothing user-visible changed. 3064 backend tests pass (3015 before D3 plus 49 new); the 15 failures in `test_entrypoint_log_level.py` are the documented pre-existing Docker-daemon baseline.

Review Date

At D4, when the first broker feed is registered as a market-data provider and `source_tier` first takes the value `"streaming"` in production.

Authoritative documents

BROKER_INTEGRATION.md (broker behavior), MARKET_DATA_ARCHITECTURE.md (market data behavior)

---

# Pending Decisions

Future decisions to document.

International Markets

Mobile App Framework

Desktop App

Vector Database

Kubernetes

AI Model Routing

Auto Trading

Enterprise Features

White Label Platform

---

# Decision Lifecycle

Proposed

↓

Review

↓

Accepted

↓

Implemented

↓

Deprecated

↓

Archived

Every major change should create a new decision record.

---

# Review Policy

Review architecture decisions:

Every major release

Every infrastructure change

Every AI provider change

Every broker expansion

Every security update

---

# Long-Term Vision

The Decisions document becomes the institutional memory of StockAssist AI.

Years from now, every developer should be able to understand why the platform evolved the way it did, reducing confusion, preventing repeated debates, and preserving engineering knowledge.

---

# End of Decisions Documentation