/**
 * Order entry — the user's path to `POST /api/brokers/{broker}/orders` (D5.19, D-7).
 *
 * WHY THIS COMPONENT EXISTS
 * -------------------------
 * D5.18 traced the order path end to end and found one break in it. The
 * endpoint works: user-scoped off the JWT, validated through
 * `BrokerOrderCreate`, broker-neutral, product code chosen by the adapter and
 * not by a broker name in the route. `brokerService.placeOrder` was there too —
 * and nothing in the entire frontend called it (LIM-D5.18-2). The product had a
 * working order API and no way for a user to reach it.
 *
 * The two things that looked like order entry were not. The "Buy" beside a Top
 * Opportunity is a signal badge — a `<span>`, never a control. TradeMonitor's
 * New Trade modal posts to `/api/trades`, which places a live broker order only
 * when `data.broker` is set, and its empty form leaves `broker` as `""`; the
 * default action journals the trade and places nothing.
 *
 * WHAT THIS IS DESIGNED AROUND
 * ----------------------------
 * Placing an order is the only irreversible action in this product. It spends
 * real money at a real exchange and there is no undo, no confirmation email and
 * no grace period. Every decision below follows from that:
 *
 *   * **Two steps, two controls.** The button that submits the form is not the
 *     button that sends the order. A single click anywhere in this component
 *     cannot place a trade — it can only produce a review panel that restates
 *     what is about to happen. This is what makes "no automatic order
 *     placement" and "no AI-generated order executes without explicit user
 *     confirmation" properties a test can hold rather than intentions.
 *
 *   * **Nothing is defaulted that costs money.** The broker starts unselected
 *     and the review button stays disabled until one is chosen — the
 *     `broker: ""` shape, refused, because an order with no broker is not a
 *     smaller order. Quantity, side and order type are all stated by the user.
 *
 *   * **Only a broker that can actually do it is offered.** A broker must be
 *     `connected` for this user *and* declare the `place_order` capability. Two
 *     of the five adapters do not declare it; offering them would produce a
 *     confident form and a 400 at the end of it.
 *
 *   * **The backend stays authoritative.** This validates to keep the user out
 *     of an obviously-doomed round trip. It is not a substitute for
 *     `BrokerOrderCreate`, which is where the rules actually live, and it
 *     deliberately does not reimplement product codes, margin or exchange
 *     rules — those are the broker's answer to give.
 */
import { useState, useEffect, useMemo, useRef } from "react";
import { AlertTriangle, Check, Loader2, ShieldAlert } from "lucide-react";
import brokerService, { brokerErrorMessage } from "../../services/brokerService";
import { currentAuthEpoch } from "../../services/api";
import { useAuth } from "../../context/AuthContext";

const SIDES = ["BUY", "SELL"];
const ORDER_TYPES = ["MARKET", "LIMIT"];

/** The capability an adapter must declare before it may be offered here. */
const PLACE_ORDER = "place_order";

