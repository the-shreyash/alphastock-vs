/**
 * D6.3 closure — LIM-D6.3-1: the browser leg, widened.
 *
 * WHY THIS EXISTS
 * ---------------
 * D6.3's §0 check drove ONE browser context with ONE tab, and recorded the rest
 * as LIM-D6.3-1: "two browsers on one account, and two accounts in two browsers
 * simultaneously, were not driven." That gap was not cosmetic. Two of the five
 * D6.3 defects — and the one this script was written to catch — are invisible to
 * a hermetic test and to a single-tab browser check, because they need two
 * identities alive at the same instant before anything crosses between them.
 *
 * WHAT IT DRIVES
 *   T1  two accounts in two isolated contexts, signed in SIMULTANEOUSLY
 *   T2  two TABS on one account sharing one cookie jar (the D6.2 grace race —
 *       the first browser-level proof that JWT_REFRESH_GRACE_SECONDS works)
 *   T3  A -> B identity transition in one tab (re-verifies D63-3 in Chrome)
 *   T4  WebSocket lifetime + private-frame isolation across two live contexts
 *   T5  private activity: one user's stock-page visit must not reach another's
 *       feed. THIS IS A REGRESSION CHECK FOR A DEFECT THIS SCRIPT FOUND — see
 *       `stock_patterns` in backend/server.py and TASK.md D6.3-closure.
 *
 * Real Chrome, real server, real Mongo. **No order is placed and no broker is
 * connected** — the broker leg of LIM-D6.3-1 remains open and needs live
 * Zerodha/Upstox credentials plus an interactive login.
 *
 * HOW TO RUN
 *   1. mongod, then `uvicorn server:app --port 8000` from backend/,
 *      then `npx craco start` from frontend/ (needs :3000 and :8000 up).
 *   2. npm i playwright            # Chromium not needed; uses installed Chrome
 *   3. node scripts/browser/d63_tenant_isolation.js
 *
 * The two test accounts are created on first run and reused after. Override the
 * endpoints with APP_URL / API_URL if you are not on the default ports.
 *
 * Exits non-zero on any failed check, so it can gate a release.
 */
const { chromium } = require("playwright");

const APP = process.env.APP_URL || "http://localhost:3000";
const API = process.env.API_URL || "http://localhost:8000";
const PASS = "D63-isolation-Pass!9";
const A = { email: "d63a@d63.test", label: "A", symbols: ["DIVISLAB", "WIPRO"] };
const B = { email: "d63b@d63.test", label: "B", symbols: [] };

/** A symbol only user A ever opens, so its appearance anywhere else is a leak. */
const A_PRIVATE_SYMBOL = "VOLTAS";
/** A symbol nobody opens. If this shows up, the check is matching noise. */
const CONTROL_SYMBOL = "BERGEPAINT";

/**
 * Create the two accounts if they are not there yet, and give A private rows.
 * Idempotent: a duplicate registration is expected on every run after the first.
 */
