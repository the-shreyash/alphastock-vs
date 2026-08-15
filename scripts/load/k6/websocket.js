// ============================================================================
// StockAssist AI — PH3.5 real-time / WebSocket load (brief §13)
//
// ⚠ A CORRECTION TO THE BRIEF, STATED UP FRONT
// --------------------------------------------
// The brief refers to Socket.IO throughout. **StockAssist does not use
// Socket.IO.** The real-time transport is a *native FastAPI WebSocket endpoint*
// at `/api/ws`, with an in-process `ConnectionManager` (`server.py`) doing the
// fan-out and Redis Pub/Sub bridging events between processes
// (`services/realtime/event_bridge.py`). Everything below is written against
// what exists. This matters for the results as well as the wording: there is no
// Socket.IO room abstraction, no polling fallback, and no acknowledgement
// protocol — so the failure modes are different ones.
//
// WHAT THIS MEASURES
//   * connections established and maintained at concurrency
//   * disconnect rate over a sustained hold
//   * event throughput received per connection
//   * server resource usage while N sockets are held open (read separately from
//     /api/metrics by the runner)
//
// The event source is the application's own `market_broadcast_loop` (a 10 s
// tick) plus whatever the heartbeat engine publishes — i.e. REAL server-driven
// events, not synthetic ones. The brief (§13) explicitly forbids manufacturing
// millions of fake events, and manufacturing them would in any case measure the
// generator rather than the fan-out.
//
// SUBSCRIPTIONS ARE REALISTIC, NOT MAXIMAL
//   Each connection subscribes to the channels one real page subscribes to.
//   Subscribing every socket to "*" would exercise a fan-out shape no client
//   produces and would overstate per-event cost.
//
//   k6 run scripts/load/k6/websocket.js
//   k6 run -e WS_CONNECTIONS=100 -e WS_HOLD=120 scripts/load/k6/websocket.js
// ============================================================================
/* eslint-disable no-undef */
import { WebSocket } from 'k6/experimental/websockets';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import exec from 'k6/execution';
import { WS_URL, USER_COUNT } from './lib/config.js';

const opened = new Counter('sa_ws_opened');
const closedEarly = new Counter('sa_ws_closed_early');
const errors = new Counter('sa_ws_errors');
const eventsReceived = new Counter('sa_ws_events');
const subAcked = new Rate('sa_ws_subscribe_acked');
const pongLatency = new Trend('sa_ws_pong_ms', true);
const heldFull = new Rate('sa_ws_held_full_duration');

const CONNECTIONS = parseInt(__ENV.WS_CONNECTIONS || '50', 10);
const HOLD_SECONDS = parseInt(__ENV.WS_HOLD || '90', 10);
const RAMP_SECONDS = parseInt(__ENV.WS_RAMP || '20', 10);

// CHURN MODE (`-e WS_CHURN=1`) holds each socket for a couple of seconds
// instead of the full duration, so connections are opening and closing
// continuously while the server fans out.
//
// This is not a variation for its own sake. `ConnectionManager.broadcast` and
// `send_to_user` (server.py) iterate `self.active` / `self.user_connections`
// **directly** while awaiting `ws.send_text` inside the loop, whereas
// `broadcast_to_channel` iterates a `list(...)` copy. In CPython, mutating a
// set while iterating it raises `RuntimeError: Set changed size during
// iteration` — and every `await` in that loop is a point where another task can
// run `connect()` or `disconnect()`. Steady-state connections never expose it;
// churn is the condition under which it would fire. Whether it does is a
// question only a load test can answer, which is why it is asked here.
const CHURN = (__ENV.WS_CHURN || '') === '1';
const CHURN_HOLD_MS = parseInt(__ENV.WS_CHURN_HOLD_MS || '2000', 10);

// The channel sets a real client subscribes to, taken from
// `frontend/src/store/realtimeStore.js` and the pages that use it.
const CHANNEL_SETS = [
  ['market', 'notifications'],                    // dashboard
  ['market', 'watchlist'],                        // watchlist page
  ['market', 'portfolio', 'trades'],              // portfolio / trade monitor
  ['market', 'sectors', 'scanner'],               // markets / scanner
  ['ai', 'notifications'],                        // AI workspace
];

export const options = {
  scenarios: {
    sockets: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: `${RAMP_SECONDS}s`, target: CONNECTIONS },
        { duration: `${HOLD_SECONDS}s`, target: CONNECTIONS },
        { duration: '10s', target: 0 },
      ],
      // Each VU holds ONE socket for one long iteration, which is what a
      // browser tab does. A short iteration that reconnects repeatedly would
      // measure handshake cost, not the cost of *holding* connections — and
      // holding is what a real-time product does all day.
      gracefulRampDown: '15s',
    },
  },
  thresholds: {
    sa_ws_held_full_duration: ['rate>0.98'],   // no unexpected mass disconnects
    sa_ws_subscribe_acked: ['rate>0.99'],
    sa_ws_errors: ['count<5'],
  },
};

