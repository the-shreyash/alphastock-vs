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

---

# ADR-032

Title

Broker Streaming Contract — Generic Transport, Broker-Owned Codec (Sprint D4.2)

Date

2026-08-21

Status

Accepted — implemented

Context

D3 (ADR-031) removed every broker *name* from the streaming path: `stream.py` stopped branching on `if self.broker == "zerodha"` and dispatched on a declared `stream_protocol` instead. What it did not remove was every broker's *wire format*. After D3, a module no broker owns still held Kite's ticker URL, Kite's binary packet layout, Kite's two subscribe frames, Kite's error-frame convention and Upstox's JSON envelope. Three consequences, the third of which is the serious one:

• **Adding a streaming broker still meant editing shared code.** Developer Rule 9 of MARKET_DATA_ARCHITECTURE.md — one adapter plus one registry entry — held for the fetch surface and not for the stream.

• **A stream had no capability gate.** Every REST call passes one before the adapter is reached; a decoded frame passed none, so a broker could deliver ticks it never declared TICK_STREAM for and nothing would object.

• **The platform's tick contract was an accident.** `parse_kite_binary` produced `{"instrument_token", "last_price"}` and that list went straight to `BrokerEngine._on_stream_tick`, `portfolio_stream.apply_broker_ticks`, `trade_stream.apply_broker_ticks` and the user's app WebSocket. Both service docstrings state the input shape as fact; it was true only because exactly one broker's parser happened to build it. A second streaming broker whose parser emitted `{"token", "ltp"}` would have type-checked, imported, connected, and silently stopped every live P&L recompute for its users — no exception, no log line, no failing test. D4 registers a broker feed as a market-data provider, which puts a second and third streaming broker on the near roadmap (Upstox, Angel One, Groww, INDmoney, Dhan, Fyers), so this had to close before the feature lands rather than after.

Decision

**The transport is generic and the codec belongs to the broker.** Three adapter methods carry the entire wire-format surface — `stream_endpoint()`, `stream_subscribe_frames()`, `decode_stream_frame()` — and one new module, `services/brokers/streaming.py`, defines what a codec is allowed to return: `BrokerStreamEndpoint`, `BrokerTick`, `BrokerStreamEvent`.

Four properties, each pinned by a falsification test:

1. **A broker can stream without inheriting a Kite-shaped assumption.** The test suite's fictional `NovaAdapter` is deliberately Kite's opposite in every axis — text frames not binary, trading-symbol identity not an opaque numeric token, string prices not integer paise, a comma-separated subscribe frame not JSON, header auth not query-string auth — and it streams end to end with no Nova-specific code anywhere outside its adapter.

2. **Unsupported streaming is refused by the capability framework**, at registration *and* at runtime. `BrokerRegistry.validate` rejects a broker that declares ORDER_STREAM or TICK_STREAM without a codec (it would open a live socket whose every frame decodes to nothing — indistinguishable in the logs from a quiet market), and rejects a `stream_protocol` declared with no streaming capability to use it. `BrokerGateway.stream_event_allowed` then drops any decoded event whose capability the broker did not declare: the capability set is the authority on what a broker serves, and a codec may not widen it.

3. **A raw broker payload cannot escape into the canonical layer.** `BrokerTick` drops every key the contract does not name — the streaming counterpart of what `contracts.py` does to Kite's `raw` blob — and the transport type-checks the codec's return value, so an adapter returning its own dict produces nothing and logs an error rather than passing a broker shape upward. Streamed orders are now coerced through the same `BrokerOrder` as fetched ones, ending two writers to `db.orders` with only one of them enforced.

4. **No core module branches on a broker name.** `stream.py`'s D3 exemption from the core-module name ban is withdrawn: it holds no broker name, no endpoint literal, no `struct`, no `json`.

**Why this supersedes DB-3.** D3 recorded a debt to move each *transport* into its owning adapter. Splitting frame decoding from connection management is strictly better, because the transport is the part that genuinely is identical everywhere: connect, subscribe, iterate, honour capabilities, reconnect with jittered backoff. A per-broker copy would duplicate the reconnect and auth-expiry handling — precisely the code where copies diverge and one broker quietly stops reconnecting. `PROTOCOL_RUNNERS` survives as an empty override table for a protocol that is not a WebSocket at all, so such a broker adds an entry rather than reintroducing a branch. DB-3 is therefore closed by a different mechanism than it proposed, and adding a WebSocket broker now changes *nothing* in shared code.

Consequences

• Adding a streaming broker is one adapter file and one registry entry, matching the fetch surface.

• `BrokerStreamEndpoint.safe_url` is the only form of a stream URL that may be logged. Kite authenticates its ticker by query string, so "connected to <url>" — the most natural log line a transport could contain — would write a live access token into the application log, the same class of defect D3 found in `BrokerAdapter._request` arriving by a second route.

• A streamed order frame with no `order_id`, or an unmapped status, is now dropped at the boundary and logged rather than written to `db.orders`. This is the `contracts.py` rule applied consistently: an untrackable row in the order book is worse than an error.

• `BrokerTick` carries an optional `symbol` that nothing consumed at D4.2. It is what lets a symbol-identified broker feed skip the holdings join that token-identified feeds require; its consumer arrived in D4.3 (**ADR-033**), where both identification styles resolve through one instrument-mapping boundary.

• The canonical tick still flows as a dict rather than a dataclass, for the reason `contracts.py` gives: these values go straight into MongoDB, onto the Event Bus and out as JSON. The dataclass is the definition; the dict is the currency.

Review Date

At D4.3, when broker ticks are first routed into the Market Gateway as a market-data feed and a second streaming adapter is written against this contract.

Authoritative documents

BROKER_INTEGRATION.md, MARKET_DATA_ARCHITECTURE.md

---

# ADR-033

Title

Canonical Instrument Identity for Broker Ticks (Sprint D4.3)

Date

2026-08-21

Status

Accepted — implemented

Context

D4.2 (ADR-032) closed the tick *shape* leak: an adapter's codec is the only code that sees a raw frame, and the only thing it may return is a canonical `BrokerStreamEvent`. It deliberately left one field broker-shaped — `BrokerTick.instrument_token`, the broker's own opaque instrument handle, typed `Any` because narrowing it to `int` would encode one broker's choice into the contract.

That handle then travelled the whole way up. `BrokerEngine._on_stream_tick` forwarded it to the user's app WebSocket, to `portfolio_stream.apply_broker_ticks` and to `trade_stream.apply_broker_ticks`, and both services performed the token→symbol join themselves against `db.holdings`. Three consequences:

• **Two core services were coupled to one broker's identifier format.** The join was written twice, in modules that have no business knowing what a broker instrument identifier looks like.

• **A symbol-identified broker silently marked nothing.** Most brokers outside Kite identify instruments by trading symbol, so their ticks carry no token to join on. Every join produced nothing, `override` stayed empty, and every live P&L recompute for those users stopped — on a healthy socket delivering good prices, with no exception, no log line and no failing test. This is the same class of defect ADR-032 found one layer down, surviving one layer up.

• **A broker's numeric handle reached the browser.** The frontend stored `brokerTicks` "keyed by instrument token", which is provider-shaped data in a client that MARKET_DATA_ARCHITECTURE.md says must never learn where a price came from.

Decision

**Resolve instrument identity at the broker boundary; hand core services a canonical tick.**

Two modules, one on each side of the D4.1 direction rule:

• `services/market_engine/ticks.py` — `MarketInstrument` (symbol, exchange) and `MarketTick` (symbol, price, exchange, volume, ingested_at). The canonical shape, on the market side, naming no broker. It invents no identity scheme: `symbol` + `exchange` is what quotes, holdings, trades and the watchlist already key on, so a canonical tick joins against all of them with no translation table.

• `services/brokers/instruments.py` — `InstrumentMap`, built from the account's synced holdings and positions. Those canonical rows carry `instrument_token`, `symbol` and `exchange` together, so the mapping table costs no broker call and is per-account by nature, which is correct: an instrument identifier is only meaningful inside the broker that issued it.

`BrokerEngine._on_stream_tick` is the boundary. Everything above it — the app WebSocket, the portfolio recompute, the trade recompute — receives `MarketTick` dicts.

Four properties, each pinned by a falsification test run against a deliberately broken version:

1. **Both identification styles pass through unchanged core code.** A numeric token resolves through the map; a trading symbol canonicalizes directly and is qualified with the account's exchange when it has one. The fictional `NovaAdapter` — symbol-identified, string-priced, never synced — reaches both core services end to end.

2. **An unmapped token is dropped, never renamed.** Using the token as a symbol would push a broker's numeric handle into `db.holdings`, the trade snapshot and the AI's context as an instrument name.

3. **The canonical shape is enforced by the type.** A lowercase symbol, a non-numeric price, a zero price (what a truncated binary packet decodes to, and what would mark a whole position at zero) and a price outside the Market Engine's own quote bounds are all refused at construction, so canonicality does not depend on every caller remembering to normalize.

4. **No broker identifier reaches a core service**, asserted on the real delivery path *and* by a source sweep over `portfolio_stream.py` and `trade_stream.py`, so the join cannot return in a helper the behavioural test does not exercise.

**Why the engine and not the transport.** Mapping needs the account's synced portfolio, which is the engine's to hold. A transport that reached for it would be back to knowing things about brokers, undoing ADR-032.

**Why the map has no TTL.** Holdings change only through `sync_portfolio`, so invalidating there is exact; `start_stream` seeds it from the same two lists that decide the subscription (the only way an intraday position — never persisted — is mappable at all), and `disconnect` drops it. A timer would only add a window in which a correct map is discarded and rebuilt from a narrower source.

Consequences

• `trade_stream` gained coverage rather than losing it: a trade in a symbol the *demat account* does not hold could not previously be marked from ticks at all and waited for the 60s monitor. A canonical tick marks any open trade in that symbol.

• A batch that resolves to nothing now stops at the boundary instead of pushing an empty tick list to the browser and waking two recomputes on every frame.

• `MarketTick` carries `ingested_at` (UTC, ours) and not the broker's timestamp, which `BrokerTick` keeps as a verbatim string precisely because brokers disagree on format and timezone and a wrong parse is worse than none.

• The frontend's `broker_price_tick` payload is now symbol-keyed. Nothing consumed the token form, so this is a contract improvement with no UI change.

• The "nothing resolved" warning is throttled to once a minute per broker. The condition is persistent (a stale map stays stale until the next sync) while the ticks hitting it arrive several times a second per account, so an unthrottled line is tens of thousands of identical warnings an hour — visible enough to bury everything else, which is the same as not being visible.

