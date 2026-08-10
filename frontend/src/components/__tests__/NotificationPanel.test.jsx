/**
 * Notification panel.
 *
 * Production failures these catch: an unread badge that never clears because
 * "mark read" updated the screen but not the server; a live push duplicating a
 * notification already in the list; and an empty list rendering as a blank
 * drawer with no explanation.
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import NotificationPanel from "../notifications/NotificationPanel";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
  realtimeStore,
  HTTP,
  pending,
  testNotification,
} from "../../test-utils";

let mock;

beforeEach(() => {
  mock = installApiMock();
  mockAuthenticatedUser(mock);
  resetRealtimeStore();
});

afterEach(() => {
  mock.restore();
});

async function renderPanel(notifications = [], setupStubs) {
  mock.onGet("/notifications").reply(HTTP.OK, notifications);
  setupStubs?.(mock);
  stubRemainingWith(mock, []);

  const utils = renderWithProviders(<NotificationPanel onClose={jest.fn()} />, { route: "/dashboard" });
  await screen.findByTestId("notification-panel");
  return utils;
}

describe("loading state", () => {
  it("shows placeholders rather than claiming there is nothing", async () => {
    mock.onGet("/notifications").reply(() => pending());
    stubRemainingWith(mock, []);

    renderWithProviders(<NotificationPanel onClose={jest.fn()} />, { route: "/dashboard" });

    await screen.findByTestId("notification-panel");
    expect(screen.queryByText(/no notifications yet/i)).not.toBeInTheDocument();
  });
});

describe("empty state", () => {
  it("says there is nothing to read", async () => {
    await renderPanel([]);

    await waitFor(() => expect(screen.getByText(/no notifications yet/i)).toBeInTheDocument());
  });

  it("shows the empty state when the request fails, without crashing", async () => {
    mock.onGet("/notifications").reply(HTTP.SERVER_ERROR, {});
    stubRemainingWith(mock, []);

    renderWithProviders(<NotificationPanel onClose={jest.fn()} />, { route: "/dashboard" });

    await screen.findByTestId("notification-panel");
    await waitFor(() => expect(screen.getByText(/no notifications yet/i)).toBeInTheDocument());
  });
});

describe("populated state", () => {
  it("lists notifications with their message", async () => {
    await renderPanel([testNotification]);

    const item = await screen.findByTestId(`notification-${testNotification._id}`);
    expect(within(item).getByText(testNotification.title)).toBeInTheDocument();
    expect(within(item).getByText(testNotification.message)).toBeInTheDocument();
  });
});

describe("marking as read", () => {
  it("tells the server when a notification is opened", async () => {
    const { user } = await renderPanel([testNotification], (m) =>
      m.onPut(`/notifications/${testNotification._id}/read`).reply(HTTP.OK, {}),
    );

    await user.click(await screen.findByTestId(`notification-${testNotification._id}`));

    await waitFor(() =>
      expect(mock.history.put.filter((r) => r.url === `/notifications/${testNotification._id}/read`)).toHaveLength(1),
    );
  });

  it("decrements the unread badge exactly once per notification", async () => {
    // Clicking an already-read item must not drive the badge negative.
    act(() => realtimeStore.setState({ unreadCount: 1 }));
    const { user } = await renderPanel([testNotification], (m) =>
      m.onPut(`/notifications/${testNotification._id}/read`).reply(HTTP.OK, {}),
    );

    const item = await screen.findByTestId(`notification-${testNotification._id}`);
    await user.click(item);
    await user.click(item);

    await waitFor(() => expect(realtimeStore.getState().unreadCount).toBe(0));
  });

  it("marks every notification read in one call", async () => {
    const { user } = await renderPanel([testNotification], (m) =>
      m.onPut("/notifications/read-all").reply(HTTP.OK, {}),
    );

    await user.click(await screen.findByTestId("mark-all-read-btn"));

    await waitFor(() =>
      expect(mock.history.put.filter((r) => r.url === "/notifications/read-all")).toHaveLength(1),
    );
  });
});

describe("live pushes", () => {
  it("prepends a notification that arrives while the panel is open", async () => {
    await renderPanel([testNotification]);

    act(() => {
      realtimeStore.getState().applyMessages([
        {
          type: "event",
          event: "notification.created",
          data: {
            notification_id: "n_test_live_0001",
            type: "trade",
            title: "Stop loss hit on OTHERCO",
            message: "OTHERCO closed below your stop.",
            severity: "critical",
            timestamp: "2026-01-15T10:00:00.000Z",
          },
        },
      ]);
    });

    expect(await screen.findByText("Stop loss hit on OTHERCO")).toBeInTheDocument();
    // The existing one is still there — a push adds, it does not replace.
    expect(screen.getByText(testNotification.title)).toBeInTheDocument();
  });

  it("does not duplicate a notification already in the list", async () => {
    await renderPanel([testNotification]);

    act(() => {
      realtimeStore.getState().applyMessages([
        {
          type: "event",
          event: "notification.created",
          data: {
            notification_id: testNotification._id,
            type: testNotification.type,
            title: testNotification.title,
            message: testNotification.message,
            timestamp: testNotification.created_at,
          },
        },
      ]);
    });

    await waitFor(() => expect(screen.getAllByText(testNotification.title)).toHaveLength(1));
  });
});

describe("accessibility baseline", () => {
  it("names the panel's controls", async () => {
    await renderPanel([testNotification]);

    expect(screen.getByTestId("close-notifications-btn")).toHaveAccessibleName();
    expect(await screen.findByTestId("mark-all-read-btn")).toHaveAccessibleName();
  });
});
