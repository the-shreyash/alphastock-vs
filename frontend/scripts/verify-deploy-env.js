#!/usr/bin/env node
/**
 * Pre-build guard for hosted (Vercel) frontend builds.
 *
 * WHY THIS EXISTS
 * ---------------
 * Create React App inlines every `REACT_APP_*` variable into the JavaScript
 * bundle at BUILD time. Two deployment mistakes therefore fail silently and
 * permanently for that build:
 *
 *   1. `REACT_APP_BACKEND_URL` unset. `services/api.js` builds its base URL as
 *      `${API_URL}/api`, so the bundle ships with the literal string
 *      "undefined/api" — every request becomes a same-origin 404 against the
 *      static host, and the WebSocket URL (`RealtimeProvider.jsx`, derived from
 *      the same variable) is `undefined`. The build itself succeeds.
 *   2. A secret placed in a `REACT_APP_*` variable. It is published to every
 *      visitor the moment the deployment goes live, and "rotating" it later
 *      does not remove it from any cached bundle.
 *
 * Both are cheaper to refuse at build time than to discover in production.
 *
 * SCOPE
 * -----
 * Invoked only by `frontend/vercel.json`'s `buildCommand`. `npm run build`,
 * local development and `frontend-ci.yml` never run it, so their behaviour is
 * unchanged. See docs/deployment/VERCEL.md.
 */

/** Name fragments that must never appear in a client-visible variable. */
const SECRET_NAME_PATTERN = /(SECRET|PASSWORD|PASSWD|PRIVATE|TOKEN|CREDENTIAL|API_KEY|MONGO|REDIS|JWT)/i;

/**
 * Validate a build environment. Pure: takes an env object, returns findings.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {{ errors: string[], warnings: string[] }}
 */
function verify(env) {
  const errors = [];
  const warnings = [];

  const raw = (env.REACT_APP_BACKEND_URL || "").trim();
  if (!raw) {
    errors.push(
      "REACT_APP_BACKEND_URL is not set. The bundle would call \"undefined/api\". " +
        "Set it to the backend's public origin, e.g. https://api.example.com."
    );
  } else {
    let url = null;
    try {
      url = new URL(raw);
    } catch (_) {
      errors.push(`REACT_APP_BACKEND_URL=${JSON.stringify(raw)} is not an absolute URL.`);
    }
    if (url) {
      if (url.protocol !== "https:") {
        // An https page cannot call an http API (mixed content), and the
        // WebSocket scheme is derived from this one (https -> wss).
        errors.push("REACT_APP_BACKEND_URL must use https:// for a hosted build.");
      }
      if (raw.endsWith("/") || (url.pathname && url.pathname !== "/") || url.search || url.hash) {
        // api.js appends "/api"; a trailing slash or path produces "//api" or
        // "/prefix/api", neither of which the backend serves.
        errors.push(
          "REACT_APP_BACKEND_URL must be a bare origin (scheme + host [+ port]) " +
            "with no path, query or trailing slash."
        );
      }
    }
  }

  const leaked = Object.keys(env)
    .filter((name) => name.startsWith("REACT_APP_") && SECRET_NAME_PATTERN.test(name))
    .sort();
  if (leaked.length) {
    errors.push(
      `Client-visible variable(s) look like secrets: ${leaked.join(", ")}. ` +
        "Every REACT_APP_* value is published in the bundle — move secrets to the backend."
    );
  }

  if (!(env.REACT_APP_GOOGLE_CLIENT_ID || "").trim()) {
    warnings.push("REACT_APP_GOOGLE_CLIENT_ID is not set — \"Continue with Google\" will be unavailable.");
  }

  return { errors, warnings };
}

function main() {
  const { errors, warnings } = verify(process.env);
  warnings.forEach((w) => console.warn(`[verify-deploy-env] warning: ${w}`));
  if (errors.length) {
    errors.forEach((e) => console.error(`[verify-deploy-env] ERROR: ${e}`));
    console.error("[verify-deploy-env] Refusing to build. See docs/deployment/VERCEL.md.");
    process.exit(1);
  }
  console.log("[verify-deploy-env] build environment OK");
}

if (require.main === module) {
  main();
}

module.exports = { verify, SECRET_NAME_PATTERN };
