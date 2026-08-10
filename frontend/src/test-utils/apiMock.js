/**
 * API mocking — request interception at the transport boundary.
 *
 * WHY THIS APPROACH
 * -----------------
 * Every network call the frontend makes goes through the single axios instance
 * in `src/services/api.js` (CODING_STANDARDS.md: networking lives in service
 * files). `axios-mock-adapter` replaces that instance's *adapter* — the last
 * step before the request leaves the process. Everything above it runs for
 * real: the request interceptor that attaches the bearer token, the 401 refresh
 * interceptor, every service module, every component.
 *
 * That is the property we want. Mocking `tradeService.create` would prove
 * nothing about the interceptors; intercepting the transport proves the whole
 * client stack behaves correctly against a given server response.
 *
 * MSW would be the other candidate. It is not used here: this app builds on
 * CRA 5 / Jest 27, whose resolver predates `package.json#exports`, and MSW v2 is
 * exports-only ESM requiring a stack of Web-streams polyfills under jsdom.
 * Intercepting the axios adapter is the smaller, deterministic tool for the
 * same job. Revisit if the build ever migrates to Vite/Vitest (see PH3.2
 * certification, Known Gaps).
 *
 * NO REAL SERVICE IS REACHABLE FROM A TEST: `setupTests.js` points the axios
 * base URL at a fake host, disables `fetch`, and stubs `WebSocket`, and this
 * adapter rejects any request a test did not explicitly stub.
 */
import MockAdapter from "axios-mock-adapter";
import api from "../services/api";

/**
 * Install the mock adapter on the app's axios instance.
 *
 * `onNoMatch: "throwException"` is deliberate: an unstubbed request is a test
 * that does not know what the component does. It fails loudly, naming the URL,
 * instead of silently hanging or falling through to a real socket.
 *
 * @returns {MockAdapter} the adapter — call `.onGet(...)` etc. to stub routes.
 */
export function installApiMock({ onNoMatch = "throwException" } = {}) {
  return new MockAdapter(api, { onNoMatch });
}

/** HTTP statuses the UI must handle. Used to drive table-driven error tests. */
export const HTTP = {
  OK: 200,
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  RATE_LIMITED: 429,
  SERVER_ERROR: 500,
};

/**
 * Stub a route that never settles, so a test can assert the *loading* state.
 * The promise is intentionally abandoned; jsdom tears down with the test.
 */
export function pending() {
  return new Promise(() => {});
}

/**
 * Register a permissive catch-all AFTER the specific stubs a test cares about.
 *
 * Pages like Dashboard fan out to a dozen endpoints on mount. A test asserting
 * one widget should stub that widget's endpoint precisely and let the rest
 * resolve to a harmless empty payload — rather than pretending to care about
 * thirteen responses. Because axios-mock-adapter matches handlers in
 * registration order, the specific stubs still win.
 */
export function stubRemainingWith(mock, body = [], status = HTTP.OK) {
  mock.onAny().reply(status, body);
  return mock;
}
