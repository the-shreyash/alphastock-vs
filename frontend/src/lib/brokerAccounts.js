/**
 * The frontend's broker-account identity (D6.4).
 *
 * WHAT CHANGED, AND WHY THE UI HAD TO
 * -----------------------------------
 * Until D6.4 the frontend addressed a brokerage account by broker name:
 * `brokerService.holdings("zerodha")`, `disconnectBroker(b.broker)`,
 * `preferred_broker`. That is not a bug in the UI so much as a faithful mirror
 * of a backend model in which a user had exactly one account per broker. Now
 * they may have several, and a broker name no longer selects one.
 *
 * THE THREE THINGS THIS MODULE EXISTS TO FORBID
 * ---------------------------------------------
 * The brief names them, and each is a shape the old UI actually had:
 *
 *   * **the broker name.** `Object.values(brokerStatus)` produced one card per
 *     brand, and every action on the card sent the brand.
 *   * **array position.** A list of accounts invites `accounts[0]`, which is
 *     "first connected" wearing a different hat.
 *   * **"last connected".** Sorting a picker by recency makes the selection
 *     move under the user between renders.
 *
 * So the selected account is stored and passed as an opaque
 * `broker_account_id`, and every helper here refuses to *derive* one. The single
 * exception is deliberate and narrow: when a user holds exactly one account,
 * `resolveSelection` selects it, because "the only one there is" is not a choice
 * and asking the user to pick from a list of one is a worse product. Two or
 * more, and the answer is `null` until they say.
 *
 * PERSISTENCE
 * -----------
 * The selection is per-user browser state, so it lives in `localStorage` and is
 * wiped by `clearTenantLocalState` on every identity transition — automatically,
 * because that module keeps a *keep-list* rather than a clear-list, so a new
 * per-user key is purged by default rather than by remembering to add it (D6.3).
 * A stale id that survives anyway (a page open across a disconnect) is still
 * validated against the server's account list on every read, so it can never
 * address an account the user no longer has.
 */

/** localStorage key holding the selected `broker_account_id`. */
export const SELECTED_ACCOUNT_KEY = "sa_broker_account";

/** Whether a value is syntactically a `broker_account_id`. Mirrors the server's
 *  `is_broker_account_id`; a shape check only, never an ownership claim. */
export function isBrokerAccountId(value) {
  return typeof value === "string" && /^ba_[0-9a-f]{32}$/.test(value);
}

export function readSelectedAccountId() {
  try {
    const value = localStorage.getItem(SELECTED_ACCOUNT_KEY);
    return isBrokerAccountId(value) ? value : null;
  } catch {
    return null;
  }
}

export function writeSelectedAccountId(accountId) {
  try {
    if (isBrokerAccountId(accountId)) localStorage.setItem(SELECTED_ACCOUNT_KEY, accountId);
    else localStorage.removeItem(SELECTED_ACCOUNT_KEY);
  } catch {
    /* storage unavailable — the selection is simply not remembered */
  }
}

export function clearSelectedAccount() {
  writeSelectedAccountId(null);
}

/**
 * Which account the UI should act on, given the server's list and a stored id.
 *
 * Returns `{ accountId, reason }`. `reason` is why, so a component can render
 * "choose an account" rather than silently doing nothing:
 *
 *   `"stored"`   — the remembered account is still in the list.
 *   `"only"`     — the user has exactly one; there is nothing to choose.
 *   `"none"`     — the user has no connected account.
 *   `"ambiguous"`— several, and none chosen. The UI must ask.
 *
 * A stored id that is not in `accounts` is discarded, not repaired: the account
 * was disconnected, deleted, or belongs to a previous sign-in in this tab.
 */
export function resolveSelection(accounts, storedId = readSelectedAccountId()) {
  const list = Array.isArray(accounts) ? accounts.filter(Boolean) : [];
  const connected = list.filter((a) => a.connected);
  const pool = connected.length ? connected : list;

  if (storedId && pool.some((a) => a.broker_account_id === storedId)) {
    return { accountId: storedId, reason: "stored" };
  }
  if (pool.length === 0) return { accountId: null, reason: "none" };
  if (pool.length === 1) return { accountId: pool[0].broker_account_id, reason: "only" };
  return { accountId: null, reason: "ambiguous" };
}

/** The account record for an id, or null. Never falls back to another account. */
export function findAccount(accounts, accountId) {
  if (!isBrokerAccountId(accountId)) return null;
  return (accounts || []).find((a) => a && a.broker_account_id === accountId) || null;
}

/**
 * What to show the user for one account.
 *
 * The broker's own account number is the label a trader recognises — it is on
 * their contract notes — and it is what distinguishes two accounts at one
 * broker. The internal `broker_account_id` is a routing handle and is never
 * rendered.
 */
export function accountLabel(account) {
  if (!account) return "";
  const name = account.display_name || account.broker || "Broker";
  const external = account.external_account_id;
  return external ? `${name} · ${external}` : name;
}
