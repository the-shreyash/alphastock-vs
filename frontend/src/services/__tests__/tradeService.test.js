/**
 * Trade service — the frontend's gateway to the Trading Engine.
 *
 * Components never call /api/trades directly (CODING_STANDARDS.md), so every
 * order, modification and exit in the product passes through this module. Two
 * things are tested: that each call hits the URL and method the backend
 * actually exposes, and that a risk-manager rejection is normalised into
 * something the UI can render.
 *
 * Production failure this catches: an exit or modify sent to the wrong path or
 * with the wrong verb — a silent 404/405 that looks to the trader like the
 * request simply did nothing.
 */
import MockAdapter from "axios-mock-adapter";
import api from "../api";
import tradeService, { tradeErrorDetails } from "../tradeService";
import { HTTP } from "../../test-utils/apiMock";
import { testOpenTrade, errorDetailRisk } from "../../test-utils/fixtures";

let mock;

beforeEach(() => {
  mock = new MockAdapter(api, { onNoMatch: "throwException" });
});

afterEach(() => {
  mock.restore();
});

describe("read endpoints", () => {
  it.each([
    ["list", "/trades"],
    ["active", "/trades/active"],
    ["history", "/trades/history"],
    ["pnl", "/trades/pnl"],
    ["riskSummary", "/trades/risk/summary"],
  ])("%s reads %s and unwraps the body", async (method, url) => {
    mock.onGet(url).reply(HTTP.OK, [testOpenTrade]);

    // Unwrapped: callers get the payload, never the axios envelope.
    await expect(tradeService[method]()).resolves.toEqual([testOpenTrade]);
    expect(mock.history.get[0].url).toBe(url);
  });
});

describe("write endpoints", () => {
  it("creates a trade with POST /trades", async () => {
    mock.onPost("/trades").reply(HTTP.OK, { _id: "t_new" });

    await tradeService.create({ symbol: "TESTCO", quantity: 10 });

    expect(mock.history.post[0].url).toBe("/trades");
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ symbol: "TESTCO", quantity: 10 });
  });

  it("validates a trade without placing it", async () => {
    mock.onPost("/trades/validate").reply(HTTP.OK, { ok: true });

    await tradeService.validate({ symbol: "TESTCO" });

    expect(mock.history.post[0].url).toBe("/trades/validate");
  });

  it("modifies a trade with PUT to its own path", async () => {
    mock.onPut(`/trades/${testOpenTrade._id}`).reply(HTTP.OK, {});

    await tradeService.modify(testOpenTrade._id, { stop_loss: 1180 });

    expect(mock.history.put[0].url).toBe(`/trades/${testOpenTrade._id}`);
    expect(JSON.parse(mock.history.put[0].data)).toEqual({ stop_loss: 1180 });
  });

  it("exits a trade through its exit sub-resource", async () => {
    mock.onPost(`/trades/${testOpenTrade._id}/exit`).reply(HTTP.OK, {});

    await tradeService.exit(testOpenTrade._id, { reason: "manual" });

    expect(mock.history.post[0].url).toBe(`/trades/${testOpenTrade._id}/exit`);
  });
});

describe("order history query", () => {
  it("requests all brokers when given no filter", async () => {
    mock.onGet("/orders").reply(HTTP.OK, []);

    await tradeService.orders();

    expect(mock.history.get[0].url).toBe("/orders");
  });

  it("filters by broker", async () => {
    mock.onGet("/orders?broker=zerodha").reply(HTTP.OK, []);

    await tradeService.orders({ broker: "zerodha" });

    expect(mock.history.get[0].url).toBe("/orders?broker=zerodha");
  });

  it("asks the backend to refresh from the broker when requested", async () => {
    mock.onGet("/orders?broker=zerodha&refresh=true").reply(HTTP.OK, []);

    await tradeService.orders({ broker: "zerodha", refresh: true });

    expect(mock.history.get[0].url).toBe("/orders?broker=zerodha&refresh=true");
  });
});

describe("tradeErrorDetails", () => {
  it("unpacks a risk-manager rejection into message, violations and warnings", async () => {
    const err = { response: { status: 422, data: errorDetailRisk } };

    expect(tradeErrorDetails(err)).toEqual({
      message: "Trade rejected by risk manager",
      violations: ["Position size exceeds 10% of capital"],
      warnings: ["Sector exposure already at 35%"],
    });
  });

  it("handles a plain string rejection", () => {
    const err = { response: { data: { detail: "Market is closed" } } };

    expect(tradeErrorDetails(err)).toEqual({ message: "Market is closed", violations: [], warnings: [] });
  });

  it("supplies empty lists when the object omits them", () => {
    const err = { response: { data: { detail: { message: "Rejected" } } } };

    expect(tradeErrorDetails(err)).toEqual({ message: "Rejected", violations: [], warnings: [] });
  });

  it.each([
    ["a network error", new Error("Network Error")],
    ["an empty response", { response: { data: {} } }],
    ["no error at all", undefined],
  ])("falls back to a usable message for %s", (_label, err) => {
    const result = tradeErrorDetails(err);

    expect(result.message).toBeTruthy();
    expect(result.violations).toEqual([]);
    expect(result.warnings).toEqual([]);
  });

  it("honours a caller-supplied fallback", () => {
    expect(tradeErrorDetails({}, "Could not modify the position.").message)
      .toBe("Could not modify the position.");
  });
});