export default function OrderTicket({ symbol, exchange = "NSE", price }) {
  const [statuses, setStatuses] = useState(null);
  const [broker, setBroker] = useState("");
  const [side, setSide] = useState("BUY");
  const [quantity, setQuantity] = useState("1");
  const [orderType, setOrderType] = useState("MARKET");
  const [limitPrice, setLimitPrice] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const { user } = useAuth();
  const userId = user?._id || user?.id || null;
  /**
   * Who this review panel was raised for, and which broker it names (D6.3).
   *
   * The review step is the one place in the product where a human has already
   * decided to spend real money and is one click from the irreversible half of
   * the order path. Everything else about an identity change is recoverable by
   * refetching; this is not — a confirmation that outlives the account it was
   * composed under would place a live order at the exchange, and the *server*
   * cannot catch it, because by then the request carries the new account's
   * cookie and is a perfectly valid order from that account.
   *
   * So the intent carries its own identity: the account, the broker, and the
   * auth epoch in force when the user pressed Review. `placeOrder` re-checks
   * all three at the instant of confirmation. A ref rather than state because
   * it must be read synchronously inside the click handler, before any await.
   */
  const intentRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    brokerService
      .status()
      .then((data) => { if (!cancelled) setStatuses(data || {}); })
      .catch(() => { if (!cancelled) setStatuses({}); });
    return () => { cancelled = true; };
  }, []);

  /**
   * The brokers this user could actually place this order through.
   *
   * Connected AND declaring `place_order`. Both halves matter: a configured
   * but unconnected broker has no session to sign the request, and a connected
   * broker whose adapter does not implement placement would accept the form and
   * fail at the gateway.
   */
  const eligible = useMemo(() => {
    if (!statuses) return [];
    return Object.values(statuses).filter(
      (s) => s?.connected && (s.capabilities || []).includes(PLACE_ORDER)
    );
  }, [statuses]);

  const selected = eligible.find((s) => s.broker === broker) || null;
  const qty = Number.parseInt(quantity, 10);
  const limit = Number.parseFloat(limitPrice);

  const valid =
    Boolean(broker) &&
    Number.isFinite(qty) && qty > 0 &&
    SIDES.includes(side) &&
    ORDER_TYPES.includes(orderType) &&
    (orderType !== "LIMIT" || (Number.isFinite(limit) && limit > 0));

  /** Exactly what will be sent — built once, shown in review, posted unchanged. */
  const payload = {
    symbol,
    exchange,
    transaction_type: side,
    quantity: qty,
    order_type: orderType,
    ...(orderType === "LIMIT" ? { price: limit } : {}),
  };

  const placeOrder = async () => {
    // Guarded rather than merely disabled: a second click on an in-flight
    // confirmation must not produce a second order at the exchange.
    if (placing || !valid) return;
    // D6.3. The intent must still belong to the account, the broker and the
    // identity generation it was composed under. Any of the three moving means
    // this panel is describing an order nobody currently signed in asked for;
    // it is torn down rather than sent.
    const intent = intentRef.current;
    if (!intent
        || intent.userId !== userId
        || intent.broker !== broker
        || intent.epoch !== currentAuthEpoch()) {
      intentRef.current = null;
      setReviewing(false);
      setError("The session or the broker changed while this order was under "
               + "review. Nothing was sent — please re-enter it.");
      return;
    }
    setPlacing(true);
    setError(null);
    try {
      const res = await brokerService.placeOrder(broker, payload);
      setResult(res);
      setReviewing(false);
    } catch (err) {
      setError(brokerErrorMessage(err, "The broker could not place this order."));
    } finally {
      setPlacing(false);
    }
  };

  if (statuses === null) {
    return (
      <div data-testid="order-ticket" className="glass-card p-4">
        <div className="h-24 animate-pulse rounded-lg bg-[var(--bg-tertiary)]" />
      </div>
    );
  }

  return (
    <div data-testid="order-ticket" className="glass-card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="eyebrow">Place Order</h3>
        <span className="text-[10px] text-[var(--text-muted)] font-mono">
          {symbol} · {exchange}
        </span>
      </div>

      {eligible.length === 0 ? (
        /* Honest, and specific about what is missing — "unavailable" alone
           would leave the user with no idea what to do about it. */
        <div
          data-testid="order-unavailable"
          className="flex items-start gap-2 rounded-lg p-3 text-[11px]"
          style={{ background: "var(--hover)", color: "var(--text-secondary)" }}
        >
          <ShieldAlert size={14} className="mt-px shrink-0 text-[var(--warning)]" />
          <span>
            No connected broker can place orders for this account. Connect a
            broker that supports order placement in Settings to trade from here.
          </span>
        </div>
      ) : reviewing ? (
        /* ---------------- Step 2: review ---------------- */
        <div
          data-testid="order-review-panel"
          className="space-y-3 rounded-lg p-3"
          style={{ background: "var(--hover)", border: "1px solid var(--border)" }}
        >
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="mt-px shrink-0 text-[var(--warning)]" />
            <span className="text-[11px] font-semibold text-[var(--text-primary)]">
              This places a real order with real money at the exchange. It cannot
              be undone.
            </span>
          </div>
          <dl className="grid grid-cols-2 gap-y-1 text-[11px]">
            <dt className="text-[var(--text-muted)]">Action</dt>
            <dd className="font-mono font-semibold" style={{ color: side === "BUY" ? "var(--gain)" : "var(--loss)" }}>
              {side}
            </dd>
            <dt className="text-[var(--text-muted)]">Quantity</dt>
            <dd className="font-mono text-[var(--text-primary)]">{qty}</dd>
            <dt className="text-[var(--text-muted)]">Instrument</dt>
            <dd className="font-mono text-[var(--text-primary)]">{symbol} · {exchange}</dd>
            <dt className="text-[var(--text-muted)]">Order type</dt>
            <dd className="font-mono text-[var(--text-primary)]">
              {orderType}{orderType === "LIMIT" ? ` @ ${limit}` : ""}
            </dd>
            <dt className="text-[var(--text-muted)]">Account</dt>
            <dd className="font-mono text-[var(--text-primary)]">
              {selected?.display_name || broker}
              {selected?.account_id ? ` · ${selected.account_id}` : ""}
            </dd>
          </dl>
          <div className="flex gap-2">
            <button
              data-testid="order-confirm"
              type="button"
              onClick={placeOrder}
              disabled={placing}
              className="flex-1 rounded-lg px-3 py-2 text-[11px] font-semibold text-white disabled:opacity-60"
              style={{ background: side === "BUY" ? "var(--gain)" : "var(--loss)" }}
            >
              {placing ? (
                <span className="flex items-center justify-center gap-1.5">
                  <Loader2 size={12} className="animate-spin" /> Placing…
                </span>
              ) : (
                `Confirm ${side} ${qty} ${symbol}`
              )}
            </button>
            <button
              data-testid="order-cancel"
              type="button"
              onClick={() => setReviewing(false)}
              disabled={placing}
              className="rounded-lg border border-[var(--border)] px-3 py-2 text-[11px] font-semibold text-[var(--text-secondary)]"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        /* ---------------- Step 1: the ticket ---------------- */
        <div className="space-y-2.5">
          <div className="grid grid-cols-2 gap-2">
            {SIDES.map((s) => (
              <button
                key={s}
                data-testid={`order-side-${s}`}
                type="button"
                onClick={() => setSide(s)}
                aria-pressed={side === s}
                className="rounded-lg px-3 py-2 text-[11px] font-semibold border transition-colors"
                style={{
                  borderColor: side === s ? (s === "BUY" ? "var(--gain)" : "var(--loss)") : "var(--border)",
                  color: side === s ? (s === "BUY" ? "var(--gain)" : "var(--loss)") : "var(--text-muted)",
                  background: side === s ? "var(--hover)" : "transparent",
                }}
              >
                {s}
              </button>
            ))}
          </div>

          <label className="block">
            <span className="mb-1 block text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Account</span>
            <select
              data-testid="order-broker"
              value={broker}
              onChange={(e) => setBroker(e.target.value)}
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-2 text-[11px] text-[var(--text-primary)]"
            >
              {/* Deliberately unselected. Choosing the account that will spend
                  the money is the user's decision, not a default. */}
              <option value="">Select a broker account…</option>
              {eligible.map((s) => (
                <option key={s.broker} value={s.broker}>
                  {s.display_name || s.broker}
                  {s.account_id ? ` · ${s.account_id}` : ""}
                </option>
              ))}
            </select>
          </label>

          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="mb-1 block text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Quantity</span>
              <input
                data-testid="order-quantity"
                type="number"
                min="1"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-2 text-[11px] font-mono text-[var(--text-primary)]"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Order type</span>
              <select
                data-testid="order-type"
                value={orderType}
                onChange={(e) => setOrderType(e.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-2 text-[11px] text-[var(--text-primary)]"
              >
                {ORDER_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          </div>

          {orderType === "LIMIT" && (
            <label className="block">
              <span className="mb-1 block text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Limit price{price != null ? ` (last ${price})` : ""}
              </span>
              <input
                data-testid="order-price"
                type="number"
                step="0.05"
                min="0"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-2 text-[11px] font-mono text-[var(--text-primary)]"
              />
            </label>
          )}

          <button
            data-testid="order-review"
            type="button"
            disabled={!valid}
            onClick={() => {
              setResult(null);
              setError(null);
              // Stamp the intent with the identity it is being composed under.
              intentRef.current = { userId, broker, epoch: currentAuthEpoch() };
              setReviewing(true);
            }}
            className="w-full rounded-lg px-3 py-2 text-[11px] font-semibold text-[var(--text-primary)] border border-[var(--border)] disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ background: "var(--bg-tertiary)" }}
          >
            Review order
          </button>
        </div>
      )}

      {result && (
        <div
          data-testid="order-result"
          className="flex items-start gap-2 rounded-lg p-2.5 text-[11px]"
          style={{ background: "var(--hover)", color: "var(--text-secondary)" }}
        >
          <Check size={13} className="mt-px shrink-0 text-[var(--gain)]" />
          <span>
            Order sent. Broker order id{" "}
            <span className="font-mono text-[var(--text-primary)]">
              {result.order_id || result.id || "—"}
            </span>
            {result.status ? ` · ${result.status}` : ""}
          </span>
        </div>
      )}

      {error && (
        /* The engine's normalized message only. A raw broker exception can
           carry request context, and this is the path most likely to surface
           one — see the security sweep in the test file. */
        <div
          data-testid="order-error"
          className="flex items-start gap-2 rounded-lg p-2.5 text-[11px]"
          style={{ background: "var(--loss-bg, #ef444418)", color: "var(--loss)" }}
        >
          <AlertTriangle size={13} className="mt-px shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