async function provision() {
  for (const user of [A, B]) {
    await fetch(`${API}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: user.email, password: PASS, name: `D63 ${user.label}` }),
    }).catch(() => {});
  }
  const res = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: A.email, password: PASS }),
  });
  const { token } = await res.json();
  for (const symbol of A.symbols) {
    await fetch(`${API}/api/watchlist`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ symbol }),
    }).catch(() => {});
  }
}

const results = [];
function check(name, pass, detail) {
  results.push({ name, pass, detail });
  console.log(`  ${pass ? "PASS" : "**FAIL**"}  ${name}${detail ? `  — ${detail}` : ""}`);
}

async function login(page, user) {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="login-email-input"]', { timeout: 20000 });
  await page.fill('[data-testid="login-email-input"]', user.email);
  await page.fill('[data-testid="login-password-input"]', PASS);
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/login") && r.status() === 200, { timeout: 20000 }),
    page.click('[data-testid="login-submit-btn"]'),
  ]);
  await page.waitForTimeout(2500);
}

async function apiFromPage(page, path, opts = {}) {
  return page.evaluate(
    async ([url, o]) => {
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrf_token="));
      const headers = { "Content-Type": "application/json", ...(o.headers || {}) };
      if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf.split("=")[1]);
      const res = await fetch(url, { credentials: "include", method: o.method || "GET", headers });
      let body = null;
      try { body = await res.json(); } catch { body = null; }
      return { status: res.status, body };
    },
    [`${API}${path}`, opts]
  );
}

(async () => {
  await provision();
  const browser = await chromium.launch({ channel: "chrome", headless: true });

  // Two contexts = two cookie jars = two independent "browsers".
  // A tall viewport: the sidebar is `h-screen overflow-hidden` and the logout
  // control sits at its foot, so a short window hides the very button T3 needs.
  const VIEWPORT = { viewport: { width: 1440, height: 1200 } };
  const ctxA = await browser.newContext(VIEWPORT);
  const ctxB = await browser.newContext(VIEWPORT);

  // ---- WebSocket observation, per context -------------------------------- //
  const sockets = { A: [], B: [] };
  const frames = { A: [], B: [] };
  function watch(page, label) {
    page.on("websocket", (ws) => {
      sockets[label].push({ url: ws.url(), closed: false, closeCode: null });
      const rec = sockets[label][sockets[label].length - 1];
      ws.on("framereceived", (f) => {
        if (typeof f.payload === "string") frames[label].push(f.payload);
      });
      ws.on("close", () => { rec.closed = true; });
    });
  }

  const pageA = await ctxA.newPage();
  const pageB = await ctxB.newPage();
  watch(pageA, "A");
  watch(pageB, "B");

  console.log("\n=== T1 — two accounts, two isolated contexts, simultaneously ===");
  await login(pageA, A);
  await login(pageB, B);

  const meA = await apiFromPage(pageA, "/api/auth/me");
  const meB = await apiFromPage(pageB, "/api/auth/me");
  check("both contexts are authenticated at the same time",
    meA.status === 200 && meB.status === 200, `A=${meA.status} B=${meB.status}`);
  check("each context resolves to its OWN account",
    meA.body?.email === A.email && meB.body?.email === B.email,
    `A=${meA.body?.email} B=${meB.body?.email}`);

  const wlA = await apiFromPage(pageA, "/api/watchlist");
  const wlB = await apiFromPage(pageB, "/api/watchlist");
  const symsA = (wlA.body || []).map((r) => r.symbol).sort();
  const symsB = (wlB.body || []).map((r) => r.symbol).sort();
  check("owner-positive control: A really has private rows",
    symsA.length === 2 && symsA.includes("DIVISLAB"), `A watchlist=${JSON.stringify(symsA)}`);
  check("B never receives A's rows while both are live",
    symsB.length === 0, `B watchlist=${JSON.stringify(symsB)}`);

  const cookiesA = await ctxA.cookies();
  const cookiesB = await ctxB.cookies();
  const acc = (cs) => cs.find((c) => c.name === "access_token")?.value || null;
  check("the two contexts hold DIFFERENT session cookies",
    acc(cookiesA) && acc(cookiesB) && acc(cookiesA) !== acc(cookiesB));

  // B's rendered DOM must not contain A's symbols anywhere.
  await pageB.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
  await pageB.waitForTimeout(3000);
  const bodyB = await pageB.evaluate(() => document.body.innerText);
  const leaked = A.symbols.filter((s) => bodyB.includes(s));
  check("A's symbols never render in B's browser", leaked.length === 0, `leaked=${JSON.stringify(leaked)}`);

  const lsB = await pageB.evaluate(() => JSON.stringify(Object.entries(localStorage)));
  const lsLeak = A.symbols.filter((s) => lsB.includes(s));
  check("A's symbols are absent from B's localStorage", lsLeak.length === 0, `leaked=${JSON.stringify(lsLeak)}`);

  console.log("\n=== T2 — two TABS, one account, one cookie jar (D6.2 grace race) ===");
  const tabA2 = await ctxA.newPage();
  watch(tabA2, "A");
  await tabA2.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
  await tabA2.waitForTimeout(2000);

  const bothTabsAuthed = await Promise.all([
    apiFromPage(pageA, "/api/auth/me"),
    apiFromPage(tabA2, "/api/auth/me"),
  ]);
  check("two tabs on one account are both authenticated",
    bothTabsAuthed.every((r) => r.status === 200 && r.body?.email === A.email));

  // Drop the access cookie so BOTH tabs are forced to refresh at once — the
  // exact shape that tripped reuse detection before JWT_REFRESH_GRACE_SECONDS.
  const before = await ctxA.cookies();
  await ctxA.clearCookies({ name: "access_token" });
  const afterClear = await ctxA.cookies();
  check("access cookie removed, refresh cookie retained (the two-tab setup)",
    !acc(afterClear) && afterClear.some((c) => c.name.includes("refresh")),
    `cookies now: ${afterClear.map((c) => c.name).join(",")}`);

  const [r1, r2] = await Promise.all([
    apiFromPage(pageA, "/api/auth/refresh", { method: "POST" }),
    apiFromPage(tabA2, "/api/auth/refresh", { method: "POST" }),
  ]);
  check("BOTH concurrent tab refreshes are accepted (grace window works in a browser)",
    r1.status === 200 && r2.status === 200, `tab1=${r1.status} tab2=${r2.status}`);

  const post1 = await apiFromPage(pageA, "/api/auth/me");
  const post2 = await apiFromPage(tabA2, "/api/auth/me");
  check("neither tab was signed out by the race",
    post1.status === 200 && post2.status === 200, `tab1=${post1.status} tab2=${post2.status}`);
  check("the account still works after the two-tab refresh",
    post1.body?.email === A.email && post2.body?.email === A.email);

  console.log("\n=== T3 — A -> B identity transition in ONE tab (D63-3 in Chrome) ===");
  // A THIRD context. Reusing pageA would sign B into A's tab and every later
  // assertion about "A's socket" would be measuring B's — a label bug that
  // reads exactly like a leak. The first run of this suite reported precisely
  // that false positive.
  const ctxC = await browser.newContext(VIEWPORT);
  const pageC = await ctxC.newPage();
  await login(pageC, A);
  // Give A some client-side private activity first.
  await pageC.goto(`${APP}/stock/DIVISLAB`, { waitUntil: "domcontentloaded" });
  await pageC.waitForTimeout(3000);
  const lsAfterVisit = await pageC.evaluate(() => JSON.stringify(Object.entries(localStorage)));
  check("owner-positive control: A's private activity IS written to localStorage",
    lsAfterVisit.includes("DIVISLAB"),
    `keys=${await pageC.evaluate(() => Object.keys(localStorage).join(","))}`);

  // Log out through the REAL UI. Calling /api/auth/logout directly would bypass
  // `clearTenantLocalState()` — the exact client-side code this test exists to
  // verify — and would prove nothing about the transition.
  await pageC.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
  // The control is present but the collapsed sidebar leaves it zero-width, so
  // Playwright's visibility gate never opens. A native click still dispatches
  // through React's delegated handler, and it is the logout LOGIC under test
  // here, not the sidebar's CSS.
  await pageC.waitForSelector('[data-testid="sidebar-logout-btn"]', { state: "attached", timeout: 20000 });
  await pageC.evaluate(() => document.querySelector('[data-testid="sidebar-logout-btn"]').click());
  await pageC.waitForTimeout(3000);
  const lsRightAfterLogout = await pageC.evaluate(() => JSON.stringify(Object.entries(localStorage)));
  check("sign-out itself purges A's private browser state",
    !A.symbols.some((s) => lsRightAfterLogout.includes(s)),
    `keys=${await pageC.evaluate(() => Object.keys(localStorage).join(",") || "(empty)")}`);
  await login(pageC, B);

  const lsAsB = await pageC.evaluate(() => JSON.stringify(Object.entries(localStorage)));
  const survived = A.symbols.filter((s) => lsAsB.includes(s));
  check("A's private activity does NOT survive into B's session in the same tab",
    survived.length === 0, `survived=${JSON.stringify(survived)}`);

  await pageC.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
  await pageC.waitForTimeout(3000);
  const domAsB = await pageC.evaluate(() => document.body.innerText);
  const domLeak = A.symbols.filter((s) => domAsB.includes(s));
  check("A's symbols never render for B after the transition",
    domLeak.length === 0, `leaked=${JSON.stringify(domLeak)}`);
  const meAfter = await apiFromPage(pageC, "/api/auth/me");
  check("the tab is now genuinely B (owner-positive control for the transition)",
    meAfter.body?.email === B.email, `identity=${meAfter.body?.email}`);

  console.log("\n=== T4 — WebSocket negotiation + private frame isolation (D63-5) ===");
  const allA = sockets.A;
  const allB = sockets.B;
  check("a WebSocket was opened in context A", allA.length > 0, `count=${allA.length}`);
  check("a WebSocket was opened in context B", allB.length > 0, `count=${allB.length}`);
  const openA = allA.filter((s) => !s.closed).length;
  check("at least one socket in A stayed OPEN (the D63-5 1006 regression)",
    openA > 0, `open=${openA}/${allA.length}`);

  // Frames B received must never name A's private user id.
  const aId = meA.body?.id || meA.body?._id;
  const bFrames = frames.B.join("\n");
  check("B's socket never received a frame carrying A's user id",
    aId ? !bFrames.includes(aId) : true, `A id=${aId}, B frames=${frames.B.length}`);
  const aFrames = frames.A.join("\n");
  const bId = meB.body?.id || meB.body?._id;
  check("A's socket never received a frame carrying B's user id",
    bId ? !aFrames.includes(bId) : true, `B id=${bId}, A frames=${frames.A.length}`);

  console.log("\n=== T5 — private activity must not reach another tenant's feed ===");
  // THE DEFECT THIS SCRIPT FOUND. `/stocks/{symbol}/patterns` logged its scan
  // line to the shared platform stream, so the symbols one account opened were
  // announced on every other signed-in user's dashboard. It needed exactly this
  // setup to be visible: two accounts, live at once, in real browsers.
  const readFeed = (page) => apiFromPage(page, "/api/ai/activity");

  const feedBBefore = JSON.stringify((await readFeed(pageB)).body);
  check("pre-state: B's feed does not yet mention A's symbol",
    !feedBBefore.includes(A_PRIVATE_SYMBOL), `symbol=${A_PRIVATE_SYMBOL}`);

  // A opens a stock page. Nothing else in the system touches this symbol.
  await pageA.goto(`${APP}/stock/${A_PRIVATE_SYMBOL}`, { waitUntil: "domcontentloaded" });
  await pageA.waitForTimeout(6000);

  const feedA = JSON.stringify((await readFeed(pageA)).body);
  check("owner-positive control: A DOES see their own scan",
    feedA.includes(A_PRIVATE_SYMBOL),
    "without this, 'B sees nothing' is also what deleting the log line produces");

  const feedB = JSON.stringify((await readFeed(pageB)).body);
  check("B's feed never learns which symbol A opened",
    !feedB.includes(A_PRIVATE_SYMBOL), `symbol=${A_PRIVATE_SYMBOL}`);
  check("noise control: a symbol nobody opened is absent from B's feed",
    !feedB.includes(CONTROL_SYMBOL), `symbol=${CONTROL_SYMBOL}`);

  await pageB.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
  await pageB.waitForTimeout(4000);
  const domB2 = await pageB.evaluate(() => document.body.innerText);
  check("and it never renders on B's dashboard",
    !domB2.includes(A_PRIVATE_SYMBOL), `symbol=${A_PRIVATE_SYMBOL}`);

  console.log("\n=== SUMMARY ===");
  const failed = results.filter((r) => !r.pass);
  console.log(`${results.length - failed.length}/${results.length} passed`);
  if (failed.length) {
    console.log("FAILURES:");
    failed.forEach((f) => console.log(`  - ${f.name} (${f.detail || ""})`));
  }

  await browser.close();
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });
