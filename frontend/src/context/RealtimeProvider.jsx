/**
 * RealtimeProvider (Sprint R3).
 *
 * Owns the ONE WebSocket per user (fixes the pre-R3 "socket per page" problem).
 * It does not expose a React context value — components read live state from the
 * Zustand `useRealtimeStore`. This component only manages the socket lifecycle
 * and writes into that store.
 *
 * Responsibilities:
 *   - Connect only for an authenticated user (no anonymous sockets).
 *   - On open: subscribe to the channels the app needs and expose an imperative
 *     `send` on the store.
 *   - Route inbound messages: the R2 `event` envelope → `applyEvent`, every
 *     legacy flat type → `applyLegacy`.
 *   - Connection state machine: connecting → live → reconnecting → offline.
 *   - 30s heartbeat (`ping`/`pong`); reconnect if a pong is missed.
 *   - Exponential backoff with jitter on reconnect (reset on a clean open).
 */
import { useEffect, useRef } from "react";
import { useAuth } from "./AuthContext";
import { useRealtimeStore } from "../store/realtimeStore";

const WS_URL = process.env.REACT_APP_BACKEND_URL
  ?.replace("https://", "wss://")
  .replace("http://", "ws://");

// Channels this app consumes. Per-user events (notifications, per-user
// portfolio/broker) are delivered regardless of subscription; public channel
// broadcasts (market indices, sectors, scanner, news) require this subscribe.
const CHANNELS = [
  "market",
  "sectors",
  "scanner",
  "news",
  "notifications",
  "portfolio",
  "trades",
  "watchlist",
  "ai",
  "broker",
];

const HEARTBEAT_MS = 30000; // send a ping every 30s
const PONG_TIMEOUT_MS = 10000; // reconnect if no pong within 10s of a ping
const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 30000;

function isAnon(id) {
  return !id || id === "anonymous" || id === "anon";
}

export function RealtimeProvider({ children }) {
  const { user } = useAuth();
  const userId = user?._id || user?.id || "";

  const wsRef = useRef(null);
  const reconnectRef = useRef(null);
  const heartbeatRef = useRef(null);
  const pongTimerRef = useRef(null);
  const attemptRef = useRef(0);
  const closedByUsRef = useRef(false);

  useEffect(() => {
    const store = useRealtimeStore.getState();

    if (!WS_URL || isAnon(userId)) {
      store.setConnection("offline");
      return undefined;
    }

    const clearTimers = () => {
      if (reconnectRef.current) { clearTimeout(reconnectRef.current); reconnectRef.current = null; }
      if (heartbeatRef.current) { clearInterval(heartbeatRef.current); heartbeatRef.current = null; }
      if (pongTimerRef.current) { clearTimeout(pongTimerRef.current); pongTimerRef.current = null; }
    };

    const scheduleReconnect = () => {
      if (closedByUsRef.current) return;
      const attempt = attemptRef.current;
      const backoff = Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_MAX_MS);
      const jitter = Math.random() * 0.3 * backoff; // ±30% to avoid thundering herd
      attemptRef.current = attempt + 1;
      useRealtimeStore.getState().setConnection("reconnecting");
      reconnectRef.current = setTimeout(connect, backoff + jitter);
    };

    const startHeartbeat = (ws) => {
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
      heartbeatRef.current = setInterval(() => {
        if (ws.readyState !== WebSocket.OPEN) return;
        try { ws.send(JSON.stringify({ type: "ping" })); } catch { /* noop */ }
        // Expect a pong before the timeout; otherwise force a reconnect.
        if (pongTimerRef.current) clearTimeout(pongTimerRef.current);
        pongTimerRef.current = setTimeout(() => {
          try { ws.close(); } catch { /* onclose handles reconnect */ }
        }, PONG_TIMEOUT_MS);
      }, HEARTBEAT_MS);
    };

    function connect() {
      const s = useRealtimeStore.getState();
      s.setConnection("connecting");
      let ws;
      try {
        ws = new WebSocket(`${WS_URL}/api/ws?user_id=${userId}`);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0; // reset backoff on a clean connection
        const st = useRealtimeStore.getState();
        st.setConnection("live", { lastPongAt: Date.now() });
        st.setSend((payload) => {
          if (ws.readyState === WebSocket.OPEN) {
            try { ws.send(JSON.stringify(payload)); } catch { /* noop */ }
          }
        });
        try { ws.send(JSON.stringify({ type: "subscribe", channels: CHANNELS })); } catch { /* noop */ }
        startHeartbeat(ws);
      };

      ws.onmessage = (event) => {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        const st = useRealtimeStore.getState();
        if (msg.type === "pong") {
          if (pongTimerRef.current) { clearTimeout(pongTimerRef.current); pongTimerRef.current = null; }
          st.setConnection("live", { lastPongAt: Date.now() });
          return;
        }
        if (msg.type === "event") {
          st.applyEvent(msg);
        } else {
          st.applyLegacy(msg);
        }
      };

      ws.onclose = () => {
        if (heartbeatRef.current) { clearInterval(heartbeatRef.current); heartbeatRef.current = null; }
        if (pongTimerRef.current) { clearTimeout(pongTimerRef.current); pongTimerRef.current = null; }
        useRealtimeStore.getState().setSend(null);
        if (closedByUsRef.current) {
          useRealtimeStore.getState().setConnection("offline");
          return;
        }
        scheduleReconnect();
      };

      ws.onerror = () => {
        try { ws.close(); } catch { /* onclose handles it */ }
      };
    }

    closedByUsRef.current = false;
    attemptRef.current = 0;
    connect();

    return () => {
      closedByUsRef.current = true;
      clearTimers();
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) { try { ws.close(); } catch { /* noop */ } }
      useRealtimeStore.getState().setSend(null);
      useRealtimeStore.getState().setConnection("offline");
    };
  }, [userId]);

  return children;
}
