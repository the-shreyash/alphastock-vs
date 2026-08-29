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

# ADR-042

Title

Provider Probation: READY Proves a Feed Is Valid, STABLE Proves It May Outrank One That Works (Sprint D5.2, closes LIM-D5.1-3)

Date

2026-08-27

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

D4.5 built the readiness gate and it does its job exactly as specified: a pushed feed becomes the primary quote source by delivering a valid canonical tick on its current link, and stops being it the instant that stops being true. The defect D5.2 closes is not in that gate. It is that the platform was reading its answer as the answer to a stronger question than it asks.

    READY   this feed can produce a valid canonical price
    STABLE  this feed has kept producing them long enough to be trusted
            with the primary position

The two come apart on a flapping link, and every individual step of the failure is correct:

    connect → one valid tick → READY → preferred
            → socket dies    → demoted, the baseline resumes
            → reconnect      → one valid tick → READY → preferred
            → socket dies    → …

The composite is a user whose tier indicator alternates between live and delayed every few seconds, with each promotion resting on a single packet from a connection that has repeatedly demonstrated it cannot survive. D5.1 fixed the transport half — the reconnect no longer returns in ~1.5s forever — and named the provider half as LIM-D5.1-3 rather than smuggling failover policy into a run loop. This is that half.

MARKET_DATA_ARCHITECTURE.md had already specified the answer and nothing had implemented it: "a provider that just recovered must deliver clean data for a probation window (e.g. 30 seconds of valid messages) before it is eligible to become primary again — this prevents flapping."

Decision

**A second axis on the existing state machine, not a second state machine.** A feed is `READY / PROBATION` and then `READY / STABLE`. `FeedStability` has two members; readiness is untouched; there is no second registry, no second readiness system, and no parallel provider lifecycle. Stability is derived from two timestamps the feed already had to record.

**The promotion rule is evidence, not elapsed time.** A feed leaves probation when valid canonical data arrives at least `PROBATION_WINDOW_SECONDS` after the tick that earned readiness on the current link. "Thirty seconds of valid messages", read literally. The alternative reading — "thirty seconds have passed since the first tick" — promotes a feed that ticked once and went silent, which is the same class of mistake as promoting on `connected`, one layer along, and would promote it *over a baseline that is at that moment the only source actually producing prices*.

**Probation ranks; it never filters.** `is_on_probation` is a term in the Source Manager's selection sort, alongside health. A probationary feed stays eligible, stays in the failover chain, and becomes the head of that chain the moment no steadier candidate remains. This is the line between "protect a working provider from unnecessary replacement" and "refuse to serve data the platform has": the second would trade a cosmetic tier flap for an outage.

**Health is asked first, probation second.** A DEGRADED provider has produced evidence of failure; a probationary one has merely not yet produced evidence of success. Ordering health first preserves this document's published rule that DEGRADED demotes a provider below a healthy lower tier, and makes probation what it is meant to be — the tie-break inside a health rank.

**The window is 30 seconds and is not a new number.** `PROBATION_WINDOW_SECONDS` (provider layer) and D5.1's `STABLE_CONNECTION_SECONDS` (transport) are two names for one published policy. They are two names rather than one because the layers may not import each other — `reliability.py` is pinned to the standard library alone, and the Market Engine may not import the broker layer at all — so drift is prevented by `test_the_two_layers_share_one_stability_window` instead of by an import. ADR-041 asked D5.2 to re-test whether the two are genuinely the same concept, and the honest answer is **the same window measured on different evidence**: the transport can only observe how long a socket lasted, this layer can observe whether data kept arriving on it. A link that stays open silently is STABLE to the transport and still on probation here. That is the correct relationship — each layer uses the strongest evidence it has — and it is documented rather than smoothed over.

**Scope is per provider instance**, and there is exactly one instance per (user, feed). Two users of the same broker therefore serve independent probations by construction rather than by a rule someone has to remember. No new scoping code was written for this; it is the D4.4 registry key doing its job.

**Nothing is scheduled.** No timer, no sweeper, no task to cancel when a link drops. The window is evaluated at resolution time from recorded values, so a feed nobody asks about costs nothing — the same discipline as the per-symbol coverage backstop.

**PRIMARY is still not stored.** Stability is derived on every read, exactly as D4.5 requires of promotion: which provider leads is the output of the resolution sort and never an input to it.

Alternatives Considered

**Apply probation only to a feed that is *recovering*.** This is the narrowest reading of the architecture sentence ("a provider that just recovered"), and it is tempting because it would have left all thirty-eight existing D4 promotion tests untouched. Rejected: a feed's *first* promotion rests on exactly as little evidence as its tenth, and the failure mode is identical — one packet from a connection nobody has seen survive. A rule that trusts a feed more the first time is a rule that is wrong in the case it was written for.

**Filter probationary providers out of the candidate list.** Rejected as the dangerous implementation. It is invisible while a baseline exists and becomes an outage the moment one does not — a user whose data is arriving perfectly well would be told no provider is available. Pinned against by `test_treating_probation_as_a_filter_would_leave_the_user_with_nothing`, which mutates the code into exactly that shape and observes the failure.

**Rank probation above health.** Rejected: it silently reverses this document's published DEGRADED rule, and does so invisibly, because both orders agree in every case where the baseline is healthy — which is every ordinary case. Pinned by `test_health_outranks_probation_and_not_the_other_way_round`, which was added *because* the mutation that swapped the two terms stayed green.

**A probation timer that promotes the feed when it fires.** Rejected twice over: it promotes silent feeds, and it introduces scheduled state that has to be cancelled correctly on every link-loss path — the kind of cleanup whose one missed branch promotes a dead feed.

**Store an `is_stable` flag set at promotion.** Rejected for the same reason PRIMARY is not stored (ADR-035): a stored flag is a lagging copy of something derivable, and the two disagree exactly when it matters.

**Give stability its own listener rather than reusing the readiness one.** Rejected: the gateway does the same thing for both — log the transition, republish the owner's status — so a second callback would be a second copy of one path with the standing risk that a later change reaches only one.

Consequences

• **LIM-D5.1-3 is closed.** A feed that flaps is never the preferred source. `test_a_flapping_feed_never_becomes_the_preferred_source` drives ten connect/tick/die cycles and the baseline serves throughout; the eleventh connection, which holds, is promoted on its own evidence with no penalty carried from the ten that did not.

• **A promotion now takes 30 seconds of live data instead of one packet.** This is the intended cost and it is worth stating plainly: a user connecting a broker mid-session sees the delayed tier for up to a window longer than they did before D5.2. They see *data* throughout — the baseline is never released early — and what they no longer see is a live indicator that is about to become a lie.

• **Thirty-eight existing D4 tests take a `no_probation_window` fixture.** They were written to assert that broker bytes reach a provider and that resolution follows readiness, and at the real window every one of them would have been asserting probation by proxy. The window is collapsed to zero for those tests and exercised at its published value in `tests/test_provider_probation.py`, including through the same real `attach_market_feed` seam. No assertion in those thirty-eight tests was weakened or deleted.

• **D4.5's readiness falsification needed a second mutation.** `test_removing_the_readiness_gate_would_promote_an_unproven_feed` went red on implementation, because with the readiness gate removed the feed was *still* held back — by probation. It now neutralises both gates, which is the only way it can still prove the D4.5 gate is load-bearing. A control that starts passing for a new reason is a control that has stopped testing what it names.

• **The reset on link loss turned out to be two independent controls, and the mutation found it.** `_discard_evidence` clears the window's timestamps, and `_advance` re-stamps `_ready_since` from the new link's first tick; each alone is sufficient, so removing either stays green. Both are removed together in the falsification test, and this is reported rather than tidied away — it is the same defence-in-depth pattern D4.11 found in `mark_link_up`, and pinning one of the two individually would be asserting an implementation detail instead of the property.

• **Fourteen source mutations, twelve red on the first pass.** The two green ones are the halves of the reset above; the pair is red. Two further mutations — probation ranked above health, and probation shared across provider instances — were green until a test was added or the mutation was made faithful, and both are red now.

• **No broker was touched.** All five adapters, `stream.py`, `streaming.py` (the broker one), `instruments.py`, `market_feed.py`, `ticks.py`, `reliability.py`, the registry, the Trading Engine, the Portfolio Engine and the frontend are unchanged. The sprint is three edited modules in the Market Engine, one new test module, and a fixture.

• **Consumer surfaces are unchanged.** `provider.status` carries tier, state and reason as before. Probation is visible on `describe()`, the admin/diagnostics surface where provider names already live, because an operator looking at a live feed that is not primary needs to be able to tell probation from a bug.

• **LIVE VALIDATION WAS NOT PERFORMED.** No broker session exists in this environment. Every claim above rests on deterministic tests with an injected clock at the published window, a real-elapsed-time pass through the registration seam for all five brokers plus a fictional one, and the mutations listed above. **The outstanding smoke test**: hold a real broker feed past 30 seconds of live ticks and confirm the tier flips to streaming exactly once; drop the socket at 15 seconds and confirm the reconnected feed serves a fresh window rather than being promoted on its first tick; run two accounts on one broker and confirm one flapping session does not move the other's tier.

Review Date

At D5.3 (stale-feed demotion), which is the slice that will want to *demote* a stable feed for going quiet — the specific claim to re-test is whether "stable" should decay, or whether coverage expiry beneath it is enough.

---

# ADR-043

Title

