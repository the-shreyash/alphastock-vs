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
 *   - Connection state machine: connecting → live → reconnecting → offline,
 *     plus `unauthenticated` for a socket that cannot be authenticated at all.
 *   - 30s heartbeat (`ping`/`pong`); reconnect if a pong is missed.
 *   - Exponential backoff with jitter on reconnect (reset on a clean open).
 *
 * D6.2 — WEBSOCKET AUTH LIFECYCLE. Two defects, opposite in shape:
 *
 *   D6.2-E — **every failed handshake was read as an auth failure.** D6.1
 *     classified "closed before it ever opened" as a credential problem, which
 *     is true when the server rejects the handshake — and equally true of a
 *     backend that is restarting, a proxy that is down, or a laptop that just
 *     lost Wi-Fi, because a browser reports ALL of them identically. (It has
 *     to: Starlette answers a pre-`accept()` close with an HTTP 403, so the
 *     browser never sees close code 1008 at all — it sees a failed handshake,
 *     code 1006.) So an ordinary outage burned the single auth retry and then
 *     stopped reconnecting **permanently**: realtime never came back until the
 *     tab was reloaded. The close code cannot tell these apart, so the client
 *     asks the question the close code cannot answer — it probes `/auth/me`,
 *     the same credential over a transport that reports real status codes.
 *
 *   D6.2-F — **an identity change could leave two live sockets.** The
 *     "are we still wanted?" flag was a ref shared across effect runs. Tearing
 *     down for user A set it, and A's in-flight `refreshSession().then(connect)`
 *     resolved *after* B's effect had already cleared it and connected — so it
 *     connected again, orphaning a socket that stayed open and kept writing
 *     into the shared store. Each effect run now owns a private `disposed`
 *     flag, and every handler additionally refuses to touch the store unless
 *     its socket is still the current one.
 */
import { useEffect, useRef } from "react";
import { useAuth } from "./AuthContext";
import api, { refreshSession, sessionIsDead } from "../services/api";
import { useRealtimeStore } from "../store/realtimeStore";

const WS_URL = process.env.REACT_APP_BACKEND_URL
  ?.replace("https://", "wss://")
  .replace("http://", "ws://");

// Channels this app consumes. Per-user events (notifications, per-user
// portfolio/broker) are delivered regardless of subscription; public channel
// broadcasts (market indices, sectors, scanner, news) require this subscribe.
// D6.1 / S6. `notifications`, `portfolio`, `trades`, `watchlist` and `broker`
// were requested here and are no longer: the server refuses them
// (`ConnectionManager.subscribe`) because they carry the private domains, and
// nothing is broadcast to them any more — per-user events are delivered by
// `send_to_user`, which needs no subscription at all. Asking for them was
// always a no-op dressed as a capability; now it is an honest one.
const CHANNELS = [
  "market",
  "sectors",
  "scanner",
  "news",
  "ai",
  // D5.14. `provider.status` has no entry in the bridge's DOMAIN_CHANNEL map,
  // so `resolve_channel` falls through to the domain name — the platform-scoped
  // feed state is broadcast on a channel literally called "provider". Without
  // this line the only feed events that ever arrived were the per-user ones
  // (which bypass subscriptions), so a user on the shared baseline saw nothing.
  "provider",
];

