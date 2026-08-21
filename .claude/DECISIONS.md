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