Stale-Feed Demotion: Stability Decays, and It Decays Through the Coverage Window That Already Existed (Sprint D5.3, answers ADR-042's review question)

Date

2026-08-27

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

D5.2 shipped probation and wrote its own review question down: *"Stability does not decay. A quiet stable feed is bounded only by the 120s coverage backstop. Should STABLE decay, or is coverage expiry beneath it sufficient?"*

D5.3 was required to answer that from the code rather than to assume it. The audit did, and the answer was that the premise of the question was wrong in a way nobody had noticed: **the 120-second coverage backstop was not beneath it.** Coverage was only ever consulted on one of the two branches that resolve a quote.

    is_eligible_for(context carrying a symbol)  →  covers(symbol)  →  120s window ✓
    is_eligible_for(context carrying no symbol) →  return True     →  no window at all ✗

The second branch is not a corner case. It is the branch `SourceManager.active_tier()`, `SourceManager.status()` and `MarketGateway.source_tier()` resolve through — the user's freshness indicator and the AI's freshness context. Reproduced against the real resolver, a feed that had served its probation window and then gone silent for 10,000 seconds reported:

    covers("RELIANCE")        False        ← the coverage backstop worked
    resolve(QUOTES, symbol)   yahoo        ← real quotes correctly fell back
    resolve(QUOTES, no symbol) feed:u1     ← and this one did not
    active_tier()             STREAMING
    status()["tier"]          "streaming"
    stability                 STABLE
    is_on_probation           False

So the platform served the user delayed baseline prices while telling them, and telling the AI, that their data was live. That is a claim about market data the platform could not support — CLAUDE.md's data rules, not a ranking blemish.

The second finding follows from the same cause. `stability` compared two *past* instants — `_last_evidence_at - _ready_since >= window` — with no upper bound on how old the newer one was. A dead feed therefore stayed STABLE forever, and since `is_on_probation` is the Source Manager's ranking term, a feed with zero fresh evidence outranked a feed that was genuinely delivering. Measured on the same fixture, the chain was `[stale feed, yahoo, live feed]`.

Audit answers to the brief's five questions, each from the code:

  **A. Can a once-STABLE provider stay ranked ahead of a healthier one after its data goes stale?** Yes — demonstrated above, on the symbol-less branch.

  **B. Does the 120-second coverage expiry already provide sufficient stale-feed demotion?** No, and this is the sprint's central finding. It is sufficient *per instrument* and was never asked *per feed*.

  **C. Does STABLE need its own decay state?** No. It needs the coverage window it was already documented as sitting on top of, asked in both places. Stale coverage does remove a feed naturally — once the question is actually put.

  **D. Should `consecutive_short_connections` influence provider stability?** No. See Alternatives.

  **E. Does the chosen design unify transport health and provider evidence?** No. See Decision.

Decision

**Stability decays, through the existing coverage window, via one derived predicate.**

`StreamingTickProvider.has_fresh_evidence` — an accepted canonical tick arrived within `tick_max_age_seconds` — is read in exactly two new places: the stability rule, and the symbol-less branch of `is_eligible_for`. That is the whole implementation.

**One window, not two.** The predicate reuses `tick_max_age_seconds` rather than declaring a stale-feed constant. The platform already publishes exactly one answer to "how old may this feed's data be before falling back is strictly better", and the honest reading of the audit is that the bug was never a missing *policy* — it was one policy asked in only one of the two places that needed it. A second constant would be two answers to one question, free to drift. Pinned by `test_staleness_reuses_the_coverage_window_rather_than_defining_a_second_one`, which sets the window to 7 seconds and asserts `has_fresh_evidence` and `covers()` flip on the same tick of the clock.

**No new state, no new constant, no new timer, no new registry.** Decay is derived on read, like `stability` and like PRIMARY-is-not-a-state before it, for the reason D4.5 gave: a stored flag is a lagging copy of something derivable, and the two disagree exactly when it matters. A feed nobody asks about costs nothing; the demotion happens on the next resolution that asks. This also means demotion needs no timer, which Rule 13 of the brief forbids for promotion and which would be no better here.

**The evidence stays market data and only market data.** `has_fresh_evidence` reads `_last_evidence_at`, which is written in exactly one place — `on_raw`, on an accepted canonical tick. It never reads `_connected`, the readiness state, the subscription, or any reconnect counter. This is the direct answer to question E: transport liveness and provider evidence remain the two separate facts D4.5 made them, and the two tests that hold that line are `test_a_connected_socket_is_never_fresh_evidence` (connect, subscribe, link-up, link-up again — none moves the predicate) and `test_transport_flap_history_is_not_consulted_by_provider_stability` (a feed that flapped 100 times and is now delivering is stable; a feed that never flapped and is delivering nothing is not).

**Evidence resuming on the same link restores STABLE immediately** rather than re-serving the probation window. The link never dropped, so nothing was discarded and the window this feed proved is the window of the connection it is still on. Requiring it to be re-proved would demote a feed for trading an illiquid instrument rather than for being unreliable, and would require *storing* decay state to express. A link that actually dropped is the other case, and D5.2's `_discard_evidence` already owns it.

**Nothing in the Source Manager changed.** `is_on_probation` is already a generic property of the provider contract, already read as a ranking term, and it now decays because what it is derived from decays. Zero lines in `source_manager.py`, and no broker adapter was opened.

Alternatives Considered

**Leave it: coverage expiry beneath STABLE is sufficient.** This was the D5.2 hypothesis and the brief explicitly permitted it — "if existing coverage expiry already guarantees this property, do not invent another decay mechanism; pin that behavior with tests instead." Rejected on evidence, not on preference: the reproduction above shows the property does not hold on the symbol-less branch, and `active_tier()` is precisely where a user sees it.

**Give stale feeds a third `FeedStability` member (e.g. `STALE`).** Rejected. A third member has to rank *somewhere*, and the only safe place to rank a feed with no current evidence is with the other unproven ones — which is what PROBATION already means. The member would carry no ranking information the boolean does not, and every consumer of the enum would grow a branch for it.

**Use `consecutive_short_connections` to influence provider stability.** Rejected on two independent grounds, which is question D's answer. *Architecturally*: it lives in `services/brokers/reliability.py`, and the Market Engine may not import the broker layer; carrying it across would mean a new provider-contract field whose only content is transport history, which is exactly the transport/evidence unification Rule 8 forbids and which ADR-041 already declined from the other side ("whether a flapping feed may be the primary quote source is the provider layer's question"). *Substantively*: it is unnecessary. D5.2's per-link evidence reset already means a flapping feed re-serves probation from zero on every reconnect, so flap history is already reflected in this layer's ranking — expressed in the currency this layer can actually verify, delivered data rather than socket lifetimes. D5.1 handles the transport half with backoff. The smallest broker-neutral path to carry that information is therefore *no path*, and the sprint carried none.

**Demote on a timer when a feed goes quiet.** Rejected. It is a scheduled callback per feed to cancel on every link drop, for a fact that is free to compute at resolution time — and it would make the demotion's timing depend on scheduler pressure rather than on the data.

**Make the stale check `bool(covered_symbols)` instead of a scalar timestamp.** Rejected as equivalent but worse. Every accepted batch stamps its ticks and `_last_evidence_at` with the same instant, so the newest coverage entry is always exactly as old as the timestamp; the scalar is O(1) and asks the per-feed question the per-feed branch is actually asking.

Consequences

• **The tier a user sees is now honest.** A feed that goes quiet past the coverage window moves the user to `delayed` and back to `streaming` when data resumes, on both the per-symbol and the per-feed path. `test_the_tier_a_user_sees_stops_saying_streaming_when_the_feed_goes_quiet` is red against the pre-D5.3 line.

• **Demotion is never an outage.** `status()["state"]` stays `available` throughout; Yahoo is asserted present, connected and resolvable at five distinct points of the decay lifecycle; and staleness ranks rather than filters, so a stale feed still answers the link-level TICKS capability its socket genuinely serves.

• **Thirteen source mutations, thirteen red.** Removing the demotion; letting a stale provider stay first; reverting the symbol-less branch; making staleness permanent; wall-clock time; socket-as-evidence; stability surviving a reconnect; a genuinely global stale timestamp; removing per-user entitlement; a broker-specific stability branch; staleness as a filter; a truncated failover chain; and a second, looser stale window. Two *earlier* attempts came back green and were investigated rather than reported as gaps — both were malformed mutations, not defence in depth and not test gaps: one assigned a class attribute that `__init__` immediately shadowed with an instance attribute, and one multiplied the probation rank by a constant, which preserves sort order. Both were reformed and both are red. Reported here because a mutation that does not mutate is the easiest way to award yourself a passing falsification run.

• **No broker was touched.** All five adapters, `stream.py`, `reliability.py`, `market_feed.py`, `instruments.py`, `ticks.py`, `source_manager.py`, `gateway.py`, `base.py`, the registry and the frontend are unchanged. The sprint is one edited module — `providers/streaming.py`, three logic lines and their documentation — and one new test module.

• **`MarketTick` was not modified.** Rule 5 of the brief; the audit found no reason to, because the tick already carries the only thing the predicate needs, and the arrival instant is recorded by the provider on the monotonic clock rather than read off the payload.

• **Known limitation, LIM-D5.3-1: decay is lazy, so it is not announced.** Promotion out of probation fires the feed-state listener from `on_raw`; decay *into* probation happens on read, with no event, so a consumer holding a rendered tier is not proactively told until the next status publish. This is deliberate — the alternative is the per-feed timer rejected above — and it is the same lazy behaviour the per-symbol coverage backstop has had since D4.5. The gap is bounded by the platform's existing status republish cadence. If it proves visible in practice, the right fix is to evaluate staleness on the existing publish path, not to add a timer.

• **LIVE VALIDATION WAS NOT PERFORMED.** No interactive broker session exists in this environment. Every claim above rests on deterministic tests with an injected monotonic clock, a pass through the real `attach_market_feed` registration seam for all five brokers plus a fictional one, and the mutations listed. **The outstanding smoke test**: hold a real broker feed to stability, stop the instrument's ticks (an illiquid symbol outside its trading burst, or a subscription the broker stops servicing) while leaving the socket up, and confirm the tier flips to `delayed` after the coverage window with the socket still open — the whole point is that the link is *not* the thing that changed; then confirm it returns to `streaming` on the next tick without re-serving the probation window.

Review Date

At D5.4 (latency scoring), which will add a third ranking term beside health and probation. The specific claim to re-test is whether the coverage window is still the right decay threshold once per-provider latency is measured — a feed whose ticks are consistently 90 seconds late is fresh by this predicate and bad by that one, and the two terms must not end up disagreeing about the same feed.

---

# ADR-044

Title

Provider Latency: The Platform Can Measure Delivery Cadence Honestly and Cannot Measure Exchange Latency At All (Sprint D5.4, answers ADR-043's review question and LIM-D5.3-3)

Date

2026-08-27

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-043 set D5.4's review question: *"whether the coverage window is still the right decay threshold once per-provider latency is measured — a feed whose ticks are consistently 90 seconds late is fresh by this predicate and bad by that one, and the two terms must not end up disagreeing about the same feed."*

MARKET_DATA_ARCHITECTURE.md has asked for latency scoring since Phase 5 was written, in three places, and one of them carries its own precondition:

    §7 Latency Monitoring
      "Stamps every event with `ingested_at`; computes `latency_ms`
       **where the provider supplies an exchange timestamp**"
      "Maintains rolling p50/p95 latency per provider, fed to the
       Source Manager's scoring"

    Source Manager §5
      "score = f(connection_state, message_freshness, error_rate, p95_latency)"

The D5.4 audit's first job was to test that precondition rather than assume it. **It is not met, by any provider, at the boundary where selection happens.** That is this sprint's central finding, and everything below follows from it.

**The canonical tick carries no exchange timestamp, deliberately.** `services/market_engine/ticks.py` states the rule and the reason: "`ingested_at` rather than the broker's own timestamp is deliberate. `BrokerTick.timestamp` is a verbatim broker string precisely because brokers disagree on format and timezone and a wrong parse is worse than no parse … so the canonical tick carries the one timestamp this platform can state truthfully — when *we* received it." A `MarketTick` has five fields and none of them is an exchange instant.

**Three of the five brokers do not put one on the wire in the mode this platform subscribes**, and the two that do disagree about units and about which clock they are on. Read from the adapters:

    Zerodha    LTP binary packet     no timestamp field at all
                                     (`>II` = token, ltp — that is the whole packet)
    Upstox     LTPC protobuf         `ltt` exists on the wire; the dependency-free
                                     decoder extracts price only, so nothing carries it
    Angel One  LTP binary            int64 epoch **milliseconds**, exchange clock
    Fyers      lite mode             none — "Lite mode carries no volume and no
                                     exchange timestamp", stated in the adapter
    Dhan       ticker packet         int32 epoch **seconds**, exchange clock
    Yahoo      polled REST           not a stream; there is no arrival event to time

So `now − broker_timestamp` would be available for two brokers out of six sources, in two different units, differencing our clock against an exchange clock whose offset this platform has never measured and cannot measure. Producing a number from that and calling it latency would be exactly the fabrication the brief forbids, and it would require widening `MarketTick`, editing five adapters and decoding two more wire fields to do it. Rule 6 of the brief ("do not use broker-specific timestamp assumptions without proof") and Rule 5 ("no broker adapter modifications unless absolutely required by a protocol fact") both point the same way, and so does the architecture document's own conditional clause.

Audit answers, each from the code:

  **A. What is latency in StockAssist?** The interval between successive *usable prices delivered by a feed*, measured end to end on the platform's own monotonic clock. See Decision. Event-time latency is not available (above); transport-receive→provider-accept latency measures this process's own decode and dispatch cost, is sub-millisecond for every provider, discriminates nothing, and would require the transport→provider seam Rule J warns about — rejected in Alternatives. Subscription→first-tick is one sample per link and Rule 8 forbids a single tick determining a score.

  **B. Can it be derived from existing canonical data without changing `MarketTick`?** Yes, entirely. `StreamingTickProvider.on_raw` already stamps `arrived_at = self._clock()` on every accepted batch and already stores it in `_last_evidence_at`. The interval between consecutive accepted batches is the difference of two values the class already holds. **No contract was widened, no field added, no adapter opened.**

  **C. Are broker timestamps semantically equivalent across the six sources?** No — table above. No cross-broker latency number was invented from them.

  **D. Aggregation?** Median over a bounded window of the nine most recent intervals. See Decision.

  **E. Interaction with probation?** Latency ranks strictly *below* probation, so it can never promote a probationary feed past a proven one. See Decision.

  **F. Interaction with freshness?** They are computed from one series of instants and therefore cannot contradict each other; and a feed that loses fresh evidence loses its latency score with it. See Decision.

  **G. Latency unavailable?** `None`, which ranks last within its group and is never zero. See Decision.

  **H. Decay?** Twice over, both through mechanisms that already exist. See Decision.

  **I. Per `(user, feed)`?** Structurally, by living on the provider instance. See Decision.

  **J. Transport information needed?** No. Zero broker-layer changes; no seam was created.

Decision

**Latency in this platform is *delivery latency*: the median of the nine most recent intervals between accepted canonical batches, measured on the provider's own injected monotonic clock, and established only while the feed has fresh evidence.**

Stated for a reader who will be tempted to assume it means something else: **this is not exchange-to-ingest latency and must never be presented as such.** It answers "how long does a consumer of this feed wait for its next usable price", which is the question provider *selection* actually needs answered, and it is the only latency question this platform can answer truthfully today. LIM-D5.4-1 records the gap.

**Clock source.** `self._clock`, already monotonic and already injectable, the same one probation and coverage measure against. No wall clock, no broker clock, no second clock. A latency measure on a clock an NTP step can move backwards would rank a provider on an artefact of time synchronisation.

**Broker timestamps are not trusted, not parsed and not read.** Nothing in this sprint touches `BrokerTick.timestamp`.

**Aggregation: median of a bounded rolling window, `LATENCY_WINDOW_SAMPLES = 9`.** One constant, and it carries two meanings that are the same meaning — the deque's `maxlen` *and* the warm-up requirement, because latency is established exactly when the window is full. A second warm-up threshold would be a second answer to one question, which is the mistake ADR-043 spent a sprint not making.

Why a median and not a mean, an EWMA or a p95:

* **A median of nine tolerates four outliers before the statistic itself moves.** That is the brief's "one outlier ≠ permanent demotion" as an arithmetic property rather than as a hoped-for behaviour: a feed has to be slow *most* of the time to be scored slow. A mean or an EWMA is moved arbitrarily far by one 600-second gap, which is precisely what a broker's midday hiccup looks like.
* **The window forgets by eviction, not asymptotically.** Nine newer intervals remove every older one completely. An EWMA's oldest sample never quite leaves, which is the brief's "old latency ≠ permanent advantage" left to a decay coefficient nobody can justify.
* **p50 at N=9 is a real observed sample.** Nine is odd on purpose. p95 needs a sample far larger than any warm-up worth waiting through and is unstable below it; the architecture document names p50/p95 together and p50 is the half that is honest at this sample size.
* **Nothing schedules anything.** Samples are produced only by arriving data, so a feed nobody is talking to costs nothing and no timer exists — Rule 12.

Nine is a new number and is stated as one. ADR-043's discipline is *do not invent a second answer to a question the platform has already answered*; the platform has never published a sample count, so there was nothing to reuse, and reaching for `DOWN_AFTER_FAILURES` because it is also 8-ish would be a false economy dressed as consistency. The justification is the outlier-tolerance property above, and `test_the_window_tolerates_a_minority_of_outliers` is what holds it.

**Established, and what happens when it is not.** `delivery_latency` returns the median only when the window is full **and** `has_fresh_evidence` is true; otherwise `None`. `None` is not zero and is not an estimate. In `_selection_rank` it becomes the sort key `math.inf`, which places the provider last **within its own (health, probation) group** and nowhere else.

Last-within-group rather than first is a decision with a specific near-miss behind it, and it is the audit's second finding. Ranking unknown latency *best* looks like the safe, generous choice and is the opposite: **Yahoo can never establish a delivery latency**, because it is polled and has no arrival event to time, so "unknown wins ties" would have promoted the permanent baseline above every streaming feed in the same health/probation group and silently undone D4.5. Ranking it last leaves Yahoo exactly where priority already puts it and can never move it. `test_the_baseline_can_never_establish_a_delivery_latency` and `test_latency_never_promotes_the_baseline_over_a_streaming_feed` are the two that hold this.

Nor does last-within-group recreate ADR-029's UNKNOWN-health deadlock, and the reason is structural rather than lucky: health improves only by being *called*, so a provider that is never selected can never leave UNKNOWN — but a pushed feed accumulates delivery intervals whether or not it is the primary. Evidence arrives without selection, so there is no cycle to deadlock.

**Interaction with READY: none, in both directions.** Latency creates no readiness and no eligibility. It is the third element of a sort key over candidates that have *already* survived entitlement, capability, health and coverage filtering; it cannot add a provider to that list. Conversely a feed does not need latency evidence to be ready — readiness is one valid canonical tick, exactly as D4.5 left it.

**Interaction with probation: latency ranks strictly below it.** `_selection_rank` is `(health, probation, latency)`. A probationary feed is behind a stable one on the second element before the third is ever compared, so no median, however good, can promote it — Rule 10 satisfied by ordering rather than by a special case. Latency does still order two feeds that are *equally* unproven, which is not a bypass of probation: it breaks a tie inside a rank, which is what a third sort element is for, and it does so on nine observed intervals rather than on the "one tick had a low latency" the brief warns about.

**Interaction with freshness: they cannot disagree, because they read one series.** This is LIM-D5.3-3's reconciliation and it is a stronger answer than a precedence rule. Freshness asks "is the *current* gap — now minus the last arrival — inside the coverage window?"; delivery latency asks "what is the typical *completed* gap?". Both are statements about the same sequence of arrival instants on the same clock, so a feed delivering every 90 seconds is simultaneously fresh (90 < 120, its data is usable) and slower than a feed delivering every 200ms — two true statements about different questions, not a contradiction. LIM-D5.3-3 anticipated the disagreement because it was written with *event-time* latency in mind, and event-time latency is the thing the audit found the platform cannot measure.

The hard rule the brief demands — "stale ⇒ never preferred because of latency" — holds twice over, and deliberately so:

1. Losing fresh evidence sets `is_on_probation` (D5.3), which ranks *above* latency, so a stale feed is demoted whatever its median.
2. `delivery_latency` itself returns `None` without fresh evidence, so the stale feed's historical median is not even in the comparison.

The second is not redundant defence, it is honesty: a median assembled from gaps that all closed ten minutes ago is not a current measurement of anything, and reporting it on `describe()` as though it were would mislead an operator.

**Decay: two existing mechanisms, no new one.** The bounded window drops a sample once nine newer ones exist, so a fast ten minutes ago is gone after nine intervals of slow. And the freshness gate expires the whole score when the feed goes quiet, which reuses `tick_max_age_seconds` for the third time in three sprints — one staleness policy, now asked per-instrument (D4.5), per-feed (D5.3) and per-score (D5.4). No decay constant, no half-life, no timer.

**Reconnect resets it**, in `_discard_evidence`, alongside the ticks and the probation timestamps that already reset there. Intervals measured on a link that no longer exists describe a connection the platform cannot ask anything of — the same argument D4.5 made for coverage and D5.2 for probation, and the third time it has been the right one. It also disposes of a bug that would otherwise need its own guard: the gap *spanning* a disconnection would be an enormous fictitious interval, and because `_last_evidence_at` is cleared the first batch after a reconnect produces no sample at all rather than that one.

**Per-user isolation is structural, not enforced.** The deque is an instance attribute of `StreamingTickProvider`, and `market_feed.feed_provider_name(user_id, broker)` gives exactly one instance per `(user, broker)` in the registry. There is no map keyed by broker, no module-level accumulator and no shared state to leak through — a second user's feed is a different object, and two brokers of one user are two objects. This is the same construction D5.2 and D5.3 rely on, and it is why the answer to "prove it structurally" is that there is nothing to prove: sharing would require a global, and none was added.

**The generic default is `None` on `MarketDataProvider`**, mirroring `is_on_probation`'s `False` for the same reason: a provider that is *polled* has no delivery cadence to measure, and saying so is a statement rather than a convenience. The Source Manager reads the contract, never the type, so a licensed exchange feed or a vendor feed gets this term by implementing the property and changes nothing here.

**Broker-neutrality.** No broker module was imported, opened or named. The measurement's whole input is one monotonic clock and the fact that a batch was accepted, both of which the Market Engine already owned. A fictional sixth broker gets latency scoring by registering through the same seam and writing zero lines.

Alternatives Considered

**Exchange-timestamp latency (`now − broker_timestamp`), as MARKET_DATA_ARCHITECTURE.md §7 describes.** Rejected on the evidence in Context, and the document's own clause "where the provider supplies an exchange timestamp" is the condition that fails. Adopting it would mean widening `MarketTick` (Rule 5), editing five adapters, decoding `ltt` from Upstox and the timestamp fields from Zerodha's wider modes, and then differencing two unsynchronised clocks in two units — producing a number that would look like a measurement and be an artefact of clock skew, for the two brokers where it existed at all, while the other four had none. LIM-D5.4-1 keeps it on the record as a prerequisite rather than pretending it was done.

**Transport-receive → provider-accept.** Rejected. It measures this process's own decode and dispatch, which is sub-millisecond and essentially identical for every provider, so it would rank on scheduler noise. It is also the only candidate that would require carrying a transport instant across the broker→market-engine boundary — the seam Rule J permits only if necessary, and it is not necessary for a signal that discriminates nothing.

**Subscription → first tick.** Rejected as a score: one sample per link, so Rule 8 forbids it, and it is dominated by whether the market happened to be trading at that instant. Kept in mind as a diagnostic, not implemented — there is no consumer for it.

**EWMA.** Rejected. Cheap and decaying, but one 600-second gap in a sub-second stream moves it by more than the true signal, so the brief's "one outlier ≠ permanent demotion" would rest on choosing an α small enough to blunt outliers and large enough to recover, which is a tuning parameter with no principled value. The median gets outlier resistance from its definition instead.

**p95, as the architecture document names alongside p50.** Rejected at this sample size. p95 of nine samples is "the largest one", which is an outlier detector, not a latency score; a sample large enough for a meaningful p95 is a warm-up long enough to be a liability. p50 is the half of the document's phrase that is honest here, and LIM-D5.4-3 records that p95 remains unimplemented rather than quietly dropping it.

**An absolute "slow" threshold in milliseconds, banding providers as fast/slow.** Rejected, and it was the most tempting wrong answer. A threshold has to be a number of milliseconds, and the same interval means opposite things on a liquid large-cap during the open and on an illiquid instrument at 14:30 — so the band would classify honest feeds as slow for trading quiet instruments. Comparative ranking needs no threshold: it asks only which of two candidates is faster, and when there is only one candidate the term does nothing at all.

**Making latency an eligibility filter, or letting it override probation or staleness.** Rejected for the same reason PROBATION_RANK is a ranking term and not a filter (ADR-042): a filter can produce "no provider at all" from a merely-slow feed, trading a ranking blemish for an outage. Yahoo is the floor and nothing in this sprint may lower it.

**A separate latency registry / scorer service.** Rejected — Rules 1–3. The state is nine floats per feed, its only reader is one sort key, and its lifecycle is exactly the feed's lifecycle. A second component would need registering, unregistering, per-user keying and reconnect invalidation, all of which the provider instance already does for free by *being* the per-feed object.

Consequences

• **Two equally healthy, fresh and stable feeds are now ordered by measured delivery cadence**, and every other pairing is ordered exactly as it was before D5.4. The term is inert for a single-feed user, inert for the baseline, and inert until nine intervals exist.

• **`MarketTick`, the broker contract, all five adapters, `stream.py`, `reliability.py`, `market_feed.py`, `instruments.py`, the registry, the gateway, the REST quote path and the frontend are unchanged.** The sprint is three edited modules — the model in `providers/streaming.py`, one contract default in `providers/base.py`, one sort element in `source_manager.py` — plus exports and one new test module.

• **Latency is a diagnostics field, never a consumer one.** `describe()` gains `delivery_latency_seconds` (an admin/diagnostics surface where provider names already live); `SourceManager.status()`, `Resolution.as_status()`, every normalized event and every API response are unchanged and still carry `source_tier` and no provider identity. `None` is emitted as `null`, never as `0` and never as `Infinity` — the sort key's `math.inf` exists only inside the comparison and is never serialised.

• **Thirty-one source mutations were attempted and every relevant test went red.** The set the brief names, plus a genuinely global (cross-provider) accumulator, a cross-user accumulator, wall-clock timing, latency as a filter, latency creating readiness, and a broker-name branch in the scorer.

• **LIM-D5.4-1 — exchange-to-ingest latency is still not measured, and this is not it.** The platform reports how fast a feed *delivers*, not how stale each price was when it arrived. A broker that batches 200ms of ticks and pushes them promptly and a broker that pushes each tick 200ms late are indistinguishable here. Closing this needs, in order: a decoded exchange timestamp on the two brokers whose wire carries one and on the two whose wider modes could, a field on `MarketTick` to carry it, and — the actual blocker — a defensible estimate of the offset between the exchange clock and ours, without which the subtraction is not a measurement. It is a prerequisite, recorded as one.

• **LIM-D5.4-2 — the delivery interval is a per-feed aggregate over a heterogeneous subscription.** A feed's median mixes every instrument it carries, so a feed subscribed to quiet instruments scores worse than one subscribed to busy ones for a reason that is nothing to do with the feed. The comparison is fairer than it sounds — `attach_market_feed` subscribes every one of a user's brokers to the *same* holdings-and-positions universe, so two feeds being compared are usually carrying the same instruments in the same market minute — but "usually" is not "always", and the mitigation is that the term is a last-place tie-break behind health, probation and freshness rather than something that can cause an outage.

• **LIM-D5.4-3 — p50 only; no p95, and no latency in `health()`.** MARKET_DATA_ARCHITECTURE.md §7 asks for rolling p50/p95 and `health()` names "measured latency". This sprint delivers p50 on the provider and leaves `ProviderHealth` untouched, because health is counter-based evidence from past *calls* and a pushed feed makes no calls — folding a push-derived statistic into it would be the transport/evidence unification ADR-043 refused, one layer along.

• **LIVE VALIDATION WAS NOT PERFORMED.** No interactive broker session exists in this environment. Every number above comes from deterministic tests with an injected monotonic clock, a pass through the real `attach_market_feed` seam for all five brokers plus a fictional one, and the mutations listed. **No real latency was measured and none is claimed.** The outstanding smoke test: connect two brokers on one account to the same instrument universe, hold both past the probation window, confirm both establish a median, confirm the faster one leads the chain, then throttle or subscribe the leader to a quiet instrument and confirm the order changes only after the window refills — and, throughout, that the tier the user sees never leaves `streaming` because a *ranking* term moved.

Review Date

At the sprint that decodes an exchange timestamp for any broker, or at D5's chaos testing, whichever comes first. The specific claim to re-test is LIM-D5.4-2: whether the per-feed aggregate is still defensible once two feeds on one account can be observed carrying genuinely different instrument sets, and whether the tie-break should become per-symbol at that point.

---


# ADR-045

Title

Entitlement Failure Is Its Own Terminal Condition: One Feed Stops, the Session Does Not (Sprint D5.5, closes the D4.11 code-806 approximation)

Date

2026-08-27

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-044 closed with a Review Date, and D5.4's scope note named what was still owed: *"a broker-neutral representation of entitlement failure"*, carried forward unchanged through D5.1, D5.2, D5.3 and D5.4. It originates in **ADR-040**, which considered a `NOT_ENTITLED` event kind for the fifth adapter's "data APIs not subscribed" disconnect code, rejected it as disproportionate for one broker's one code, approximated it as `AUTH_EXPIRED`, and recorded the approximation as a limitation rather than hiding it.

**The audit's finding is that the approximation is not cosmetic, and that no message text can make it so.** The two conditions have different *blast radii*:

  * `AUTH_EXPIRED` is a fact about the **session**. `BrokerEngine._on_stream_expired` drops the cached session, stops **every** channel of that broker, writes a `broker.session.expired` audit row and pushes `session_expired: true` to the user.
  * A refused entitlement is a fact about **one capability on one feed**. The token is valid: REST portfolio, funds, order placement and the order stream all keep working.

So the approximation tore down a working trading session and told the user their login had expired, on the strength of a statement the broker had not made. That is a functional loss *and* a false statement, and it is invisible in the message text — the state the engine moves to is the lie, not the string.

The audit also established that the other existing kind cannot carry it. `ERROR` deliberately leaves the connection alone, so a broker that closes the socket after sending one drives the reconnect ladder indefinitely — **paced** by D5.1's flap suppression to the 60-second ceiling, **never stopped** by it — with the account's provider still registered and still nominally a priority-1 candidate. Retrying cannot make an unlicensed account licensed. That is the bar for widening a closed set, and it is met: the set genuinely could not express the required semantics.

Decision

**One new member of `StreamEventKind`: `NOT_ENTITLED`, and nothing else new anywhere.** No new contract, no new capability, no new registry, no new consumer surface, no field on `MarketTick`, and no timer.

**Terminal for one channel of one user's stream, and for nothing else.** `_NotEntitled` is a distinct exception from `_AuthExpired` and deliberately **not** a subclass of it — `except _AuthExpired` is what tears down the session, and an inheritance relationship would silently restore exactly the behaviour this sprint exists to remove. The run loop `return`s on it rather than falling through to `next_pause()`, which is the substance of the sprint: every other exit from that loop reconnects.

**Recovery is unregistration, not demotion, and it reuses the path that already existed.** `detach_market_feed` — the response to an ended entitlement since D4.4 — takes the account's provider out of the registry, so the baseline serves the very next resolution. Demoting instead was rejected: a demoted feed is still a candidate the moment nothing steadier remains, so a feed that has lost its entitlement could return to serving quotes. Unregistered, there is no state — READY, STABLE, primary — in which it can stay selected. That is a structural guarantee rather than a rule.

**The market side is unchanged, and that is the strongest evidence the D4.4/D4.5 decomposition was right.** `StreamingTickProvider`, the readiness gate, probation, freshness, the latency term, the Source Manager's sort key, the Market Gateway and the provider registry are byte-for-byte untouched by this sprint. An entitlement failure is expressed to the Market Engine as *a provider going away*, which it has always known how to handle, and the Market Engine never learns that a broker refused anything.

**Scoped by the channel's own declaration, not by a broker name.** The engine detaches the market feed only when the refused channel is the one carrying ticks — the same `_channel_carries_ticks` gate D4.7 added for link state, asked for the same reason: an entitlement refused on an *order* channel says nothing about the market feed, and detaching one because the other was refused would drop a feed delivering prices perfectly well.

**Per-user isolation is structural.** One `BrokerStream` is one `(user, broker, channel)` and one provider is one `(user, broker)`, so a second user of the same broker is a different object that nothing on this path can reach. It is asserted with two users on one broker, resolved *through the registry* — the arrangement ADR-040 found was the only one in which a broker-scoped mistake is visible.

**`stream_connect_error` widened its return type, not its signature.** It may now answer with a terminal `BrokerStreamEvent` as well as with a reason string, so a broker whose 403 means "not licensed" rather than "token rejected" can say which. Widening the *signature* was rejected for the reason D4.7 and D4.10 rejected it: it changes what every adapter and every test double implements, so an unmigrated broker fails on a live socket rather than at import. Every adapter written before D5.5 returns a string or `None` and is unaffected.

**Entitlement is never inferred.** A socket that opens, a subscribe frame the broker accepted, a timeout, silence, and a malformed frame are all absence of evidence, and each is pinned by its own test. This is the sharpest rule in the sprint because the failure it prevents is silent and permanent: an inferred entitlement failure stops a working feed forever and nothing in the system will ever contradict it.

Alternatives considered

**Keep the approximation and improve the message.** Rejected — this is ADR-040's position, re-examined with the evidence ADR-040 did not have. The message was already honest; the *state* was not, and a user whose valid session is torn down does not get it back because the log line was accurate.

**A reason argument on the existing `on_expired` callback instead of a second callback.** Rejected. The two outcomes have different blast radii, and a flag would have put that distinction in the hands of every consumer to remember — a consumer that forgot to branch would tear down the session, which is the exact defect being fixed, reintroduced silently one layer up. Two callbacks put the distinction in the type.

**Make `_NotEntitled` a subclass of `_AuthExpired` "so existing handlers keep working".** Rejected, and it is worth naming because it is the tempting shortcut: the existing handler is the session teardown, and inheritance would mean the new condition takes the old path by default. The mutation that makes it a subclass is red.

**Demote the provider (`mark_link_down`) rather than unregister it.** Rejected — see the Decision. A demoted feed remains a candidate; it also remains able to *deliver*, since the sink stays bound, so a socket the user has not disconnected could go on pushing into the gateway. `unregister_streaming_provider` disconnects, unbinds and unregisters, which is the behaviour an ended entitlement has always had.

**Give the user a dedicated notification or a new `provider.status` reason.** Rejected as out of scope and unnecessary. Unregistration already publishes a user-scoped `provider.status`, whose payload shape is unchanged, and the frontend already renders the tier flip from it. A new field on a consumer payload is a frontend contract change, and D5.5 has no frontend work. Recorded as LIM-D5.5-2.

**Fix DB-5-adjacent flap behaviour, or add a give-up-after-N policy for repeated non-terminal failures.** Rejected as separable and left as remaining D5 work. The audit specifically checked whether entitlement classification was inseparable from reconnect pacing and found the opposite: entitlement is terminal by classification and never reaches the ladder, so the two do not interact. DB-5 itself was closed in D5.1.

Consequences

• **The closed event set gained exactly one member, in the fifth sprint in which the smallest correct extension turned out to be an extension at all.** `EVENT_CAPABILITY` is unchanged — the new kind is a connection-level fact and is ungated, exactly as `AUTH_EXPIRED` is, so a broker that mis-declares what it serves cannot thereby lose the ability to say "stop".

• **The blast radius is asserted from both sides.** The session survives; the account's other channels keep running; a second user of the same broker keeps a READY feed; another broker of the same user keeps serving; the guest/baseline status is byte-identical before and after; and the refused feed can no longer deliver into the gateway at all.

• **Twenty source mutations were attempted and twenty went red**, including the fifteen the brief names: the classification degraded to transient and to auth expiry (at the adapter *and* at the contract constructor), terminal handling turned back into retry and into immediate reconnect, the ineligibility removed, detach downgraded to a demotion, entitlement inferred from silence and from a malformed frame, made global across users and across every provider, a broker error class put on the consumer status, a broker-name branch added to the transport, the refusal reaching Yahoo and reaching an unrelated provider of the same user, one broker special-cased in the engine, the handshake string answer ignored, `_NotEntitled` made a subclass of `_AuthExpired`, the refusal routed through the capability gate, the channel gate removed, and the finished stream left in the registry. **One earlier attempt is reported rather than hidden:** the first form of the "refusal affects Yahoo" mutation had an ambiguous anchor and did not apply; it was reformed against a unique anchor and is red.

• **A broker name was caught in this sprint's own docstring by its own sweep** — the `StreamEventKind` narrative named the adapter whose code motivated the member — and was rewritten rather than exempted, as D5.1's sweep required of D5.1.

• **LIM-D5.5-1 — no broker other than the fifth adapter classifies an entitlement failure today.** The mechanism is generic and is exercised by a fictional broker through the real transport, but four of the five shipped adapters have no documented entitlement code to map, so their 401/403 handling remains session expiry — which is what their documentation says it is. This is a statement about those brokers' protocols, not a gap in the mechanism; it closes for a given broker when that broker's protocol is shown to distinguish the two.

• **LIM-D5.5-2 — the user is told their tier moved, not why.** The consumer surface is the existing user-scoped `provider.status`, whose `reason` vocabulary is the Source Manager's `UnavailableReason` and which reports the baseline as *available* — correctly, because it is. A user whose broker feed was refused therefore sees their tier drop to `delayed` with no explanation, and learns why only from the audit row. Closing it means a consumer-payload field and a frontend change, which D5.5 has no mandate for.

• **LIM-D5.5-3 — a refused feed does not retry, and nothing re-probes it.** That is the intended behaviour and it is also a one-way door until a lifecycle event: if a user's entitlement is granted *while connected*, the feed will not come back until they reconnect the broker or the process restores sessions. A generalized re-probe is Phase 5 work that ADR-029 already owes for demoted providers, and this is the second caller for it.

• **LIVE VALIDATION WAS NOT PERFORMED.** No interactive broker session exists in this environment. Every claim rests on deterministic fixtures — a disconnect packet built from the documented wire layout, a fictional broker refusing both in a frame and at the handshake, and the real `attach_market_feed` seam — plus the mutations above and a DEBUG-level pass through the real logging stack with live-looking credentials. **The outstanding smoke test:** connect an account that genuinely lacks the data-API entitlement, confirm the socket opens and the refusal arrives in a frame, confirm the feed stops after exactly one connection, confirm the user's tier falls to `delayed` **while the same account can still fetch its portfolio and place an order**, then grant the entitlement and confirm that reconnecting the broker — and only that — brings the feed back.

Review Date

At the sixth streaming broker, or at the first live session against an unentitled account, whichever comes first. The specific claim to re-test is the one this ADR rests on: that entitlement failure and session expiry are genuinely two conditions rather than one condition seen twice. The evidence to look for is a broker whose protocol distinguishes them *and* whose account can be observed trading normally while its feed is refused.

---

# ADR-046

Title

Generalized Provider Re-Probe: Recovery Is Classified, and the Probe Is One Ordinary Attach (Sprint D5.6, closes LIM-D5.5-3 and answers ADR-029's deferred re-probe)

Date

2026-08-29

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-045 gave the platform its first genuinely terminal feed condition and recorded, on the record, that it was also a one-way door (LIM-D5.5-3): a `NOT_ENTITLED` feed is detached and nothing ever attaches it again, so an entitlement granted while the session stays valid cannot be discovered. ADR-029 had recorded the same shape two sprints earlier for a demoted provider and deferred the fix to Phase 5. This is that work, and the point of doing it once is that both are instances of one question rather than two special cases.

The audit inspected all ten states the brief names and found the deadlock is **not** general. Seven of them already recover, and recover correctly:

  * a **transient disconnect** recovers through D5.1's reconnect ladder — sub-second in the healthy case — and the provider re-earns readiness on the new link;
  * a **stale but open feed** recovers on the next accepted canonical tick with no control plane involved at all (D5.3), because the link never dropped and nothing was discarded;
  * a **link-down demotion** is not an unregistration: the provider stays registered and un-resolvable, and climbs back through the readiness gate on the connection that actually exists (D4.5);
  * **probation** and an **unestablished latency score** cannot deadlock, because a pushed feed accumulates evidence whether or not it is currently primary — the property ADR-044 already established;
  * an **auth expiry** requires a new session, and that is not a deadlock but the correct answer;
  * a **new broker session** and a **user reconnect** already re-attach everything through `start_stream`.

Three genuinely deadlock, and they are listed rather than one being fixed:

  1. **An entitlement-refused feed** (LIM-D5.5-3). Detached, the stream task returned, and nothing schedules another attach. The immediate caller for this sprint.
  2. **A provider whose health reached DOWN** (ADR-029). `candidates_for` excludes DOWN, health improves only on a successful call, and the chain never reaches an excluded provider — so it is never called and can never climb back. For the *baseline*, which is the only polled provider, that is a total feed outage that never self-heals.
  3. **A stream whose channel or transport disappeared from configuration.** `BrokerStream._run` returns without reconnecting when the adapter no longer declares the channel or no transport serves its protocol. Correct, and terminal until the deployment changes.

Decision

**Five recovery classes, not one retry flag.** `RecoveryClass` names what has to happen before an attach is worth attempting again: `TRANSPORT` (the reconnect ladder owns it), `EVIDENCE` (the next valid tick owns it), `REPROBE` (a paced attach), `SESSION` (a new valid session, never a retry) and `CONFIGURATION` (a deployment change). Collapsing them is precisely the defect ADR-045 found one layer down — two conditions with different blast radii sharing one response — so the taxonomy is the deliverable, and only `REPROBE` is ever retried on a schedule.

**`TRANSPORT` and `EVIDENCE` are refused registration outright**, rather than registered and skipped. Both already heal themselves, and a candidate for a self-healing condition is a second recovery mechanism racing the first. This is the sprint's largest deliberate omission and it is asserted from both sides: a stale feed's natural recovery is pinned by a test, and creating a candidate for it is a mutation that goes red.

**The probe is one ordinary attach through the existing lifecycle — there is no control-plane probe and no new adapter method.** A `check_entitlement()` on the adapter contract was rejected for two reasons: it would widen what every adapter and every test double implements, which D4.7, D4.10 and D5.5 each refused in turn; and it answers the wrong question. A control-plane "yes" is not evidence that a feed can serve a price, and MARKET_DATA_ARCHITECTURE.md has exactly one definition of a usable feed. So a re-probe calls `start_stream` scoped to the one withdrawn channel, and the outcome is read off the callbacks that already exist — a second refusal, an expiry, or market data arriving.

**Recovery therefore creates no eligibility.** A recovered feed is a *new* `StreamingTickProvider` — `attach_market_feed` constructs one — so it must earn READY from a valid canonical tick, must serve a full probation window before it may outrank a steady provider, and inherits neither readiness, nor stability, nor delivery-latency evidence from the connection that was refused. There is no state to inherit because there is no object left holding it, and the property is pinned rather than the mechanism.

**Re-probe pacing is its own ladder, and that separation is the core architectural rule.** Reconnect asks *is the same socket reachable*; re-probe asks *has a provider-level condition changed*. Sharing a ladder would mean re-asking an entitlement every two seconds because that is how fast a socket should come back — the churn D5.5 exists to stop. `STILL_UNAVAILABLE_BASE_DELAY` is 300 seconds, doubling, capped at 3600, and is honestly a new number: unlike the 30-second window this document already published, there was nothing to reuse, and reaching for a health threshold because it is also a duration would be the false economy ADR-044 named. The justification is what the condition is — an entitlement changes when a person changes it. The one thing the two ladders share is `reconnect_pause`, equal-jitter arithmetic for decorrelating a herd, which is not connection semantics.

**A dead session is excluded twice, by two guards that catch different facts.** `RecoveryClass.SESSION` excludes a feed whose token expired, and it is *recorded* rather than merely absent so the exclusion is a fact a test can read and a mutation can break; a session predicate asked again at attempt time excludes a feed whose entitlement was refused and whose session went away *afterwards*, which no classification made at withdrawal time can know. An expiry on any channel reclassifies every outstanding candidate of that account, because a token is a fact about the account.

**The ladder is cleared only by a deliberate lifecycle event, never by an apparent success.** The register keeps the attempt count outside the candidate, in a history map cleared only by `complete_auth` and `disconnect`. Without that separation a broker that accepts a socket, ticks once and refuses the entitlement a moment later would reset the ladder to five minutes on every cycle — DB-5's accept-then-refuse storm reappearing on a five-minute period instead of a 1.5-second one.

**The candidate is discharged by evidence, not by a socket.** Market data arriving on the account's feed is what clears an outstanding withdrawal, taken at the engine's tick boundary *before* canonical mapping: the question a re-probe asks is whether the account may consume the feed at all, and a broker frame carrying market data answers it. Readiness is a different question, asked further down and answered only by a valid canonical tick reaching the provider.

**Scope is `(user, broker, channel)`, structurally.** The same key `BrokerStreamManager` uses and the same granularity `ConnectionStability` is instantiated at. Two users on one broker hold two candidates nothing can confuse; one user's refused market feed says nothing about the same user's order channel. `start_stream` gained an optional `channels=` filter so a market-feed re-probe does not blip a healthy order socket, and the market-feed registration below it is now gated on the tick-carrying channel actually having been (re)opened — without that gate an order-channel re-probe would replace a live provider and discard its readiness, probation and latency evidence.

**Make-before-break is preserved by doing nothing.** Recovery is additive: the baseline is never released to make room for a probe, the refused provider is not resurrected, and the recovered one joins the chain only after the normal gates. There is no instant during a recovery at which a user has no feed, and it is asserted at every step of a full cycle.

**One background timer, bounded.** A single process-wide sweeper wakes each `REPROBE_SWEEP_INTERVAL` (60s) and acts only on due candidates; a sweep with an empty register performs no I/O and reads two dictionaries. It is capped per sweep so one wake-up cannot burst against a broker, started from `load_sessions` and stopped in `shutdown`. `sweep_once()` is factored out so the policy is testable without a clock, a sleep or a task — the property D5.2 insisted on for probation, for the same reason.

**Broker-neutrality is structural.** `services/brokers/recovery.py` imports nothing from `services.` except `reliability`, names no broker anywhere including its comments, and receives no broker vocabulary; `broker` is an opaque account-scoping token, never compared to a literal. `ReprobeOutcome`'s seven members carry no broker code, no protocol and no credential. The Market Engine imports none of it and never learns that anything was re-probed. The three things the service can do are injected callables, which is what keeps the module free of a cycle back into the engine and every branch in it assertable without a database, a socket or an adapter.

Alternatives Considered

**A `check_entitlement()` control-plane probe on the adapter contract.** Rejected — see the Decision. It widens the contract for every adapter and it proves the wrong thing.

**Reuse D5.1's `ConnectionStability` ladder for re-probe pacing.** Rejected, and pinned by a test asserting the slowest reconnect is still faster than the fastest re-probe. The two schedules measure different things; a shared one is a reconnect storm wearing a recovery name.

**Register a candidate for every withdrawal, including transport blips and stale feeds, and skip the ones that do not need it.** Rejected. A registered candidate is state a later change can accidentally make re-probeable, and the conditions in question recover in under a second and on the next tick respectively. Refusing registration makes the omission structural.

**Clear the recovery candidate on link-up.** Rejected — that is the socket answering a question about entitlement, and a broker that accepts and then refuses would clear and re-create the candidate on every cycle, resetting the ladder each time.

**Fix the ADR-029 health deadlock in this sprint too.** Rejected as separable and recorded as LIM-D5.6-1 rather than hidden. Re-probing a DOWN provider means *calling* it, which is the Market Engine's business, and the Market Engine may not import the broker layer — so it needs its own caller, its own probe symbol and its own decision about whether an excluded provider may be re-admitted to the chain or probed out of band. That is a resolution-path change with real regression surface, and this sprint's mandate was the mechanism. The taxonomy covers it; the caller does not exist yet.

**Give recovery its own event topic (`provider.reprobe.started` / `.failed` / `provider.recovered`).** Rejected as event-topic sprawl. The user-scoped `provider.status` the existing register/unregister path already publishes reports exactly what a consumer may know — the tier moved — and a recovery that succeeds is indistinguishable from a first attachment by design. Recovery detail lives in the logs and in `describe()`, the admin surface where provider names already live.

Consequences

• **LIM-D5.5-3 is closed.** A refused feed now has a way back that is neither a retry nor a reconnect: paced at five minutes and climbing to an hour, per user and per channel, blocked while the session is dead, blocked while the channel is already attached, and discharged by market data rather than by a socket.

• **Twenty-eight source mutations were attempted and twenty-eight went red**, including all sixteen the brief names. Every one was verified to fail on a targeted assertion rather than on an import or collection error.

• **`StreamEventKind`, `MarketTick`, the broker adapter contract, the provider contract, the Source Manager's sort key, the Market Gateway and every consumer payload are unchanged.** The only signature change anywhere is an optional `channels=` keyword on `BrokerEngine.start_stream`, whose default reproduces the previous behaviour byte for byte.

• **LIM-D5.6-1 — the ADR-029 health deadlock is still open.** A provider that reaches DOWN is excluded from candidates, is therefore never called, and can therefore never improve. For the polled baseline that is a feed outage that survives until a process restart or an external `record_success`. It is now *classified* — the taxonomy has a place for it — and it has no caller, because its caller must live in the Market Engine. `test_a_demoted_provider_has_no_self_recovery_path_in_d2` still passes, unchanged.

• **LIM-D5.6-2 — `RecoveryClass.CONFIGURATION` is classified and never recorded.** The two transport paths that are terminal for configuration reasons (`_codec is None`, no transport for the protocol) return without a callback, and adding one would widen `BrokerStream` for a condition that is correctly unrecoverable anyway. The class exists so the register refuses to re-probe one if a caller ever records one, which is pinned by a test.

• **LIM-D5.6-3 — a re-probe that the broker refuses again costs one connection.** That is the mechanism working as designed (there is no cheaper way to ask), and it is bounded by the ladder rather than eliminated. An account refused for a day makes roughly a dozen connection attempts, against a reconnect ladder whose *ceiling* is one per minute.

• **LIM-D5.6-4 — the sweeper is process-local**, like `BrokerHealth` before it (DB-1). A multi-worker deployment holds one register per worker, so an account whose stream lives in worker A is re-probed by worker A alone — correct, because that is the worker holding the session — but a restart loses every ladder and every candidate, and the first sweep after a restart starts at the base delay. Acceptable while a restart also re-attaches every session anyway.

• **LIVE VALIDATION WAS NOT PERFORMED.** No interactive broker session exists in this environment. Every claim rests on deterministic fixtures — a fictional broker refusing through the real transport, the real `attach_market_feed` seam, the real provider registry and an injected clock — plus the mutations above and a DEBUG-level pass through the real logging stack with live-looking credentials. **The outstanding smoke test:** connect an account that genuinely lacks the data-API entitlement; confirm the refusal arrives and the feed stops after exactly one connection while the account still trades; confirm the withdrawal is registered as `reprobe`; leave it and confirm the re-probe fires at roughly five minutes and then at ten, twenty and forty; grant the entitlement externally; confirm the next re-probe attaches, that the first valid tick earns READY, that the feed serves a full probation window before it is preferred, and that the user's tier flips to `streaming` with no reconnect on their part.

Review Date

At the first live session against an account whose entitlement is granted mid-session, or at the sprint that gives the ADR-029 health deadlock a caller — whichever comes first. The specific claim to re-test is the one this ADR rests on: that a re-probe can be *one ordinary attach* rather than a probe of its own. The evidence that would refute it is a broker for which an attach is materially more expensive than a control-plane check, or one whose refusal costs the account something a probe would not.

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---


# ADR-047

Title

Health Recovery Is a Failure Cool-Down on the Existing Resolution Path (Sprint D5.7, closes the ADR-029 health deadlock and LIM-D5.6-1)

Date

2026-08-29

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-029 (D2) recorded a cycle and deferred it to Phase 5. ADR-046 (D5.6) classified it, gave the entitlement instance a caller, and deliberately left this one without one (LIM-D5.6-1). The cycle:

    health reaches DOWN → excluded from `candidates_for` → never selected
                        → never called → health improves only on a call → DOWN forever

**The audit's first finding is that the deadlock is narrower than "a demoted provider", and the narrowing is what made it fixable.** It is specific to a provider whose only evidence comes from *being called*:

• A **pushed** feed records a success from `MarketGateway._ingest_ticks` whenever a canonical batch is accepted, and that path never consults resolution. Evidence arrives without selection, so the cycle does not close — the same structural property ADR-044 relied on when it ranked unestablished delivery latency last.
• A **DEGRADED** provider is not excluded at all. It is still a candidate, still in the failover chain, and recovers the moment the chain reaches it. It is *unreached*, not *unreachable*. `test_a_demoted_provider_has_no_self_recovery_path_in_d2` is about this case, remains true, and is unchanged by this sprint — which is worth saying plainly, because ADR-029's prose ran the two cases together and D5.6 cited that test as the pin for a deadlock it does not actually pin.
• A **polled** provider at DOWN closes the cycle completely. The only polled provider the platform ships is the permanent baseline, so the untreated case is not a corner case: it is a total feed outage that survives until the process restarts.

**The audit's second finding is that the mechanism was already specified and half-implemented.** MARKET_DATA_ARCHITECTURE.md's Resolution procedure, step 2, has always read:

    Filter out candidates whose health state is `down` **or that are inside a failure cool-down**.

D1 implemented the first half as an unconditional filter and the second half not at all. A cool-down with no expiry is a permanent exclusion, and that is the whole of the bug. Failover rule 4 ("a provider that fails repeatedly within a window enters an extended cool-down") and the failover diagram's closing line ("Retry loop continues; first provider to recover restores the feed") say the same thing twice more.

Decision

1. **The recovery path is the resolution path, and there is no schedule.** `SourceManager.resolve_feed` asks the new `ProviderHealthRecovery` register whether any DOWN-but-otherwise-eligible provider's cool-down has run, and appends the ones that have. No task, no sweeper, no timer, no `asyncio` import — pinned by a test. Rule 7 of the brief asked for an existing path if one could do the job, and one could: the platform is already asking this provider's question several times a minute; it had simply stopped including the provider in the ask.

2. **Re-admission is a place at the tail of the chain, and nothing else.** `HEALTH_RANK` gains `DOWN: 2` — the worst band — and health is the *first* element of the selection key, so "a re-probed provider can never outrank a healthy or a probationary one" is true by the position of an element rather than by a branch. There is no branch; there is a row in a table. The provider's health is untouched by being re-admitted and stays DOWN until a real `record_success` from a real call.

3. **The ladder is charged by evidence, never by the offer.** `SourceManager.record_failure` climbs the ladder when the provider is (still) DOWN afterwards; `record_success` clears it, gated on the resulting state so an *empty* success — which does not reset the failure streak — does not clear the cool-down either. A provider offered at the tail and never reached, because something healthier answered, costs nothing and keeps its trial. Pacing is therefore exact rather than approximate: at most one call per cool-down.

4. **Everything else is still asked, through one eligibility pass.** `ProviderRegistry.candidates_for` and the new `down_candidates_for` are the exact complement of each other over a single `_eligible_for` traversal, so entitlement, capability, readiness and per-symbol coverage cannot answer differently on the two paths. A re-admitted feed that is not READY is still not eligible; a feed belonging to another user is still invisible. The DOWN filter itself is untouched.

5. **Pacing: 60 seconds, doubling, capped at 240.** The base is honestly a new number. Nothing the platform publishes measures this — D5.1/D5.2's 30 seconds is one window measured on a socket and on its data, D5.3's 120 seconds is how old a price may be, D5.6's 300 seconds is how long to wait before re-asking a human-timescale question — and borrowing one would be the mistake ADR-044 named when it declined to reuse a health threshold as a latency window. DOWN is eight consecutive failed calls, and for the baseline it means the user is currently seeing nothing, so the cost of waiting is at its maximum and the cost of being wrong is one HTTP call. The **ceiling** is derived rather than picked: 240 < 300 makes the *slowest* health cool-down still faster than the *fastest* D5.6 re-probe, which is the same form of pin ADR-046 used for reconnect-vs-re-probe, and it is the right way round because a DOWN provider is a machine-timescale condition this process can observe ending while an unresolved entitlement is a human-timescale one it cannot. **No jitter**, deliberately: jitter decorrelates a herd released by a shared schedule and there is no schedule here — each worker arms its own cool-down when its own eighth failure landed — so a third copy of D5.1's arithmetic, in a layer that may not import the one holding it, would decorrelate something that is not correlated.

6. **Per-user and per-provider isolation is a property of the key.** The register is keyed `(owner scope, provider name)` rather than by name alone, so a per-user feed's cool-down cannot be read or charged on behalf of another user even if two accounts' feeds were ever minted with the same name — which today's naming makes unreachable, and which the isolation must not depend on.

7. **Consumer surfaces are unchanged.** `source_manager.status()` is byte-identical in shape and carries no recovery vocabulary; the cool-down appears only on the admin `diagnostics()` surface, where provider names already live. Asserted by exact key set at every stage of a cycle.

Alternatives Considered

• **Reuse D5.6's REPROBE machinery.** Rejected, and the brief asked for the refutation from the architecture rather than an assumption. D5.6's unit is `(user, broker, channel)` and its probe is *one ordinary attach of a broker stream*; a DOWN provider may be the polled baseline, which has no broker, no channel and no attach. Its register lives in `services/brokers/`, which the Market Engine may not import at all — pinned by `test_the_market_engine_never_imports_a_broker_module`. And REPROBE exists for conditions nothing in this process observes changing; a DOWN provider's condition is *this process's own calls failing*, which it observes directly. Reusing it would have meant a broker-shaped mechanism for a provider-shaped fact, plus a scheduled probe for a question a request already asks. The taxonomy's own answer is that these are different classes, and D5.6 wrote that down.

• **A periodic background re-probe of DOWN providers.** Rejected under rule 7. It needs a task, a clock and a shutdown path, it would call providers nobody is asking about, and it would race the resolution path it is trying to help.

• **Re-admit a DOWN provider at the head of the chain, or ignore DOWN when nothing else is available.** Rejected. The first violates brief rule 10 outright. The second is the same thing wearing a condition, and it makes the failure mode "the provider we know is broken is now the primary" in exactly the situation — a total outage — where a wrong answer is most expensive. The tail placement gives the same recovery with none of that, because the gateway reaches the tail precisely when it would otherwise have returned nothing.

• **Clear DOWN on a timer instead of re-admitting.** Rejected: that *is* "DOWN is treated as UP", one indirection along. Health must be restored by evidence, and the only evidence is a call that worked.

• **Change `MarketTick`, a broker adapter, or `stream.py`.** None was needed and none was made. The audit found no broker contract insufficiency: nothing about this mechanism reaches the broker layer.

Consequences

• The polled baseline can recover from a total outage without a process restart. That is the case LIM-D5.6-1 named, and it is now asserted end to end through the real gateway and the real adapter class.

• A DOWN provider costs at most one call per cool-down instead of one call per request, which is the property the unconditional filter was protecting and the only property it was protecting.

• **A behaviour change on the `ALL_PROVIDERS_DOWN` path.** When a trial is due, a resolution that previously returned `available=False` now returns a chain of one, so a gateway method that previously returned its empty default may now re-raise the provider's error. This is the D2 contract applied consistently — "return the default when there is no provider at all, re-raise when a call fails" — and every caller already faces the raise whenever any candidate exists. Named here rather than discovered later.

• **D5.6's boundary sweep was rewritten, and the rewrite is reported rather than quiet.** `test_the_market_engine_imports_no_recovery_implementation` asserted `"recovery" not in source` across four market-engine modules. The property it defends is that the Market Engine never reaches for the *broker layer's* re-probe machinery; the word was a proxy that worked only while no market-engine module had a recovery concept of its own. It is now a list of D5.6's actual symbols, the structural ban on importing `services.brokers` is untouched, and a second test asserts the same boundary in the other direction — `services/brokers/recovery.py` may not name provider health.

• **Yahoo is unaffected throughout, including when Yahoo is the provider being recovered.** Nothing here disconnects, unregisters or suppresses any provider; the mechanism only ever *adds* a last resort to a chain. Asserted at every stage of a full cycle.

Requires Approval

None.

Review Date

At the first live session in which a provider genuinely reaches DOWN — see the smoke test in TASK.md — or at the sprint that relocates health to Redis (DB-1), which is the change that would make the cool-down register cross-process and require deciding whether one worker's trial discharges another's.

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---


# ADR-048

Title

Distributed Health Is a Shared Record for Evidence About Shared Things, Read Once Before Resolution (Sprint D5.8, closes DB-1 and LIM-D5.7-2)

Date

2026-08-29

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

Every health mechanism D5 built is process-local, and each was correct while it was the only thing making a decision. With `N` uvicorn workers behind one deployment they are `N` independent opinions about the same remote dependency:

    BrokerHealth                     one counter per worker per broker
    ProviderHealth                   one counter per worker per provider
    ProviderHealthRecovery (D5.7)    one cool-down per worker per provider
    RecoveryRegister (D5.6)          one ladder per worker per (user, broker, channel)

Two consequences are visible and one is not. The Admin Portal reports whichever worker answered (DB-1's original wording). A provider needs `8 x N` failed calls before every worker has excluded it. And — the one that actually matters — **D5.7 promised "at most one trial per cool-down" and delivers it per worker**, so a provider that is genuinely down is retried `N` times per cool-down. LIM-D5.7-2 named that and said the decision was owed here.

**The audit's first finding is that "is this in memory?" is the wrong question, and asking it would have made the system worse.** The right one is *is this state evidence about something every worker observes?*

• A **broker's API** is one remote system; every worker's calls to it are evidence about the same outage. Shared.
• A **polled provider** — the permanent baseline — is registered in every worker and called by every worker over HTTP. Shared. It is also, from ADR-047, the exact place the recovery-trial double-spend happens.
• A **streaming provider** is one live socket held by one worker. Its health, readiness, probation window, freshness evidence and delivery-latency intervals are all evidence about *that link*, and ADR-043 already rules that a reconnect discards them. Publishing them would let a dead socket's DOWN verdict be inherited by the fresh link a *different* worker opened — the opposite of what ADR-045 and ADR-046 established, where a re-attached feed is a new feed that must earn READY again. **Not shared.**
• **D5.6's REPROBE register** records the withdrawals *this worker's own sockets* suffered, and a re-probe is one ordinary attach. Sharing it would let worker B attach a channel whose stream lives in worker A — two sockets for one `(user, broker, channel)`, which is a worse failure than the one being fixed. **Not shared** (LIM-D5.6-4 stands, restated as a decision rather than an omission).

**The audit's second finding is a contract obstacle, and the shape of the answer is the sprint.** `SourceManager.resolve_feed` is synchronous and is called from routes, diagnostics, the scanner and the gateway. DB-1 needs an *atomic* decision on that path — "may this worker spend this provider's trial?" — and atomic means Redis, and Redis means `await`. Making resolution awaitable would have changed a five-module contract for a question asked about a handful of DOWN providers.

Decision

1. **The store is one neutral module with one connection stack.** `infrastructure/health_state.py`. Not `services/`, because `services.market_engine` may not import `services.brokers` (pinned) and the broker layer must not acquire market-engine vocabulary; `infrastructure/` is the one place both may reach, and it already owns the Redis connection. It builds no client of its own — everything goes through `infrastructure/redis_client.py`, so the pool, the retry budget and the circuit breaker PH2.7 established apply unchanged. Pinned by an AST test that no `redis` module is imported there.

2. **The awaitable work is lifted out of resolution, not pushed into it.** `SourceManager.prepare()` runs once per gateway call and does exactly two things: refresh every eligible provider's health from the shared record, and atomically claim at most one recovery trial per DOWN provider. Its result travels as a `SharedResolution` value into the *unchanged*, still-synchronous `resolve_feed`, where `due_from(claims=...)` **filters and re-decides nothing**. A caller that does not prepare gets exactly the D5.7 behaviour, which is what every existing call site and every single-process deployment still run. Pinned by an AST test that `resolve_feed` is not a coroutine.

3. **Every mutation is one Lua script, and therefore one atomic transition and one round trip.** `GET` → modify → `SET` loses exactly the updates that matter: two workers recording the seventh and eighth consecutive failure in the same instant must produce a streak of 8, and two workers finding the same trial due must not both take it. Falsified both ways — a structural test forbids a bare `hset`/`hget` outside a script, and a concurrency test drives eight workers at one provider and asserts the threshold lands exactly.

4. **The scripts read the clock with `redis.call('TIME')`.** D5.1–D5.7 use `time.monotonic` because a duration must not be movable by an NTP step; monotonic clocks are *not comparable between processes*, so a shared ladder cannot use one, and accepting each worker's wall clock would make the ladder as skewed as the worst-set clock in the fleet. Redis's own clock is one clock for every worker. (Requires effect-based script replication — the default from Redis 7, which `docker/redis` runs.)

5. **A trial claim leases the *offer*, never the ladder.** ADR-047's rule is that the ladder is charged by evidence and never by the offer: "a provider that is offered and never reached — because something healthier answered — costs nothing at all." Advancing the ladder at claim time would have broken it. So a claim takes a 30-second exclusive lease on the *right to offer* the provider and touches neither `attempts` nor the next-probe instant; only a failed probe climbs the ladder, and the same write releases the lease. Both lease bounds are semantic: longer than the longest provider HTTP timeout in the platform (12s, `services/http_client.py`), so a second worker cannot take the trial while the first is still making the call; shorter than the base cool-down (60s), so a worker that died holding the lease cannot park a due trial for a whole cool-down.

6. **The key carries kind, owner and name.** `sa:health:{provider|broker}:{owner|-}:{name}`, and the trial key is *derived from* the health key rather than built independently, so the two can never be scoped differently. `kind` because a broker and a provider can share a name and are different subjects. `owner` because two users can hold a feed from the same broker and one account's outage is not the other's — the same reason ADR-047 keyed the local register that way, and it must not depend on a naming convention a later sprint could change. No token, key, session or URL is ever written to a key or a value.

7. **TTL is bounded in both directions, from semantics.** One hour. It may not be shorter than the longest cool-down it has to outlive (ADR-047's 240s ceiling) or a provider would return to UNKNOWN — re-admitted with no evidence, its failure streak erased by a key expiry rather than by a successful call. It may not be unbounded or a per-user feed for a closed account holds a key forever, the growth trap `forget_user_status` and `ProviderHealthRecovery.forget` both avoid. What it means operationally: a provider nothing has called for an hour reports UNKNOWN, which is the honest answer.

8. **Redis unavailable: bounded local fallback, chosen from what the deployment already guarantees.** Redis is registered `critical=False` in the readiness probe, `services/cache.py` degrades to a per-process dict, and `infrastructure/redis_client.py` exists to make degradation a first-class path. So neither of the two decisive-sounding options is taken. *Fail closed* — unreachable Redis reads as DOWN — turns a blip in an explicitly non-critical dependency into a total market-data outage. *Fail open* — ignore health while Redis is away — throws away the evidence this worker holds and hammers a provider it knows is failing. Every method returns `(ok, ...)` and the caller applies the mutation locally with the code that has always been there: the platform reverts to exactly its pre-D5.8 behaviour, per worker, for the duration. Falsified in all three directions, including that a *single* failure with Redis down is still a single failure and not an escalation.

9. **The Lua state machine is not trusted; it is pinned against the Python one.** The transitions had to be expressed twice — once in Python for the fallback, once in Lua so they can run atomically — and a second expression is a second thing to get wrong. Parametrised parity tests replay the same event sequences through `BrokerHealth`/`ProviderHealth` and through the store and assert identical snapshots. The Python version is the oracle because it is the one D1 and D3 shipped and every earlier test already pins. Nothing about the *policy* changed: the same two thresholds, the same four states, the same auth-failure exclusion, the same empty-success rule.

10. **Broker health gains a read path, because that is what DB-1 originally complained about.** `broker_gateway.health_shared()` and `diagnostics_shared()` adopt the shared record before rendering, so an operator asking "is this broker up?" gets the deployment's answer instead of whichever replica served the request. The synchronous `health()` stays, unchanged, for callers that cannot await.

Alternatives Considered

• **Make `resolve_feed` async.** Rejected — see Decision 2. It is a five-module contract change for a question about a handful of DOWN providers, and it would have put an `await` on a path the scanner and the diagnostics endpoints call synchronously today.

• **Move every provider field to Redis.** Rejected, and this is the decision the brief warned hardest about. Readiness, probation, freshness and latency are evidence about one live socket in one process; sharing them creates a second source of truth for facts that already have one, and actively breaks ADR-043's reconnect rule. `MarketDataProvider.health_is_shared` states the discriminator once, `StreamingTickProvider` overrides it, and a test asserts that a live socket's health never reaches the store on *any* write path — so the flag is behaviour rather than decoration.

• **Distribute D5.6's REPROBE register.** Rejected — see Context. It would produce duplicate attaches, which is worse than the double-spent trial it would fix.

• **Advance the cool-down ladder at claim time** so a claim is self-limiting without a lease. Rejected: it silently repeals ADR-047's charge-by-evidence rule, and the visible symptom would be a provider that gets *further* from recovery every time a healthier feed happened to answer first.

• **A distributed lock around the resolution path.** Rejected. It serialises every worker's resolution on one Redis key to protect a decision that concerns only DOWN providers, and a lock holder that dies takes the feed down with it. The lease is scoped to the one thing that must be exclusive.

• **Publish health changes over the existing Redis pub/sub instead of reading before resolution.** Rejected as the *authority*. Pub/sub is best-effort and a worker that misses a message drifts silently until something else corrects it; a read that costs one sub-millisecond round trip per gateway call is cheaper than the class of bug that would introduce. It remains available later as an optimisation *on top of* an authoritative read, not instead of one.

Consequences

• **The core DB-1 property holds and is asserted end to end**: a failure recorded by worker A is seen by worker B, B cannot reset A's streak, DOWN excludes the provider in every worker, and two — or four — workers resolving simultaneously spend exactly one recovery trial. LIM-D5.7-2 is closed.

• **A broker reaches DOWN on the deployment's eight failures rather than on eight per worker.** During a rolling deploy that is the difference between an outage that is reported and one that every fresh replica reports as healthy.

• **Cost is flat and measured**: one Redis operation per resolution with nothing down, plus one per currently-DOWN provider; one per health mutation. Measured against a local Redis at p50 0.23–0.30 ms, p95 0.36 ms per operation. A deployment with no Redis pays nothing.

• **Health state now survives a worker restart**, which is a change in kind rather than degree: a restarted worker builds fresh UNKNOWN provider objects and previously erased everything the deployment knew.

• **`BrokerHealth`'s local counters can be one mutation stale** on a worker that neither recorded nor read since another worker's last write. That is inherent to a mirror and is why the operator-facing read is `health_shared`. Named rather than hidden.

• **Nothing on any consumer surface changed.** No provider identity, no broker identity and no health vocabulary reaches a consumer payload; Developer Rule 4 is untouched, and the shared state adds no field to `source_manager.status()`.

Requires Approval

None.

Review Date

At the first multi-worker deployment with Redis configured — the smoke test in TASK.md — or at the sprint that gives health-driven *automation* a caller, which is the point at which "one mutation stale" stops being acceptable for the local mirror.

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---

# ADR-049

Title

p95 Delivery Latency Is the Same Series Read Over a Wider Window, and Latency Enters `health()` Without Entering Ranking (Sprint D5.9, closes LIM-D5.4-3)

Date

2026-08-29

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

ADR-044 delivered delivery latency as a median and recorded LIM-D5.4-3: no p95, and no latency in `health()`. It gave a reason for the first half — *"p95 needs a sample larger than any warm-up worth waiting through"* — and a reason for the second: health is counter-based evidence from past *calls*, a pushed feed makes no calls, and folding a push-derived statistic into the counters would be the transport/evidence unification ADR-043 refused.

**The audit's central finding is that the first reason is arithmetic, and the conclusion was one step short of it.** Under the nearest-rank method the p95 of `N` samples is the `ceil(0.95 * N)`-th smallest. That expression equals `N` for every `N` up to and including 19 — so a p95 over D5.4's nine-sample window **is the maximum**, which is precisely the one-outlier sensitivity `LATENCY_WINDOW_SAMPLES = 9` was chosen to avoid. ADR-044 was right that nine cannot carry a p95. It did not go on to ask what can: `ceil(0.95 * N) < N` first holds at **`N = 20`**.

That turns the sample size from a judgement into a derivation, which is the difference between this sprint being possible and it producing a number with precision it does not have.

**The audit's second finding is that the second reason survives, and shapes the answer.** ADR-044's objection was to latency becoming *a counter* — accumulated on the same object, by the same arithmetic, as evidence from calls. It was never an objection to health *reporting* cadence. A block that is **derived on every read** and never stored is not a counter, cannot drift from the intervals it summarises, and cannot cache a cadence that has since lapsed.

**The audit's third finding is that D5.9 needed no change to D5.8's shared state, and the reason is structural rather than careful.** Only a provider that is *pushed into* has a delivery cadence, and exactly those providers declare `health_is_shared = False` (ADR-048). The set of providers with latency and the set whose health is shared are disjoint by construction, so there is no code path along which a cadence could reach Redis.

Decision

1. **One series, two windows.** The deque widens from `LATENCY_WINDOW_SAMPLES` (9) to `LATENCY_TAIL_WINDOW_SAMPLES` (20). The **median reads the newest 9 of it** and the p95 reads all 20. One recording site, one eviction rule, one reset, one monotonic clock — no second series, which D5.9's rule 6 forbids and which would have been the obvious way to get a p95 wrong.

   The consequence that matters is that **ADR-044's definition is unchanged**: `delivery_latency` is still the median of the last nine intervals, establishes at nine, and is bit-identical to what a nine-long deque produced. Widening the buffer added older samples *behind* the ones the median reads. This is deliberate: the D5.9 brief states the D5.4 definition must remain, and the only reading under which both that requirement and a defensible p95 hold is a wider buffer whose newest slice is the old window.

2. **Twenty is derived, and that is the whole justification.** It is the smallest `N` at which `ceil(0.95 * N) < N` — the smallest sample size at which a p95 is a different statistic from a maximum. At 20 the rank is 19, so exactly one worst sample is excluded and one catastrophic gap cannot become the reported tail. Unlike ADR-047's 60 seconds this is not a judgement about operational cost: any smaller window makes the statistic degenerate and any larger one buys tail resolution with warm-up nothing has asked for. Pinned by a test that recomputes the derivation rather than restating the constant.

3. **The percentile method is nearest-rank, and it is documented and pinned rather than assumed.** Sort ascending, take the `ceil(p * N)`-th value, one-indexed. No interpolation, no distribution assumption. Two reasons, both ADR-044's reasons for an odd median window: the result is an interval this feed was *observed* to deliver rather than a number between two of them, and it is exactly reproducible from the retained samples, so a test asserts a value and not a tolerance. Falsified against the mean, against linear interpolation, against the maximum and against an off-by-one rank.

4. **Two windows means two warm-ups, and that is not the drift hazard ADR-044 warned about.** That hazard was *one* statistic with a `maxlen` and a separate warm-up free to disagree. Here **each statistic's window is its own warm-up**, exactly as before — there is no second answer to one question, there are two questions. A feed therefore legitimately spends the interval between its 9th and 20th delivery with a median and no tail figure, and `health()` is built to say so.

5. **`health()` carries a derived `LatencyProfile`, not a counter.** Four fields: `established`, `p50_seconds`, `p95_seconds`, `samples`. Each statistic's `None` is its own "not established" — never `0`, which would read as instantaneous delivery and is the inversion `LATENCY_RANK_UNKNOWN` exists to prevent, and never `math.inf`, which is a sort key that exists only inside that comparison and is not JSON. `established` is stated rather than left to be inferred from `p50_seconds is not None`, because a reader of a payload must not have to know that convention. No name, no broker, no session, no token, no raw monotonic instant: `samples` is a count, not a clock reading.

   Recomputed on every read. That is what makes a *lapsed* cadence — stale, or discarded by a reconnect — report itself honestly without anything having to remember to clear it, and it is what makes the field impossible to corrupt from the shared-health path.

6. **The three introspection contracts stay separate.** `status()` is the consumer payload and gains nothing — a latency figure there would be a provider-shaped fact on a consumer surface, breaching Developer Rule 4. `health()` gains the cadence and remains identity-free. `describe()` remains the named admin surface and gains the p95 beside the p50 D5.4 already put there. Swept in all three directions.

7. **Ranking is unchanged: the selection metric stays the median.** This is the sprint's central scope decision and it is a refusal. §7's `score = f(connection_state, message_freshness, error_rate, p95_latency)` is a *continuous score*, and ADR-044 rejected that form wholesale in favour of a three-element sort key because a scalar `f(...)` hides which term decided and the terms are not commensurable. There is therefore no surviving specification asking for p95 in ranking — only a formula the platform already declined. Adding a fourth sort element would also be new selection policy with real failover surface, on evidence nobody has: the platform has never observed a case where two feeds tie on median and the tail should break it. The p95 is reported so that operators can *see* tail behaviour; a sprint that finds it should decide selection can have that argument with data. Falsified in both directions — a mutation making p95 the selection metric and a mutation appending it as a fourth element are each caught.

8. **Nothing is added to the D5.8 shared store.** Not by restraint but by construction: latency exists only on providers whose health is not shared. Both halves are pinned — a mutation that makes a pushed feed declare its health shareable is caught, and so is a mutation that teaches the store a latency field.

Alternatives Considered

• **Compute a p95 over the existing nine samples.** Rejected, and it is the mistake this ADR exists to avoid. It returns the maximum under a percentile's name. It would have "closed" LIM-D5.4-3 with a number whose only property is being the worst thing that happened, and the closure would have been false.

• **One window of twenty for both statistics.** The tidier option — one constant, one establishment point, no state in which a median exists without a tail. Rejected because it silently redefines the platform's selection metric: the median would become the median of twenty, latency would establish after twenty deliveries instead of nine, and every D5.4 ranking property would have to be re-argued. D5.9 is a reporting sprint; buying tidiness with a change to what selection means is the wrong trade, and the brief says the D5.4 definition must remain.

• **A second deque for the tail.** Rejected — rule 6, and correctly. Two buffers means two eviction rules, two reset paths and two chances for a reconnect to clear one and not the other. Pinned structurally: `StreamingTickProvider` contains exactly one `deque(` and exactly one `_delivery_intervals.append`.

• **Store the profile on `ProviderHealth` and update it when an interval is recorded.** Rejected. It makes the cadence a counter, which is ADR-044's objection restated, and it introduces the one bug this design cannot have: a feed that goes stale or reconnects records nothing, so nothing updates the block, so `health()` keeps reporting a cadence the feed no longer has. Derivation on read has no such state.

• **Share the latency summary in Redis so an operator sees one figure per provider.** Rejected — ADR-048's boundary, and the case it was drawn for. A cadence is evidence about *one socket in one worker*; a shared figure would average or overwrite across links that have nothing to do with each other, and would let a dead socket's tail describe the fresh link a different worker opened.

• **Make p95 an eligibility filter or a demotion trigger.** Rejected for ADR-042's and ADR-044's shared reason: a filter can produce "no provider at all" from a merely-slow feed, trading a ranking blemish for an outage. Yahoo is the floor and nothing in this sprint may lower it.

Consequences

• **LIM-D5.4-3 is closed**, and both halves are actually implemented: a p95 that is a percentile rather than a maximum, and latency inside `health()`.

• **LIM-D5.4-1 remains open and is restated deliberately**: exchange-to-ingest latency is still unmeasured, because no provider supplies an exchange timestamp at the canonical boundary and there is no defensible clock-offset estimate. Nothing in this sprint moves toward it, and the p95 must never be presented as it. `MarketTick` is unchanged.

• **A feed's warm-up to a full cadence is longer than its warm-up to a median** — 20 deliveries rather than 9. On any real feed that is a fraction of a second; on a feed slow enough for it to matter, the feed is stale long before, and a stale feed reports no cadence at all.

• **The rounding mode in the nearest-rank index is inert at the published window**, because `0.95 * 20` is exactly 19.0 and `floor` and `ceil` agree. Found by falsification and recorded rather than smoothed over: the choice is pinned at a window where the two differ, so the general method stays falsifiable even though the shipped configuration cannot distinguish them.

• **Selection behaviour is unchanged.** The sort key is the same three elements with the same values, and the 949 D4/D5 tests pass under five shuffled file orderings.

• **Nothing on any consumer surface changed.** `status()` gains no field; no provider identity, broker vocabulary, credential or monotonic instant reaches `health()` or `describe()`; verified against the real logging stack at DEBUG with live-looking fake credentials, and against a live Redis key/value sweep.

Requires Approval

None.

Review Date

At the sprint that has evidence about tail behaviour from a real feed — which is the first point at which "should p95 influence selection?" can be answered with data rather than with a formula the platform already declined. Also at the first sprint to obtain an exchange timestamp, which would make LIM-D5.4-1 addressable and would change what "latency" means on this platform for the first time since D5.4.

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---




# ADR-050

Title

A Shard Is Not a Provider: One Logical Streaming Feed May Own Several Broker Connections (Sprint D5.10, instrument sharding)

Date

2026-08-29

Status

Accepted — implemented; **live validation not performed** (see Consequences)

Context

Every streaming broker caps how many instruments one connection may carry, and until D5.10 every adapter answered an over-cap subscription the same way: take a deterministic prefix, log a warning, and leave the account's feed quietly narrower than its portfolio. That was recorded as a limitation five times over — ADR-036, ADR-037, D4.9, D4.10, ADR-040 — each time with the same note, *"D5 owns sharding"*. This is that sprint.

**The audit's first finding is that the brokers do not all cap the same thing, and the numbers are not interchangeable.** Four adapters cap **instruments per connection**, which another connection genuinely raises. One caps **tokens per session**, counted across the client code — a quota sharding cannot raise, and declaring it as a per-connection limit would open a second socket the same quota refuses, spending one of that broker's three permitted connections to subscribe to nothing. Two more numbers already in the adapters are **per-frame** limits, which are wire framing on a single socket and are not sharding at all. Treating any of the three as the other would have made a working broker worse.

**The audit's second finding is that the transport was already almost right.** D4.7 generalised `BrokerStreamManager` from `(user, broker)` to `(user, broker, channel)` because a broker's realtime surface can be more than one socket. Sharding is the same generalisation one scope further in — several sockets of *one* channel — and the transport needed one key element and nothing else.

**The audit's third finding is where the work actually was.** `StreamingTickProvider` held four scalars — link state, the instant readiness was earned, the instant valid data last arrived, and the recent delivery intervals — because a feed was one socket and a scalar was the whole truth. Every one of those is a fact about **one connection**, and with several connections the scalars answer the wrong question in a way that is invisible: a provider whose `_last_evidence_at` is advanced by any shard reports itself live forever while a third of the portfolio has no socket at all. That is ADR-043's stale-feed defect arriving by a second route.

**The two requirements pull against each other, and that is the substance of this ADR.** The sprint brief asks both that a failing shard preserve the data its healthy siblings deliver, and that a healthy shard never mask a dead one. No single predicate does both.

Decision

1. **A shard is not a provider, and this is the invariant everything else serves.** One `StreamingTickProvider` per account per broker, however many connections it holds. No shard has a registry entry, is ranked by the Source Manager, earns a readiness a consumer can observe, appears in a market event, or appears in a consumer-facing payload. `MarketTick`, `InstrumentMap`, the Market Gateway, the Source Manager, the provider registry, the fallback chain and the readiness state machine are untouched and unduplicated.

2. **The planner is broker-neutral arithmetic, in its own module.** `services/brokers/sharding.py` splits `N` instruments into `ceil(N / L)` contiguous batches in input order, de-duplicating first. It imports nothing from the platform, names no broker, and is asserted to contain no broker name in *comments as well as code* — a stricter sweep than `stream.py`'s, because this module is new and nothing forces its prose to discuss a broker. Its first draft named two in a docstring, which is exactly the drift a stripped sweep would have let through.

3. **The limit is a declared capability, and it says "per connection" for a reason.** `BrokerStreamChannel.max_instruments_per_connection` and `max_connections`, both defaulting to `None`. `None` means *no shardable limit known*, never "unlimited", and it plans exactly one connection — so every channel written before D5.10 is byte-for-byte unaffected. A broker whose cap is a session quota declares `None` and keeps trimming in its own `subscribe_frames`, where it always did.

4. **The evidence ledger becomes one `_ShardEvidence` per connection, and the provider's answers become aggregations over it.** Which aggregation each answer uses is the whole design:

   * `_last_evidence_at` = the **minimum** over declared shards, `None` if any has none. So `has_fresh_evidence` — and through it `stability` — mean "**every** connection is delivering". Neither predicate needed a line changed. The maximum is the mask, and it is what this refuses.
   * `_ready_since` = the **maximum**, `None` if any shard has not earned readiness. Probation is therefore re-served whenever any connection reconnects, and never re-served by connections that did not drop.
   * `delivery_latency` / `delivery_latency_p95` = per-shard series, aggregated by **maximum**, `None` if any shard is untimed. Merging shard arrivals into one series would make three connections delivering once a second read as a third of a second — a latency advantage bought by owning more sockets rather than by delivering any instrument sooner. A consumer waits for *their* instrument, which arrives on exactly one connection.
   * `_last_tick` carries the delivering shard, so a connection dropping discards exactly the prices it was covering.

5. **The two requirements are resolved along the seam the architecture already had.** `READY` and per-symbol `covers()` — the *serving* gate — stay "at least one connection is delivering", which is the faithful reading of what READY has always meant. Partial coverage has lived in `covers()` since ADR-035 and it is exact. Every provider-level *claim* becomes "every connection". A feed of three that loses one goes on answering for the instruments the other two deliver, and stops claiming to be a live feed.

6. **The shard is bound into the engine's callbacks, not carried by the transport.** `stream.py` gains a key element, a task-name label and a log label, and learns nothing about sharding. The engine built the plan, so it knows which shard it is opening at the moment it opens it, and binds the answer with a partial application. Widening the transport's callbacks instead would have moved every existing signature — the trade D4.7 and D4.10 both refused, refused again.

7. **Resharding is make-before-break, and reuses connections it does not need to change.** A connection whose instruments, session and credentials are unchanged and which is running is left alone — not stopped and restarted. New and changed connections are opened, and connections the plan no longer has are stopped **last**. No instrument is left uncovered because the planner was rebuilding, and a portfolio sync that adds one instrument no longer tears down every connection the account had.

8. **No second recovery ladder, and no second health model.** D5.1 owns reconnect, D5.3 owns evidence freshness, D5.5 classifies entitlement, D5.6's register stays keyed on `(user, broker, channel)` — an entitlement is a statement about a capability, identical on every connection of one channel, so a refusal ends the whole channel and the re-probe re-opens whichever of its connections are down. D5.8's boundary is untouched: `health_is_shared` is already `False` for exactly the reason that applies to a shard, and the Source Manager contains no occurrence of the word.

Consequences

• **Five limitations recorded across D4.6–D4.11 are closed for four brokers**: an over-cap subscription now opens another connection instead of trimming the account's portfolio.

• **LIM-D5.10-1 — no concurrent-connection ceiling is declared for four of the five brokers**, because the repository documents one for only one of them and D5.10 does not invent numbers. The honest consequence is that a portfolio far beyond one connection is planned into as many connections as it needs, and a broker that refuses the excess will refuse it at the handshake, where D5.1's ladder paces the retry.

• **LIM-D5.10-2 — one broker's 1,000-token session quota is still enforced by trimming with a warning.** Sharding cannot raise a quota, and this is the correct outcome rather than an unfinished one.

• **LIM-D5.10-3 — a partially failed sharded feed is ranked below the delayed baseline for every instrument**, including the ones its healthy connections are still delivering, until the lost connection is restored and has served a full probation window. This follows mechanically: `stability` reads `has_fresh_evidence` (ADR-043), and ADR-042 ranks a probationary provider below a steady one. The feed's data is genuinely preserved — it stays eligible, keeps its coverage, and answers whenever nothing steadier remains — but it is not preferred. The alternative, a per-shard stability term, is a second ranking system, and it would let a feed with a permanently dead connection hold the primary position indefinitely.

• **The reconnect reset went from two independent controls to one, deliberately.** ADR-042 found `_discard_evidence` and `_advance`'s re-stamp each sufficient alone. The second stamped a single provider-level timestamp on a readiness transition, which a sharded feed cannot have — its connections earn readiness at different moments — so it moved to the connection that earned it. Recorded rather than tidied away, and the D5.2 test now mutates the single control that exists.

• **A single-connection feed is unaffected.** Every aggregate over one shard is that shard's own value; every widened signature defaults to the single connection; the full suite matches its pre-sprint baseline exactly.

• **35 falsification mutations were applied and all 35 observed RED.** Five were malformed or green on the first pass and were reformed — three of them by adding the test coverage they proved was missing, including the end-to-end attribution of a tick to the connection it arrived on.

• **Live validation not performed.** No interactive broker session exists, and no account in this repository holds a portfolio that exceeds any broker's per-connection limit. A smoke-test checklist is in BROKER_INTEGRATION.md.

Requires Approval

None.

Review Date

At the first sprint with a live broker session against an account large enough to shard — which is the first point at which any of this is observed rather than derived. Also at the first broker whose per-connection limit is small enough that a retail portfolio reaches it, which would move sharding from a ceiling-raiser to an everyday path and would make LIM-D5.10-3's ranking cost a product decision rather than a footnote.

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---



# End of Decisions Documentation