• Still deliberately not done: no broker is registered as a `MarketDataProvider`, there is no `subscribe`/`on_raw` push surface, no make-before-break switching and no provider failover. Those need a canonical tick to exist first, which is what this ADR provides.

Review Date

At D4.4, when broker ticks are first routed into the Market Gateway as a registered market-data feed.

Authoritative documents

BROKER_INTEGRATION.md, MARKET_DATA_ARCHITECTURE.md

---

# ADR-034

Title

The Broker Feed as a Registered Market-Data Provider (Sprint D4.4)

Date

2026-08-21

Status

Accepted — implemented

Context

After D4.3 a connected broker's ticks were canonical `MarketTick`s and reached three consumers: the user's app WebSocket, `portfolio_stream` and `trade_stream`. They drove P&L and nothing else. The Market Engine did not know a live feed existed; `source_manager.status()` reported the delayed baseline to a user watching tick-by-tick prices; and the `TICKS` capability — declared in D1 specifically so the Source Manager could resolve *nothing* for it rather than have call sites invent a provider — resolved to nothing for every user in the platform. A broker feed was real data that was not market data.

MARKET_DATA_ARCHITECTURE.md has always specified the missing link (a push surface on the adapter contract, `subscribe`/`unsubscribe`/`on_raw`) and D1 deliberately deferred it (ADR-028) because it shipped one request/response provider and no consumer able to receive a pushed tick. D4.2 and D4.3 built the two segments underneath it. D4.4 is the join.

Decision

**A pushed feed enters the platform as an ordinary `MarketDataProvider`, through one generic market-side class and one broker-side construction seam.**

