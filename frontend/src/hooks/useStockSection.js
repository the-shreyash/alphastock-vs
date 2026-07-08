import { useState, useEffect, useCallback } from "react";
import api from "../services/api";

// Module-level cache so switching tabs (or briefly navigating away and back)
// doesn't refetch a section that was already loaded this session.
const sectionCache = new Map();

/**
 * Fetch a stock-detail section lazily.
 *
 * @param {string} url     Full API path (e.g. `/stocks/RELIANCE/profile`).
 * @param {object} opts    `{ enabled }` — fetch only when true (lazy tabs).
 * @returns {{ data, loading, error, retry }}
 */
export default function useStockSection(url, { enabled = true } = {}) {
  const [data, setData] = useState(() => sectionCache.get(url) ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0); // bumped by retry()

  useEffect(() => {
    if (!enabled || !url) return undefined;
    const cached = sectionCache.get(url);
    if (cached) {
      setData(cached);
      setError(null);
      return undefined;
    }
    let cancelled = false;
    setData(null);
    setLoading(true);
    setError(null);
    api
      .get(url)
      .then((res) => {
        if (cancelled) return;
        sectionCache.set(url, res.data);
        setData(res.data);
      })
      .catch((err) => {
        if (cancelled) return;
        const detail = err?.response?.data?.detail;
        setError(typeof detail === "string" ? detail : "Could not load this section.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [url, enabled, nonce]);

  const retry = useCallback(() => {
    sectionCache.delete(url);
    setNonce((n) => n + 1);
  }, [url]);

  return { data, loading, error, retry };
}
