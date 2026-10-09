/**
 * Design-token integrity (D7.1).
 *
 * Why this test exists
 * --------------------
 * A `var(--name)` pointing at a custom property that was never defined is
 * silently invalid CSS: the declaration is dropped and the element renders with
 * *no* background / colour at all. Nothing throws, no test fails, and the page
 * still "works" — it just quietly stops looking like the rest of the product.
 *
 * This is not hypothetical. Before this test existed the codebase carried 85
 * such references across 13 files (`--bg-secondary`, `--bg-tertiary`,
 * `--bg-hover`, `--profit`, `--warning`, `--surface`, `--text`), which is why
 * the entire Markets / Scanner / Rankings / Sector / Economic-Calendar surface
 * rendered with transparent cards and invisible active states, and why the
 * ErrorBoundary recovery screen — the one screen a user sees when everything
 * else has failed — rendered unstyled.
 *
 * The second check covers a subtler variant of the same bug: the shadcn compat
 * tokens hold bare HSL *components* ("240 4.8% 95.9%"), not colours. Written as
 * `background: var(--accent)` that is invalid; it is only valid wrapped as
 * `hsl(var(--accent))`. Twenty sites had it the wrong way round.
 */
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..");

/** Custom properties supplied by a library at runtime, not by our stylesheets. */
const EXTERNAL_PREFIXES = ["--radix-", "--tw-"];

function walk(dir, acc = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      walk(full, acc);
    } else if (/\.(jsx?|css)$/.test(entry.name)) {
      // This file quotes broken token names in its own documentation, so it
      // must not scan itself.
      if (full !== __filename) acc.push(full);
    }
  }
  return acc;
}

const FILES = walk(SRC);

/**
 * Every custom property defined anywhere in src — including the ones the
 * landing pages declare inside their inline `<style>` template literals, which
 * is why we scan .jsx for definitions too rather than only index.css.
 */
function collectDefinitions() {
  const defined = new Set();
  for (const file of FILES) {
    const text = fs.readFileSync(file, "utf8");
    for (const m of text.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) {
      defined.add(m[1]);
    }
  }
  return defined;
}

/** `name` -> list of "file:line" where `var(--name)` is referenced. */
function collectReferences() {
  const refs = new Map();
  for (const file of FILES) {
    const rel = path.relative(SRC, file);
    const lines = fs.readFileSync(file, "utf8").split("\n");
    lines.forEach((line, i) => {
      for (const m of line.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)\s*([,)])/g)) {
        const [, name, next] = m;
        // `var(--x, fallback)` still renders, but it is a reference to a token
        // that does not exist; we want those cleaned up too, so they count.
        if (!refs.has(name)) refs.set(name, []);
        refs.get(name).push(`${rel}:${i + 1}`);
        void next;
      }
    });
  }
  return refs;
}

describe("design tokens", () => {
  const defined = collectDefinitions();
  const references = collectReferences();

  it("scans a meaningful amount of source (guards against a vacuous pass)", () => {
    // If the walker or the regexes silently stopped matching, every assertion
    // below would pass by finding nothing. Anchor them to real numbers.
    expect(FILES.length).toBeGreaterThan(100);
    expect(defined.size).toBeGreaterThan(50);
    expect(references.size).toBeGreaterThan(30);
  });

  it("defines every custom property that is referenced with var()", () => {
    const undefinedRefs = [];
    for (const [name, sites] of references) {
      if (EXTERNAL_PREFIXES.some((p) => name.startsWith(p))) continue;
      if (defined.has(name)) continue;
      undefinedRefs.push(`${name}  <-  ${sites.slice(0, 4).join(", ")}${sites.length > 4 ? ` (+${sites.length - 4} more)` : ""}`);
    }
    expect(undefinedRefs).toEqual([]);
  });

  it("never uses an HSL-component token as a raw colour value", () => {
    // Tokens whose *value* is a bare HSL triple ("240 4.8% 95.9%") are only
    // valid inside hsl(). Find them by value, so a newly added shadcn token is
    // covered automatically rather than needing to be listed here.
    const cssText = fs.readFileSync(path.join(SRC, "index.css"), "utf8");
    const hslComponentTokens = new Set();
    for (const m of cssText.matchAll(/(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);/g)) {
      const [, name, value] = m;
      if (/^\s*[\d.]+\s+[\d.]+%\s+[\d.]+%\s*$/.test(value)) hslComponentTokens.add(name);
    }
    expect(hslComponentTokens.size).toBeGreaterThan(5); // the probe found them

    const rawUses = [];
    for (const file of FILES) {
      if (path.relative(SRC, file).startsWith("components/ui/")) continue; // shadcn primitives use hsl() correctly
      const rel = path.relative(SRC, file);
      const lines = fs.readFileSync(file, "utf8").split("\n");
      lines.forEach((line, i) => {
        for (const name of hslComponentTokens) {
          const bare = `var(${name})`;
          let idx = line.indexOf(bare);
          while (idx !== -1) {
            const before = line.slice(Math.max(0, idx - 4), idx);
            if (!/hsla?\($/.test(before)) rawUses.push(`${name}  <-  ${rel}:${i + 1}`);
            idx = line.indexOf(bare, idx + 1);
          }
        }
      });
    }
    expect(rawUses).toEqual([]);
  });

  it("defines each themeable token in BOTH the light and dark blocks", () => {
    // A token defined only under :root leaks the light value into dark mode.
    const cssText = fs.readFileSync(path.join(SRC, "index.css"), "utf8");
    const lightBlock = cssText.slice(
      cssText.indexOf(':root,'),
      cssText.indexOf('[data-theme="dark"]')
    );
    const darkBlock = cssText.slice(cssText.indexOf('[data-theme="dark"]'));

    const namesIn = (block) =>
      new Set([...block.matchAll(/^\s*(--[a-zA-Z0-9-]+)\s*:/gm)].map((m) => m[1]));

    const light = namesIn(lightBlock);
    const dark = namesIn(darkBlock);
    expect(light.size).toBeGreaterThan(50);
    expect(dark.size).toBeGreaterThan(40);

    // Type/spacing/radius scales are deliberately theme-independent and live
    // only in the light block.
    const THEME_INDEPENDENT = /^--(fs-|lh-|tracking-|page-|radius$)/;
    const missingInDark = [...light].filter(
      (n) => !THEME_INDEPENDENT.test(n) && !dark.has(n)
    );
    expect(missingInDark).toEqual([]);
  });
});
