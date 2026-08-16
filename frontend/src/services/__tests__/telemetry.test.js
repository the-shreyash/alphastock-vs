/**
 * PH3.7 — client telemetry: what gets reported, and what must never be.
 *
 * The privacy assertions here are the point of the file. A frontend reporter is
 * the easiest place in a codebase to leak a token, because the values are all
 * sitting right there in `window` and the code that sends them is written in a
 * hurry after an incident. Each `expect(...).not.toContain(...)` below stands
 * for a specific thing this app has in scope at the moment an error fires: an
 * OAuth `code` in the URL, a recovery token in a query string, a JWT in
 * localStorage, a trade id in the path.
 */
import {
  reportClientError,
  routeOf,
  isChunkLoadError,
  installTelemetry,
  recentReports,
  resetTelemetryForTests,
  TELEMETRY_KINDS,
} from "@/services/telemetry";

/** Capture what would go over the wire, without a network. */
function captureBeacon() {
  const sent = [];
  navigator.sendBeacon = jest.fn((url, blob) => {
    sent.push({ url, blob });
    return true;
  });
  return sent;
}

/**
 * Read back the JSON that was handed to `sendBeacon`.
 *
 * jsdom's `Blob` here (jest-environment-jsdom 27) implements neither `.text()`
 * nor `.arrayBuffer()`, so `FileReader` is the only way to get the bytes out.
 * Reading the real payload matters more than the convenience of a shortcut:
 * asserting against an object the test itself constructed would prove nothing
 * about what actually leaves the browser, which is the entire question these
 * privacy tests exist to answer.
 */
function bodyOf(entry) {
  if (!entry?.blob) return Promise.resolve(null);
  if (typeof entry.blob.text === "function") {
    return entry.blob.text().then(JSON.parse);
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        resolve(JSON.parse(reader.result));
      } catch (err) {
        reject(err);
      }
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsText(entry.blob);
  });
}

describe("routeOf", () => {
  it("keeps a static path as-is", () => {
    expect(routeOf("/dashboard")).toBe("/dashboard");
  });

  it.each([
    ["/trades/5f8d0d55b54764421b7156c3", "/trades/:id"],
    ["/orders/12345", "/orders/:id"],
    ["/x/550e8400-e29b-41d4-a716-446655440000", "/x/:id"],
  ])("normalises %s to %s", (input, expected) => {
    // Without this, an id in the path becomes part of a server-side log field
    // and, worse, invites someone to make it a metric label later.
    expect(routeOf(input)).toBe(expected);
  });

  it("falls back to / for a missing path", () => {
    expect(routeOf(undefined)).toBe("/");
    expect(routeOf("")).toBe("/");
  });
});

describe("isChunkLoadError", () => {
  it.each([
    "Loading chunk 42 failed.",
    "Loading CSS chunk 7 failed.",
    "Failed to fetch dynamically imported module: /static/js/main.js",
  ])("recognises %s", (message) => {
    expect(isChunkLoadError(new Error(message))).toBe(true);
  });

  it("recognises the ChunkLoadError name", () => {
    const error = new Error("nope");
    error.name = "ChunkLoadError";
    expect(isChunkLoadError(error)).toBe(true);
  });

  it("does not misclassify an ordinary bug", () => {
    // Misclassifying a real bug as a chunk error would trigger an automatic
    // page reload against a deterministically failing render — a refresh loop.
    expect(isChunkLoadError(new TypeError("x is not a function"))).toBe(false);
  });
});

describe("reportClientError", () => {
  beforeEach(() => {
    resetTelemetryForTests();
    captureBeacon();
  });

  it("posts to the backend origin, not the document origin", async () => {
    const sent = captureBeacon();
    reportClientError(TELEMETRY_KINDS.RENDER, new Error("boom"));
    // sendBeacon resolves a relative URL against the document, which in every
    // deployment of this app is the static host — where nothing is listening,
    // and nothing would ever say so.
    expect(sent[0].url).toBe("http://backend.test/api/observability/client-errors");
  });

  it("sends the kind, name, message and route", async () => {
    const sent = captureBeacon();
    reportClientError(TELEMETRY_KINDS.RENDER, new TypeError("x is undefined"), {
      route: "/portfolio",
    });
    const body = await bodyOf(sent[0]);
    expect(body).toMatchObject({
      kind: "render",
      name: "TypeError",
      message: "x is undefined",
      route: "/portfolio",
    });
  });

  it("reclassifies a chunk-load failure regardless of the kind passed", async () => {
    const sent = captureBeacon();
    reportClientError(TELEMETRY_KINDS.RENDER, new Error("Loading chunk 9 failed."));
    expect((await bodyOf(sent[0])).kind).toBe("chunk_load");
  });

  it("tolerates a thrown non-Error", async () => {
    // `throw "boom"` and promise rejections with a plain object are real and
    // reach these handlers; crashing the reporter on them is not acceptable.
    const sent = captureBeacon();
    expect(reportClientError(TELEMETRY_KINDS.UNCAUGHT, "just a string")).toBe(true);
    expect((await bodyOf(sent[0])).message).toBe("just a string");
  });

  it("deduplicates identical failures", () => {
    const sent = captureBeacon();
    for (let i = 0; i < 5; i += 1) {
      reportClientError(TELEMETRY_KINDS.RENDER, new Error("same"), { route: "/x" });
    }
    expect(sent).toHaveLength(1);
  });

  it("stops after the per-session cap", () => {
    // A render loop throws thousands of times a second. Without a cap, the
    // reporting path becomes a self-inflicted denial of service against our
    // own API at the exact moment the app is already failing.
    const sent = captureBeacon();
    for (let i = 0; i < 200; i += 1) {
      reportClientError(TELEMETRY_KINDS.RENDER, new Error(`distinct ${i}`));
    }
    expect(sent.length).toBeLessThanOrEqual(20);
  });

  it("clips an enormous message and stack", async () => {
    const sent = captureBeacon();
    reportClientError(TELEMETRY_KINDS.UNCAUGHT, {
      name: "E",
      message: "A".repeat(10000),
      stack: "B".repeat(100000),
    });
    const body = await bodyOf(sent[0]);
    expect(body.message.length).toBeLessThanOrEqual(300);
    expect(body.stack.length).toBeLessThanOrEqual(2000);
  });

  it("strips newlines so a report cannot forge a server log line", async () => {
    const sent = captureBeacon();
    reportClientError(TELEMETRY_KINDS.UNCAUGHT, new Error("a\nb\rc"));
    expect((await bodyOf(sent[0])).message).not.toMatch(/[\r\n]/);
  });

  it("never throws, even if sending fails", () => {
    navigator.sendBeacon = () => {
      throw new Error("beacon exploded");
    };
    global.fetch = () => {
      throw new Error("fetch exploded");
    };
    expect(() => reportClientError(TELEMETRY_KINDS.RENDER, new Error("x"))).not.toThrow();
  });
});