const HEARTBEAT_MS = 30000; // send a ping every 30s
const PONG_TIMEOUT_MS = 10000; // reconnect if no pong within 10s of a ping
const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 30000;
// Event batching (Sprint R9): inbound messages are queued for this window and
// applied as one burst via `applyMessages`, so a heartbeat cycle that emits a
// dozen events produces one coalesced store update instead of twelve.
// setTimeout (not requestAnimationFrame) so batching keeps working in
// background tabs. 40ms is imperceptible against 15s+ data cadences.
const BATCH_WINDOW_MS = 40;

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
  const batchRef = useRef([]); // queued inbound messages (Sprint R9 batching)
  const batchTimerRef = useRef(null);
  const lastIdentityRef = useRef(null); // previous socket identity (D6.1 / S8)
  // True once a socket has actually opened for the current attempt. A close
  // that happens with this still false means the connection never came up —
  // which may be a rejected credential OR an unreachable server, and D6.2-E is
  // about the fact that a browser cannot tell you which.
  const openedRef = useRef(false);
  // Refresh attempts spent on handshake recovery since the last clean open.
  // Bounded at one: nothing but re-authentication changes a credential, so a
  // second attempt with the same one cannot succeed.
  const authRetryRef = useRef(0);
  // One handshake diagnosis at a time (the probe below is a round trip).
  const diagnosingRef = useRef(false);

  useEffect(() => {
    // Owned by this effect run alone: true once React has torn it down. Every
    // async continuation below (reconnect timer, refresh promise, probe) checks
    // it before touching the socket or the store (D6.2-F).
    let disposed = false;
    const store = useRealtimeStore.getState();

    // D6.1 / S8. Reset the WHOLE store on an identity change, not just the feed
    // slice. `setFeedIdentity` clears `feedState` and nothing else, so
    // `portfolioUpdate`, `brokerStatus`, `brokerOrders`, `brokerTicks`,
    // `tradeUpdates`, `alerts` and `unreadCount` survived A -> logout -> B login
    // in the same tab and were rendered as B's until fresh events replaced them.
    // AuthContext resets on login/logout too; this covers the transitions that
    // do not pass through either (a reload, a restored session, an expiry).
    if (lastIdentityRef.current !== null && lastIdentityRef.current !== userId) {
      store.reset();
    }
    lastIdentityRef.current = userId;

    // Bind the feed state to this account before any event can land (D5.14).
    // Done for the anonymous case too, so signing out clears the previous
    // account's feed state instead of leaving it on screen.
    useRealtimeStore.getState().setFeedIdentity(isAnon(userId) ? null : userId);

    if (!WS_URL || isAnon(userId)) {
      store.setConnection("offline");
      return undefined;
    }

    const flushBatch = () => {
      batchTimerRef.current = null;
      const batch = batchRef.current;
      if (!batch.length) return;
      batchRef.current = [];
      useRealtimeStore.getState().applyMessages(batch);
    };

    const queueMessage = (msg) => {
      batchRef.current.push(msg);
      if (!batchTimerRef.current) {
        batchTimerRef.current = setTimeout(flushBatch, BATCH_WINDOW_MS);
      }
    };

    const clearTimers = () => {
      if (reconnectRef.current) { clearTimeout(reconnectRef.current); reconnectRef.current = null; }
      if (heartbeatRef.current) { clearInterval(heartbeatRef.current); heartbeatRef.current = null; }
      if (pongTimerRef.current) { clearTimeout(pongTimerRef.current); pongTimerRef.current = null; }
      if (batchTimerRef.current) { clearTimeout(batchTimerRef.current); batchTimerRef.current = null; }
      batchRef.current = [];
    };

    const scheduleReconnect = () => {
      if (disposed) return;
      const attempt = attemptRef.current;
      const backoff = Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_MAX_MS);
      const jitter = Math.random() * 0.3 * backoff; // ±30% to avoid thundering herd
      attemptRef.current = attempt + 1;
      useRealtimeStore.getState().setConnection("reconnecting");
      reconnectRef.current = setTimeout(connect, backoff + jitter);
    };

    /**
     * Ask the API which kind of failure we just had.
     *
     * D6.2-E — WHY A PROBE AND NOT THE CLOSE CODE. The server closes an
     * unauthenticated handshake with 1008 *before* `accept()` (PH3.10, so an
     * anonymous caller never occupies a connection slot). At the ASGI layer
     * that is answered as an HTTP 403 to the upgrade request, and a browser
     * surfaces a handshake that never completed as `CloseEvent` code 1006 —
     * exactly what it reports for a server that is not listening at all. The
     * close code therefore carries no information here, and the previous code's
     * "never opened ⇒ bad credential" inference was wrong for every network
     * failure. `GET /api/auth/me` carries the same credential over a transport
     * that *does* report a status code, so it answers the question directly:
     *
     *   200                 → the session is fine; the socket failed for some
     *                         other reason. Reconnect normally.
     *   401                 → the credential is stale. One coordinated refresh.
     *   403                 → the account is blocked. Nothing to retry.
     *   no response / 5xx   → the API is unreachable or broken, so the socket's
     *                         failure was never about authentication either.
     *
     * `/auth/me` is on the api client's NEVER_REFRESH list, so this probe reads
     * the raw answer instead of triggering a refresh of its own — the refresh
     * below stays the single, deliberate one.
     */
    const diagnoseHandshakeFailure = async () => {
      try {
        await api.get("/auth/me");
        return "authenticated";
      } catch (err) {
        const status = err?.response?.status;
        if (status === 401) return "credential_expired";
        if (status === 403) return "account_blocked";
        return "api_unreachable";
      }
    };

    /**
     * A close that happened before the socket ever opened.
     *
     * The response is bounded and useful: diagnose first, then either reconnect
     * normally (network) or spend the single re-authentication attempt
     * (credential). The refresh goes through the shared queue the REST client
     * uses, so a page-wide expiry produces one refresh, not one per subsystem.
     * If it succeeds we reconnect immediately with the fresh cookie. If it is
     * definitively refused we stop — the api client has already announced
     * SESSION_EXPIRED, AuthContext will drop `user`, and this effect will tear
     * the socket down. Retrying past that point cannot succeed by definition:
     * nothing changes a credential except re-authentication.
     */
    const handleHandshakeFailure = async () => {
      if (disposed || diagnosingRef.current) return;
      if (sessionIsDead()) {
        useRealtimeStore.getState().setConnection("unauthenticated");
        return;
      }
      diagnosingRef.current = true;
      let verdict;
      try {
        verdict = await diagnoseHandshakeFailure();
      } finally {
        diagnosingRef.current = false;
      }
      if (disposed) return;

      if (verdict === "authenticated" || verdict === "api_unreachable") {
        // Not an authentication problem. This is the ordinary-reconnect path
        // and it must stay unbounded-with-backoff: a backend restart has to
        // heal on its own, without a page reload.
        authRetryRef.current = 0;
        scheduleReconnect();
        return;
      }

      useRealtimeStore.getState().setConnection("unauthenticated");
      if (verdict === "account_blocked") return; // refresh cannot fix a block
      if (authRetryRef.current >= 1) return;
      authRetryRef.current += 1;
      try {
        await refreshSession();
      } catch {
        if (disposed) return;
        // A definitive refusal already announced SESSION_EXPIRED and this
        // effect is about to be torn down — stay stopped. A transient failure
        // (the refresh endpoint itself unreachable) is a network problem after
        // all, so fall back to the ordinary reconnect path (D6.2-A).
        if (!sessionIsDead()) {
          authRetryRef.current = 0;
          scheduleReconnect();
        }
        return;
      }
      if (disposed) return;
      attemptRef.current = 0;
      connect();
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
      if (disposed) return;
      // D6.2-F. Never leave a previous attempt's socket behind: if one is still
      // referenced here, it is superseded by definition, and two open sockets
      // means duplicated frames and a private stream nobody is tracking.
      const previous = wsRef.current;
      if (previous) {
        wsRef.current = null;
        try { previous.close(); } catch { /* noop */ }
      }
      const s = useRealtimeStore.getState();
      openedRef.current = false;
      s.setConnection("connecting");
      let ws;
      try {
        // PH3.10. The socket's identity is the ACCESS TOKEN, never a user id.
        // This used to send `?user_id=<id>`, which the server trusted as the
        // key it fans per-user events out on — so anyone could bind to anyone
        // else's account by supplying their id. The server now ignores that
        // parameter entirely and derives identity from the credential below.
        //
        // The token rides in the subprotocol list rather than the query string:
        // a browser cannot set headers on a WebSocket handshake, and a query
        // string is written verbatim into server access logs, proxy logs and
        // browser history — which for a live credential is a leak. The server
        // echoes the `stockassist.auth` marker back (it must select one of the
        // offered subprotocols, or the browser drops the connection).
        //
        // When the handshake is same-origin the `access_token` cookie already
        // authenticates it and the server prefers that; this list is what makes
        // a cross-origin deployment work with the localStorage token.
        const token = localStorage.getItem("token");
        ws = token
          ? new WebSocket(`${WS_URL}/api/ws`, ["stockassist.auth", token])
          : new WebSocket(`${WS_URL}/api/ws`);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        // D6.2-F. A socket that is no longer the current one has been
        // superseded (identity change, or a reconnect that raced it). It must
        // not adopt the store's `send`, and it must not report itself live.
        if (disposed || ws !== wsRef.current) {
          try { ws.close(); } catch { /* noop */ }
          return;
        }
        openedRef.current = true;
        authRetryRef.current = 0; // a successful handshake re-arms the auth path
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
        // D6.2-F. A stale socket's frames are the previous identity's private
        // data. Drop them rather than writing them into the shared store.
        if (disposed || ws !== wsRef.current) return;
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        if (msg.type === "pong") {
          // Connection liveness is handled immediately — never batched.
          if (pongTimerRef.current) { clearTimeout(pongTimerRef.current); pongTimerRef.current = null; }
          useRealtimeStore.getState().setConnection("live", { lastPongAt: Date.now() });
          return;
        }
        // Everything else joins the micro-batch window (Sprint R9): the store
        // coalesces price-bearing messages and applies the rest in order.
        queueMessage(msg);
      };

      ws.onclose = () => {
        // A superseded socket closing says nothing about the live one; it must
        // not clear `send`, change the connection state, or start a reconnect.
        // This also covers teardown, which nulls `wsRef` before closing and
        // then sets the offline state itself.
        if (disposed || ws !== wsRef.current) return;
        if (heartbeatRef.current) { clearInterval(heartbeatRef.current); heartbeatRef.current = null; }
        if (pongTimerRef.current) { clearTimeout(pongTimerRef.current); pongTimerRef.current = null; }
        useRealtimeStore.getState().setSend(null);
        // Never opened => the connection never came up. That is either a
        // rejected credential or an unreachable server and the close code
        // cannot tell them apart, so ask (D6.2-E). Opened and then closed =>
        // an ordinary network event; back off and reconnect.
        if (!openedRef.current) {
          handleHandshakeFailure();
          return;
        }
        scheduleReconnect();
      };

      ws.onerror = () => {
        try { ws.close(); } catch { /* onclose handles it */ }
      };
    }

    attemptRef.current = 0;
    authRetryRef.current = 0;
    // These are refs, so they outlive an effect run. Reset the diagnosis latch
    // too: a run torn down mid-probe would otherwise leave it set until that
    // probe settled, and a handshake failure in the next run would be dropped.
    diagnosingRef.current = false;
    connect();

    return () => {
      // D6.2-F. `disposed` is local to THIS effect run, so a continuation left
      // over from a previous identity can never be re-enabled by the next one.
      // (A shared ref was: the teardown set it, and the next effect body
      // cleared it again before A's pending refresh resolved — which then
      // opened a second socket for an account that had already gone.)
      disposed = true;
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
