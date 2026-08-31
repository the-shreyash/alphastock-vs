/**
 * `provider.status` ingestion (D5.14).
 *
 * The D5.13 audit found the store dropping the whole `provider` domain at
 * `applyEvent`'s `default: break`, so the backend's feed contract reached the
 * browser and went nowhere. These tests drive the store with the EXACT envelope
 * `services/realtime/event_bridge.py` builds from
 * `SourceManager.publish_status`, not a frontend-invented shape.
 *
 * Production failures these catch: the domain being dropped again by a future
 * refactor; a platform-wide broadcast overwriting the feed state of a user who
 * owns a promoted broker feed (the D4.5 defect, re-created in React); and one
 * account's entitlement refusal being shown to another account.
 */
import { useRealtimeStore, selectFeedState, selectFeedIsLive } from "../realtimeStore";

const store = () => useRealtimeStore.getState();

/**
 * The bridged envelope. `provider.status` is not in `DOMAIN_CHANNEL`, so
 * `resolve_channel` falls through to the domain name — channel "provider".
 */
const statusEvent = (data, timestamp = "2026-01-15T09:30:00.000Z") => ({
  type: "event",
  event: "provider.status",
  channel: "provider",
  data,
  timestamp,
});

const AVAILABLE = {
  state: "available",
  tier: "streaming",
  reason: null,
  capabilities: ["indices", "quotes", "ticks"],
  previous_tier: "delayed",
};

beforeEach(() => {
  useRealtimeStore.getState().reset();
});

describe("the provider domain is consumed, not dropped", () => {
  it("starts with no feed state rather than a fabricated one", () => {
    expect(selectFeedState(store())).toBeNull();
    expect(selectFeedIsLive(store())).toBe(false);
  });

  it("records an available feed from the real envelope", () => {
    store().applyEvent(statusEvent(AVAILABLE));

    const feed = selectFeedState(store());
    expect(feed.state).toBe("available");
    expect(feed.tier).toBe("streaming");
    expect(selectFeedIsLive(store())).toBe(true);
  });

  it("records `recovering` without ever reporting it as live", () => {
    store().applyEvent(statusEvent({ ...AVAILABLE, state: "recovering", tier: null }));

    expect(selectFeedState(store()).state).toBe("recovering");
    expect(selectFeedIsLive(store())).toBe(false);
  });

  it("records `unavailable` distinguishably from `recovering`", () => {
    store().applyEvent(statusEvent({
      ...AVAILABLE, state: "unavailable", tier: null, reason: "all_providers_down",
    }));

    expect(selectFeedState(store()).state).toBe("unavailable");
    expect(selectFeedState(store()).reason).toBe("all_providers_down");
    expect(selectFeedIsLive(store())).toBe(false);
  });

  it("carries the change reason of a transition", () => {
    store().applyEvent(statusEvent({
      ...AVAILABLE, state: "unavailable", tier: null, reason: "not_entitled",
      previous_tier: "streaming", change_reason: "entitlement_refused",
    }));

    expect(selectFeedState(store()).changeReason).toBe("entitlement_refused");
    expect(selectFeedState(store()).previousTier).toBe("streaming");
  });

  it("survives a malformed provider event without wiping the last good state", () => {
    store().applyEvent(statusEvent(AVAILABLE));
    expect(() => store().applyEvent({ type: "event", event: "provider.status", channel: "provider" }))
      .not.toThrow();

    // A frame with no data at all is not evidence the feed changed.
    expect(selectFeedState(store()).state).toBe("available");
  });

  it("ignores a provider event that is not a status", () => {
    store().applyEvent(statusEvent(AVAILABLE));
    store().applyEvent({ ...statusEvent({ state: "unavailable" }), event: "provider.registered" });

    expect(selectFeedState(store()).state).toBe("available");
  });

  it("routes through the batched ingest path the socket actually uses", () => {
    store().applyMessages([statusEvent({ ...AVAILABLE, state: "recovering", tier: null })]);

    expect(selectFeedState(store()).state).toBe("recovering");
  });

  it("stamps the arrival time from the envelope", () => {
    store().applyEvent(statusEvent(AVAILABLE));

    expect(selectFeedState(store()).updatedAt).toBe("2026-01-15T09:30:00.000Z");
  });
});

describe("per-user isolation", () => {
  it("ignores a user-scoped event addressed to a different account", () => {
    store().setFeedIdentity("user-a");
    store().applyEvent(statusEvent(AVAILABLE));

    store().applyEvent(statusEvent({
      ...AVAILABLE, state: "unavailable", tier: null, reason: "not_entitled",
      change_reason: "entitlement_refused", user_id: "user-b",
    }));

    expect(selectFeedState(store()).state).toBe("available");
    expect(selectFeedState(store()).changeReason).toBeNull();
  });

  it("accepts a user-scoped event addressed to this account", () => {
    store().setFeedIdentity("user-a");
    store().applyEvent(statusEvent({
      ...AVAILABLE, state: "unavailable", tier: null, change_reason: "session_expired",
      user_id: "user-a",
    }));

    expect(selectFeedState(store()).changeReason).toBe("session_expired");
  });

  it("does not let a platform broadcast overwrite this user's own feed state", () => {
    // D4.5: a user promoted to a streaming broker feed is invisible in the
    // platform view. The platform view still says "delayed"; showing it to that
    // user is exactly the indicator bug D4.5 fixed on the backend.
    store().setFeedIdentity("user-a");
    store().applyEvent(statusEvent({ ...AVAILABLE, user_id: "user-a" }));

    store().applyEvent(statusEvent({ ...AVAILABLE, tier: "delayed", previous_tier: null }));

    expect(selectFeedState(store()).tier).toBe("streaming");
  });

  it("accepts a platform broadcast while the user owns no feed of their own", () => {
    store().setFeedIdentity("user-a");
    store().applyEvent(statusEvent({ ...AVAILABLE, tier: "delayed" }));

    expect(selectFeedState(store()).tier).toBe("delayed");
  });

  it("forgets one account's feed state when the identity changes", () => {
    store().setFeedIdentity("user-a");
    store().applyEvent(statusEvent({ ...AVAILABLE, user_id: "user-a" }));

    store().setFeedIdentity("user-b");

    expect(selectFeedState(store())).toBeNull();
  });

  it("drops the feed state on reset so a logout leaves nothing behind", () => {
    store().setFeedIdentity("user-a");
    store().applyEvent(statusEvent({ ...AVAILABLE, user_id: "user-a" }));
    store().reset();

    expect(selectFeedState(store())).toBeNull();
  });
});

describe("nothing sensitive survives ingestion", () => {
  it("keeps tokens, credentials and provider identity out of store state", () => {
    // A payload shaped like a regression: the backend forbids all of this on
    // `provider.status`, and the store must not become the place it leaks.
    store().applyEvent(statusEvent({
      ...AVAILABLE,
      state: "unavailable",
      change_reason: "entitlement_refused",
      provider: "brokerfeed:zerodha:user-a",
      broker: "zerodha",
      access_token: "tok_live_51H8sKqZ",
      api_key: "ak_live_9f2c",
      error: "TokenException: Incorrect `api_key` or `access_token`.",
      redis_key: "sa:health:zerodha",
    }));

    const blob = JSON.stringify(useRealtimeStore.getState().feedState);
    for (const secret of [
      "tok_live_51H8sKqZ", "ak_live_9f2c", "zerodha", "brokerfeed",
      "TokenException", "api_key", "sa:health", "redis",
    ]) {
      expect(blob).not.toMatch(new RegExp(secret, "i"));
    }
  });
});
