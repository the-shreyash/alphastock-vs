/**
 * D6.4 — the frontend's broker-account identity.
 *
 * WHAT THIS PINS
 * --------------
 * The UI used to address a brokerage account by broker name, because the
 * backend model had exactly one account per broker. It now addresses one by
 * `broker_account_id`, and the three ways a UI silently reintroduces the old
 * behaviour are what these tests forbid:
 *
 *   * inferring the account from a broker name;
 *   * indexing into a list (`accounts[0]` — "first connected" renamed);
 *   * following recency ("last connected"), which makes the selection move
 *     under the user between renders.
 *
 * The one selection this module DOES make is stated and tested: a user with
 * exactly one account has it selected, because "the only one there is" is not
 * a choice and a picker with one option is a worse product.
 */
import {
  SELECTED_ACCOUNT_KEY,
  accountLabel,
  clearSelectedAccount,
  findAccount,
  isBrokerAccountId,
  readSelectedAccountId,
  resolveSelection,
  writeSelectedAccountId,
} from "../lib/brokerAccounts";
import { clearTenantLocalState } from "../lib/tenantState";

const ID_A = "ba_" + "a".repeat(32);
const ID_B = "ba_" + "b".repeat(32);
const ID_C = "ba_" + "c".repeat(32);

const account = (id, extra = {}) => ({
  broker_account_id: id,
  broker: "zerodha",
  display_name: "Zerodha",
  external_account_id: "AB1234",
  connected: true,
  ...extra,
});

beforeEach(() => {
  localStorage.clear();
});

describe("the account id is opaque and validated by shape", () => {
  it.each([
    ["a broker name", "zerodha"],
    ["a Mongo ObjectId", "6a9d9bb6b14cacdbb7843dd5"],
    ["a broker's own client code", "AB1234"],
    ["the prefix alone", "ba_"],
    ["the right shape but not hex", "ba_" + "z".repeat(32)],
    ["one character short", "ba_" + "a".repeat(31)],
    ["an empty string", ""],
    ["null", null],
    ["a number", 12345],
    ["an object", { broker_account_id: ID_A }],
  ])("rejects %s", (_label, value) => {
    expect(isBrokerAccountId(value)).toBe(false);
  });

  it("accepts a minted id", () => {
    expect(isBrokerAccountId(ID_A)).toBe(true);
  });

  it("refuses to persist anything that is not one", () => {
    writeSelectedAccountId("zerodha");
    expect(localStorage.getItem(SELECTED_ACCOUNT_KEY)).toBeNull();
    expect(readSelectedAccountId()).toBeNull();
  });
});

describe("selection never infers an account", () => {
  it("selects the single account a user has, and says so", () => {
    expect(resolveSelection([account(ID_A)], null)).toEqual({
      accountId: ID_A,
      reason: "only",
    });
  });

  it("refuses to choose between two accounts", () => {
    const result = resolveSelection([account(ID_A), account(ID_B)], null);
    expect(result.accountId).toBeNull();
    expect(result.reason).toBe("ambiguous");
  });

  it("does not fall back to the first entry when several exist", () => {
    // The exact shape of "first connected". If this ever returns ID_A the UI
    // has started picking for the user again.
    expect(resolveSelection([account(ID_A), account(ID_B), account(ID_C)], null).accountId)
      .toBeNull();
  });

  it("does not follow recency", () => {
    const older = account(ID_A, { connected_at: "2026-01-01T00:00:00Z" });
    const newer = account(ID_B, { connected_at: "2026-09-01T00:00:00Z" });
    expect(resolveSelection([older, newer], null).accountId).toBeNull();
    // And reversing the input order changes nothing, so no ordering of the
    // server's list can become a selection.
    expect(resolveSelection([newer, older], null).accountId).toBeNull();
  });

  it("keeps a remembered account that is still in the list", () => {
    expect(resolveSelection([account(ID_A), account(ID_B)], ID_B)).toEqual({
      accountId: ID_B,
      reason: "stored",
    });
  });

  it("discards a remembered account that is gone, and does not substitute another", () => {
    // The account was disconnected, deleted, or belonged to a previous sign-in
    // in this tab. Substituting a sibling would silently retarget every action
    // the user takes next — including an order.
    const result = resolveSelection([account(ID_A), account(ID_B)], ID_C);
    expect(result.accountId).toBeNull();
    expect(result.reason).toBe("ambiguous");
  });

  it("prefers connected accounts and still refuses when several are connected", () => {
    const disconnected = account(ID_A, { connected: false });
    const live = account(ID_B);
    expect(resolveSelection([disconnected, live], null).accountId).toBe(ID_B);
    expect(resolveSelection([live, account(ID_C)], null).accountId).toBeNull();
  });

  it("answers 'none' for a user with no accounts", () => {
    expect(resolveSelection([], null)).toEqual({ accountId: null, reason: "none" });
    expect(resolveSelection(null, null)).toEqual({ accountId: null, reason: "none" });
  });
});

