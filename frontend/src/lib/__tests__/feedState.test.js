/**
 * Feed-state projection (D5.14).
 *
 * The frontend's ONLY interpretation of the backend's consumer feed contract.
 * Every rule the backend spent D5.12/D5.13 establishing — `recovering` is a
 * refinement of "not available", a tier is a claim about data actually being
 * served, a change reason is the platform's vocabulary and never a broker's —
 * has to survive the trip through this module or the UI un-does the contract.
 *
 * Production failures these catch: a probing provider rendered as a live feed
 * (a trader acting on prices nothing has delivered); a broker's raw refusal
 * string rendered to a user; a token riding a payload into the DOM.
 */
import {
  FEED_STATE,
  projectFeedState,
  describeFeed,
  FEED_CHANGE_REASON,
} from "../feedState";

/** The exact `data` object the backend publishes on `provider.status`. */
const payload = (over = {}) => ({
  state: "available",
  tier: "streaming",
  reason: null,
  capabilities: ["indices", "quotes"],
  previous_tier: "delayed",
  ...over,
});

describe("projectFeedState — the backend's three states", () => {
  it("keeps `available` available and carries its tier", () => {
    const feed = projectFeedState(payload());

    expect(feed.state).toBe(FEED_STATE.AVAILABLE);
    expect(feed.tier).toBe("streaming");
  });

  it("keeps `recovering` distinct from `available`", () => {
    const feed = projectFeedState(payload({ state: "recovering", tier: null }));

    expect(feed.state).toBe(FEED_STATE.RECOVERING);
    expect(feed.state).not.toBe(FEED_STATE.AVAILABLE);
  });

  it("keeps `unavailable` distinct from `recovering`", () => {
    const un = projectFeedState(payload({ state: "unavailable", tier: null, reason: "all_providers_down" }));
    const rec = projectFeedState(payload({ state: "recovering", tier: null }));

    expect(un.state).toBe(FEED_STATE.UNAVAILABLE);
    expect(un.state).not.toBe(rec.state);
  });

  it("drops a tier the backend should never have sent in `recovering`", () => {
    // Defence in depth against LIM-D5.12-1: the backend nulls the tier itself,
    // and the frontend must not render one even if a future payload carries it.
    const feed = projectFeedState(payload({ state: "recovering", tier: "streaming" }));

    expect(feed.tier).toBeNull();
  });

  it("drops a tier in `unavailable` too", () => {
    const feed = projectFeedState(payload({ state: "unavailable", tier: "delayed" }));

    expect(feed.tier).toBeNull();
  });

  it("treats an unknown state as unavailable, never as available", () => {
    const feed = projectFeedState(payload({ state: "degraded_probably" }));

    expect(feed.state).toBe(FEED_STATE.UNAVAILABLE);
  });

  it("treats a missing payload as unavailable without throwing", () => {
    expect(projectFeedState(null).state).toBe(FEED_STATE.UNAVAILABLE);
    expect(projectFeedState(undefined).state).toBe(FEED_STATE.UNAVAILABLE);
    expect(projectFeedState({}).state).toBe(FEED_STATE.UNAVAILABLE);
  });

  it("drops an unrecognised tier rather than rendering it", () => {
    const feed = projectFeedState(payload({ tier: "realtime_plus" }));

    expect(feed.tier).toBeNull();
  });

  it("copies only the contract's fields — nothing a payload smuggles in", () => {
    const feed = projectFeedState(
      payload({ provider: "zerodha_kite", access_token: "tok_live_x", broker: "upstox" }),
    );

    expect(Object.keys(feed).sort()).toEqual(
      ["capabilities", "changeReason", "previousTier", "reason", "state", "tier"].sort(),
    );
    expect(JSON.stringify(feed)).not.toMatch(/zerodha|upstox|tok_live_x/i);
  });

  it("keeps capabilities only when they are a list of strings", () => {
    expect(projectFeedState(payload({ capabilities: ["quotes"] })).capabilities).toEqual(["quotes"]);
    expect(projectFeedState(payload({ capabilities: "quotes" })).capabilities).toEqual([]);
    expect(projectFeedState(payload({ capabilities: undefined })).capabilities).toEqual([]);
  });
});