describe("reportClientError — what is never collected", () => {
  beforeEach(() => resetTelemetryForTests());

  it("sends the pathname only, never the query string or hash", async () => {
    const sent = captureBeacon();
    // The real shapes: a Google OAuth code, a broker callback token, and a
    // password-recovery token all arrive in this application as query strings.
    delete window.location;
    window.location = {
      pathname: "/auth/google/callback",
      search: "?code=4/0AY0e-g7SECRET_OAUTH_CODE&state=abc",
      hash: "#access_token=SECRETTOKEN",
      href: "http://app.test/auth/google/callback?code=4/0AY0e-g7SECRET_OAUTH_CODE",
    };

    reportClientError(TELEMETRY_KINDS.RENDER, new Error("crash during callback"));
    const raw = JSON.stringify(await bodyOf(sent[0]));

    expect(raw).not.toContain("SECRET_OAUTH_CODE");
    expect(raw).not.toContain("SECRETTOKEN");
    expect(raw).not.toContain("code=");
    expect(raw).not.toContain("?");
  });

  it("reads nothing from localStorage", async () => {
    const sent = captureBeacon();
    localStorage.setItem("token", "eyJhbGciOiJIUzI1NiJ9.SECRETJWT.sig");
    localStorage.setItem("user", JSON.stringify({ email: "someone@example.com" }));

    reportClientError(TELEMETRY_KINDS.RENDER, new Error("boom"));
    const raw = JSON.stringify(await bodyOf(sent[0]));

    expect(raw).not.toContain("SECRETJWT");
    expect(raw).not.toContain("someone@example.com");
    localStorage.clear();
  });

  it("sends only the documented fields", async () => {
    const sent = captureBeacon();
    reportClientError(TELEMETRY_KINDS.API, new Error("x"));
    // An allowlist assertion, not a denylist: it fails when someone ADDS a
    // field, which is how a user id gets into a payload.
    expect(Object.keys(await bodyOf(sent[0])).sort()).toEqual(
      ["appVersion", "kind", "message", "name", "route", "stack"],
    );
  });
});

describe("installTelemetry", () => {
  beforeEach(() => resetTelemetryForTests());

  it("reports an unhandled promise rejection", async () => {
    const sent = captureBeacon();
    installTelemetry();
    window.dispatchEvent(
      Object.assign(new Event("unhandledrejection"), { reason: new Error("async boom") }),
    );
    expect((await bodyOf(sent[0])).kind).toBe("unhandled_rejection");
  });

  it("reports an uncaught error", async () => {
    const sent = captureBeacon();
    installTelemetry();
    window.dispatchEvent(Object.assign(new Event("error"), { error: new Error("sync boom") }));
    expect((await bodyOf(sent[0])).kind).toBe("uncaught");
  });

  it("ignores a resource load failure", () => {
    // A broken <img> or a blocked third-party script fires `error` with no
    // `error` property. Reporting those would bury the real signal under noise
    // from ad blockers.
    const sent = captureBeacon();
    installTelemetry();
    window.dispatchEvent(new Event("error"));
    expect(sent).toHaveLength(0);
  });

  it("is idempotent", () => {
    const sent = captureBeacon();
    installTelemetry();
    installTelemetry();
    installTelemetry();
    window.dispatchEvent(
      Object.assign(new Event("unhandledrejection"), { reason: new Error("once") }),
    );
    // Three installs must not mean three reports of the same failure.
    expect(sent).toHaveLength(1);
  });
});

describe("recentReports", () => {
  beforeEach(() => resetTelemetryForTests());

  it("keeps a bounded newest-first buffer", () => {
    captureBeacon();
    for (let i = 0; i < 15; i += 1) {
      reportClientError(TELEMETRY_KINDS.RENDER, new Error(`e${i}`));
    }
    const recent = recentReports();
    expect(recent.length).toBeLessThanOrEqual(10);
    expect(recent[0].message).toBe("e14");
  });
});
