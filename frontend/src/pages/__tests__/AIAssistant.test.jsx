/**
 * AI Workspace.
 *
 * The AI is the product's core, so its failure modes matter as much as its
 * successes. Production failures these catch: a chat that stays stuck on the
 * thinking indicator after the request fails; a lost reply leaving the thread
 * with the user's message and nothing else; an empty conversation list
 * rendering as a blank panel; and a send button that lets a second request go
 * out while the first is still running.
 *
 * PRODUCT RULE ENFORCED HERE: an AI failure must be *stated*. Silence is the
 * one response the workspace may never give.
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import AIAssistant from "../AIAssistant";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
  realtimeStore,
  HTTP,
  pending,
  testConversation,
  testAIResponse,
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

/**
 * @param {object} opts
 * @param {Array}  [opts.conversations] the /ai/conversations payload
 * @param {Array}  [opts.history]       the /chat/history payload
 */
async function renderWorkspace({ conversations = [], history = [] } = {}) {
  mock.onGet("/ai/conversations").reply(HTTP.OK, conversations);
  mock.onGet("/chat/history").reply(HTTP.OK, history);
  stubRemainingWith(mock, []);

  const utils = renderWithProviders(<AIAssistant />, { route: "/assistant" });
  await screen.findByTestId("ai-workspace-page");
  return utils;
}

