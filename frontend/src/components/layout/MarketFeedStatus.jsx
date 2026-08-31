/**
 * MarketFeedStatus (D5.14) — the market feed's own indicator.
 *
 * WHY THIS IS NOT PART OF ConnectionStatus
 * ----------------------------------------
 * They answer different questions and can disagree in both directions. The
 * connection pill says whether THIS BROWSER'S SOCKET is up; a socket can be
 * perfectly live while every market-data provider behind it is down. This pill
 * says whether the MARKET FEED is serving usable data. Folding the second into
 * the first is how a user ends up reading "Live" over prices that nothing has
 * delivered — the defect D5.12 found on the backend, which would simply have
 * moved into the UI if the two states shared a badge.
 *
 * It owns no logic. Every judgement — is there a candidate, is it healthy, has
 * it answered, how fresh is the data — was made by the Source Manager and
 * arrived on `provider.status`. This component reads the store and renders a
 * word. There is no timer here, and there must never be one: "recovering" ends
 * when the backend says it ends, not when the frontend gets bored waiting.
 */
import { useRealtimeStore, selectFeedState } from "../../store/realtimeStore";
import { describeFeed } from "../../lib/feedState";

const TONE = {
  profit: "var(--profit)",
  neutral: "var(--text-secondary)",
  warn: "#F59E0B",
  loss: "var(--loss)",
};

export default function MarketFeedStatus() {
  const feed = useRealtimeStore(selectFeedState);

  // Nothing published yet. Rendering "Unavailable" here would assert an outage
  // the platform has not reported; rendering "Live" would assert a feed it has
  // not promised. Silence is the only honest state before the first event.
  if (!feed) return null;

  const view = describeFeed(feed);

  return (
    <div className="flex items-center gap-2" data-testid="market-feed">
      <div
        data-testid="market-feed-status"
        data-feed-state={view.state}
        data-feed-live={String(view.live)}
        className="hidden sm:flex items-center gap-1.5 px-2.5 h-8 rounded-lg"
        style={{ background: "var(--hover)", border: "1px solid var(--border)" }}
        title={view.detail}
      >
        <span
          className={`w-2 h-2 rounded-full ${view.state === "recovering" ? "animate-pulse" : ""}`}
          style={{ background: TONE[view.tone] || TONE.neutral }}
        />
        <span
          className="text-[11px] font-mono uppercase tracking-wider"
          style={{ color: "var(--text-secondary)" }}
        >
          {view.label}
        </span>
      </div>
      {/* The sentence is always in the DOM — screen readers and narrow viewports
          get the explanation even where the pill's tooltip is unreachable. */}
      <span
        data-testid="market-feed-detail"
        className="hidden lg:inline text-[11px] max-w-[22rem] truncate"
        style={{ color: "var(--text-muted)" }}
      >
        {view.detail}
      </span>
    </div>
  );
}