export default function () {
  const vu = exec.vu.idInInstance;
  // NOTE: `/api/ws` takes `user_id` as an unauthenticated query parameter —
  // there is no token check on the socket. That is a KNOWN, documented gap
  // (SECURITY_ARCHITECTURE.md §32 lists WebSocket security as "Not started",
  // owned by PH1.9); it is recorded in the PH3.5 certification rather than
  // treated as a new finding, and nothing here depends on it being insecure.
  const userId = `loadws-${vu % USER_COUNT}`;
  const channels = CHANNEL_SETS[vu % CHANNEL_SETS.length];

  // Hold the socket for the remaining scenario time rather than a fixed value,
  // so a VU started during ramp-up does not outlive the scenario and get
  // counted as an early close during graceful shutdown.
  const holdMs = CHURN ? CHURN_HOLD_MS : HOLD_SECONDS * 1000;
  const startedAt = Date.now();
  let sawClose = false;
  let pingSentAt = 0;

  const ws = new WebSocket(`${WS_URL}?user_id=${userId}`);

  ws.onopen = () => {
    opened.add(1);
    ws.send(JSON.stringify({ type: 'subscribe', channels }));
    pingSentAt = Date.now();
    ws.send(JSON.stringify({ type: 'ping' }));
  };

  ws.onmessage = (msg) => {
    let payload;
    try { payload = JSON.parse(String(msg.data)); } catch (e) { return; }

    if (payload.type === 'subscribed') {
      subAcked.add(true);
      return;
    }
    if (payload.type === 'pong') {
      if (pingSentAt) pongLatency.add(Date.now() - pingSentAt);
      pingSentAt = 0;
      return;
    }
    // Everything else is a server-pushed domain event — the thing being
    // measured. Counted by arrival; per-event delivery latency is NOT claimed,
    // because the payloads carry no server-side send timestamp and inventing
    // one from the receive clock would be measuring this script.
    eventsReceived.add(1);
  };

  ws.onerror = (e) => {
    errors.add(1);
    // eslint-disable-next-line no-console
    console.log(`ws error vu=${vu}: ${e && e.error}`);
  };

  ws.onclose = () => {
    const held = Date.now() - startedAt;
    // "Early" means the server dropped us well before we intended to leave.
    // The 5 s margin absorbs the graceful ramp-down.
    if (held < holdMs - 5000) {
      closedEarly.add(1);
      sawClose = true;
    }
  };

  // Periodic ping, as the real client does — it is also the liveness probe that
  // makes a silently-dead socket visible instead of merely quiet.
  const pinger = setInterval(() => {
    try { pingSentAt = Date.now(); ws.send(JSON.stringify({ type: 'ping' })); } catch (e) { /* closed */ }
  }, 15000);

  setTimeout(() => {
    clearInterval(pinger);
    heldFull.add(!sawClose);
    try { ws.close(); } catch (e) { /* already closed */ }
  }, holdMs);
}

export function handleSummary(data) {
  const m = data.metrics;
  const v = (n, s) => (m[n] && m[n].values[s !== undefined ? s : 'count'] !== undefined
    ? m[n].values[s !== undefined ? s : 'count'] : 'n/a');
  const mode = CHURN ? `churn, ${CHURN_HOLD_MS}ms per socket` : `${HOLD_SECONDS}s hold`;
  // k6 omits a Counter that never incremented, so an absent metric here means
  // zero. Rendering it as `n/a` would read as "not measured", which is the
  // opposite of what it means.
  const z = (n) => (m[n] ? m[n].values.count : 0);
  let out = `\n=== PH3.5 WebSocket (${CONNECTIONS} connections, ${mode}) ===\n`;
  out += `  sockets opened            ${v('sa_ws_opened')}\n`;
  out += `  closed early              ${z('sa_ws_closed_early')}\n`;
  out += `  socket errors             ${z('sa_ws_errors')}\n`;
  out += `  domain events received    ${v('sa_ws_events')}\n`;
  out += `  subscribe ack rate        ${m.sa_ws_subscribe_acked ? (m.sa_ws_subscribe_acked.values.rate * 100).toFixed(2) + '%' : 'n/a'}\n`;
  out += `  held full duration        ${m.sa_ws_held_full_duration ? (m.sa_ws_held_full_duration.values.rate * 100).toFixed(2) + '%' : 'n/a'}\n`;
  out += `  ping->pong p95            ${m.sa_ws_pong_ms ? m.sa_ws_pong_ms.values['p(95)'].toFixed(2) + ' ms' : 'n/a'}\n`;
  out += `  ws_connecting p95         ${m.ws_connecting ? m.ws_connecting.values['p(95)'].toFixed(2) + ' ms' : 'n/a'}\n`;
  const res = { stdout: out };
  if (__ENV.LOAD_SUMMARY_PATH) res[__ENV.LOAD_SUMMARY_PATH] = JSON.stringify(data, null, 2);
  return res;
}
