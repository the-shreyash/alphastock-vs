import { useState, useEffect, useRef, useCallback } from "react";

const WS_URL = process.env.REACT_APP_BACKEND_URL?.replace("https://", "wss://").replace("http://", "ws://");

export function useWebSocket(userId = "anonymous") {
  const [connected, setConnected] = useState(false);
  const [marketData, setMarketData] = useState(null);
  const [priceTicks, setPriceTicks] = useState({});
  const [tradeUpdates, setTradeUpdates] = useState([]);
  const [engineEvents, setEngineEvents] = useState([]);
  const [activityUpdates, setActivityUpdates] = useState(null);
  const [portfolioUpdate, setPortfolioUpdate] = useState(null);
  const [alerts, setAlerts] = useState([]);
  // Sprint R2 — message-contract fixes (G6): these types were produced by the
  // backend but previously dropped by this hook. Captured here additively.
  const [marketAlerts, setMarketAlerts] = useState([]);
  const [brokerStatus, setBrokerStatus] = useState(null);
  const [portfolioSynced, setPortfolioSynced] = useState(null);
  const [brokerOrders, setBrokerOrders] = useState([]);
  const [brokerTicks, setBrokerTicks] = useState(null);
  const wsRef = useRef(null);
  const reconnectRef = useRef(null);

  const connect = useCallback(() => {
    if (!WS_URL || userId === "anonymous" || userId === "anon") return;
    try {
      const ws = new WebSocket(`${WS_URL}/api/ws?user_id=${userId}`);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        if (reconnectRef.current) {
          clearTimeout(reconnectRef.current);
          reconnectRef.current = null;
        }
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "market_update") {
            setMarketData(msg.data);
          } else if (msg.type === "price_tick") {
            setPriceTicks((prev) => ({ ...prev, [msg.data.symbol]: msg.data }));
          } else if (msg.type === "prices") {
            // Batched live prices: { SYMBOL: { price, change_pct }, ... }
            setPriceTicks((prev) => ({ ...prev, ...msg.data }));
          } else if (msg.type === "trade_update") {
            setTradeUpdates((prev) => [msg.data, ...prev.slice(0, 49)]);
          } else if (msg.type === "trade_engine_event") {
            // Trading Engine lifecycle pushes (trailing SL moved, target hit,
            // SL exit) — consumers refetch positions when one arrives.
            setEngineEvents((prev) => [msg.data, ...prev.slice(0, 19)]);
          } else if (msg.type === "portfolio_update") {
            setPortfolioUpdate(msg.data);
          } else if (msg.type === "alert") {
            setAlerts((prev) => [msg.data, ...prev.slice(0, 49)]);
          } else if (msg.type === "activity_feed") {
            setActivityUpdates(msg.data);
          } else if (msg.type === "ai_alert") {
            // Proactive market alert (Nifty big move / key-level cross).
            setMarketAlerts((prev) => [
              { message: msg.message, severity: msg.severity, ...msg.data, timestamp: msg.timestamp },
              ...prev.slice(0, 49),
            ]);
          } else if (msg.type === "broker_status") {
            setBrokerStatus(msg.data);
          } else if (msg.type === "portfolio_synced") {
            setPortfolioSynced(msg.data);
          } else if (msg.type === "broker_order_update") {
            setBrokerOrders((prev) => [msg.data, ...prev.slice(0, 49)]);
          } else if (msg.type === "broker_price_tick") {
            // Raw broker feed: { broker, ticks: [...] } keyed by instrument
            // token (not symbol). Kept separate from the symbol-keyed price
            // store; R3 maps tokens → symbols for the price UI.
            setBrokerTicks(msg.data);
          }
        } catch {}
      };

      ws.onclose = () => {
        setConnected(false);
        // Reconnect after 5 seconds
        reconnectRef.current = setTimeout(connect, 5000);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch (err) {
      console.error("WebSocket error:", err);
    }
  }, [userId]);

  useEffect(() => {
    connect();
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
    };
  }, [connect]);

  const subscribePrices = useCallback((symbols) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "subscribe_prices", symbols }));
    }
  }, []);

  const sendPing = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "ping" }));
    }
  }, []);

  return { connected, marketData, priceTicks, tradeUpdates, engineEvents, activityUpdates, portfolioUpdate, alerts, marketAlerts, brokerStatus, portfolioSynced, brokerOrders, brokerTicks, subscribePrices, sendPing };
}
