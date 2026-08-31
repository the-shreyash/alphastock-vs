/**
 * RealtimeProvider — the market-feed wiring (D5.14).
 *
 * The D5.13 audit found the feed contract reaching the browser and going
 * nowhere, and there were TWO independent reasons: the store dropped the
 * `provider` domain, and the socket never subscribed to the channel the
 * platform-scoped event is broadcast on. Fixing either alone changes nothing
 * observable, which is exactly why a unit test of the store cannot stand in for
 * this one — it drives the real provider, the real socket lifecycle, the real
 * batching window and the real store.
 */
import { render, act } from "@testing-library/react";
import { RealtimeProvider } from "../RealtimeProvider";
import { useRealtimeStore, selectFeedState } from "../../store/realtimeStore";

let mockUser = { _id: "user-a", email: "trader@example.com" };
jest.mock("../AuthContext", () => ({
  useAuth: () => ({ user: mockUser }),
}));

const socket = () => global.WebSocket.instances.at(-1);

/** Drive the mock socket through a real open handshake. */
const open = () => act(() => { socket().readyState = 1; socket().onopen?.(); });

const deliver = (msg) =>
  act(() => {
    socket().onmessage?.({ data: JSON.stringify(msg) });
    jest.advanceTimersByTime(50); // the provider's 40ms batch window
  });

const STATUS = {
  type: "event",
  event: "provider.status",
  channel: "provider",
  data: {
    state: "available", tier: "streaming", reason: null,
    capabilities: ["indices", "quotes"], previous_tier: "delayed",
  },
  timestamp: "2026-01-15T09:30:00.000Z",
};

beforeEach(() => {
  jest.useFakeTimers();
  mockUser = { _id: "user-a", email: "trader@example.com" };
  useRealtimeStore.getState().reset();
});

afterEach(() => {
  jest.useRealTimers();
});

it("subscribes to the channel the platform feed state is broadcast on", () => {
  render(<RealtimeProvider><div /></RealtimeProvider>);
  const sent = jest.spyOn(socket(), "send");
  open();

  const subscribe = sent.mock.calls
    .map(([raw]) => JSON.parse(raw))
    .find((m) => m.type === "subscribe");

  // `provider.status` has no DOMAIN_CHANNEL entry on the backend bridge, so it
  // is broadcast on the channel named after its own domain.
  expect(subscribe.channels).toContain("provider");
});

it("delivers a platform feed event end to end: socket → store", () => {
  render(<RealtimeProvider><div /></RealtimeProvider>);
  open();
  deliver(STATUS);

  expect(selectFeedState(useRealtimeStore.getState())).toMatchObject({
    state: "available", tier: "streaming", scope: "platform",
  });
});

it("delivers a user-scoped feed change end to end", () => {
  render(<RealtimeProvider><div /></RealtimeProvider>);
  open();
  deliver({
    ...STATUS,
    data: {
      ...STATUS.data, state: "unavailable", tier: null, reason: "not_entitled",
      previous_tier: "streaming", change_reason: "entitlement_refused",
      user_id: "user-a",
    },
  });

  expect(selectFeedState(useRealtimeStore.getState())).toMatchObject({
    state: "unavailable", tier: null, changeReason: "entitlement_refused", scope: "user",
  });
});

it("binds the feed state to the signed-in account", () => {
  render(<RealtimeProvider><div /></RealtimeProvider>);

  expect(useRealtimeStore.getState().feedUserId).toBe("user-a");
});

it("drops the previous account's feed state when the user changes", () => {
  const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);
  open();
  deliver({ ...STATUS, data: { ...STATUS.data, user_id: "user-a" } });
  expect(selectFeedState(useRealtimeStore.getState())).not.toBeNull();

  mockUser = { _id: "user-b" };
  act(() => { rerender(<RealtimeProvider><div /></RealtimeProvider>); });

  expect(selectFeedState(useRealtimeStore.getState())).toBeNull();
});

it("clears the feed state on sign-out rather than leaving it on screen", () => {
  const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);
  open();
  deliver(STATUS);

  mockUser = null;
  act(() => { rerender(<RealtimeProvider><div /></RealtimeProvider>); });

  expect(selectFeedState(useRealtimeStore.getState())).toBeNull();
  expect(useRealtimeStore.getState().feedUserId).toBeNull();
});

it("starts no timer of its own to approximate recovery", () => {
  render(<RealtimeProvider><div /></RealtimeProvider>);
  open();
  deliver({ ...STATUS, data: { ...STATUS.data, state: "recovering", tier: null } });

  // `recovering` ends when the backend says so. Letting time pass must not
  // move the state — no countdown, no self-promotion back to available.
  act(() => { jest.advanceTimersByTime(120000); });

  expect(selectFeedState(useRealtimeStore.getState()).state).toBe("recovering");
});