describe("projectFeedState — change reasons are an allow-list", () => {
  it.each([
    ["entitlement_refused", FEED_CHANGE_REASON.ENTITLEMENT_REFUSED],
    ["session_expired", FEED_CHANGE_REASON.SESSION_EXPIRED],
    ["feed_disconnected", FEED_CHANGE_REASON.FEED_DISCONNECTED],
  ])("passes the known reason %s through", (wire, expected) => {
    expect(projectFeedState(payload({ change_reason: wire })).changeReason).toBe(expected);
  });

  it("drops an unknown reason instead of rendering it", () => {
    expect(projectFeedState(payload({ change_reason: "nova_not_subscribed_806" })).changeReason).toBeNull();
  });

  it("drops a reason that is a raw broker sentence", () => {
    const feed = projectFeedState(payload({
      change_reason: "TokenException: Incorrect `api_key` or `access_token`.",
    }));

    expect(feed.changeReason).toBeNull();
    expect(JSON.stringify(feed)).not.toMatch(/api_key|access_token|TokenException/);
  });

  it("leaves the reason absent when the backend omits it", () => {
    expect(projectFeedState(payload()).changeReason).toBeNull();
  });

  it("drops an unrecognised unavailability reason", () => {
    expect(projectFeedState(payload({ state: "unavailable", reason: "kite_403" })).reason).toBeNull();
    expect(projectFeedState(payload({ state: "unavailable", reason: "not_entitled" })).reason)
      .toBe("not_entitled");
  });
});

describe("describeFeed — what the user is actually told", () => {
  const describe_ = (over) => describeFeed(projectFeedState(payload(over)));

  it("presents an available streaming feed as live", () => {
    const view = describe_({ state: "available", tier: "streaming" });

    expect(view.live).toBe(true);
    expect(view.label).toBe("Live");
  });

  it("presents an available delayed feed as delayed, not live", () => {
    const view = describe_({ state: "available", tier: "delayed" });

    expect(view.live).toBe(false);
    expect(view.label).toBe("Delayed");
  });

  it("never presents `recovering` as live", () => {
    const view = describe_({ state: "recovering", tier: null });

    expect(view.live).toBe(false);
    expect(view.label).not.toMatch(/live/i);
  });

  it("never presents `unavailable` as live", () => {
    const view = describe_({ state: "unavailable", tier: null, reason: "all_providers_down" });

    expect(view.live).toBe(false);
    expect(view.label).not.toMatch(/live/i);
  });

  it("gives `recovering` and `unavailable` different labels", () => {
    expect(describe_({ state: "recovering" }).label)
      .not.toBe(describe_({ state: "unavailable" }).label);
  });

  it("fabricates no percentage, countdown, ETA or broker name while recovering", () => {
    const view = describe_({ state: "recovering", tier: null });
    const text = `${view.label} ${view.detail}`;

    expect(text).not.toMatch(/\d+\s*%|\d+\s*(second|minute|sec|min)|ETA|retry in/i);
    expect(text).not.toMatch(/zerodha|upstox|angel|fyers|yahoo|kite/i);
  });

  it.each([
    ["entitlement_refused", /needs attention/i],
    ["session_expired", /expired/i],
    ["feed_disconnected", /no longer connected|disconnected/i],
  ])("explains %s in the platform's own words", (wire, matcher) => {
    const view = describe_({ state: "unavailable", tier: null, change_reason: wire });

    expect(view.detail).toMatch(matcher);
  });

  it("tells a session expiry apart from an entitlement refusal", () => {
    const expired = describe_({ state: "unavailable", change_reason: "session_expired" }).detail;
    const refused = describe_({ state: "unavailable", change_reason: "entitlement_refused" }).detail;

    expect(expired).not.toBe(refused);
  });

  it("tells a disconnection apart from a successful feed", () => {
    const gone = describe_({ state: "unavailable", change_reason: "feed_disconnected" });

    expect(gone.live).toBe(false);
    expect(gone.detail).not.toMatch(/connected successfully|live/i);
  });

  it("stays safe when the reason is unknown or missing", () => {
    const unknown = describe_({ state: "unavailable", change_reason: "surprise" });

    expect(unknown.live).toBe(false);
    expect(typeof unknown.detail).toBe("string");
    expect(unknown.detail).not.toMatch(/surprise/);
  });

  it("never crashes on a null projection", () => {
    const view = describeFeed(null);

    expect(view.live).toBe(false);
    expect(typeof view.label).toBe("string");
  });

  it("exposes no provider identity in any state it can render", () => {
    const blob = ["available", "recovering", "unavailable"].flatMap((state) =>
      [null, "entitlement_refused", "session_expired", "feed_disconnected"].map((r) =>
        JSON.stringify(describe_({ state, change_reason: r })),
      ),
    ).join(" ");

    expect(blob).not.toMatch(/zerodha|upstox|angel|fyers|yahoo|kite|alpha_vantage|provider/i);
  });
});