• `MarketDataProvider` gains the push surface. `subscribe`/`unsubscribe` are pull-direction calls meaningful to both provider families and live on the base class with bookkeeping defaults (MARKET_DATA_ARCHITECTURE.md's adapter rule 5: "the rest of the system cannot distinguish the two"). `on_raw` is push-direction, is meaningless for a provider that cannot push, and defaults to raising.

• `providers/streaming.py` holds `StreamingTickProvider` — generic, naming no broker, no exchange and no vendor. `services/brokers/market_feed.py` constructs one per connected account and injects it through the Market Gateway. broker → market is the permitted direction; the Market Engine still imports no broker module.

• The Market Gateway owns registration and the sink. A provider cannot deliver into anything but the gateway, because the gateway is the only thing that ever binds its sink (Developer Rule 2).

• Registration validates the provider's declarations about itself (`validate_provider`), raising `ProviderContractError` on three contradictions: a push capability without `kind=STREAMING`; `tier=STREAMING` without `kind=STREAMING`; `kind=STREAMING` without an `on_raw`.

Alternatives considered

**Register the broker feed with `QUOTES` and let it take the quote path immediately.** This is the headline feature and it was rejected for this sprint. A priority-1 provider declaring QUOTES outranks the baseline the instant it registers, which *is* the feed switch — performed without the make-before-break gate MARKET_DATA_ARCHITECTURE.md requires ("connect the new provider, confirm first valid data, then release the old one"). The registration seam and the switch are separable, and shipping them together would mean the switch's failure modes could only be tested through the registration path. The provider therefore declares `TICKS` alone: a capability nothing has ever served, so nothing is taken from anybody, and the baseline continues to answer every quote for every user.

**A per-user feed registry inside the broker layer.** Rejected outright: the provider registry already answers "which providers exist for this user", and a second one would have to be kept in step across register, unregister, replace and process restart, and would answer differently the first time one was missed. `publish_market_ticks` looks the account's provider up in the existing registry.

**Normalize broker ticks in the gateway, like every other provider payload.** There is nothing to normalize. Normalization converts a *provider's* shape into the platform's, and what arrives here is already `MarketTick`, the platform's own canonical tick, produced at the broker adapter boundary in D4.3. `normalizer_key` says so rather than naming a family that does not exist.

**One bus event per tick.** Rejected on cost: a feed frame is already a batch of up to hundreds of packets, and the event bridge mirrors every event to Redis. One `market.tick` event carries the batch.

Consequences

• `resolve_feed(TICKS)` now resolves for a user with a streaming broker connected, at `tier=streaming`, and for nobody else — `owner_user_id` plus D2's entitlement filter make cross-user leakage impossible by construction.

• The tick event carries `user_id` when the feed is owned by one. The event bridge delivers a payload with a `user_id` to that user alone, which is what stops data consumed under one user's entitlement from being broadcast to every socket on the market channel. This is an entitlement boundary, not a preference.

• **Readiness became a distinct concept from health.** A registered provider whose socket is not up has no failures to its name and is still unusable, so health — which is evidence from past calls — cannot express it. `is_ready` can, and `StreamingTickProvider.is_eligible_for` consults it, which is the override `base.py` anticipated in D2.

• **The contract check found two pre-existing test doubles that were not valid providers.** `FakeStreamingProvider` and `UserScopedProvider` in `test_market_gateway.py` both declared `kind=STREAMING` with no way to be pushed into — a double for a broker feed that could not receive a broker feed. Both gained a real `on_raw`. That is the check doing its job before a real adapter made the same mistake.

• `kind=STREAMING` requires `on_raw`, not a push *capability*. The stricter first draft would have rejected a streaming provider serving pushed quotes — exactly the shape the D4.5 feed switch produces — which is how the two existing doubles exposed the over-strict rule.

• Still deliberately not done: no make-before-break switch, no broker→baseline failover, no `QUOTES` on a broker provider, no additional broker adapters, no Zerodha market-feed integration. Each is separately testable and none is entangled with this seam.

Authoritative documents

MARKET_DATA_ARCHITECTURE.md, BROKER_INTEGRATION.md

Review Date

At D4.5, when the make-before-break feed switch promotes a broker feed to primary for quotes.

---

# ADR-035

Title

Make-Before-Break Provider Switching and Baseline Failover (Sprint D4.5)

Date

2026-08-21

Status

Accepted — implemented

Context

D4.4 registered a connected account's tick stream as a real `MarketDataProvider` and deliberately stopped one step short of the headline feature: the provider declared `TICKS` and not `QUOTES`, so the polled baseline continued to answer every quote for every user. The reason was explicit in ADR-034 — a priority-1 provider that declares `QUOTES` outranks the baseline the instant it registers, and that *is* the feed switch, performed without the gate MARKET_DATA_ARCHITECTURE.md requires: "connect the new provider, confirm first valid data, then release the old one."

D4.5 builds that gate. The requirement it has to satisfy is narrow and unforgiving: a user's feed may become the primary quote source only after it has demonstrably produced valid canonical data, must revert to the baseline the moment that stops being true, and neither transition may affect any other user or leave a moment in which no provider serves.

Decision

**Promotion is not an operation. It is the outcome of eligibility, recomputed on every resolution.**

• `StreamingTickProvider` now declares `QUOTES` alongside `TICKS`, and declaring it grants nothing. `is_eligible_for` is the gate: entitlement (unchanged from D2), then link state for the pushed capabilities, then readiness *and* per-symbol coverage for quotes.

• **Readiness is earned by data, and only by data.** A feed reaches `READY` when a record survives coercion into a canonical `MarketTick` while its link is up and instruments are subscribed. Not on socket open, not on authentication, not on a subscribe frame, and never on elapsed time. The states are `REGISTERED → CONNECTING → CONNECTED → SUBSCRIBED → READY`, with `FAILED` / `DISCONNECTED` on link loss.

• **`PRIMARY` is deliberately not a provider state.** Being primary is the head of one `resolve_feed` chain for one capability and one context. Storing it on a provider would create a second, lagging copy of a fact the resolver already computes — and would make it possible for two providers to believe they were primary for one quote stream, the state this document forbids. With promotion expressed only as "what does resolution return", atomicity is free: one function, one head, recomputed from current readiness, no lock and no handover protocol.

• **Make-before-break falls out of the same property.** The baseline is never disconnected, never unregistered, and never made ineligible. It moves from head of the failover chain to standby *inside* the chain, so at every instant of the switch there is a provider that can serve.

• **Failover is push-driven.** `BrokerStream` reports its transport's connect/disconnect through a new `on_link_state` callback; `set_market_feed_link` relays it to the provider. Nothing polls, nothing sweeps, and no health counter has to escalate. A stale-tick bound on coverage (`DEFAULT_TICK_MAX_AGE_SECONDS`, 120s) is the lazy backstop for a link that dies without saying so — evaluated at resolve time, with no timer.

• **`ResolutionContext` gains a `capability` field**, stamped by `ProviderRegistry.candidates_for`. `is_eligible_for` is the documented per-provider extension point and D4.5 needs it to answer differently by capability: a live stream is a legitimate answer to "is a tick feed attached to this user" the moment its link is up, and is not a legitimate answer to "who serves this user's quotes" until it has proved it can.

Alternatives considered

**A promotion/demotion orchestrator in the Source Manager, holding the current primary per user.** Rejected. It is a cache of a derived fact, needing invalidation on link state, readiness, registration, unregistration, entitlement and health — six paths guarding a sorted traversal of a short list. Every one of them is a way for two providers to be primary at once, which is the exact failure the orchestrator would exist to prevent.

**Gate readiness on a timer ("connected for N seconds").** Rejected outright. It promotes a feed that connected and then said nothing at all, which is the precise failure make-before-break exists to prevent, and it is a poll loop wearing a different name.

**Unregister the feed on a dropped socket and re-attach on reconnect.** Rejected. A blip is not an ended entitlement. It would churn the registry, discard the feed's diagnostics, and conflate a reconnecting transport with a revoked token — two events with opposite correct responses. `mark_link_down` leaves the feed registered and un-resolvable; `detach_market_feed` remains what an ended entitlement calls.

**Let a promoted feed answer every symbol.** Rejected. A tick feed prices what it streams. Claiming the rest would answer a US index with silence while a provider that carries it sat one rank below — and it contradicts this document's own per-symbol rule. Coverage is "I hold a recent tick for this instrument".

**Stitch the missing quote fields (previous close, OHLC) from the baseline's last quote.** Rejected as fabrication. The result would present two readings from two sources at two timestamps as one quote, and nothing downstream could tell.

Consequences

• A user with a connected, ready, streaming broker feed resolves to that feed for quotes on the instruments it streams, at `tier=streaming`, and to the baseline for everything else — in the same session, with no call site aware two providers were involved.

• `source_manager.publish_status` accepts `user_id` and publishes a user-scoped `provider.status` carrying `user_id`, which the event bridge already delivers to that user alone. No new topic. Without it the one user whose tier actually flipped would be the only consumer never told.

• **A tick-derived quote carries no `change` / `change_pct` and no OHLC**, because a canonical tick carries no previous close. This is a known limitation recorded in TASK.md, not an oversight: the canonical tick grows those fields when a real feed that populates them lands. Until per-user quote routing is wired into the REST surface, no production caller passes `user_id` to `market_gateway.get_quote`, so nothing regresses today — and that wiring is gated on closing this gap.

• `attach_market_feed` takes the account's canonical instrument universe (`InstrumentMap.symbols`). A feed attached without symbols can never become ready — the safe default, and the correct behaviour for an account with nothing to stream.

• The switching machinery names no broker anywhere: not in an import, not in an identifier, not in a string literal. Pinned by `test_the_switching_machinery_names_no_broker`, proved non-vacuous against a planted `if broker == "zerodha"`.

• Still deliberately not done: no Zerodha Kite market-feed adapter and no other broker adapters, no generalized provider re-probe, no probation windows or latency scoring (D5), no frontend tier-indicator work.

Authoritative documents

MARKET_DATA_ARCHITECTURE.md, BROKER_INTEGRATION.md

Review Date

At D5, when probation windows, latency scoring and flap suppression are designed on top of this gate.

---

# ADR-036

Title

Zerodha Kite as the First Concrete Stream Adapter (Sprint D4.6)

Date

2026-08-21

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

D4.1–D4.5 built a generic broker-streaming architecture — a broker-owned codec, a canonical tick, a canonical instrument identity, a registered market-data provider, a readiness gate and make-before-break switching — and proved every part of it against `NovaAdapter`, a broker that does not exist. That is the right way round: a framework validated only by its first real user is a framework shaped by that user. But a framework validated *only* by a fictional user has never met a real wire format, and the question D4.6 exists to answer is whether the generality was real or merely untested.

The Kite protocol was already partly present. D4.2 moved Kite's binary framing, ticker URL, subscribe frames and error convention out of the shared transport and into the adapter. Moving working code is where a silent behaviour change hides, and D4.2's parity test asserted the *moved* code against what the removed code produced — which is a regression test, not a specification test. Nothing had re-derived the implementation against the Kite Connect v3 documentation.

Decision

**Zerodha is the first concrete stream adapter. Zerodha is not the market-data architecture.**

• **The adapter owns the entire Kite surface and nothing else does.** Endpoint construction, query-string authentication, subscribe frames, binary decoding, instrument-token interpretation, price scaling, and the meaning of a Kite error. `test_kite_added_no_kite_knowledge_outside_its_own_adapter` sweeps every module under `services/` for Kite's vocabulary in executable code — comments and string literals stripped, so prose about Kite stays legal and computing with Kite does not — and permits exactly one file.

• **Stream mode is LTP, and the decision is a named constant.** `STREAM_MODE = "ltp"`, which is what TASK.md and the adapter already documented. The tick feed marks holdings and open trades and answers streamed quotes; all three need a last price. Quote mode multiplies every frame for OHLC and depth no consumer reads; full mode multiplies it again for a twenty-level book with no surface. The decoder reads only the first eight bytes of each packet — the token and last price, which every tradable Kite mode puts there — so widening the mode later is a subscribe-frame change rather than a decoder rewrite.

• **Four protocol defects in the pre-existing implementation were corrected**, each of which fails silently rather than loudly, which is why none had been noticed:

  1. **Segment-aware price scaling.** Kite encodes the exchange segment in the low byte of the instrument token and quotes `cds` at ÷10⁷ and `bcd` at ÷10⁴, not ÷100. A flat divisor prices a currency instrument four to five orders of magnitude wrong — plausible on a chart, and marked against a real position.
  2. **Unsigned packet reads (`>II`, not `>ii`).** A token above 2³¹ read signed comes back negative, matches nothing in the account's `InstrumentMap`, and drops every tick for that instrument with no exception and no log line.
  3. **A truncated frame stops the parse instead of resynchronising.** A Kite packet is two integers, so a misaligned read produces a plausible token at a plausible price — the one outcome worse than returning nothing. A packet merely *too short to price* is still skipped, because its own length prefix keeps the framing intact; the two cases are distinct and are told apart.
  4. **Instrument tokens are coerced, not type-tested.** `isinstance(token, int)` rejected `"738561"` — the same value after a MongoDB round trip, a split `InstrumentMap` already documents on the resolution side. On the subscription side the instrument is simply absent from the subscribe frame: the wire never carries it, and the missing prices look exactly like an instrument that has not traded.

• **A new adapter hook, `stream_connect_error(error) -> str | None`, for a session refused before any frame exists.** Kite answers a stale token with HTTP 403 during the WebSocket handshake, so the `{"type": "error"}` frame `decode_stream_frame` reads for a mid-session token death never arrives. Unclassified, the transport could not tell a dead token from a broker outage: it reconnected on the backoff schedule indefinitely, the account's market feed stayed registered, and the user was never asked to reconnect — every connected user, every morning, since Kite invalidates all access tokens daily at ~06:00 IST.

  The hook is deliberately **classification, not action**. The adapter says what the failure meant; the transport raises its own `_AuthExpired` and the existing expiry path (stop the stream, detach the market feed, notify the user) runs unchanged. An adapter that acted on its own would be reintroducing the per-broker branch this framework exists to remove. The default is `None` — retry on the normal backoff — so no other adapter changes.

Alternatives considered

**Implement quote or full mode for the volume field.** Rejected for now. It multiplies the bandwidth of every frame for a field one consumer would use, against a documented decision that already exists. The absent volume is recorded as a limitation instead, and the decoder is written so the change is a subscribe frame rather than a rewrite.

**Fetch Kite's full instrument dump so the feed can stream anything.** Rejected as out of scope, explicitly rather than silently. ~80k rows refreshed daily is a catalog with its own storage, refresh schedule and staleness semantics — a sprint, not a line — and nothing in D4.6 needs it: a tick for an instrument the account does not hold has nothing to be joined to. Holdings-and-positions scope is unchanged from D4.3.

**Implement `{"a": "unsubscribe"}` for completeness.** Rejected as speculative generality. The framework has no incremental-subscription caller — a portfolio sync restarts the stream, which resubscribes from the current holdings — so the frame would be code nothing sends and nothing could exercise honestly. It is a one-line adapter addition the day an incremental caller exists.

**Handle the 403 inside the Zerodha adapter by stopping the stream itself.** Rejected. That is broker-specific failover logic, which is the branch D3 removed and D4 was built to keep out. The adapter interprets; the generic lifecycle acts.

**Trust D4.2's parity test as sufficient evidence the codec was correct.** Rejected, and this is the general lesson: a test that asserts moved code against what the removed code produced proves the move, not the protocol. Four defects survived it, all of them silent. A concrete adapter needs a specification test, and the specification is the broker's documentation.

Consequences

• A user with a connected Zerodha account and a synced portfolio gets an exchange-grade streaming feed for the instruments they hold, promoted over the Yahoo baseline only after a valid canonical `MarketTick` has arrived on the current link, and demoted back the instant the link drops. All of that is the unchanged D4.5 machinery; D4.6 added no switching, no readiness and no failover of its own.

• **A Kite-derived `MarketTick` carries no volume.** LTP packets have none. The D4.5 limitation stands unchanged alongside it: a tick-derived quote still carries no `change` / `change_pct` and no OHLC.

• Kite's 3,000-instrument-per-connection cap is neither enforced nor sharded. D5 owns multi-connection sharding; a retail portfolio is nowhere near the cap.

• **LIVE VALIDATION WAS NOT PERFORMED.** `KITE_API_KEY` and `KITE_API_SECRET` are configured in this environment, but a ticker connection needs a per-user `access_token` obtainable only through an interactive browser login (`request_token` → `POST /session/token`), and no connected Zerodha session exists here. Every claim rests on deterministic validation against fixtures built from the published Kite Connect v3 binary specification, plus 15 source mutations observed red. A live smoke test — connect, subscribe, real tick, canonical tick, readiness, promotion over the baseline, disconnect, fallback — is outstanding and must be run before this is called production-verified.

• The multi-broker acceptance criterion is met and asserted, not assumed: all Nova tests remain green, and one test drives Kite and Nova through the identical transport function and asserts a single canonical output shape. A second broker with a different protocol and instrument model still needs no change to the Market Engine, the Market Gateway, the Source Manager, `StreamingTickProvider`, the provider registry or the canonical tick contract.

• The frontend is untouched and shows generic source status. No Kite detail, no broker label on a market-data surface.

Authoritative documents

MARKET_DATA_ARCHITECTURE.md, BROKER_INTEGRATION.md

Review Date

When the first live Zerodha session is available, to close the live-validation gap; and at the second streaming broker (Upstox), which is the real test of whether D4.6 generalised or merely worked.

---

# ADR-037

Title

Upstox as the Second Concrete Stream Adapter, and the Multi-Channel Stream Transport It Required (Sprint D4.7)

Date

2026-08-21

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-036 closed with an explicit open question: whether D4.1–D4.6 had *generalised* or had merely *worked* for the one broker they were built against. Its own Review Date named the test — "at the second streaming broker (Upstox), which is the real test of whether D4.6 generalised or merely worked." D4.7 is that test.

Upstox was chosen because it agrees with Kite about almost nothing at the wire. Its market feed is Protocol Buffers rather than bespoke binary; it identifies instruments by a compound string (`NSE_EQ|INE002A01018`) rather than a 32-bit integer; it quotes an IEEE `double` in rupees rather than integer paise on three segment-dependent scales; it authenticates by bearer header rather than by query string; it subscribes with one JSON frame sent as **binary** rather than two sent as text. Most consequentially, **it serves order updates and market ticks on two entirely separate WebSockets**, where Kite multiplexes both onto one.

The protocol was taken from Upstox's own published SDK (`upstox/upstox-python`, `upstox_client/feeder/`) and its official `MarketDataFeedV3.proto`, not inferred from Kite and not inferred from the Upstox portfolio stream this platform already spoke. ADR-036's general lesson — that a test asserting moved code against what the removed code produced proves the move and not the protocol — applies with more force to a second broker, where the temptation to reason "like the first one, but…" is the whole risk.

Decision

**The market side generalised completely, and that is reported as the finding it is.** Nothing changed in the Market Engine, the Market Gateway, the Source Manager, `StreamingTickProvider`, the provider registry, the canonical `MarketTick`, the readiness gate, the failover path, the Portfolio Engine, the Trading Engine or the frontend. `InstrumentMap` needed no extension either: it matches on the *stringified* identifier — a detail D4.3 introduced for a MongoDB round trip — so a compound string key resolves through the same table an integer token does, with no second identity model and no per-broker branch.

**One assumption did not generalise, and it is in the broker transport.** `BrokerStream` held one endpoint, one codec and one protocol, and `BrokerStreamManager` keyed its registry on `(user, broker)`. Nothing had ever said "a broker has one connection"; Kite simply could not expose the assumption, because it multiplexes. Under the old key, Upstox's second `start_stream` for an account would have silently **replaced** the first — one feed live, one feed gone, no exception, no log line.

The generalisation is `BrokerStreamChannel`: a name, a protocol and a codec. Three properties were required of it and all three hold:

• **It names no broker.** The transport opens one connection per channel and still cannot tell Kite from Upstox; a channel is as opaque to it as an adapter was.

• **It is free for a single-channel broker.** `BrokerAdapter.stream_channels()` defaults to one channel backed by the same five `stream_*` methods every adapter already implements, so a broker that has never heard of channels *is* a single-channel broker. Zerodha is unchanged byte for byte, and so is every test double written before D4.7.

• **It carries no market-side change.** The channel concept stops at the broker package boundary. Nothing above it — no provider, no gateway, no resolver — learns that a broker may hold more than one socket.

Two consequences of channels had to be handled deliberately, because a broker's connections **fail independently**:

  1. **Link state is per channel, and only the tick-carrying channel drives the market feed.** Relaying every channel's link state would let a broker's order socket blinking demote a market feed delivering prices perfectly well, and — the sharper direction — let that order socket *re-arm the readiness gate* for a tick feed that is not connected at all. Which channel counts comes from the channel's own `delivers` declaration, never from a broker name.

  2. **A decoded event is narrowed by the channel before the broker capability gate.** The broker legitimately declares both realtime capabilities, so the existing gate would pass a tick decoded on the order channel. Without the narrowing, that account's market-data provider would be driven — marked live, marked ready — by a socket carrying no market data.

`BrokerRegistry` gained the matching startup validation: every declared realtime capability must be carried by some channel, channel names must be unique within a broker, and every channel must declare a protocol. The failure it converts into a startup error is otherwise entirely silent — the provider registers on the strength of the capability, the sockets connect, the reconnect loop is content, and every tick is dropped by the narrowing, which from outside is indistinguishable from a market with no trades in it.

**A dependency-free protobuf reader, checked against an independent oracle.** The v3 feed is Protocol Buffers. `protobuf` is absent from `requirements.txt` by a documented decision (PH2.8 removed it with grpcio after PH2.1 measured the runtime-image cost of packages no application module imports), and `protoc` is not a build dependency here, so a generated `_pb2` stub would have had to be hand-built as a descriptor and then kept from going stale. Re-adding a C-extension runtime dependency plus a build artifact to read one `double` out of a map is a poor trade.

The adapter therefore carries a ~90-line proto3 reader that decodes only the fields the canonical contract can hold. The risk of hand-decoding a wire format is getting the *schema* wrong, and a test written by the same hand as the decoder cannot catch that — so the fixtures are not ours. `tests/_upstox_proto.py` transcribes Upstox's official `MarketDataFeedV3.proto` and serializes through **Google's** protobuf runtime; the bytes the tests feed the adapter are the bytes Upstox's own SDK produces. `protobuf` is pinned in **requirements-dev.txt only**.

Alternatives considered

**Let the Upstox adapter open its own second socket.** Rejected outright. It would have duplicated the reconnect loop, the jittered backoff, the link-state reporting, the auth-expiry path and the capability checks inside a broker module — the exact duplication ADR-032 rejected DB-3 for, and the exact code where a per-broker copy diverges and one broker quietly stops reconnecting.

**Add a `channel` argument to the five existing adapter methods.** Rejected. It changes the signature every existing adapter and every test double implements, so a broker not yet updated would fail at the first frame rather than at import — a compatibility break discovered on a live socket. Wrapping the adapter as its own default channel preserves the old contract exactly instead of reimplementing it beside a new one.

**Drop the Upstox portfolio stream and use the one socket for ticks.** Rejected: it silently removes live Upstox order updates that ship today. A regression, not a simplification.

**Add `protobuf` to the production runtime with a vendored `.proto` and generated `_pb2`.** Rejected, above.

**Subscribe in `full` mode so the tick can carry volume.** Rejected, for the same shape of reason ADR-036 rejected Kite's quote mode, but re-derived against what Upstox's modes actually carry rather than copied. `full` adds five depth levels, 1-minute/30-minute/daily candles, greeks and open interest — a multiple of the bandwidth per frame for one field one consumer would use. `full_d30` additionally requires an Upstox Plus entitlement this platform must not require of its users. The absent volume is recorded as a limitation.

**Map `LTPC.ltq` to the canonical `volume` field.** Rejected, and worth naming because it is the plausible mistake. `ltq` is the *last traded* quantity — one trade's size — not the day's cumulative volume, which lives in the `full` modes as `vtt`. Putting it in `volume` would populate a canonical field with a number that means something else, which is worse than leaving it unset.

**Fetch Upstox's instrument catalogue.** Rejected as unnecessary rather than merely out of scope: a synced holding or position already carries the Upstox instrument key beside the symbol and the exchange, which *is* the mapping table in both directions. Requiring the catalogue would have turned an adapter sprint into a data-pipeline sprint for no gain — a tick for an instrument the account does not hold has nothing to be joined to.

**Use the `/v3/feed/market-data-feed/authorize` REST step.** Rejected. Upstox answers the handshake with a 307 to a signed socket URL and the client follows it, so the extra call buys nothing — and what it returns is a credential-bearing URL that would then have to be kept out of every log line. Connecting directly with a bearer header means **nothing credential-bearing is in the URL at all**, which is strictly stronger than masking it.

Consequences

• A user with a connected Upstox account and a synced portfolio gets a streaming feed for the instruments they hold, promoted over the Yahoo baseline only after a valid canonical `MarketTick` has arrived on the current link, and demoted the instant that link drops — the unchanged D4.5 machinery, reached by a second broker that shares no wire format with the first.

• **The second-broker architecture proof holds where it matters and is stated honestly where it does not.** No core *market* module changed. The broker *transport* did, generically, and that change is the sprint's real finding rather than an embarrassment to be buried: an assumption that only a second broker could expose was exposed, and removed, by the second broker.

• **A pre-existing Upstox lifecycle defect was closed on the way.** The Upstox portfolio stream had no handshake-refusal classification, so a token dead at 03:30 IST reconnected on the backoff schedule indefinitely — the same defect ADR-036 found in Kite, present in Upstox since before D4.6 and never noticed because Upstox had no market feed to lose. One classifier now serves both Upstox channels.

• **An Upstox-derived `MarketTick` carries no volume**, for the `ltq`/`vtt` reason above. The D4.5 limitation stands unchanged alongside it: a tick-derived quote still carries no `change` / `change_pct` and no OHLC, and D4.7 deliberately does not solve that by stitching two providers together.

• **A latent staleness window in `StreamingTickProvider` was found by falsification and closed.** Neutralising `_discard_evidence` left the suite green, because demotion on link loss is driven by the readiness state. But the cache it clears is per symbol while readiness is re-earned by *any* symbol: a feed that ticked A and B on link 1, lost it, reconnected, and received one fresh tick for A would have answered a quote for **B from the dead link's price** — inside the freshness window, labelled `streaming`, with a newer price sitting in the baseline underneath. This predates D4.7 and affects Zerodha equally. A test now pins it.

• Upstox's 5,000-key `ltpc` limit is enforced by trimming with a warning, not by sharding across connections. D5 owns sharding; a retail portfolio is nowhere near the limit. Note that "a broker needs several connections" (D4.7) and "one subscription is sharded across several connections" (D5) are different problems, and only the first is solved here.

• **LIVE VALIDATION WAS NOT PERFORMED.** An Upstox market-feed connection needs a per-user `access_token` obtainable only through an interactive browser OAuth login, and no connected Upstox session exists in this environment. Every claim rests on deterministic validation against fixtures encoded from Upstox's official schema by Google's protobuf runtime, plus 12 source mutations observed red. A live smoke test remains outstanding for **both** streaming brokers, and neither is production-verified until it is run.

• The frontend is untouched and shows generic source status. No Upstox detail, no instrument key, no broker label on any market-data surface.

Authoritative documents

MARKET_DATA_ARCHITECTURE.md, BROKER_INTEGRATION.md

Review Date

When the first live Upstox or Zerodha session is available, to close the live-validation gap that now spans both streaming brokers; and at the third streaming broker, which is where the channel model itself gets its first independent test.

---

# ADR-038

Title

Angel One SmartAPI as the Third Concrete Stream Adapter, and the Application-Level Keep-Alive It Required (Sprint D4.9)

Date

2026-08-24

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-037 closed with a Review Date naming its own test: "at the third streaming broker, which is where the channel model itself gets its first independent test." D4.9 is that test. Upstox forced `BrokerStreamChannel` into existence and was the only broker using it, so the concept had never been exercised by a broker that did *not* need it — and a generalisation validated only by the case that motivated it is a generalisation on a sample of one.

Angel One arrives from the opposite direction. Its market feed is **one** socket, so it takes the free single-channel path: it declares nothing about channels, inherits `AdapterStreamChannel`, and is opened by the same transport that opens Upstox's two. It also agrees with neither predecessor at the wire: fixed 51-byte little-endian packets carrying **one tick per frame** (where Kite packs hundreds and Upstox returns a map), a numeric instrument token that is unique only *within an exchange segment*, four authentication headers, and integer paise on a segment rule that is Kite's trap without being Kite's rule.

The protocol was taken from SmartAPI's own published WebSocket 2.0 request/response contract and its official Python SDK (`angel-one/smartapi-python`, `SmartApi/smartWebSocketV2.py`), not inferred from either broker already integrated. ADR-037's lesson applies with more force at a third broker, where the temptation to reason "like one of the other two, but…" is the whole risk — and two of this adapter's decisions (the price rule and the identity) are places where that reasoning produces code that runs and is wrong.

Decision

**The market side generalised again, and that is reported as the finding it is.** Nothing changed in the Market Engine, the Market Gateway, the Source Manager, `StreamingTickProvider`, the provider registry, the canonical `MarketTick`, the readiness gate, the failover path, the Portfolio Engine, the Trading Engine or the frontend. `InstrumentMap` needed no extension for the second consecutive broker.

**The channel model passed its independent test.** Angel One declares no channels and gets one, backed by the five `stream_*` methods every adapter has implemented since D4.2. The transport, the registry validation, the per-channel `delivers` narrowing and the channel-scoped link routing all behaved as specified for a broker that had no part in their design.

**One assumption did not generalise, and it is in the transport again.** `ping_interval` has always configured the WebSocket **protocol's** ping frames — opcode 0x9, exchanged by the two libraries, invisible to both applications. SmartAPI does not count those as liveness: it requires the **text** frame `ping` on the data channel every 30 seconds and closes a connection that stops sending it. Nothing in the platform could express that.

The failure it would have produced is the kind this architecture keeps finding: not loud. The socket connects, subscribes, delivers ticks for half a minute and is closed by the broker — then reconnects, and does it again. In the logs that is a flapping feed, not a missing frame; the account's market-data provider would spend its life re-earning readiness it keeps losing, and the user would see the tier flicker between live and delayed all session.

`BrokerStreamEndpoint` therefore gained `heartbeat_frame` and `heartbeat_interval`. Three properties were required and all three hold:

• **It names no broker.** The frame is an opaque `str`/`bytes` the adapter supplies; the transport sends it on a timer and cannot tell what it means.

• **It is free for a broker that needs none.** Both fields default to `None`; Zerodha and Upstox are unchanged, and the contract refuses *half* a declaration (a frame with no interval, or an interval with no frame) because either one silently leaves the feed without the keep-alive it declared.

• **It stops at the broker package boundary.** Nothing above the transport — no provider, no gateway, no resolver — learns that a feed has a heartbeat.

**A second generic change, in the engine: `feed_token` joined `TOKEN_FIELDS`.** Angel One's socket authenticates with a per-session credential *separate* from the token its REST API takes. `TOKEN_FIELDS` is the list of session fields encrypted at rest and cleared on disconnect; a session credential outside it would have been the one field in `db.broker_accounts` that SECURITY.md's encryption-at-rest rule did not cover, and it is enough — with the app key and the client code — to open a market feed for the account. The list is generic session-credential names rather than a per-broker registry: an adapter's `exchange_token` decides which of them its broker issues, and a broker that issues none of one never sets it. The disconnect path now clears every field in the list rather than three named ones, so the next such addition cannot be half-applied.

**Two protocol decisions where copying a predecessor produces code that runs and is wrong.**

  1. **Identity is `(exchangeType, token)`, never the token alone.** SmartAPI numbers instruments per exchange segment: NSE 2885 and BSE 2885 are different instruments. Kite's shape — store the bare token — would have let a BSE tick resolve to an NSE holding and mark a position at another instrument's price. Nothing raises; the number is simply wrong. The adapter writes a segment-qualified `"1|2885"` onto every synced row and rebuilds the identical string from every decoded tick, from one function, so the two sides cannot drift.

  2. **The price scale is keyed on the segment field, not on the token.** Both brokers quote paise and both have a currency exception, which makes `zerodha.price_divisor` look reusable. It is not: Kite derives its scale from the *low byte of its 32-bit token*, and applying that to a SmartAPI token consults a byte that means nothing at all. Same class of trap, different rule, decided from SmartAPI's own contract.

**Trading-symbol series suffixes are stripped at the adapter boundary.** SmartAPI names an equity `TATASTEEL-EQ` where every other broker here names it `TATASTEEL`. Left alone, a user holding one stock at two brokers would hold two different canonical symbols — a split portfolio, a split watchlist, and a feed whose coverage never matches the platform's own instrument universe. The adapter is the only code entitled to know that the suffix is a series code rather than part of the name.

Alternatives considered

**Let the Angel One adapter run its own keep-alive task.** Rejected for the reason ADR-032 rejected per-broker transports and ADR-037 rejected a broker-opened second socket: the adapter would own a task whose lifetime has to match a connection it does not hold. Getting that wrong leaks one task per reconnect — forever, on precisely the flapping feed the keep-alive exists to prevent — and the correct cancellation point is inside the transport's `finally`, where the adapter cannot reach.

**Reuse `ping_interval` by setting it to 30 and hoping.** Rejected because it is not the same mechanism. A protocol ping satisfies libraries, not SmartAPI's application-level liveness check, and a design that confuses the two produces a connection that looks healthy at every layer that can see it and is closed anyway.

**Use SmartAPI's `loginByPassword` (client code + PIN + TOTP).** Rejected outright. It would require this platform to hold a user's trading PIN and TOTP seed — a class of secret no other broker here needs and SECURITY.md forbids. The publisher login is an ordinary browser redirect.

**Use the documented query-string form of the socket URL** (`?clientCode=&feedToken=&apiKey=`), which SmartAPI provides for browser clients that cannot set headers. Rejected: it puts two live credentials into the string that every connection log line names. The header form means **nothing credential-bearing is in the URL at all**, which is strictly stronger than masking it — the same reasoning ADR-037 applied to Upstox's authorize step.

**Store the feed token in the existing `public_token` field** to avoid touching the engine. Rejected: it is Kite's vocabulary carrying another broker's credential, which is a lie in the schema to avoid a four-word change, and the next broker with a second credential would inherit the confusion.

**Subscribe in Quote or Snap Quote mode so the tick can carry volume.** Rejected for the third time and re-derived for the third broker: Quote is 123 bytes per tick against LTP's 51 and Snap Quote 379, for fields no consumer reads. The last-traded quantity those modes carry is *one trade's size*, not the day's cumulative volume, so it would populate `volume` with a number that means something else.

**Declare `SESSION_REFRESH` using SmartAPI's `generateTokens` endpoint.** Deferred, not rejected. The endpoint exists and consumes a refresh token; the publisher-login redirect is documented as returning `auth_token` and `feed_token` only. Declaring a refresh whose input this platform may not hold would make the engine attempt a renewal that cannot succeed instead of asking the user to reconnect — a worse outcome than the absence. Whether the redirect carries a refresh token is an explicit question for live validation.

**Declare Angel One's order and trading capabilities.** Deferred. D4.9 is a market-data sprint, SmartAPI's order surface is unvalidated against a live account here, and its order updates arrive on a *different* socket that would be a second channel. The capability model exists so a partial broker is declared partial rather than integrated with stub methods that lie.

**Fetch SmartAPI's master scrip file.** Rejected as unnecessary rather than out of scope, for the third time: a synced holding or position already carries the token beside the symbol and the exchange, which *is* the mapping table in both directions.

Consequences

• A user with a connected Angel One account and a synced portfolio gets a streaming feed for the instruments they hold, promoted over the Yahoo baseline only after a valid canonical `MarketTick` has arrived on the current link, and demoted the instant that link drops — the unchanged D4.5 machinery, reached by a third broker sharing no wire format with either predecessor.

• **The adapter count went from two to three; the number of market-data architectures stayed at one.** That is the sprint's actual claim, and it is asserted rather than asserted-by-eye: `test_three_brokers_speak_three_protocols_and_produce_identical_canonical_ticks` drives all three brokers' real bytes through their real codecs and compares the canonical ticks field for field, and `test_four_users_on_four_providers_stay_on_their_own` runs Angel One, Zerodha, Upstox and the baseline at once.

• **Angel One is a partial broker by declaration.** Market data, portfolio and funds; no orders, no order stream, no session refresh. A user connecting it gets a live feed and a synced portfolio and cannot trade through it, and the UI can say so because the capability set is what it reads.

• **An Angel-One-derived `MarketTick` carries no volume.** Third broker, same limitation, reached independently each time — which is itself a finding: the canonical `volume` field is unpopulated by every streaming broker this platform has, because every one of them puts cumulative volume behind a bandwidth-heavy mode. If volume becomes a product requirement, it is a mode decision at three adapters, not a contract change.

• **Four mutations initially stayed green during falsification and each found a real test gap** — a planted `_is_angelone` branch that the vocabulary sweep could not see (string literals are stripped by design, and `\bangel` does not match after an underscore), a codec exception made fatal to the stream (this adapter *declines* damaged frames rather than raising, so the existing resilience test could not tell the two apart), and both keep-alive mutations (the timer test called the helper directly and proved nothing about the transport using it). All four are closed and red. The sweep now also bans broker-name *comparisons* on literal-preserving source, which is the one shape a vocabulary sweep structurally cannot catch.

• **LIVE VALIDATION WAS NOT PERFORMED.** An Angel One feed needs a per-user session obtainable only through an interactive SmartAPI browser login, and no connected Angel One session exists in this environment. Every claim rests on deterministic validation against fixtures built from SmartAPI's published byte layout, plus 20 source mutations observed red. The outstanding smoke test includes two items unique to this broker: **holding the connection past 60 seconds**, which is the only way to prove the keep-alive is accepted, and **confirming whether the publisher-login redirect carries a `refresh_token`**, which decides `SESSION_REFRESH`. A live smoke test now remains outstanding for **all three** streaming brokers, and none is production-verified until it is run.

• The frontend is untouched and shows generic source status. No SmartAPI detail, no exchange segment, no client code on any market-data surface.

Authoritative documents

MARKET_DATA_ARCHITECTURE.md, BROKER_INTEGRATION.md, SECURITY.md

Review Date

When the first live session for any of the three streaming brokers is available, to close a live-validation gap that now spans all of them; and at the fourth streaming broker, or at the first broker whose keep-alive is not a text frame, which is where the heartbeat contract gets its own independent test.

---

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

# ADR-039

Title

Fyers API v3 as the Fourth Concrete Stream Adapter, and the Per-Connection Codec Scope It Required (Sprint D4.10)

Date

2026-08-24

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-038 reported that the market side had generalised for a second consecutive broker and that the one thing which had not generalised was in the transport again. D4.10 asks the fourth time, and gets a different kind of answer: Fyers is the first broker that disagrees with the **framework** rather than only with its predecessors.

Three brokers had made two assumptions look like facts, because all three happened to satisfy both:

  1. **A broker authenticates in the handshake.** Kite by query string, Upstox by bearer header, Angel One by four headers. `BrokerStreamChannel.subscribe_frames(instruments)` therefore takes instruments and nothing else — a session was never needed, because by the time frames are sent the socket is already authenticated.

  2. **A frame can be decoded on its own.** All three put an instrument's identity and its price in the same frame, so a codec is a pure function and a channel object can be a registry singleton shared by every user of the broker.

Fyers' HSM feed satisfies neither. It authenticates with a **binary frame on the data channel** — the handshake carries nothing at all, so the first thing the feed sends is a per-session credential. And it publishes one **snapshot** per instrument (the topic name, the price scale, the full field list) followed by **updates** that carry a server-minted numeric topic id and the changed values and nothing else. A lite update is seven bytes; six of them are not the price.

The protocol was transcribed from Fyers' own reference client (`fyers-apiv3` 3.1.16, `fyers_apiv3/FyersWebsocket/data_ws.py` and the segment/field tables in its bundled `map.json`) rather than from a published byte table — the first adapter here sourced that way, and worth naming, because the reference client is evidence about what a *working* client sends while a byte table is a description of what it should.

Decision

**The market side generalised again, for the third consecutive broker, and that is reported as the finding it is.** Nothing changed in the Market Engine, the Market Gateway, the Source Manager, `StreamingTickProvider`, the provider registry, the canonical `MarketTick`, the readiness gate, the failover path, `InstrumentMap`, the Portfolio Engine, the Trading Engine or the frontend.

**One generic change, and it is one method: `BrokerStreamChannel.open(session, credentials)`.** It returns the channel's view of the connection about to be opened. The transport calls it once per connection, uses what it returns for that connection's `subscribe_frames` and `decode`, and drops it when the socket ends. **The default returns `self`**, so a broker that has never heard of connection scope *is* a stateless broker — which is what it always was — and Zerodha, Upstox and Angel One are byte-for-byte unaffected.

Three properties were required and all three hold:

• **It names no broker.** `stream.py` asks a channel for a value and holds it for a socket's lifetime; it cannot tell what the value is for.

• **It is free for a broker that needs none.** The identity default is asserted rather than assumed, by a test that fails if it ever stops being the identity — otherwise every stateless adapter would silently start decoding against something other than itself.

• **It stops at the broker package boundary.** Nothing above the transport learns that a codec has a scope.

**One scope, because it is one problem.** The credential and the topic table are the same requirement wearing two hats: both are facts about *this socket and this user*, and both are invalidated by a reconnect. Solving them separately — a session argument on `subscribe_frames`, a state object threaded through `decode` — would have been two changes to signatures every adapter and every test double implements, so an unmigrated channel would fail on a live socket rather than at import. That is the trade `AdapterStreamChannel` refused in D4.7, refused again here.

**Why the state could not live on the channel.** A channel object is a registry singleton. The server mints topic ids per connection, so a table held there would be shared across every user of the broker *and* carried across reconnects. The failure is not an exception: one account's reconnect renumbers another account's instruments, and a price is filed under the wrong company's name — marked against a real position, plausible on a chart, and silent everywhere.

**Two protocol decisions where copying a predecessor produces code that runs and is wrong.**

  1. **The price scale is carried on the wire, per instrument.** `raw / (10**precision * multiplier)`, both read from the snapshot. Kite derives its scale from the low byte of its token, Angel One keys a table on its segment field, Upstox needs none because it sends a `double` — all three are constants of the broker. The trap is sharpest because a hardcoded ÷100 is **correct for NSE cash** (`precision=2, multiplier=1`) and wrong for currency (`precision=4`): it does not fail on the first tick anybody tests with, it fails on a real position.

  2. **The exchange is the exchange, not the segment.** SmartAPI's segments *are* its exchange names, so `NFO` and `CDS` are what its rows carry and what its ticks must say. Fyers is the other design: a symbol is `EXCHANGE:NAME` and the exchange half is only ever NSE, BSE or MCX — a futures contract is `NSE:NIFTY25AUGFUT`, a currency contract is `NSE:USDINR25AUGFUT`. Copying SmartAPI's table would make a tick report an exchange the account's own holding row never uses.

**Depth topics are refused rather than priced.** Field zero of a scrip or index record is the last traded price; field zero of a *depth* record is the best bid. This adapter never subscribes depth, but a decoder that read field zero of whatever arrived would publish a bid as the traded price — a real, plausible, tradeable-looking number that nothing downstream can detect as wrong.

**Every record in a frame is walked, never sampled.** A data frame carries a count and then that many records of mixed kinds. Skipping one by guessing its length desynchronises every record after it, and the symptom is not an exception but prices decoded out of the middle of other records. Reads are bounds-checked, so a truncated frame costs only the records after the damage; the reference client slices bare and decodes past the end.

Alternatives considered

**Widen `subscribe_frames` and `decode` instead of adding a scope.** Rejected. It changes what every existing channel and every test double implements, so a broker that had not been updated fails at the first frame of a live connection rather than at import — the compatibility break is discovered on a socket. The identity default costs nothing and cannot break anybody.

**Let the Fyers adapter hold the topic table keyed by user id.** Rejected, and it is the tempting one because it needs no framework change at all. It puts per-connection lifetime management inside an adapter that does not own the connection: nothing tells the adapter a socket died, so the table would be evicted by guesswork, and a reconnect would decode against the previous connection's numbering exactly as often as the guess was wrong. It also re-creates, per broker, the state-lifetime bug the transport already solves once.

**Subscribe in full mode (70) so every frame carries more.** Rejected, and it does not even solve the problem: full-mode updates are deltas too, keyed by the same topic id. It would put the whole 21-field record on the wire on every price change for fields no consumer reads, and leave the connection scope just as necessary.

**Implement HSM's acknowledgement protocol.** Deferred, and reported rather than quietly skipped. The credential response carries an "acknowledge every N frames" count and the reference client honours it with a ReqType-3 frame. A codec here returns a decoded event and has no way to put a frame back on the wire, so honouring it needs a *second* generic extension — on a protocol detail never observed non-zero, without a live session to observe it with. Extending a contract on a guess is what this architecture's review history is mostly a record of avoiding. The failure if the server does enforce it is bounded: the feed goes quiet with the socket open, `StreamingTickProvider`'s tick-freshness backstop expires the prices within two minutes, and the account falls back to the delayed baseline. The adapter logs a named warning when the count arrives non-zero, so the cause is not a mystery. **This is the first item on the live-validation list.**

**Use Fyers' `data/symbol-token` endpoint and the SDK's bundled segment map.** Rejected as unnecessary rather than out of scope, for the fourth time — but it is the closest call of the four, because Fyers' *own* client does exactly this. It does not need to: a `fyToken` is already on every synced holding and position row, and the HSM topic is `fyToken[:4]` → segment and `fyToken[10:]` → exchange token, derived locally. That is the difference between D4.10 being an adapter sprint and a data-pipeline sprint.

**Declare `SESSION_REFRESH` using Fyers' refresh token.** Rejected, not deferred. Fyers issues one, but redeeming it requires the user's trading **PIN** — a class of secret SECURITY.md forbids this platform from holding, and the same reason ADR-038 rejected SmartAPI's `loginByPassword`.

**Strip BSE single-letter group suffixes (`-A`, `-B`, `-X`) along with the cash series.** Rejected. A one-letter suffix is indistinguishable from a real part of a name, and stripping one wrongly renames an instrument permanently. `EQ`, the documented NSE cash series and `INDEX` are stripped; the rest is a recorded limitation rather than a guess.

**"Fix" the reference client's inconsistent frame-length fields.** Rejected. The auth frame declares its true payload length, the mode frame declares **0**, and the subscribe frame declares a number that includes the lengths of strings not in the frame. A server that read the field could not accept all three, so it does not — and the values a working client sends are evidence while the values we reason our way to are a hypothesis. Getting this wrong is not a crash; it is a socket that connects, authenticates and never delivers a price.

Consequences

• A user with a connected Fyers account and a synced portfolio gets a streaming feed for the instruments they hold, promoted over the Yahoo baseline only after a valid canonical `MarketTick` has arrived on the current link, and demoted the instant that link drops — the unchanged D4.5 machinery, reached by a fourth broker sharing no wire format with any predecessor.

• **The adapter count went from three to four; the number of market-data architectures stayed at one.** Asserted rather than asserted-by-eye: `test_four_brokers_speak_four_protocols_and_produce_identical_canonical_ticks` drives all four brokers' real bytes through their real codecs and compares the canonical ticks field for field, and `test_five_users_on_five_providers_stay_on_their_own` runs Fyers, Angel One, Zerodha, Upstox and the baseline at once.

• **The streaming contract can now express a stateful codec, and three brokers do not know it changed.** That is the shape every generic extension in this sprint sequence has taken — D4.7's channels, D4.9's keep-alive, D4.10's connection scope — and it is the reason the transport still names no broker after four of them.

• **Fyers is a partial broker by declaration.** Market data, portfolio and funds; no orders, no order stream, no session refresh. A user connecting it gets a live feed and a synced portfolio and cannot trade through it, and the UI can say so because the capability set is what it reads.

• **A Fyers-derived `MarketTick` carries no volume.** Fourth broker, same limitation — and this one is the sharpest case, because Fyers publishes a *genuine* cumulative day volume and even sends it in the snapshot. Carrying it once and freezing it for the session would be worse than absent. If volume becomes a product requirement, it is a mode decision at four adapters, not a contract change.

• **One protocol requirement is knowingly unimplemented** (the acknowledgement count above), with its blast radius bounded by an existing backstop and its occurrence made visible by a log line. It is recorded here rather than discovered later.

• **All 22 falsification mutations were red on the first pass**, which is itself worth reporting: the previous three sprints each had mutations that started green and exposed a real test gap. The difference is that D4.10's tests were written against the two failure modes the connection scope exists to prevent — a shared topic table and a reused one — rather than against the happy path. One mutation (`if self.broker == "fyers"` planted in the transport) is caught only by the literal-preserving comparison ban and **not** by the vocabulary sweep, exactly as ADR-038 predicted: `_strip_source` removes string literals by design, so a broker-name comparison is invisible to it. The layered sweep introduced in D4.9 is what makes that mutation red, and it earned its place here.

• **LIVE VALIDATION WAS NOT PERFORMED.** A Fyers feed needs a per-user session obtainable only through an interactive browser login, and no connected Fyers session exists in this environment. Every claim rests on deterministic validation against fixtures built from the reference client's own framing, plus 22 source mutations observed red. The outstanding smoke test includes two items unique to this broker: **whether the acknowledgement count is ever non-zero**, and **whether a lite subscription still receives the snapshot** that every later record depends on. A live smoke test now remains outstanding for **all four** streaming brokers, and none is production-verified until it is run.

• The frontend is untouched and shows generic source status. No Fyers detail, no HSM topic, no segment, no fyToken on any market-data surface.

---

# ADR-040

Title

Dhan (DhanHQ v2) as the Fifth Concrete Stream Adapter, and the First That Required No Framework Change At All (Sprint D4.11)

Date

2026-08-25

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-039 closed with a Review Date naming its own test: whether `BrokerStreamChannel.open()` had generalised or had merely served the broker that forced it. D4.11 is that test, and it returns the answer this sequence has been working towards for four sprints: **Dhan needed nothing.** No transport change, no contract change, no new capability, no new event kind, no widened signature. One adapter module and one registry line.

That is the first time in five brokers. D4.7 needed channels, D4.9 needed a keep-alive frame, D4.10 needed a connection scope — each a real gap a real protocol exposed. Dhan exposed none, and it is worth being precise about *why*, because "the fifth broker fit" is only evidence if the fifth broker was actually different from the four. It was, on every axis the framework abstracts:

  * **endpoint and auth**: query string (Kite's style), where Upstox uses a bearer header, Angel One four headers, and Fyers a credential frame on the data channel;
  * **framing**: little-endian binary responses to **JSON text** subscribe frames — the only broker here that mixes the two directions;
  * **identity**: `(exchange segment name, security id)`, where Kite has an integer token, Upstox a compound ISIN key, Angel One a numeric segment pair, Fyers an HSM topic;
  * **price**: an unscaled `float32` in rupees — **no divisor at all**, where the other four have four different scaling rules;
  * **volume**: a genuine cumulative day volume, which none of the other four's chosen modes carry;
  * **expiry**: a duration (24 hours from login), where the other three Indian brokers die at a fixed calendar hour.

The protocol was read from **two independent sources set against each other**: DhanHQ's published v2 documentation (Live Market Feed, Annexure, Authentication, Portfolio, Funds) and Dhan's own reference client `DhanHQ-py` (`src/dhanhq/marketfeed.py`), whose `struct` format strings are the authority on byte layout because they are what a working client actually reads. The two disagree in two places, and both disagreements changed a decision.

Decision

**Quote mode (RequestCode 17), not Ticker and not Full.** The choice is decided entirely by what the canonical `MarketTick` can hold. Ticker (16 bytes) has no volume field at all, so every tick would reach the Market Engine permanently half-empty. Full (162 bytes) adds open interest and five depth levels the canonical tick has nowhere to put, at three times the bytes. Quote (50 bytes) is the **narrowest mode that leaves nothing canonical unfilled**. This is the first adapter in the sequence where the richer mode was chosen rather than rejected, and the reason is that Dhan's middle mode carries a field the canonical contract already has — where Upstox's and Fyers' richer modes carried fields it does not.

**The price is used exactly as it arrives.** ADR-038 and ADR-039 both record a scaling rule derived independently per broker; Dhan's derivation returns *no rule*. A `float32` of the rupee price is on the wire. This is called out as the single line in the adapter most likely to be "corrected" by analogy with a predecessor, and doing so would publish every price at one hundredth of its true value with nothing raised anywhere. It is pinned by a mutation.

**Volume is the cumulative day volume at offset 22 and nothing else.** One Quote packet carries four volume-shaped fields — `LTQ` (this trade's size, offset 12), `volume` (the day's cumulative traded quantity, offset 22), `total_sell_quantity` (26) and `total_buy_quantity` (30). Only the second is what `MarketTick.volume` means. Every one is a distinct value in the test fixtures, so reading the wrong field is a wrong number rather than a coincidence.

**Prev Close is never priced, and the response code is the only thing that makes that possible.** A Prev Close packet (code 6) is **byte-for-byte the same shape** as a Ticker packet (code 2): 16 bytes, `<BHBIfI`, a `float32` at offset 8. Dhan sends one unsolicited for **every instrument the moment a subscription lands**. A codec that priced "any frame with a float at offset 8" would therefore publish **yesterday's close as today's price, once per holding, immediately after every connect and every reconnect** — marking a whole portfolio at stale prices with nothing raised. The priceable packets are consequently a table keyed on the response code rather than a size check, and an unlisted code is never priced.

**Identity is `"<SEGMENT>|<security id>"`, by segment NAME.** A Dhan security id is unique only within its segment — NSE 1333 and BSE 1333 are different companies — so the pair is the identity, the same principle Angel One's adapter applies. The *opposite* encoding is used, deliberately: Angel One's ids carry its numeric segment because SmartAPI's subscription is numeric, while Dhan's subscribe frame takes the segment name verbatim and `/positions` already returns `exchangeSegment: "NSE_EQ"`. Copying the predecessor's encoding would have meant translating on every subscribe.

**The `/holdings` "exchange" field is honoured where it names an exchange and refused where it does not — the sprint's sharpest finding, and the first of the two doc/SDK disagreements.** Dhan's published sample shows `"exchange": "ALL"`; the SDK's own response fixture shows `"exchange": "NSE"`. Both shapes are handled and neither is guessed at. A row naming a real exchange is a delivery holding and can only be cash, so the segment follows. A row saying `"ALL"` names no exchange at all, and **defaulting it to `NSE_EQ` was rejected**: it would be right most of the time and wrong *silently* — a BSE-only holding subscribed as whatever NSE numbers that id, with another company's price published under the user's stock's name. Such a row keeps its symbol, so it remains a real holding everywhere else in the platform; what it cannot do is be subscribed for ticks, and the count is WARNed rather than swallowed. Recorded as limitation LIM-D4.11-1.

**The partner consent flow, not the app flow the reference SDK uses — the second doc/SDK disagreement.** `auth.py`'s `/app/generate-consent` requires the user's own `dhanClientId` as a query parameter **before they log in**, which a multi-tenant platform by definition does not have: learning who the user is at Dhan is the entire purpose of the login. The `/partner/*` flow takes no client id and returns one on consume. The SDK's flow is a single-account developer convenience, not a disagreement about the protocol. The partner secret is sent as a **request header** and appears in no URL anywhere, which matters because the consent login URL is shown to the user.

**No application-level keep-alive is declared, and that is a finding rather than an omission.** Dhan's server sends a WebSocket **protocol** ping every 10 seconds and closes a connection unanswered for 40. A protocol ping is answered by the `websockets` library in both peers without either application seeing it, so the mechanism D4.9 added `heartbeat_frame` for is already satisfied. Angel One is the exact contrast — it does not count protocol pings at all. The `ping_interval` / `ping_timeout` defaults are left in place because they are the same values Dhan's own reference client runs with.

**Session death is classified in a frame, not at the handshake — the reverse of Angel One and Fyers.** The token rides in the connection's query string, so the socket opens first and the rejection arrives on it as response code 50. Codes 807/808/809 (expired token, invalid client id, authentication failed) stop the stream through the existing `AUTH_EXPIRED` path. `stream_connect_error` is *also* implemented for handshake 401/403 as a second line, because a gateway in front of the feed may reject a malformed token at the HTTP upgrade and an unclassified 401 is indistinguishable from a broker outage.

**Code 806 is treated as fatal and code 805 is not — the one judgement call in this adapter.** 806 ("Data APIs not subscribed") is an *entitlement* failure rather than an authentication one: the token is fine, the account is simply not licensed for the feed. The closed `StreamEventKind` set offers exactly two outcomes, "stop" and "retry forever", and retrying forever cannot make an unlicensed account licensed — so it stops, and the message carried through names the entitlement rather than claiming an expired token. 805 ("too many active connections") is a *concurrency* condition: Dhan drops the oldest socket when a sixth opens, so the next attempt may well succeed, and permanently killing a user's feed because they opened Dhan's own app would be the wrong trade. It is reported as an ERROR and left to the transport's backoff.

Alternatives considered

**Add a sixth `StreamEventKind` for "stop, but the session is not dead".** Rejected. It is the honest modelling of code 806, and it was seriously considered — but the kind set is closed by design, the closure is what makes the transport's dispatch and the capability gate exhaustive, and a sixth member ripples into `EVENT_CAPABILITY`, the dispatch, the gateway and every test double. One broker's one disconnect code does not justify that, and the approximation's cost (a user asked to reconnect a session that is technically valid) is bounded, visible in the message text, and recorded as a limitation. This is the fourth consecutive sprint in which the smallest correct extension turned out to be *no* extension, and the first in which the temptation was real.

**Fix the backoff so a server-side "stop doing this" cannot produce a tight reconnect loop.** Rejected as out of scope, and recorded as debt DB-5. The transport resets its backoff after any connection that *completed*, so a socket Dhan accepts and immediately closes with code 805 reconnects roughly every 1.5 seconds indefinitely — against a broker whose own documentation warns that further requests may get the user blocked. It is a real, broker-neutral defect that Dhan's 805 is simply the first protocol to expose. Fixing it means resetting the backoff only after a connection that lasted a minimum duration, which **is flap suppression**, which MARKET_DATA_ARCHITECTURE.md and the D4.10 scope note both assign to D5. Named rather than quietly fixed, and named rather than quietly ignored.

**Use Dhan's security-master CSV to resolve the `"ALL"` holdings.** Rejected as a data-pipeline sprint in disguise, for the fifth consecutive time. It would resolve LIM-D4.11-1 completely and correctly, and it would also introduce a downloaded, cached, refreshed instrument catalogue with its own staleness semantics — which is precisely the scope every previous adapter here has kept out, and which belongs with the sprint that decides the platform needs one.

**Subscribe in Ticker mode to minimise bandwidth.** Rejected: see the Quote decision above. Ticker's absent volume would have been the fifth consecutive adapter shipping a permanently half-empty canonical tick, and this is the first broker whose middle mode carries the field.

**Strip a series suffix from Dhan trading symbols**, as the Angel One and Fyers adapters do. Rejected as unproven. Both of Dhan's official samples and its SDK fixtures show bare names (`"TCS"`, `"HDFC"`), and inventing a strip rule for a suffix this broker does not appear to send would risk renaming an instrument permanently — a hyphen this platform cannot prove is a series code is part of the name. Flagged for live validation instead of guessed at.

Consequences

• **The framework absorbed a fifth broker with zero generic edits**, which is the strongest evidence yet that the D4.2 split (transport generic, codec broker-owned) plus D4.7's channels, D4.9's keep-alive and D4.10's connection scope is the right decomposition. `stream.py` is byte-for-byte unchanged by this sprint and still names no broker; so are `streaming.py`, `instruments.py`, `market_feed.py`, `ticks.py`, the Source Manager, the Market Gateway, `StreamingTickProvider`, the provider registry, the readiness gate, the failover path, the Portfolio Engine, the Trading Engine and the frontend.

• **The adapter count went from four to five; the number of market-data architectures stayed at one.**

• **A real test gap was found by mutation and closed.** Removing the user id from the provider registry key left every isolation test in this file green, because each existing test puts *one* user on each broker — so the key stayed unique per broker and the collision was invisible. It is only visible with **two users on one broker**, resolved **through the registry** rather than through the object the attach returned; the attach itself never looks a feed up by name, and every consumer does. `test_two_dhan_users_of_the_SAME_broker_never_share_a_feed` is the fix, and it is the fifth consecutive sprint in which a mutation found something review did not.

• **One mutation stayed green for a good reason, and it is reported rather than papered over.** Removing the evidence discard in `mark_link_up` changes nothing, because the same method also demotes a READY feed — and removing the demotion changes nothing either, because the discard covers it. Each line is individually redundant with the other; removing **both** is red. That is genuine defence in depth rather than a test gap, and manufacturing a test that pins one of the two lines would be asserting an implementation detail instead of the property.

• **LIVE VALIDATION WAS NOT PERFORMED.** A Dhan feed needs a per-user access token obtainable only through an interactive browser consent login, and no connected Dhan session exists in this environment. Every claim above rests on deterministic validation against fixtures packed with the reference client's own `struct` format strings, plus 27 source mutations of which 25 were observed red. **The outstanding smoke test**, with the items unique to this broker marked: partner consent → browser login → redirect carries `tokenId` → consume-consent returns `accessToken` **and `dhanClientId`, without which the feed cannot be opened at all** → socket connects with the token in the query string → **confirm no acknowledgement of the subscription arrives**, since this codec assumes there is none → **confirm a Prev Close packet arrives per instrument at subscribe time**, which is the premise of the response-code table → a real Quote packet decodes → **confirm the float32 price needs no scaling against a known live price** → `BrokerTick` → `InstrumentMap` → `MarketTick` → readiness → promotion over Yahoo → **hold the connection past 40 seconds to prove the library's pong satisfies Dhan's ping** → **confirm whether a real `/holdings` row returns `"ALL"` or a real exchange**, which decides how much of LIM-D4.11-1 actually bites → **confirm whether trading symbols carry a series suffix** → disconnect → Yahoo fallback → reconnect → re-readiness → 24-hour expiry classified in a code-50 frame. A live smoke test now remains outstanding for **all five** streaming brokers, and none is production-verified until it is run.

• The frontend is untouched and shows generic source status. No Dhan detail, no segment, no security id on any market-data surface.

Review Date

At the sixth streaming broker, or at the first live Dhan session — whichever comes first. The specific claim to re-test is the one this ADR is proudest of: that the framework needed nothing. A single data point is a data point; the question is whether the sixth broker also needs nothing, or whether Dhan simply happened to be an ordinary WebSocket broker with an unusual payload.

---

# ADR-041

Title

Flap Suppression: The Reconnect Backoff Resets After a Connection That Lasted, Not After One That Happened (Sprint D5.1, closes DB-5)

Date

2026-08-25

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

D4.11 named DB-5 and deliberately left it. The stream transport's run loop reset its reconnect ladder immediately after the transport coroutine returned:

    await runner(self)
    delay = RECONNECT_BASE_DELAY   # clean close → quick reconnect

The comment asserts a clean close. The code cannot observe one. A socket the broker accepted and closed one frame later reaches that assignment exactly as readily as a socket that streamed all session, so a broker-side "stop doing this" produced connect → accept → close → reset → reconnect ~1.5s later, indefinitely — a reconnect storm against a broker whose own documentation warns that continuing may get the user blocked.

Three facts made this worth a slice of its own rather than a two-line patch:

  * It is **broker-neutral**. One of the five current protocols has a disconnect code that means "too many connections", and that code is what first exposed the loop; nothing about the defect belongs to that broker. All five adapters ride the same loop, and any broker that closes a socket promptly — rate limiting, maintenance, a duplicate session, an unsupported subscription — reproduces it.
  * It is **invisible in the logs**. Every individual line of the storm reads as a routine reconnect. It survived four broker integrations for exactly that reason.
  * The obvious fix is **wrong**. Raising `RECONNECT_BASE_DELAY` suppresses the storm and simultaneously makes every genuine blip cost every healthy user a slower recovery — a constant tax to pay for a rare pathology.

Decision

**The ladder resets after a connection that *lasted*.** One condition, expressed as a small generic model in a new module, `services/brokers/reliability.py`.

**A new module rather than more lines in `stream.py`.** `stream.py` is mechanism — open the socket, send the adapter's frames, hand frames back to the codec. *How fast to retry* and *what evidence a connection owes* are policy, and D5 adds several more of them (probation, stale-feed demotion, failure classification). Keeping policy beside the run loop is how a run loop acquires an embedded policy engine, which is what D3 spent a sprint removing from this same file. `reliability.py` imports nothing from `services.` at all — pinned by a test — so it cannot acquire broker knowledge by accident.

**Three outcomes, one ladder.** `ConnectionStability` classifies each attempt as `STABLE` (link up for at least the stability window → reset the ladder, clear the flap streak), `SHORT_LIVED` (came up, died young → keep the ladder, count one flap) or `NEVER_ESTABLISHED` (never reached link-up → keep the ladder). The ladder doubles on every attempt regardless, capped and jittered exactly as before. A feed that runs for an hour and drops still reconnects in ~1–2 seconds; a feed that keeps dying young climbs 2 → 4 → 8 → 16 → 32 → 60 and stays there.

**`NEVER_ESTABLISHED` is not counted as a flap**, though it escalates identically. The distinction costs nothing operationally and matters for the next slice: "the broker will not talk to us" and "the broker keeps hanging up on us" are different diagnoses, and `consecutive_short_connections` is the evidence D5's failure-classification slice will read.

**The stability window is 30 seconds, and the number is not invented here.** MARKET_DATA_ARCHITECTURE.md already fixes the platform's definition of a provider that has proved itself — "a provider that just recovered must deliver clean data for a probation window (e.g. 30 seconds of valid messages) before it is eligible to become primary again — this prevents flapping". A connection that dies before that window has, by the platform's own published definition, never got far enough to be trusted, so it has no claim on a reset backoff either. One constant serves both layers; D5's probation slice consumes it rather than declaring a second that drifts.

**The model is driven from `_notify_link`, not from the run loop.** That method is already change-gated — it reports one transition per real transition — so it is the one place that knows a connection began and ended exactly once. A transport added to `PROTOCOL_RUNNERS` later therefore inherits flap suppression by reporting link state, which it must do anyway, instead of by remembering to opt in.

**"Established" keeps the transport's existing meaning**: the socket is open *and* the subscribe frames are away. A broker that accepts a connection and closes it before anything was asked of it is `NEVER_ESTABLISHED`, which is the truthful reading — nothing was established to flap.

**One `ConnectionStability` per `BrokerStream`**, i.e. per (user, broker, channel). Per-connection rather than per-broker: two users on the same broker hold two ladders, so one user's rejected session cannot slow another user's reconnect, and a broker's order socket flapping cannot slow its market feed — which would silently recouple the independence D4.7 established.

Alternatives Considered

**Raise `RECONNECT_BASE_DELAY`.** Rejected, and pinned against by `test_a_stable_connection_resets_the_ladder_and_a_short_one_does_not`. It suppresses the storm by taxing the healthy path, which is what the D5 brief rules out.

**Give up after N consecutive short connections.** Rejected as the wrong layer. "This will never work, stop" is a *classification* judgement — entitlement failure, permanent misconfiguration — and this module sees timestamps, not frames; it has no way to tell a permanently unlicensed account from a broker having a bad ten minutes. Escalating to the 60-second ceiling is the honest response until the failure-classification slice can express the difference. The evidence it will need is exposed as `consecutive_short_connections` rather than left in log lines.

**Have the transport demote the market-data provider when it detects flapping.** Rejected: failover policy would then live in two places. Whether a flapping feed may be the *primary* quote source is the provider layer's question, answered by `StreamingTickProvider`'s readiness gate, and D5's probation slice sharpens it there.

**Track stability per broker rather than per connection.** Rejected — it fails Rule 6 of the D5 brief. Pinned by `test_one_users_flapping_broker_does_not_pace_another_users_reconnects` and by a mutation that made the ladder a per-broker singleton and turned that test red.

**Leave `RECONNECT_BASE_DELAY`, `RECONNECT_MAX_DELAY` and `reconnect_pause` in `stream.py`.** Rejected, but only just: moving names is churn. They are reconnect *policy*, which is exactly what the new module is for, and leaving them behind would have meant `reliability.py` importing from the transport it is supposed to be independent of. They are re-exported from `stream.py` unchanged, so every existing caller and every existing test imports them under the name it always did.

Consequences

• **DB-5 is closed.** A broker that accepts and immediately closes now backs off to the 60-second ceiling instead of reconnecting every ~1.5 seconds forever. `test_a_broker_that_accepts_and_immediately_closes_does_not_reconnect_forever_at_the_base_delay` drives the real run loop and is red against the pre-D5.1 line.

• **The healthy path is unchanged.** A long-lived connection that drops reconnects within the base delay, as it did in D4. This is asserted rather than assumed.

• **No broker was touched.** All five adapters, `streaming.py`, `instruments.py`, `market_feed.py`, `ticks.py`, the Source Manager, the Market Gateway, `StreamingTickProvider`, the readiness gate, the failover path and the frontend are byte-for-byte unchanged. The whole sprint is one new module, one new test module, and 82 changed lines in `stream.py` — most of them documentation.

• **Ten mutations, ten red.** Reinstating DB-5's assignment; suppressing flapping by never resetting; inverting the threshold comparison; freezing the ladder; removing the ceiling; counting a never-established attempt as a flap; deleting the flap warning; making the ladder a per-broker singleton; unwiring the transport hook; and handing broker identity to the model. Every one was observed red against the targeted suites and restored.

• **The broker-agnostic sweep found a breach in this sprint's own prose, and it was fixed rather than exempted.** `test_the_reliability_module_names_no_broker` runs against the source *with comments and strings left in*, so the docstring explaining DB-5 could not name the protocol that exposed it. That is a stricter bar than the D3/D4 sweeps, which strip comments — deliberately so, because a reliability module is the one place where "we handled that broker's case" would start as a comment.

• **LIVE VALIDATION WAS NOT PERFORMED**, and cannot be in this environment: reproducing a flap requires a broker that actually hangs up, which requires a live authenticated session. Every claim rests on the deterministic run-loop tests and the mutation results. **The outstanding smoke test**: connect a real session, hold it past 30 seconds, drop it, and confirm one reconnect at ~1–2s; then induce repeated immediate closes (a duplicate session on a broker that caps concurrent connections is the cheapest trigger) and confirm the interval climbs to the ceiling and that the flap warning names a rising streak.

• **Deliberately still open after D5.1**: probation windows, latency scoring, stale-feed demotion, richer failure classification (including a broker-neutral representation of entitlement failure), broker health's process-local scope (DB-1), and instrument sharding. Each is its own D5 slice; none was started.

Review Date

At D5.2, when probation is designed on top of this — the specific claim to re-test is that `STABLE_CONNECTION_SECONDS` is genuinely the same concept at both layers, rather than two ideas that happened to want the same number.

---

# End of Decisions Documentation