/**
 * Global Jest setup — runs once before every test file (auto-detected by
 * react-scripts as `setupFilesAfterEach`).
 *
 * Responsibilities:
 *   1. Install the jest-dom matchers (toBeInTheDocument, toHaveAccessibleName…).
 *   2. Polyfill the browser APIs jsdom does not implement but the app calls
 *      during render. Without these, components crash on mount for reasons that
 *      have nothing to do with the behaviour under test.
 *   3. Guarantee no test can reach a real network: axios is pointed at a fake
 *      backend origin, and `fetch`/`WebSocket` are inert stubs that fail loudly.
 *
 * Nothing here mocks application code. Application behaviour is always exercised
 * for real; only the browser/network boundary is substituted.
 */
import "@testing-library/jest-dom";

/* ------------------------------------------------------------------ *
 * Environment — a deterministic fake backend origin.
 * `services/api.js` reads REACT_APP_BACKEND_URL at import time, so this must
 * be set before any test imports it. Tests assert against this host.
 * ------------------------------------------------------------------ */
process.env.REACT_APP_BACKEND_URL = "http://backend.test";

/* ------------------------------------------------------------------ *
 * jsdom gaps used by the app on mount.
 * ------------------------------------------------------------------ */

// jsdom (via jest-environment-jsdom 27) ships no TextEncoder/TextDecoder, but
// react-router v7 references them at module scope. Node's implementations are
// spec-compliant, so this is a polyfill rather than a substitute.
const { TextEncoder, TextDecoder } = require("util");
if (!global.TextEncoder) global.TextEncoder = TextEncoder;
if (!global.TextDecoder) global.TextDecoder = TextDecoder;

// framer-motion `whileInView` + useCardEntrance
if (!global.IntersectionObserver) {
  global.IntersectionObserver = class IntersectionObserver {
    constructor(callback) { this.callback = callback; }
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return []; }
  };
}

// Recharts ResponsiveContainer + resizable panels
if (!global.ResizeObserver) {
  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// next-themes / responsive components
if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

// AIAssistant scrolls the thread to the bottom after every message.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// jsdom implements neither of these; lightweight-charts and Radix use them.
if (!window.HTMLCanvasElement.prototype.getContext) {
  window.HTMLCanvasElement.prototype.getContext = () => null;
}
if (!window.DOMRect) {
  window.DOMRect = class DOMRect {
    constructor(x = 0, y = 0, width = 0, height = 0) {
      Object.assign(this, { x, y, width, height, top: y, left: x, right: x + width, bottom: y + height });
    }
  };
}

// useAIWorkspace derives an AI run correlation id from crypto.randomUUID.
if (!global.crypto) global.crypto = {};
if (!global.crypto.randomUUID) {
  let n = 0;
  global.crypto.randomUUID = () => `test-uuid-${++n}`;
}

/* ------------------------------------------------------------------ *
 * Network isolation.
 *
 * RealtimeProvider opens a WebSocket for every authenticated user. jsdom has no
 * WebSocket, and a real one would be a live connection attempt — so we install
 * an inert stub that never opens. Components therefore render in the "offline"
 * realtime state by default, which is exactly the state we want deterministic.
 * Tests that need live realtime data write into the Zustand store directly.
 * ------------------------------------------------------------------ */
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    MockWebSocket.instances.push(this);
  }
  send() {}
  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code: 1000 });
  }
  addEventListener() {}
  removeEventListener() {}
}
MockWebSocket.instances = [];
global.WebSocket = MockWebSocket;

// Any bare fetch() is a bug in a unit test — surface it instead of hanging.
global.fetch = jest.fn(() =>
  Promise.reject(new Error("Unexpected network call: fetch() is disabled in tests. Use the axios mock (test-utils/apiMock).")),
);

/* ------------------------------------------------------------------ *
 * Console hygiene — a React `act()` warning or a PropType error means the test
 * is racing the component. Left visible on purpose (not silenced) so they are
 * caught in review rather than hidden.
 * ------------------------------------------------------------------ */
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  MockWebSocket.instances.length = 0;
});
