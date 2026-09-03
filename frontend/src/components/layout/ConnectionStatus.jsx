/**
 * ConnectionStatus (Sprint R3).
 *
 * Global real-time connection pill driven by the Zustand store's connection
 * state machine (Live / Connecting / Reconnecting / Offline / Session expired).
 * Replaces the Dashboard-only LIVE/OFFLINE badge so the socket state is visible
 * app-wide.
 *
 * D6.1 / L3. `unauthenticated` is a distinct state from `offline` on purpose.
 * The socket used to retry an expired token forever and show "Reconnecting"
 * while doing it — a spinner that could never resolve, describing a condition no
 * amount of waiting fixes. A dead credential is a thing the USER has to act on,
 * so it says so.
 */
import { useRealtimeStore, selectConnectionStatus } from "../../store/realtimeStore";

const STATES = {
  live: { label: "Live", color: "var(--profit)", pulse: false },
  connecting: { label: "Connecting", color: "#F59E0B", pulse: true },
  reconnecting: { label: "Reconnecting", color: "#F59E0B", pulse: true },
  offline: { label: "Offline", color: "var(--loss)", pulse: false },
  unauthenticated: { label: "Session expired", color: "var(--loss)", pulse: false },
};

export default function ConnectionStatus() {
  const status = useRealtimeStore(selectConnectionStatus);
  const s = STATES[status] || STATES.offline;

  return (
    <div
      data-testid="connection-status"
      className="hidden sm:flex items-center gap-1.5 px-2.5 h-8 rounded-lg"
      style={{ background: "var(--hover)", border: "1px solid var(--border)" }}
      title={status === "unauthenticated"
        ? "Real-time paused — your session expired. Sign in again to resume."
        : `Real-time connection: ${s.label}`}
    >
      <span
        className={`w-2 h-2 rounded-full ${s.pulse ? "animate-pulse" : ""}`}
        style={{ background: s.color }}
      />
      <span
        className="text-[11px] font-mono uppercase tracking-wider"
        style={{ color: "var(--text-secondary)" }}
      >
        {s.label}
      </span>
    </div>
  );
}
