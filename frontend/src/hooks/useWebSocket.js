/**
 * useWebSocket (Sprint R3) — store-backed selector shim.
 *
 * The single WebSocket is now owned by `RealtimeProvider`, which writes into the
 * Zustand `useRealtimeStore`. This hook no longer opens a socket; it simply
 * reads live slices from that store and returns the same interface the pages
 * already consume, so existing consumers keep working unchanged while the app
 * uses exactly one socket per user.
 *
 * The `userId` argument is accepted for backward compatibility and ignored (the
 * provider derives it from the auth context).
 */
import { useCallback } from "react";
import { useRealtimeStore } from "../store/realtimeStore";

export function useWebSocket(_userId = "anonymous") {
  // Narrow selectors — a component re-renders only when the slice it reads changes.
  const connectionStatus = useRealtimeStore((s) => s.connection.status);
  const marketData = useRealtimeStore((s) => s.marketData);
  const priceTicks = useRealtimeStore((s) => s.priceTicks);
  const tradeUpdates = useRealtimeStore((s) => s.tradeUpdates);
  const engineEvents = useRealtimeStore((s) => s.engineEvents);
  const activityUpdates = useRealtimeStore((s) => s.activityUpdates);
  const portfolioUpdate = useRealtimeStore((s) => s.portfolioUpdate);
  const alerts = useRealtimeStore((s) => s.alerts);
  const marketAlerts = useRealtimeStore((s) => s.marketAlerts);
  const brokerStatus = useRealtimeStore((s) => s.brokerStatus);
  const portfolioSynced = useRealtimeStore((s) => s.portfolioSynced);
  const brokerOrders = useRealtimeStore((s) => s.brokerOrders);
  const brokerTicks = useRealtimeStore((s) => s.brokerTicks);

  const connected = connectionStatus === "live";

  const subscribePrices = useCallback((symbols) => {
    useRealtimeStore.getState().send?.({ type: "subscribe_prices", symbols });
  }, []);

  const sendPing = useCallback(() => {
    useRealtimeStore.getState().send?.({ type: "ping" });
  }, []);

  return {
    connected,
    connectionStatus,
    marketData,
    priceTicks,
    tradeUpdates,
    engineEvents,
    activityUpdates,
    portfolioUpdate,
    alerts,
    marketAlerts,
    brokerStatus,
    portfolioSynced,
    brokerOrders,
    brokerTicks,
    subscribePrices,
    sendPing,
  };
}