describe("initial state", () => {
  it("greets the user and explains what the assistant can do", async () => {
    await renderWorkspace();

    expect(await screen.findByText(/I'm StockAssist AI/i)).toBeInTheDocument();
  });

  it("offers starter prompts while the thread is empty", async () => {
    await renderWorkspace();

    expect(screen.getByRole("button", { name: /explain rsi in simple terms/i })).toBeInTheDocument();
  });

  it("keeps the send button disabled until something has been typed", async () => {
    await renderWorkspace();

    expect(screen.getByTestId("chat-send-btn")).toBeDisabled();
  });

  it("shows the empty state when the user has no conversation history", async () => {
    await renderWorkspace({ conversations: [] });

    const sidebar = await screen.findByTestId("conversation-sidebar");
    await waitFor(() => expect(within(sidebar).getByText(/no conversations yet/i)).toBeInTheDocument());
  });
});

describe("conversation history", () => {
  it("lists previous conversations", async () => {
    await renderWorkspace({ conversations: [testConversation] });

    const sidebar = await screen.findByTestId("conversation-sidebar");
    await waitFor(() => expect(within(sidebar).getByText(testConversation.title)).toBeInTheDocument());
  });

  it("opens the most recent conversation on arrival", async () => {
    await renderWorkspace({
      conversations: [testConversation],
      history: [
        { role: "user", content: "What about TESTCO?" },
        { role: "assistant", content: "TESTCO is consolidating." },
      ],
    });

    expect(await screen.findByText("What about TESTCO?")).toBeInTheDocument();
    expect(await screen.findByText("TESTCO is consolidating.")).toBeInTheDocument();
  });

  it("falls back to the welcome message when the history cannot be loaded", async () => {
    mock.onGet("/ai/conversations").reply(HTTP.OK, [testConversation]);
    mock.onGet("/chat/history").reply(HTTP.SERVER_ERROR, {});
    stubRemainingWith(mock, []);

    renderWithProviders(<AIAssistant />, { route: "/assistant" });

    expect(await screen.findByText(/I'm StockAssist AI/i)).toBeInTheDocument();
  });

  it("degrades to an empty list when the conversation list cannot be loaded", async () => {
    mock.onGet("/ai/conversations").reply(HTTP.SERVER_ERROR, {});
    stubRemainingWith(mock, []);

    renderWithProviders(<AIAssistant />, { route: "/assistant" });

    const sidebar = await screen.findByTestId("conversation-sidebar");
    await waitFor(() => expect(within(sidebar).getByText(/no conversations yet/i)).toBeInTheDocument());
  });
});

describe("sending a message", () => {
  it("posts the message with the session and a run correlation id", async () => {
    mock.onPost("/chat").reply(HTTP.OK, testAIResponse);
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "What is the outlook for TESTCO?");
    await user.click(screen.getByTestId("chat-send-btn"));

    await waitFor(() => expect(mock.history.post.filter((r) => r.url === "/chat")).toHaveLength(1));
    const body = JSON.parse(mock.history.post.find((r) => r.url === "/chat").data);
    expect(body.message).toBe("What is the outlook for TESTCO?");
    // run_id is what pairs the live ai.step WebSocket frames with this request.
    expect(body.run_id).toBeTruthy();
    expect(body).toHaveProperty("session_id");
  });

  it("shows the user's message immediately, before the reply arrives", async () => {
    mock.onPost("/chat").reply(() => pending());
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Optimistic echo");
    await user.click(screen.getByTestId("chat-send-btn"));

    expect(await screen.findByText("Optimistic echo")).toBeInTheDocument();
  });

  it("renders the assistant's reply", async () => {
    mock.onPost("/chat").reply(HTTP.OK, testAIResponse);
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Why?");
    await user.click(screen.getByTestId("chat-send-btn"));

    expect(await screen.findByText(/momentum breakout/i)).toBeInTheDocument();
  });

  it("clears the composer once the message is sent", async () => {
    mock.onPost("/chat").reply(HTTP.OK, testAIResponse);
    const { user } = await renderWorkspace();

    const input = screen.getByTestId("chat-input");
    await user.type(input, "Clear me");
    await user.click(screen.getByTestId("chat-send-btn"));

    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("sends on Enter", async () => {
    mock.onPost("/chat").reply(HTTP.OK, testAIResponse);
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Sent with the keyboard{Enter}");

    await waitFor(() => expect(mock.history.post.filter((r) => r.url === "/chat")).toHaveLength(1));
  });

  it("sends a starter prompt when one is clicked", async () => {
    mock.onPost("/chat").reply(HTTP.OK, testAIResponse);
    const { user } = await renderWorkspace();

    await user.click(screen.getByRole("button", { name: /explain rsi in simple terms/i }));

    await waitFor(() => expect(mock.history.post.filter((r) => r.url === "/chat")).toHaveLength(1));
    expect(JSON.parse(mock.history.post.find((r) => r.url === "/chat").data).message)
      .toMatch(/explain rsi/i);
  });

  it("ignores a whitespace-only message", async () => {
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "   ");
    await user.keyboard("{Enter}");

    expect(mock.history.post.filter((r) => r.url === "/chat")).toHaveLength(0);
  });
});

describe("while the AI is working", () => {
  it("shows a working indicator rather than an idle screen", async () => {
    // The AI must never look idle while it is thinking. With no live run
    // streamed yet (socket offline, or the first paint), the fallback pulse is
    // what carries that signal.
    mock.onPost("/chat").reply(() => pending());
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Take your time");
    await user.click(screen.getByTestId("chat-send-btn"));

    expect(await screen.findByTestId("ai-thinking-dots")).toBeInTheDocument();
  });

  it("replaces the pulse with the real stages once the backend streams them", async () => {
    // The run id the page generated is what pairs the ai.step frames to this
    // request; pushing a matching run through the store proves that wiring.
    mock.onPost("/chat").reply(() => pending());
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Show your work");
    await user.click(screen.getByTestId("chat-send-btn"));
    await screen.findByTestId("ai-thinking-dots");

    const runId = JSON.parse(mock.history.post.find((r) => r.url === "/chat").data).run_id;
    act(() => {
      realtimeStore.getState().applyMessages([
        {
          type: "event",
          event: "ai.run.started",
          data: { run_id: runId, user_id: "u_test_000000000001", steps: ["Fetching quote", "Debating"] },
        },
        {
          type: "event",
          event: "ai.step",
          data: { run_id: runId, user_id: "u_test_000000000001", index: 0, status: "running", label: "Fetching quote" },
        },
      ]);
    });

    expect(await screen.findByTestId("ai-step-timeline")).toBeInTheDocument();
    expect(screen.getByText("Fetching quote")).toBeInTheDocument();
  });

  it("locks the composer so a second request cannot overlap the first", async () => {
    mock.onPost("/chat").reply(() => pending());
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "First");
    await user.click(screen.getByTestId("chat-send-btn"));

    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeDisabled());
    expect(screen.getByTestId("chat-send-btn")).toBeDisabled();
    expect(mock.history.post.filter((r) => r.url === "/chat")).toHaveLength(1);
  });
});

describe("AI failures", () => {
  it.each([
    ["a server fault", HTTP.SERVER_ERROR],
    ["a rate limit", HTTP.RATE_LIMITED],
    ["an expired session", HTTP.UNAUTHORIZED],
  ])("tells the user the request failed on %s", async (_label, status) => {
    mock.onPost("/chat").reply(status, { detail: "AI provider unavailable" });
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Anything");
    await user.click(screen.getByTestId("chat-send-btn"));

    expect(await screen.findByText(/hit an error/i)).toBeInTheDocument();
  });

  it("tells the user the request failed when the backend is unreachable", async () => {
    mock.onPost("/chat").networkError();
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Anything");
    await user.click(screen.getByTestId("chat-send-btn"));

    expect(await screen.findByText(/hit an error/i)).toBeInTheDocument();
  });

  it("releases the composer after a failure so the user can retry", async () => {
    mock.onPost("/chat").reply(HTTP.SERVER_ERROR, {});
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Anything");
    await user.click(screen.getByTestId("chat-send-btn"));

    await screen.findByText(/hit an error/i);
    expect(screen.getByTestId("chat-input")).toBeEnabled();
    expect(screen.queryByTestId("ai-thinking-dots")).not.toBeInTheDocument();
  });

  it("keeps the thinking indicator from outliving the request", async () => {
    // A stuck spinner is indistinguishable from a hung backend.
    mock.onPost("/chat").reply(HTTP.OK, testAIResponse);
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Quick one");
    await user.click(screen.getByTestId("chat-send-btn"));

    await screen.findByText(/momentum breakout/i);
    expect(screen.queryByTestId("ai-thinking-dots")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ai-step-timeline")).not.toBeInTheDocument();
  });

  it("renders an empty AI response without crashing the thread", async () => {
    mock.onPost("/chat").reply(HTTP.OK, { response: "", session_id: "s" });
    const { user } = await renderWorkspace();

    await user.type(screen.getByTestId("chat-input"), "Say nothing");
    await user.click(screen.getByTestId("chat-send-btn"));

    // The thread survives and the composer is usable again.
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeEnabled());
    expect(screen.getByTestId("ai-workspace-page")).toBeInTheDocument();
  });
});

describe("workspace tabs", () => {
  it("switches between the assistant's modes", async () => {
    const { user } = await renderWorkspace();

    await user.click(screen.getByRole("button", { name: "Learning" }));

    await waitFor(() => expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument());
  });
});

describe("accessibility baseline", () => {
  it("names the composer and its send control", async () => {
    // Regression guard (PH3.2 defect FE-005): the send button was icon-only,
    // so a screen reader announced it as an unlabelled "button".
    await renderWorkspace();

    expect(screen.getByTestId("chat-input")).toHaveAccessibleName();
    expect(screen.getByTestId("chat-send-btn")).toHaveAccessibleName(/send/i);
  });

  it("keeps the tab bar reachable by keyboard", async () => {
    const { user } = await renderWorkspace();

    const learning = screen.getByRole("button", { name: "Learning" });
    learning.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument());
  });
});
