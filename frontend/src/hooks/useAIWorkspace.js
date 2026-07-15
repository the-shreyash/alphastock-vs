import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "sonner";
import api from "../services/api";
import { useRealtimeStore } from "../store/realtimeStore";

const WELCOME = {
  role: "assistant",
  content:
    "Hi! I'm StockAssist AI. I remember your goals, portfolio and past chats. " +
    "Ask me about a stock, your portfolio, or a strategy — I'll always explain the why, the risks and what to watch next.",
};

/**
 * useAIWorkspace — manages Conversation History + chat state for the AI Workspace.
 *
 * Encapsulates all /api/ai/conversations + /api/chat calls so the AIAssistant
 * page stays presentational. Keeps the conversation list, the active session,
 * and the current thread's messages in sync as the user chats, switches
 * sessions, starts a new chat, or deletes one.
 */
export default function useAIWorkspace() {
  const [conversations, setConversations] = useState([]);
  const [loadingConvos, setLoadingConvos] = useState(true);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([WELCOME]);
  const [sending, setSending] = useState(false);
  // Correlation id for the request in flight, so the live AI step timeline
  // (ai.run.* / ai.step over WebSocket) can be matched to it (Sprint R7).
  const [activeRunId, setActiveRunId] = useState(null);
  const initialised = useRef(false);

  const refreshConversations = useCallback(async () => {
    try {
      const { data } = await api.get("/ai/conversations");
      setConversations(Array.isArray(data) ? data : []);
      return data;
    } catch {
      setConversations([]);
      return [];
    } finally {
      setLoadingConvos(false);
    }
  }, []);

  const loadThread = useCallback(async (sessionId) => {
    setActiveId(sessionId);
    try {
      const { data } = await api.get("/chat/history", { params: { session_id: sessionId } });
      setMessages(data.length ? data.map((m) => ({ role: m.role, content: m.content })) : [WELCOME]);
    } catch {
      setMessages([WELCOME]);
    }
  }, []);

  // On mount: load conversations and open the most recent (or a fresh session).
  useEffect(() => {
    if (initialised.current) return;
    initialised.current = true;
    (async () => {
      const convos = await refreshConversations();
      if (convos && convos.length) {
        loadThread(convos[0].session_id);
      } else {
        setActiveId(`chat-${Date.now()}`);
      }
    })();
  }, [refreshConversations, loadThread]);

  const newChat = useCallback(async () => {
    try {
      const { data } = await api.post("/ai/conversations");
      setActiveId(data.session_id);
    } catch {
      setActiveId(`chat-${Date.now()}`);
    }
    setMessages([WELCOME]);
  }, []);

  const selectConversation = useCallback((sessionId) => {
    if (sessionId === activeId) return;
    loadThread(sessionId);
  }, [activeId, loadThread]);

  const deleteConversation = useCallback(async (sessionId) => {
    try {
      await api.delete(`/ai/conversations/${sessionId}`);
      const remaining = await refreshConversations();
      if (sessionId === activeId) {
        if (remaining && remaining.length) loadThread(remaining[0].session_id);
        else newChat();
      }
      toast.success("Conversation deleted");
    } catch {
      toast.error("Could not delete conversation");
    }
  }, [activeId, refreshConversations, loadThread, newChat]);

  const send = useCallback(async (text) => {
    const msg = (text || "").trim();
    if (!msg || sending) return;
    const isFirstUserMessage = !messages.some((m) => m.role === "user");
    const runId = (crypto?.randomUUID?.() || `run-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setActiveRunId(runId);
    setSending(true);
    try {
      const { data } = await api.post("/chat", { message: msg, session_id: activeId, run_id: runId });
      setMessages((prev) => [...prev, { role: "assistant", content: data.response }]);
      // First exchange creates the session server-side — refresh the sidebar.
      if (isFirstUserMessage) refreshConversations();
    } catch {
      useRealtimeStore.getState().resolveAIRun(runId, "warning");
      setMessages((prev) => [...prev, { role: "assistant", content: "Sorry, I hit an error. Please try again." }]);
    } finally {
      setSending(false);
      setActiveRunId(null);
      // Settle + drop the run: the reply has arrived, so any step still
      // "running" (lost WS frames) must not stay animated, and the keyed
      // aiRuns map must not accumulate finished chat runs.
      const store = useRealtimeStore.getState();
      store.resolveAIRun(runId);
      store.clearAIRun(runId);
    }
  }, [sending, activeId, messages, refreshConversations]);

  return {
    conversations,
    loadingConvos,
    activeId,
    messages,
    sending,
    activeRunId,
    send,
    newChat,
    selectConversation,
    deleteConversation,
  };
}