describe("lookup never falls back", () => {
  it("returns null for an id the user does not have", () => {
    expect(findAccount([account(ID_A)], ID_B)).toBeNull();
  });

  it("returns null for a broker name", () => {
    expect(findAccount([account(ID_A)], "zerodha")).toBeNull();
  });
});

describe("the label identifies the account, not the brand", () => {
  it("carries the broker's own account number", () => {
    expect(accountLabel(account(ID_A))).toBe("Zerodha · AB1234");
  });

  it("never renders the internal routing handle", () => {
    expect(accountLabel(account(ID_A))).not.toContain(ID_A);
  });

  it("degrades to the broker name when the broker named no account", () => {
    expect(accountLabel(account(ID_A, { external_account_id: null }))).toBe("Zerodha");
  });
});

describe("identity transition clears the selected account", () => {
  it("is wiped by the D6.3 tenant purge without being listed there", () => {
    // The purge keeps a keep-list rather than a clear-list, so a per-user key
    // added by a later sprint is purged by default. This asserts that property
    // for THIS key rather than trusting it.
    writeSelectedAccountId(ID_A);
    expect(readSelectedAccountId()).toBe(ID_A);

    clearTenantLocalState();

    expect(readSelectedAccountId()).toBeNull();
    expect(localStorage.getItem(SELECTED_ACCOUNT_KEY)).toBeNull();
  });

  it("can be cleared explicitly on disconnect", () => {
    writeSelectedAccountId(ID_A);
    clearSelectedAccount();
    expect(readSelectedAccountId()).toBeNull();
  });

  it("survives storage being unavailable", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("blocked");
      },
    });
    try {
      expect(() => writeSelectedAccountId(ID_A)).not.toThrow();
      expect(readSelectedAccountId()).toBeNull();
    } finally {
      if (original) Object.defineProperty(window, "localStorage", original);
    }
  });
});

describe("the broker service addresses accounts by id", () => {
  it("builds account-scoped URLs and never a broker-scoped one for account data", async () => {
    jest.resetModules();
    const calls = [];
    jest.doMock("../services/api", () => ({
      __esModule: true,
      default: {
        get: (url) => { calls.push(["GET", url]); return Promise.resolve({ data: {} }); },
        post: (url) => { calls.push(["POST", url]); return Promise.resolve({ data: {} }); },
        patch: (url) => { calls.push(["PATCH", url]); return Promise.resolve({ data: {} }); },
        delete: (url) => { calls.push(["DELETE", url]); return Promise.resolve({ data: {} }); },
      },
    }));
    const { default: service } = await import("../services/brokerService");

    await service.account.holdings(ID_A);
    await service.account.placeOrder(ID_A, {});
    await service.account.disconnect(ID_A);
    await service.account.cancelOrder(ID_A, "ORD-1");

    expect(calls).toEqual([
      ["GET", `/brokers/accounts/${ID_A}/holdings`],
      ["POST", `/brokers/accounts/${ID_A}/orders`],
      ["POST", `/brokers/accounts/${ID_A}/disconnect`],
      ["DELETE", `/brokers/accounts/${ID_A}/orders/ORD-1`],
    ]);
    for (const [, url] of calls) expect(url).not.toContain("zerodha");
    jest.dontMock("../services/api");
  });

  it("recognises the server's refusal to choose between accounts", async () => {
    const { isAmbiguousAccountError } = await import("../services/brokerService");
    expect(isAmbiguousAccountError({
      response: { status: 409, data: { detail: "…must be addressed to a specific broker_account_id." } },
    })).toBe(true);
    expect(isAmbiguousAccountError({ response: { status: 409, data: { detail: "other" } } })).toBe(false);
    expect(isAmbiguousAccountError({ response: { status: 404, data: {} } })).toBe(false);
  });
});
