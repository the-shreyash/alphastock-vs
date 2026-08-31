/**
 * Feed-state security sweep (D5.14).
 *
 * Drives the REAL path — socket frame → RealtimeProvider → batching window →
 * store → projection → rendered pill — with payloads shaped like the leaks the
 * platform is guarding against: live-looking credentials, broker wire codes,
 * raw exception strings, transport URLs, Redis keys, provider object names.
 *
 * The backend forbids every one of these on `provider.status` and D5.13 has
 * tests proving it does not send them. That is a different claim from the one
 * here: this asserts the FRONTEND does not become the place they leak if a
 * payload ever carries them — a regression in one layer, a future field added
 * by a well-meaning sprint, or a compromised upstream. Both claims are needed;
 * neither substitutes for the other.
 */
import { render, act, screen } from "@testing-library/react";
import { RealtimeProvider } from "../context/RealtimeProvider";
import MarketFeedStatus from "../components/layout/MarketFeedStatus";
import { useRealtimeStore } from "../store/realtimeStore";

jest.mock("../context/AuthContext", () => ({
  useAuth: () => ({ user: { _id: "user-a", email: "trader@example.com" } }),
}));

/** Values that must never survive the trip, and the reason each is here. */
const FORBIDDEN = [
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",   // a JWT
  "tok_live_51H8sKqZzQ",                     // an access token
  "ak_live_9f2c8d",                          // an API key
  "hunter2SuperSecret",                      // a password
  "zerodha", "upstox", "angelone", "fyers",  // broker identity
  "brokerfeed:zerodha:user-a",               // an internal provider object name
  "TokenException",                          // a raw broker exception class
  "Incorrect `api_key` or `access_token`",   // a raw broker error string
  "wss://ws.kite.trade",                     // transport internals
  "sa:health:zerodha", "redis",              // recovery/Redis internals
  "cooldown", "probation", "reprobe",        // recovery implementation details
];

const HOSTILE = {
  provider: "brokerfeed:zerodha:user-a",
  broker: "zerodha",
  broker_name: "Zerodha Kite",
  access_token: "tok_live_51H8sKqZzQ",
  api_key: "ak_live_9f2c8d",
  password: "hunter2SuperSecret",
  jwt: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def",
  error: "TokenException: Incorrect `api_key` or `access_token`.",
  ws_url: "wss://ws.kite.trade?api_key=ak_live_9f2c8d",
  redis_key: "sa:health:zerodha",
  cooldown_remaining_s: 42,
  probation: true,
  reprobe_due_at: "2026-01-15T09:31:00Z",
  failover_chain: ["upstox", "angelone", "fyers"],
};

const frame = (over) => ({
  type: "event",
  event: "provider.status",
  channel: "provider",
  data: {
    state: "unavailable", tier: null, reason: "not_entitled",
    capabilities: ["indices"], previous_tier: "streaming",
    ...HOSTILE, ...over,
  },
  timestamp: "2026-01-15T09:30:00.000Z",
});

const socket = () => global.WebSocket.instances.at(-1);

beforeEach(() => {
  jest.useFakeTimers();
  useRealtimeStore.getState().reset();
});
afterEach(() => jest.useRealTimers());

const drive = (over) => {
  render(
    <RealtimeProvider>
      <MarketFeedStatus />
    </RealtimeProvider>,
  );
  act(() => { socket().readyState = 1; socket().onopen?.(); });
  act(() => {
    socket().onmessage?.({ data: JSON.stringify(frame(over)) });
    jest.advanceTimersByTime(50);
  });
};

const assertClean = (subject, label) => {
  for (const secret of FORBIDDEN) {
    expect(
      subject.toLowerCase().includes(secret.toLowerCase()) ? `${label} leaked ${secret}` : "clean",
    ).toBe("clean");
  }
};

describe.each([
  ["entitlement refusal", { change_reason: "entitlement_refused" }],
  ["session expiry", { change_reason: "session_expired" }],
  ["disconnection", { change_reason: "feed_disconnected" }],
  ["an unexplained outage", {}],
  ["a broker wire code as the reason", { change_reason: "TokenException_403" }],
  ["a recovering probe", { state: "recovering", tier: null, change_reason: null }],
  ["a restored live feed", { state: "available", tier: "streaming", change_reason: null }],
])("%s", (_label, over) => {
  it("puts nothing sensitive into the store", () => {
    drive(over);

    assertClean(JSON.stringify(useRealtimeStore.getState().feedState || {}), "the store");
  });

  it("renders nothing sensitive", () => {
    drive(over);

    assertClean(document.body.innerHTML, "the DOM");
  });

  it("still tells the user something true", () => {
    drive(over);

    // The point is not silence — a stripped payload must still produce an
    // honest, readable state, or "leaks nothing" would be satisfied by a
    // component that renders nothing at all.
    expect(screen.getByTestId("market-feed-status")).toBeInTheDocument();
    expect(screen.getByTestId("market-feed-detail").textContent.length).toBeGreaterThan(10);
  });
});

it("never presents a hostile payload as live unless the state says available", () => {
  drive({ state: "recovering", tier: "streaming", change_reason: "entitlement_refused" });

  expect(screen.getByTestId("market-feed-status")).toHaveAttribute("data-feed-live", "false");
});
