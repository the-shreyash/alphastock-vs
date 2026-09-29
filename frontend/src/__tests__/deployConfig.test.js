/**
 * Deployment preparation — the hosted-build guard and the Vercel project file.
 *
 * Why pin a JSON file with a test: every property below exists because the
 * platform default is WRONG for this tree (see docs/deployment/VERCEL.md), and
 * each one fails silently or only at deploy time. A reviewer "simplifying"
 * vercel.json back to the defaults would otherwise get a green CI and a broken
 * deployment.
 */
const { verify } = require("../../scripts/verify-deploy-env");
const vercel = require("../../vercel.json");

const OK = {
  REACT_APP_BACKEND_URL: "https://api.example.com",
  REACT_APP_GOOGLE_CLIENT_ID: "123.apps.googleusercontent.com",
};

describe("verify-deploy-env", () => {
  test("accepts a bare https origin", () => {
    expect(verify(OK)).toEqual({ errors: [], warnings: [] });
  });

  test("accepts an origin with an explicit port", () => {
    expect(verify({ ...OK, REACT_APP_BACKEND_URL: "https://api.example.com:8443" }).errors).toEqual([]);
  });

  test.each([
    ["unset", undefined],
    ["blank", "   "],
    ["not a URL", "api.example.com"],
    ["plain http", "http://api.example.com"],
    ["trailing slash", "https://api.example.com/"],
    ["has a path", "https://api.example.com/api"],
    ["has a query", "https://api.example.com?x=1"],
  ])("rejects a backend URL that is %s", (_label, value) => {
    const { errors } = verify({ ...OK, REACT_APP_BACKEND_URL: value });
    expect(errors.length).toBeGreaterThan(0);
  });

  test.each(["REACT_APP_JWT_SECRET", "REACT_APP_MONGO_URL", "REACT_APP_OPENAI_API_KEY",
              "REACT_APP_ADMIN_PASSWORD", "REACT_APP_BROKER_TOKEN"])(
    "refuses a secret-shaped client variable %s", (name) => {
      const { errors } = verify({ ...OK, [name]: "x" });
      expect(errors.join(" ")).toContain(name);
    });

  test("does not flag the public Google client id", () => {
    expect(verify(OK).errors).toEqual([]);
  });

  test("missing Google client id is a warning, not an error", () => {
    const { errors, warnings } = verify({ REACT_APP_BACKEND_URL: OK.REACT_APP_BACKEND_URL });
    expect(errors).toEqual([]);
    expect(warnings).toHaveLength(1);
  });
});

describe("frontend/vercel.json", () => {
  test("installs exactly the lockfile with the flag CI uses", () => {
    // Without --legacy-peer-deps npm refuses eslint 9 alongside react-scripts'
    // eslint 8; without `ci` Vercel may pick yarn.lock (both lockfiles exist).
    expect(vercel.installCommand).toBe("npm ci --legacy-peer-deps");
  });

  test("build runs the guard first and neutralises Vercel's CI=1", () => {
    // CI=1 makes react-scripts fail on the 62 pre-existing ESLint warnings;
    // frontend-ci.yml builds with CI unset for the same reason.
    expect(vercel.buildCommand.startsWith("node scripts/verify-deploy-env.js && ")).toBe(true);
    expect(vercel.buildCommand).toContain("CI=false npm run build");
    expect(vercel.outputDirectory).toBe("build");
  });

  test("client-side routes (incl. OAuth callbacks) fall back to index.html", () => {
    const [rule] = vercel.rewrites;
    expect(rule.destination).toBe("/index.html");
    // Vercel `source` patterns are path-to-regexp; this one is also a plain
    // regular expression, so it can be exercised directly.
    const re = new RegExp(`^${rule.source}$`);
    expect(re.test("/auth/google/callback")).toBe(true);
    expect(re.test("/broker/callback")).toBe(true);
    // A missing hashed chunk must 404, not return HTML that fails to parse.
    expect(re.test("/static/js/main.abc123.js")).toBe(false);
  });
});
