/**
 * Formatters — the last transform before a number reaches a trader's eyes.
 *
 * Production failures these catch: a null/NaN price rendering as "NaN" or
 * "₹NaN" instead of the "--" placeholder; a loss rendering without its sign; a
 * market cap losing its Cr/L scale. All of these are silent-wrong-number bugs,
 * the worst class in a trading UI.
 */
import {
  formatCurrency,
  formatNumber,
  formatPercent,
  formatLargeNumber,
  cn,
} from "../formatters";

describe("formatCurrency", () => {
  it("renders INR with Indian digit grouping", () => {
    expect(formatCurrency(1234.5)).toBe("₹1,234.50");
    // Indian grouping is 2-2-3, not 3-3-3 — a lakh must not read as "123,456".
    expect(formatCurrency(123456.789)).toBe("₹1,23,456.79");
  });

  it("honours the decimals argument", () => {
    expect(formatCurrency(1234.567, 0)).toBe("₹1,235");
    expect(formatCurrency(1234.5, 4)).toBe("₹1,234.5000");
  });

  it("renders negatives with a sign rather than dropping it", () => {
    expect(formatCurrency(-2500)).toContain("2,500.00");
    expect(formatCurrency(-2500)).toMatch(/^-/);
  });

  it.each([
    ["null", null],
    ["undefined", undefined],
    ["a non-numeric string", "not-a-number"],
    ["NaN", NaN],
  ])("returns the placeholder for %s instead of NaN", (_label, value) => {
    expect(formatCurrency(value)).toBe("--");
  });

  it("formats zero as a real value, not as missing data", () => {
    expect(formatCurrency(0)).toBe("₹0.00");
  });
});

describe("formatNumber", () => {
  it("groups in the Indian system", () => {
    expect(formatNumber(123456.789)).toBe("1,23,456.79");
  });

  it("returns the placeholder for missing values", () => {
    expect(formatNumber(null)).toBe("--");
    expect(formatNumber(undefined)).toBe("--");
  });

  it("formats zero rather than treating it as missing", () => {
    expect(formatNumber(0)).toBe("0.00");
  });
});

describe("formatPercent", () => {
  it("prefixes gains with + so direction is unambiguous", () => {
    expect(formatPercent(1.014)).toBe("+1.01%");
    expect(formatPercent(0)).toBe("+0.00%");
  });

  it("keeps the minus sign on losses", () => {
    expect(formatPercent(-2.567)).toBe("-2.57%");
  });

  it("returns the placeholder for missing values", () => {
    expect(formatPercent(null)).toBe("--");
    expect(formatPercent(undefined)).toBe("--");
  });
});

describe("formatLargeNumber", () => {
  it.each([
    [250000000, "25.00 Cr"],
    [10000000, "1.00 Cr"],
    [2500000, "25.00 L"],
    [100000, "1.00 L"],
    [15000, "15.0K"],
    [1000, "1.0K"],
  ])("scales %d to %s", (input, expected) => {
    expect(formatLargeNumber(input)).toBe(expected);
  });

  it("leaves sub-thousand values unscaled", () => {
    expect(formatLargeNumber(999)).toBe("999");
  });

  it("returns the placeholder for missing values", () => {
    expect(formatLargeNumber(null)).toBe("--");
  });
});

describe("cn", () => {
  it("joins truthy class names and drops falsy ones", () => {
    expect(cn("a", false && "b", null, undefined, "", "c")).toBe("a c");
  });

  it("returns an empty string when nothing applies", () => {
    expect(cn(false, null)).toBe("");
  });
});
