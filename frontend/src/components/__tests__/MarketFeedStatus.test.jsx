/**
 * MarketFeedStatus (D5.14) — the visible half of the consumer feed contract.
 *
 * The pill next to the connection pill answers a different question from it:
 * ConnectionStatus says whether the SOCKET is up, this says whether the MARKET
 * FEED is serving usable data. Conflating them is what let a user watch a
 * "Live" badge over prices no provider had delivered.
 *
 * Driven through the real store with the real backend envelope, so a change to
 * either the event shape or the store's routing breaks these rather than
 * passing against a frontend-only fixture.
 */
import { render, screen } from "@testing-library/react";
import MarketFeedStatus from "../layout/MarketFeedStatus";
import { useRealtimeStore } from "../../store/realtimeStore";

const statusEvent = (data) => ({
  type: "event",
  event: "provider.status",
  channel: "provider",
  data,
  timestamp: "2026-01-15T09:30:00.000Z",
});

const BASE = {
  state: "available",
  tier: "streaming",
  reason: null,
  capabilities: ["indices", "quotes"],
  previous_tier: "delayed",
};

const publish = (over = {}) =>
  useRealtimeStore.getState().applyEvent(statusEvent({ ...BASE, ...over }));

beforeEach(() => {
  useRealtimeStore.getState().reset();
});

describe("what the badge shows", () => {
  it("renders nothing before the backend has said anything", () => {
    render(<MarketFeedStatus />);

    expect(screen.queryByTestId("market-feed-status")).not.toBeInTheDocument();
  });

  it("shows a streaming available feed as live", () => {
    publish();
    render(<MarketFeedStatus />);

    const pill = screen.getByTestId("market-feed-status");
    expect(pill).toHaveAttribute("data-feed-state", "available");
    expect(pill).toHaveAttribute("data-feed-live", "true");
    expect(pill).toHaveTextContent(/live/i);
  });

  it("shows a delayed available feed as delayed and not live", () => {
    publish({ tier: "delayed" });
    render(<MarketFeedStatus />);

    const pill = screen.getByTestId("market-feed-status");
    expect(pill).toHaveAttribute("data-feed-live", "false");
    expect(pill).toHaveTextContent(/delayed/i);
    expect(pill).not.toHaveTextContent(/^live$/i);
  });

  it("does NOT render `recovering` as live", () => {
    publish({ state: "recovering", tier: null });
    render(<MarketFeedStatus />);

    const pill = screen.getByTestId("market-feed-status");
    expect(pill).toHaveAttribute("data-feed-state", "recovering");
    expect(pill).toHaveAttribute("data-feed-live", "false");
    expect(pill).not.toHaveTextContent(/live/i);
  });

  it("shows no tier at all while recovering", () => {
    publish({ state: "recovering", tier: null });
    render(<MarketFeedStatus />);

    const pill = screen.getByTestId("market-feed-status");
    expect(pill).not.toHaveTextContent(/streaming|delayed/i);
  });

  it("does not fabricate a tier when a recovering payload carries one", () => {
    publish({ state: "recovering", tier: "streaming" });
    render(<MarketFeedStatus />);

    expect(screen.getByTestId("market-feed-status")).not.toHaveTextContent(/live|streaming/i);
  });

  it("keeps `unavailable` visually distinguishable from `recovering`", () => {
    publish({ state: "recovering", tier: null });
    const { unmount } = render(<MarketFeedStatus />);
    const recovering = screen.getByTestId("market-feed-status").textContent;
    unmount();

    useRealtimeStore.getState().reset();
    publish({ state: "unavailable", tier: null, reason: "all_providers_down" });
    render(<MarketFeedStatus />);

    expect(screen.getByTestId("market-feed-status").textContent).not.toBe(recovering);
    expect(screen.getByTestId("market-feed-status")).toHaveAttribute("data-feed-state", "unavailable");
  });

  it("invents no countdown, percentage or ETA while recovering", () => {
    publish({ state: "recovering", tier: null });
    render(<MarketFeedStatus />);

    const text = screen.getByTestId("market-feed-status").parentElement.textContent;
    expect(text).not.toMatch(/\d+\s*%|\d+\s*(s|sec|second|min|minute)\b|ETA|retrying in/i);
  });
});

describe("feed-change reasons", () => {
  it.each([
    ["entitlement_refused", /needs attention/i],
    ["session_expired", /expired/i],
    ["feed_disconnected", /no longer connected|disconnected/i],
  ])("explains %s safely", (reason, matcher) => {
    publish({ state: "unavailable", tier: null, change_reason: reason, previous_tier: "streaming" });
    render(<MarketFeedStatus />);

    expect(screen.getByTestId("market-feed-detail")).toHaveTextContent(matcher);
  });

  it("does not describe a session expiry as an entitlement refusal", () => {
    publish({ state: "unavailable", tier: null, change_reason: "session_expired" });
    const { unmount } = render(<MarketFeedStatus />);
    const expired = screen.getByTestId("market-feed-detail").textContent;
    unmount();

    useRealtimeStore.getState().reset();
    publish({ state: "unavailable", tier: null, change_reason: "entitlement_refused" });
    render(<MarketFeedStatus />);

    expect(screen.getByTestId("market-feed-detail").textContent).not.toBe(expired);
  });

  it("does not describe a disconnection as a live feed", () => {
    publish({ state: "unavailable", tier: null, change_reason: "feed_disconnected" });
    render(<MarketFeedStatus />);

    expect(screen.getByTestId("market-feed-status")).toHaveAttribute("data-feed-live", "false");
    expect(screen.getByTestId("market-feed-detail")).not.toHaveTextContent(/live/i);
  });

  it("renders an unknown reason as nothing at all rather than as raw text", () => {
    publish({ state: "unavailable", tier: null, change_reason: "nova_not_subscribed_806" });
    render(<MarketFeedStatus />);

    expect(document.body.textContent).not.toMatch(/nova_not_subscribed_806/);
    expect(screen.getByTestId("market-feed-status")).toBeInTheDocument();
  });

  it("does not crash when the reason is missing", () => {
    publish({ state: "unavailable", tier: null });

    expect(() => render(<MarketFeedStatus />)).not.toThrow();
    expect(screen.getByTestId("market-feed-status")).toBeInTheDocument();
  });
});

describe("nothing sensitive is rendered", () => {
  it("renders no broker identity, credential or transport internal", () => {
    useRealtimeStore.getState().applyEvent(statusEvent({
      ...BASE,
      state: "unavailable",
      tier: null,
      change_reason: "entitlement_refused",
      provider: "brokerfeed:zerodha:user-a",
      broker: "zerodha",
      access_token: "tok_live_51H8sKqZ",
      api_key: "ak_live_9f2c",
      error: "TokenException: Incorrect `api_key` or `access_token`.",
      ws_url: "wss://ws.kite.trade?api_key=ak_live_9f2c",
      redis_key: "sa:health:zerodha",
    }));
    render(<MarketFeedStatus />);

    const html = document.body.innerHTML;
    for (const secret of [
      "tok_live_51H8sKqZ", "ak_live_9f2c", "zerodha", "brokerfeed", "kite",
      "TokenException", "wss://", "redis", "sa:health",
    ]) {
      expect(html).not.toMatch(new RegExp(secret, "i"));
    }
  });

  it("names no provider in any state it can render", () => {
    for (const state of ["available", "recovering", "unavailable"]) {
      useRealtimeStore.getState().reset();
      publish({ state, tier: state === "available" ? "streaming" : null });
      const { unmount } = render(<MarketFeedStatus />);
      expect(document.body.textContent)
        .not.toMatch(/zerodha|upstox|angel one|fyers|yahoo|kite|alpha vantage/i);
      unmount();
    }
  });
});
