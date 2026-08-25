import { Link } from "react-router-dom";
import { useState, useEffect, useRef, useLayoutEffect } from "react";
import { motion, MotionConfig } from "framer-motion";
import { ArrowRight, Menu, X } from "lucide-react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import api from "../services/api";

gsap.registerPlugin(ScrollTrigger);

/* ═══════════════════════════════════════════════════════════
   LIGHT LANDING — CSS
   Scoped under .sl-root. Does not touch dashboard styles.
═══════════════════════════════════════════════════════════ */
const LIGHT_STYLES = `
  @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@300;400;500;600;700;800&family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

  /* Kill the app's dark theme background when on the light landing page.
     We need to override [data-theme="dark"] on html which sets --bg: #0A0C15.
     Using every selector variation to guarantee the ivory wins. */
  html:has(.sl-root),
  html[data-theme]:has(.sl-root),
  html[data-theme="dark"]:has(.sl-root),
  html[data-theme="light"]:has(.sl-root) {
    background: #F5F5F2 !important;
    background-color: #F5F5F2 !important;
    --bg: #F5F5F2 !important;
  }
  body:has(.sl-root) {
    background: #F5F5F2 !important;
    background-color: #F5F5F2 !important;
  }

  .sl-root {
    background: #F5F5F2;
    color: #111318;
    font-family: 'Manrope', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    min-height: 100vh;
    position: relative;
    isolation: isolate;
  }

  /* ── Keyframes ── */
  @keyframes sl-ticker {
    0%   { transform: translateX(0); }
    100% { transform: translateX(-50%); }
  }
  @keyframes sl-fade-up {
    from { opacity: 0; transform: translateY(24px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  @keyframes sl-blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.25; }
  }
  @keyframes sl-sparkline {
    from { stroke-dashoffset: 480; }
    to   { stroke-dashoffset: 0; }
  }
  @keyframes sl-draw-line {
    from { stroke-dashoffset: 200; opacity: 0; }
    30%  { opacity: 1; }
    to   { stroke-dashoffset: 0; opacity: 0.35; }
  }

  /* ── Nav ── */
  .sl-nav {
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    backdrop-filter: blur(20px) saturate(1.6);
    -webkit-backdrop-filter: blur(20px) saturate(1.6);
    background: rgba(245, 245, 242, 0.92);
    border-bottom: 1px solid rgba(17, 19, 24, 0.07);
    transition: background 0.25s ease, box-shadow 0.25s ease;
  }
  .sl-nav-scrolled {
    background: rgba(245, 245, 242, 0.98);
    box-shadow: 0 1px 0 rgba(17, 19, 24, 0.06);
  }

  /* ── Typography ── */
  .sl-eyebrow {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: #9A9EA6;
  }
  .sl-display {
    font-family: 'Outfit', -apple-system, sans-serif;
    font-size: clamp(3rem, 5.5vw, 4.8rem);
    font-weight: 700;
    line-height: 1.05;
    letter-spacing: -0.035em;
    color: #111318;
  }
  .sl-h2 {
    font-family: 'Outfit', -apple-system, sans-serif;
    font-size: clamp(2.2rem, 3.5vw, 3.2rem);
    font-weight: 700;
    line-height: 1.1;
    letter-spacing: -0.03em;
    color: #111318;
  }
  .sl-body {
    font-size: 16px;
    line-height: 1.75;
    color: #686B70;
  }
  .sl-mono {
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
  }

  /* ── Buttons ── */
  .sl-btn-primary {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 12px 24px; border-radius: 8px;
    font-family: 'Manrope', sans-serif; font-size: 14px; font-weight: 600;
    background: #111318; color: #FAFAFA;
    border: none; cursor: pointer; white-space: nowrap;
    text-decoration: none;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    letter-spacing: -0.01em;
  }
  .sl-btn-primary:hover {
    background: #1E2028;
    transform: translateY(-1px);
    box-shadow: 0 8px 24px rgba(17, 19, 24, 0.18);
  }
  .sl-btn-ghost {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 12px 24px; border-radius: 8px;
    font-family: 'Manrope', sans-serif; font-size: 14px; font-weight: 600;
    background: transparent; color: #686B70;
    border: 1px solid rgba(17, 19, 24, 0.14);
    cursor: pointer; white-space: nowrap; text-decoration: none;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    letter-spacing: -0.01em;
  }
  .sl-btn-ghost:hover {
    border-color: rgba(17, 19, 24, 0.28);
    color: #111318;
    transform: translateY(-1px);
  }
  .sl-btn-nav {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 16px; border-radius: 6px;
    font-family: 'Manrope', sans-serif; font-size: 13px; font-weight: 600;
    background: #111318; color: #FAFAFA;
    border: none; cursor: pointer; white-space: nowrap; text-decoration: none;
    transition: all 0.18s ease;
    letter-spacing: -0.01em;
  }
  .sl-btn-nav:hover { background: #1E2028; }

  /* ── Surfaces ── */
  .sl-panel {
    background: #FFFFFF;
    border: 1px solid rgba(17, 19, 24, 0.08);
    border-radius: 12px;
  }
  .sl-surface {
    background: #FAFAF8;
    border: 1px solid rgba(17, 19, 24, 0.07);
    border-radius: 8px;
  }

  /* ── Dividers ── */
  .sl-divider {
    height: 1px;
    background: rgba(17, 19, 24, 0.06);
  }
  .sl-rule {
    height: 1px;
    background: rgba(17, 19, 24, 0.05);
  }

  /* ── Ticker ── */
  .sl-ticker-track {
    display: flex; width: max-content;
    animation: sl-ticker 72s linear infinite;
  }
  .sl-ticker-track:hover { animation-play-state: paused; }

  /* ── Live dot ── */
  .sl-blink { animation: sl-blink 2.4s ease-in-out infinite; }

  /* ── Sparkline ── */
  .sl-spark {
    stroke-dasharray: 480;
    animation: sl-sparkline 2.8s cubic-bezier(0.22, 1, 0.36, 1) forwards;
  }

  /* ── Flow line ── */
  .sl-flow-line {
    stroke-dasharray: 200;
    animation: sl-draw-line 2.8s ease-in-out infinite;
  }

  /* ── Anchor offset ── */
  .sl-root section[id] { scroll-margin-top: 80px; }

  /* ── Paper texture ── */
  .sl-root::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.018'/%3E%3C/svg%3E");
    pointer-events: none;
    z-index: 0;
  }

  /* ── Responsive ── */
  @media (max-width: 1024px) {
    .sl-nav-links    { display: none !important; }
    .sl-hamburger    { display: flex !important; }
    .sl-hero-grid    { grid-template-columns: 1fr !important; }
    .sl-hero-term    { display: none !important; }
    .sl-mkt-grid     { grid-template-columns: 1fr !important; }
    .sl-ai-grid      { grid-template-columns: 1fr !important; gap: 48px !important; }
    .sl-port-meta    { grid-template-columns: 1fr 1fr !important; }
  }
  @media (max-width: 768px) {
    .sl-display      { font-size: clamp(2.2rem, 8vw, 3rem) !important; }
    .sl-h2           { font-size: clamp(1.8rem, 6vw, 2.4rem) !important; }
    .sl-port-meta    { grid-template-columns: 1fr !important; }
    .sl-footer-grid  { grid-template-columns: 1fr 1fr !important; }
  }
  @media (max-width: 560px) {
    .sl-footer-grid  { grid-template-columns: 1fr !important; }
  }

  /* ── Reduced motion ── */
  @media (prefers-reduced-motion: reduce) {
    .sl-ticker-track,
    .sl-spark,
    .sl-flow-line,
    .sl-blink { animation: none !important; }
  }
`;

/* ─── Design tokens ─── */
const GAIN   = "#16A34A";
const LOSS   = "#DC2626";
const MUTED  = "#9A9EA6";
const TEXT2  = "#686B70";
const BDR    = "rgba(17, 19, 24, 0.08)";
const PANELBG = "#FFFFFF";
const SECTBG  = "#FAFAF8";

const TICKER_ITEMS = [
  { sym: "NIFTY 50",   val: "24,320.15", pct: "+0.82%", gain: true  },
  { sym: "SENSEX",     val: "80,142.30", pct: "+0.74%", gain: true  },
  { sym: "BANKNIFTY",  val: "52,418.60", pct: "+1.12%", gain: true  },
  { sym: "RELIANCE",   val: "2,847.25",  pct: "+1.24%", gain: true  },
  { sym: "INFY",       val: "1,642.80",  pct: "+0.86%", gain: true  },
  { sym: "TCS",        val: "3,912.40",  pct: "+0.55%", gain: true  },
  { sym: "HDFCBANK",   val: "1,723.10",  pct: "-0.31%", gain: false },
  { sym: "VIX",        val: "14.82",     pct: "+0.34%", gain: false },
  { sym: "WIPRO",      val: "542.60",    pct: "+0.92%", gain: true  },
  { sym: "BAJFINANCE", val: "7,142.50",  pct: "-0.18%", gain: false },
];

const NAV_LINKS = [
  { label: "Markets",      href: "#market-context" },
  { label: "Intelligence", href: "#ai-analysis"    },
  { label: "Portfolio",    href: "#portfolio"       },
  { label: "How it works", href: "#ecosystem"       },
];

/* ─── Logo ─── */
function SLLogo({ size = 28 }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#111318" />
      <rect x="5"  y="20" width="3.5" height="7"  rx="1" fill={GAIN} opacity="0.45" />
      <rect x="10" y="17" width="3.5" height="10" rx="1" fill={GAIN} opacity="0.62" />
      <rect x="15" y="13" width="3.5" height="14" rx="1" fill={GAIN} opacity="0.78" />
      <rect x="20" y="9"  width="3.5" height="18" rx="1" fill={GAIN} opacity="0.92" />
      <rect x="25" y="5"  width="3.5" height="22" rx="1" fill={GAIN} />
      <polyline points="6.75,20 11.75,17 16.75,13 21.75,9 26.75,5"
        stroke="#FAFAFA" strokeWidth="1.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ═══════════════════════════════════════════════════════════
   MARKET TERMINAL — light version
═══════════════════════════════════════════════════════════ */
function LightTerminal({ liveData }) {
  const fmt    = (v) => v ? Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "24,320";
  const fmtPct = (v) => v != null ? `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%` : "+0.82%";
  const gc     = (v) => (v ?? 0.82) >= 0 ? GAIN : LOSS;
  const isLive = liveData?.available === true && liveData?.nifty?.value != null;

  return (
    <div className="sl-hero-term" style={{ position: "relative", width: "100%", maxWidth: 580, marginLeft: "auto" }}>
      {/* Soft shadow bloom behind */}
      <div aria-hidden="true" style={{
        position: "absolute", bottom: "-8%", left: "8%", right: "8%", height: "50%",
        background: "radial-gradient(ellipse at 50% 100%, rgba(22,163,74,0.08) 0%, transparent 70%)",
        filter: "blur(28px)", borderRadius: "50%", pointerEvents: "none", zIndex: 0,
      }} />

      {/* Terminal surface */}
      <div style={{
        position: "relative", zIndex: 1,
        background: PANELBG,
        border: `1px solid ${BDR}`,
        borderRadius: 16,
        overflow: "hidden",
        boxShadow: "0 4px 6px rgba(17,19,24,0.04), 0 20px 40px rgba(17,19,24,0.08)",
      }}>
        {/* Header bar */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 20px",
          borderBottom: `1px solid ${BDR}`,
          background: SECTBG,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ display: "flex", gap: 5 }}>
              {["#F87171","#FCD34D","#4ADE80"].map((c,i) => (
                <div key={i} style={{ width: 8, height: 8, borderRadius: "50%", background: c, opacity: 0.55 }} />
              ))}
            </div>
            <span className="sl-mono" style={{ fontSize: 11, color: MUTED, letterSpacing: "0.06em" }}>
              StockAssist — Market View
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <div className="sl-blink" style={{ width: 6, height: 6, borderRadius: "50%", background: GAIN }} />
            <span className="sl-mono" style={{ fontSize: 9, color: GAIN, fontWeight: 700, letterSpacing: "0.12em" }}>
              {isLive ? "LIVE" : "DEMO"}
            </span>
          </div>
        </div>

        {/* Main index */}
        <div style={{ padding: "24px 24px 16px" }}>
          <div style={{ fontSize: 11, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 6 }}>NIFTY 50</div>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 14, marginBottom: 16 }}>
            <div className="sl-mono" style={{ fontSize: 40, fontWeight: 700, color: "#111318", lineHeight: 1, letterSpacing: "-0.02em" }}>
              {fmt(liveData?.nifty?.value)}
            </div>
            <div style={{ paddingBottom: 4 }}>
              <div className="sl-mono" style={{ fontSize: 15, fontWeight: 700, color: gc(liveData?.nifty?.change_pct) }}>
                {fmtPct(liveData?.nifty?.change_pct)}
              </div>
              <div style={{ fontSize: 10, color: MUTED, marginTop: 2 }}>today</div>
            </div>
          </div>

          {/* Sparkline */}
          <svg viewBox="0 0 480 64" style={{ width: "100%", height: 64, display: "block", marginBottom: 16 }} aria-hidden="true">
            <defs>
              <linearGradient id="sl-spark-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={GAIN} stopOpacity="0.12" />
                <stop offset="100%" stopColor={GAIN} stopOpacity="0" />
              </linearGradient>
            </defs>
            {[16, 32, 48].map(y => (
              <line key={y} x1="0" y1={y} x2="480" y2={y} stroke="rgba(17,19,24,0.05)" strokeWidth="1" />
            ))}
            <path
              d="M0,50 Q40,47 80,44 T160,38 T240,40 T320,30 T400,20 T480,14 L480,64 L0,64 Z"
              fill="url(#sl-spark-fill)"
            />
            <path
              className="sl-spark"
              d="M0,50 Q40,47 80,44 T160,38 T240,40 T320,30 T400,20 T480,14"
              fill="none" stroke={GAIN} strokeWidth="1.5" strokeLinecap="round"
            />
            <circle cx="480" cy="14" r="3" fill={GAIN} />
          </svg>

          {/* Three indices */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 1, borderRadius: 8, overflow: "hidden", marginBottom: 20, border: `1px solid ${BDR}` }}>
            {[
              { label: "SENSEX",     val: fmt(liveData?.sensex?.value),    pct: fmtPct(liveData?.sensex?.change_pct),    gc: gc(liveData?.sensex?.change_pct)    },
              { label: "BANK NIFTY", val: fmt(liveData?.bank_nifty?.value),pct: fmtPct(liveData?.bank_nifty?.change_pct),gc: gc(liveData?.bank_nifty?.change_pct) },
              { label: "VIX",        val: "14.82",                         pct: "+0.34%",                                gc: LOSS                                 },
            ].map((idx, i) => (
              <div key={idx.label} style={{
                padding: "10px 12px",
                background: i % 2 === 0 ? SECTBG : PANELBG,
                borderRight: i < 2 ? `1px solid ${BDR}` : "none",
              }}>
                <div style={{ fontSize: 9, color: MUTED, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 5 }}>{idx.label}</div>
                <div className="sl-mono" style={{ fontSize: 12, color: "#111318", fontWeight: 600 }}>{idx.val}</div>
                <div className="sl-mono" style={{ fontSize: 10, color: idx.gc, fontWeight: 700, marginTop: 2 }}>{idx.pct}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Watchlist */}
        <div style={{ borderTop: `1px solid ${BDR}`, padding: "16px 24px" }}>
          <div style={{ fontSize: 9, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 12 }}>WATCHLIST</div>
          {[
            { sym: "RELIANCE",  val: "2,847", pct: "+1.24%", gain: true  },
            { sym: "INFY",      val: "1,642", pct: "+0.86%", gain: true  },
            { sym: "HDFCBANK",  val: "1,723", pct: "-0.31%", gain: false },
            { sym: "TCS",       val: "3,912", pct: "+0.55%", gain: true  },
          ].map((s, i, arr) => (
            <div key={s.sym} style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "7px 0",
              borderBottom: i < arr.length - 1 ? `1px solid rgba(17,19,24,0.04)` : "none",
            }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: "#686B70", letterSpacing: "0.04em" }}>{s.sym}</span>
              <div style={{ display: "flex", gap: 16 }}>
                <span className="sl-mono" style={{ fontSize: 11, color: MUTED }}>{s.val}</span>
                <span className="sl-mono" style={{ fontSize: 11, fontWeight: 700, color: s.gain ? GAIN : LOSS }}>{s.pct}</span>
              </div>
            </div>
          ))}
        </div>

        {/* AI signal strip */}
        <div style={{
          borderTop: `1px solid ${BDR}`,
          padding: "11px 24px",
          background: "rgba(22,163,74,0.04)",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <div className="sl-blink" style={{ width: 6, height: 6, borderRadius: "50%", background: GAIN, flexShrink: 0 }} />
          <span style={{ fontSize: 11, color: GAIN, lineHeight: 1.5, fontWeight: 500 }}>
            Banking and IT strength driving broad-based advance. Breadth improving.
          </span>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   TICKER BAR
═══════════════════════════════════════════════════════════ */
function TickerBar() {
  const doubled = [...TICKER_ITEMS, ...TICKER_ITEMS];
  return (
    <div style={{
      background: PANELBG,
      borderTop: `1px solid ${BDR}`,
      borderBottom: `1px solid ${BDR}`,
      overflow: "hidden",
      padding: "9px 0",
    }}>
      <div className="sl-ticker-track">
        {doubled.map((t, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "0 28px", flexShrink: 0 }}>
            <span className="sl-mono" style={{ fontSize: 10, fontWeight: 700, color: MUTED, letterSpacing: "0.06em" }}>{t.sym}</span>
            <span className="sl-mono" style={{ fontSize: 10, color: "#9A9EA6" }}>{t.val}</span>
            <span className="sl-mono" style={{ fontSize: 10, fontWeight: 700, color: t.gain ? GAIN : LOSS }}>{t.pct}</span>
            <span style={{ width: 1, height: 10, background: BDR, flexShrink: 0 }} />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   NAVIGATION
═══════════════════════════════════════════════════════════ */
function Navigation({ scrolled, onNavClick }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  return (
    <nav className={`sl-nav${scrolled ? " sl-nav-scrolled" : ""}`}>
      <div style={{ maxWidth: 1280, margin: "0 auto", padding: "0 32px", height: 60, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        {/* Brand */}
        <Link to="/light" style={{ display: "flex", alignItems: "center", gap: 9, textDecoration: "none" }}>
          <SLLogo size={26} />
          <span style={{ fontSize: 15, fontWeight: 700, color: "#111318", fontFamily: "'Outfit', sans-serif", letterSpacing: "-0.025em" }}>
            STOCKASSIST
          </span>
        </Link>

        {/* Desktop links */}
        <div className="sl-nav-links" style={{ display: "flex", alignItems: "center", gap: 2 }}>
          {NAV_LINKS.map((l) => (
            <a
              key={l.label}
              href={l.href}
              onClick={(e) => onNavClick(e, l.href)}
              style={{ padding: "6px 14px", borderRadius: 7, fontSize: 13, fontWeight: 500, color: TEXT2, textDecoration: "none", transition: "color 0.15s, background 0.15s", letterSpacing: "-0.01em" }}
              onMouseEnter={(e) => { e.currentTarget.style.color = "#111318"; e.currentTarget.style.background = "rgba(17,19,24,0.05)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = TEXT2; e.currentTarget.style.background = "transparent"; }}
            >
              {l.label}
            </a>
          ))}
        </div>

        {/* Auth + theme toggle */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* Theme toggle pill */}
          <Link
            to="/"
            title="Switch to dark theme"
            style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              padding: "5px 12px", borderRadius: 20,
              border: `1px solid rgba(17,19,24,0.12)`,
              fontSize: 11, fontWeight: 700, color: MUTED,
              textDecoration: "none", letterSpacing: "0.06em",
              transition: "all 0.15s",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = "rgba(17,19,24,0.25)"; e.currentTarget.style.color = "#111318"; }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = "rgba(17,19,24,0.12)"; e.currentTarget.style.color = MUTED; }}
          >
            <span style={{ fontSize: 12 }}>◑</span> Dark
          </Link>

          <Link
            to="/login"
            data-testid="light-landing-login-btn"
            style={{ fontSize: 13, fontWeight: 500, color: TEXT2, textDecoration: "none", padding: "6px 12px", transition: "color 0.15s", letterSpacing: "-0.01em" }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "#111318"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = TEXT2; }}
          >
            Log in
          </Link>
          <Link to="/register" data-testid="light-landing-signup-btn" className="sl-btn-nav">
            Get started
          </Link>

          {/* Hamburger */}
          <button
            className="sl-hamburger"
            aria-label="Toggle menu"
            onClick={() => setMobileOpen((v) => !v)}
            style={{ display: "none", background: "none", border: "none", cursor: "pointer", color: TEXT2, padding: 4 }}
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div style={{ background: "rgba(245,245,242,0.99)", borderTop: `1px solid ${BDR}`, padding: "16px 32px 24px" }}>
          {NAV_LINKS.map((l) => (
            <a key={l.label} href={l.href} onClick={(e) => { onNavClick(e, l.href); setMobileOpen(false); }}
              style={{ display: "block", padding: "12px 0", fontSize: 15, fontWeight: 500, color: TEXT2, textDecoration: "none", borderBottom: `1px solid rgba(17,19,24,0.05)` }}>
              {l.label}
            </a>
          ))}
          <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
            <Link to="/login"    className="sl-btn-ghost"   style={{ flex: 1, justifyContent: "center" }}>Log in</Link>
            <Link to="/register" className="sl-btn-primary" style={{ flex: 1, justifyContent: "center" }}>Get started</Link>
          </div>
        </div>
      )}
    </nav>
  );
}

/* ═══════════════════════════════════════════════════════════
   HERO
═══════════════════════════════════════════════════════════ */
function HeroSection({ liveData, heroRef, onNavClick }) {
  const container = {
    hidden:  { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.1, delayChildren: 0.05 } },
  };
  const item = {
    hidden:  { opacity: 0, y: 24 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.7, ease: [0.16, 1, 0.3, 1] } },
  };
  const termVar = {
    hidden:  { opacity: 0, y: 40, scale: 0.97 },
    visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 1, ease: [0.16, 1, 0.3, 1], delay: 0.25 } },
  };

  return (
    <section
      ref={heroRef}
      style={{
        minHeight: "100vh",
        display: "flex", flexDirection: "column", justifyContent: "center",
        paddingTop: 80, position: "relative", overflow: "hidden",
      }}
    >
      {/* Subtle grid lines */}
      <div aria-hidden="true" style={{ position: "absolute", inset: 0, zIndex: 0 }}>
        <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", opacity: 0.04 }}>
          <defs>
            <pattern id="sl-grid" width="52" height="52" patternUnits="userSpaceOnUse">
              <path d="M 52 0 L 0 0 0 52" fill="none" stroke="rgba(17,19,24,1)" strokeWidth="0.5"/>
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#sl-grid)" />
        </svg>
        {/* Very faint green glow at upper right */}
        <div style={{
          position: "absolute", top: "-10%", right: "-5%",
          width: 600, height: 600, borderRadius: "50%",
          background: "radial-gradient(circle, rgba(22,163,74,0.05) 0%, transparent 70%)",
          pointerEvents: "none",
        }} />
      </div>

      <div style={{ maxWidth: 1280, margin: "0 auto", padding: "80px 32px 60px", position: "relative", zIndex: 1, width: "100%" }}>
        <motion.div
          className="sl-hero-grid"
          variants={container}
          initial="hidden"
          animate="visible"
          style={{ display: "grid", gridTemplateColumns: "55fr 45fr", gap: 72, alignItems: "center" }}
        >
          {/* Left */}
          <div>
            <motion.div variants={item} style={{ marginBottom: 24 }}>
              <span className="sl-eyebrow">Market Intelligence</span>
            </motion.div>
            <motion.div variants={item}>
              <h1 className="sl-display" style={{ marginBottom: 24 }}>
                See the market<br />before you act.
              </h1>
            </motion.div>
            <motion.div variants={item}>
              <p className="sl-body" style={{ maxWidth: 420, marginBottom: 40, fontSize: 17 }}>
                StockAssist brings market data, portfolio intelligence and AI-assisted analysis into one place — so every decision starts with context.
              </p>
            </motion.div>
            <motion.div variants={item} style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <Link
                to="/register"
                data-testid="light-hero-cta-btn"
                className="sl-btn-primary"
                style={{ padding: "13px 28px", fontSize: 15 }}
              >
                Explore StockAssist
                <ArrowRight size={16} />
              </Link>
              <a
                href="#ecosystem"
                onClick={(e) => onNavClick(e, "#ecosystem")}
                className="sl-btn-ghost"
                style={{ padding: "13px 24px", fontSize: 15 }}
              >
                See how it works
              </a>
            </motion.div>
          </div>

          {/* Right — terminal */}
          <motion.div variants={termVar} className="sl-hero-term-wrapper">
            <LightTerminal liveData={liveData} />
          </motion.div>
        </motion.div>
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════
   MARKET CONTEXT
═══════════════════════════════════════════════════════════ */
function MarketContextSection({ liveData }) {
  const fmt    = (v) => v ? Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "24,320";
  const fmtPct = (v) => v != null ? `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%` : "+0.82%";
  const gc     = (v) => (v ?? 0.82) >= 0 ? GAIN : LOSS;
  const isLive = liveData?.available === true && liveData?.nifty?.value != null;

  const SECTORS = [
    { name: "Banking",     pct: 78, change: "+1.2%", up: true  },
    { name: "IT",          pct: 72, change: "+1.8%", up: true  },
    { name: "FMCG",        pct: 55, change: "+0.3%", up: true  },
    { name: "Pharma",      pct: 48, change: "-0.2%", up: false },
    { name: "Auto",        pct: 42, change: "-0.5%", up: false },
    { name: "Metals",      pct: 35, change: "-0.9%", up: false },
  ];

  const TOP_MOVERS = [
    { sym: "HDFC BANK",  val: "1,723", pct: "+2.1%",  gain: true  },
    { sym: "INFOSYS",    val: "1,642", pct: "+1.9%",  gain: true  },
    { sym: "RELIANCE",   val: "2,847", pct: "+1.2%",  gain: true  },
    { sym: "BAJFINANCE", val: "7,142", pct: "-1.4%",  gain: false },
    { sym: "TATASTEEL",  val: "154",   pct: "-1.8%",  gain: false },
  ];

  return (
    <section id="market-context" style={{ padding: "140px 32px", background: "#F5F5F2" }}>
      <div style={{ maxWidth: 1280, margin: "0 auto" }}>
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
          style={{ marginBottom: 72 }}
        >
          <div className="sl-eyebrow" style={{ marginBottom: 20 }}>Market Intelligence</div>
          <h2 className="sl-h2" style={{ maxWidth: 440, marginBottom: 20 }}>
            Context before<br />conviction.
          </h2>
          <p className="sl-body" style={{ maxWidth: 480 }}>
            Price is only one part of the story. StockAssist brings market movement, news, fundamentals and portfolio context together before you make a decision.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 36 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-40px" }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
        >
          <div
            className="sl-mkt-grid"
            style={{ display: "grid", gridTemplateColumns: "3fr 2fr", gap: 1, background: BDR, border: `1px solid ${BDR}`, borderRadius: 16, overflow: "hidden" }}
          >
            {/* Left panel */}
            <div style={{ background: PANELBG }}>
              {/* Panel header */}
              <div style={{
                padding: "20px 28px", borderBottom: `1px solid ${BDR}`,
                display: "flex", justifyContent: "space-between", alignItems: "center",
              }}>
                <div>
                  <div style={{ fontSize: 10, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 4 }}>NIFTY 50</div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                    <span className="sl-mono" style={{ fontSize: 28, fontWeight: 700, color: "#111318", letterSpacing: "-0.02em" }}>{fmt(liveData?.nifty?.value)}</span>
                    <span className="sl-mono" style={{ fontSize: 14, fontWeight: 700, color: gc(liveData?.nifty?.change_pct) }}>{fmtPct(liveData?.nifty?.change_pct)}</span>
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div className="sl-blink" style={{ width: 6, height: 6, borderRadius: "50%", background: isLive ? GAIN : MUTED }} />
                  <span className="sl-mono" style={{ fontSize: 9, color: isLive ? GAIN : MUTED, fontWeight: 700, letterSpacing: "0.12em" }}>{isLive ? "LIVE" : "SAMPLE"}</span>
                </div>
              </div>

              {/* Market Breadth */}
              <div style={{ padding: "20px 28px", borderBottom: `1px solid ${BDR}` }}>
                <div style={{ fontSize: 10, color: MUTED, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: 14 }}>Market Breadth</div>
                <div style={{ display: "flex", gap: 32, marginBottom: 12 }}>
                  <div>
                    <div className="sl-mono" style={{ fontSize: 24, fontWeight: 700, color: GAIN, lineHeight: 1 }}>312</div>
                    <div style={{ fontSize: 10, color: "rgba(22,163,74,0.7)", marginTop: 3 }}>Advancing</div>
                  </div>
                  <div>
                    <div className="sl-mono" style={{ fontSize: 24, fontWeight: 700, color: LOSS, lineHeight: 1 }}>138</div>
                    <div style={{ fontSize: 10, color: "rgba(220,38,38,0.7)", marginTop: 3 }}>Declining</div>
                  </div>
                  <div>
                    <div className="sl-mono" style={{ fontSize: 24, fontWeight: 700, color: MUTED, lineHeight: 1 }}>50</div>
                    <div style={{ fontSize: 10, color: MUTED, marginTop: 3 }}>Unchanged</div>
                  </div>
                </div>
                <div style={{ height: 4, borderRadius: 2, background: "rgba(17,19,24,0.07)", overflow: "hidden" }}>
                  <div style={{ width: "68%", height: "100%", background: GAIN, opacity: 0.65, borderRadius: 2 }} />
                </div>
                <div style={{ fontSize: 10, color: MUTED, marginTop: 6 }}>68% stocks advancing on NSE</div>
              </div>

              {/* Sector Movement */}
              <div style={{ padding: "20px 28px", borderBottom: `1px solid ${BDR}` }}>
                <div style={{ fontSize: 10, color: MUTED, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: 14 }}>Sector Movement</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {SECTORS.map((s) => (
                    <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <div style={{ width: 80, fontSize: 10, color: TEXT2, flexShrink: 0 }}>{s.name}</div>
                      <div style={{ flex: 1, height: 3, background: "rgba(17,19,24,0.07)", borderRadius: 2, overflow: "hidden" }}>
                        <div style={{ width: `${s.pct}%`, height: "100%", borderRadius: 2, background: s.up ? GAIN : LOSS, opacity: 0.65 }} />
                      </div>
                      <div className="sl-mono" style={{ fontSize: 10, fontWeight: 700, color: s.up ? GAIN : LOSS, width: 40, textAlign: "right", flexShrink: 0 }}>{s.change}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Top Movers */}
              <div style={{ padding: "20px 28px" }}>
                <div style={{ fontSize: 10, color: MUTED, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: 14 }}>Top Movers</div>
                {TOP_MOVERS.map((m, i, arr) => (
                  <div key={m.sym} style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "7px 0",
                    borderBottom: i < arr.length - 1 ? `1px solid rgba(17,19,24,0.04)` : "none",
                  }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: TEXT2, letterSpacing: "0.03em" }}>{m.sym}</span>
                    <div style={{ display: "flex", gap: 16 }}>
                      <span className="sl-mono" style={{ fontSize: 11, color: MUTED }}>{m.val}</span>
                      <span className="sl-mono" style={{ fontSize: 11, fontWeight: 700, color: m.gain ? GAIN : LOSS }}>{m.pct}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Right: editorial */}
            <div style={{ background: SECTBG, padding: "40px 36px", display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div style={{ marginBottom: 48 }}>
                <p style={{ fontSize: 22, fontWeight: 600, color: "#111318", lineHeight: 1.45, letterSpacing: "-0.02em", fontFamily: "'Outfit', sans-serif", marginBottom: 20 }}>
                  "Every number on the screen is part of a larger story. StockAssist helps you read it."
                </p>
                <p className="sl-body" style={{ fontSize: 14 }}>
                  Sector rotation, breadth signals, index composition and news events are synthesised before they reach you — not after.
                </p>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                {[
                  { label: "Index movement", desc: "Live NIFTY, SENSEX and BANK NIFTY with intraday context" },
                  { label: "Sector analysis", desc: "Which sectors are leading, lagging and reversing" },
                  { label: "Market breadth",  desc: "Advance-decline ratio across NSE, updated continuously" },
                  { label: "AI signal",       desc: "What the data means, in plain language" },
                ].map((it, i) => (
                  <div key={i} style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                    <div style={{ width: 4, height: 4, borderRadius: "50%", background: GAIN, flexShrink: 0, marginTop: 7, opacity: 0.8 }} />
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "#111318", marginBottom: 2, letterSpacing: "-0.01em" }}>{it.label}</div>
                      <div style={{ fontSize: 12, color: TEXT2, lineHeight: 1.6 }}>{it.desc}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════
   AI ANALYSIS
═══════════════════════════════════════════════════════════ */
function AIAnalysisSection() {
  return (
    <section
      id="ai-analysis"
      style={{
        padding: "140px 32px",
        background: "rgba(22,163,74,0.025)",
        borderTop: `1px solid rgba(22,163,74,0.08)`,
        borderBottom: `1px solid rgba(22,163,74,0.08)`,
      }}
    >
      <div style={{ maxWidth: 1280, margin: "0 auto" }}>
        <div
          className="sl-ai-grid"
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 100, alignItems: "center" }}
        >
          {/* Left: copy */}
          <motion.div
            initial={{ opacity: 0, x: -28 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true, margin: "-80px" }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="sl-eyebrow" style={{ marginBottom: 20 }}>AI Assistance</div>
            <h2 className="sl-h2" style={{ marginBottom: 24 }}>An analyst in<br />your workflow.</h2>
            <p className="sl-body" style={{ marginBottom: 48, maxWidth: 400 }}>
              StockAssist helps you understand what changed, why it changed, and what deserves your attention next.
            </p>

            {/* Financial brief — left column */}
            <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
              {[
                { label: "What happened",  text: "Market moved higher after banking and IT strength, with broad participation across large-cap names." },
                { label: "Why it matters", text: "Banking remains the primary contributor to today's index move. Sector breadth is improving, suggesting the advance is not narrow." },
                { label: "What to watch",  text: "Watch Bank Nifty breadth and volume confirmation in the final hour. Any reversal in banking would weaken the overall signal." },
              ].map((b, i, arr) => (
                <div key={i} style={{
                  paddingBottom: i < arr.length - 1 ? 20 : 0,
                  marginBottom: i < arr.length - 1 ? 20 : 0,
                  borderBottom: i < arr.length - 1 ? `1px solid rgba(17,19,24,0.08)` : "none",
                }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: MUTED, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 8 }}>{b.label}</div>
                  <p style={{ fontSize: 14, lineHeight: 1.7, color: TEXT2, margin: 0 }}>{b.text}</p>
                </div>
              ))}
            </div>
          </motion.div>

          {/* Right: AI panel */}
          <motion.div
            initial={{ opacity: 0, x: 28 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true, margin: "-80px" }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          >
            <div style={{
              background: PANELBG,
              border: `1px solid ${BDR}`,
              borderRadius: 16,
              overflow: "hidden",
              boxShadow: "0 4px 6px rgba(17,19,24,0.04), 0 16px 32px rgba(17,19,24,0.06)",
            }}>
              {/* Panel header */}
              <div style={{ padding: "16px 24px", borderBottom: `1px solid ${BDR}`, display: "flex", justifyContent: "space-between", alignItems: "center", background: SECTBG }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: MUTED, letterSpacing: "0.12em", textTransform: "uppercase" }}>Market Analysis</span>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div className="sl-blink" style={{ width: 6, height: 6, borderRadius: "50%", background: GAIN }} />
                  <span className="sl-mono" style={{ fontSize: 9, color: GAIN, fontWeight: 700, letterSpacing: "0.12em" }}>ACTIVE</span>
                </div>
              </div>

              {/* Blocks */}
              {[
                { label: "Market Update",  text: '"Market moved higher after banking and IT strength."' },
                { label: "Why it matters", text: "Banking remains the primary contributor to today's index move. Sector breadth is improving across mid-cap indices." },
                { label: "What to watch",  text: "Watch Bank Nifty breadth and volume confirmation. A sustained move above 52,400 improves the probability of further upside." },
              ].map((b, i, arr) => (
                <div key={i} style={{ padding: "22px 24px", borderBottom: i < arr.length - 1 ? `1px solid ${BDR}` : "none" }}>
                  <div style={{ fontSize: 10, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 10 }}>{b.label}</div>
                  <p style={{ fontSize: i === 0 ? 15 : 14, lineHeight: 1.65, color: i === 0 ? "#111318" : TEXT2, margin: 0, letterSpacing: i === 0 ? "-0.01em" : 0 }}>{b.text}</p>
                </div>
              ))}

              {/* Signal grid */}
              <div style={{ padding: "16px 24px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, background: SECTBG }}>
                {[
                  { label: "Momentum",  val: "Positive",      color: GAIN  },
                  { label: "Breadth",   val: "Improving",     color: GAIN  },
                  { label: "IT Sector", val: "+1.8% leading", color: GAIN  },
                  { label: "VIX",       val: "14.8 — stable", color: MUTED },
                ].map((s) => (
                  <div key={s.label} style={{ padding: "9px 12px", background: PANELBG, borderRadius: 6, border: `1px solid ${BDR}` }}>
                    <div style={{ fontSize: 9, color: MUTED, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: 3 }}>{s.label}</div>
                    <div className="sl-mono" style={{ fontSize: 11, fontWeight: 600, color: s.color }}>{s.val}</div>
                  </div>
                ))}
              </div>

              <div style={{ padding: "10px 24px 14px", textAlign: "center" }}>
                <span style={{ fontSize: 10, color: "rgba(17,19,24,0.2)" }}>Illustrative preview · Not financial advice</span>
              </div>
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════
   PORTFOLIO
═══════════════════════════════════════════════════════════ */
function PortfolioSection() {
  const POSITIONS = [
    { sym: "RELIANCE",  price: "2,847", pct: "+1.24%", value: "1,42,350", weight: "29.5%", gain: true  },
    { sym: "INFY",      price: "1,642", pct: "+0.86%", value: "82,100",   weight: "17.0%", gain: true  },
    { sym: "HDFCBANK",  price: "1,723", pct: "-0.31%", value: "86,150",   weight: "17.9%", gain: false },
    { sym: "TCS",       price: "3,912", pct: "+0.55%", value: "97,800",   weight: "20.3%", gain: true  },
  ];

  return (
    <section id="portfolio" style={{ padding: "140px 32px", background: "#F5F5F2" }}>
      <div style={{ maxWidth: 1280, margin: "0 auto" }}>
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
          style={{ marginBottom: 72 }}
        >
          <div className="sl-eyebrow" style={{ marginBottom: 20 }}>Portfolio Intelligence</div>
          <h2 className="sl-h2" style={{ marginBottom: 20 }}>Your portfolio.<br />One clear view.</h2>
          <p className="sl-body" style={{ maxWidth: 440 }}>
            Position sizing, sector concentration, risk indicators and today's P&L — unified and always current.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 36 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-40px" }}
          transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
        >
          <div style={{
            background: PANELBG,
            border: `1px solid ${BDR}`,
            borderRadius: 16,
            overflow: "hidden",
            boxShadow: "0 4px 6px rgba(17,19,24,0.04), 0 20px 48px rgba(17,19,24,0.07)",
          }}>
            {/* Summary bar */}
            <div style={{ padding: "32px 40px", borderBottom: `1px solid ${BDR}`, display: "flex", alignItems: "flex-end", gap: 56, background: SECTBG }}>
              <div>
                <div style={{ fontSize: 11, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 8 }}>Portfolio Value</div>
                <div className="sl-mono" style={{ fontSize: 38, fontWeight: 700, color: "#111318", letterSpacing: "-0.025em", lineHeight: 1 }}>₹4,82,310</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 8 }}>Today's P&L</div>
                <div className="sl-mono" style={{ fontSize: 28, fontWeight: 700, color: GAIN, letterSpacing: "-0.02em", lineHeight: 1 }}>+₹6,842</div>
                <div className="sl-mono" style={{ fontSize: 13, color: "rgba(22,163,74,0.7)", marginTop: 4 }}>+1.44% today</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 8 }}>Month to Date</div>
                <div className="sl-mono" style={{ fontSize: 28, fontWeight: 700, color: GAIN, letterSpacing: "-0.02em", lineHeight: 1 }}>+4.82%</div>
                <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>↑ above NIFTY</div>
              </div>
            </div>

            {/* Meta grid */}
            <div
              className="sl-port-meta"
              style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", borderBottom: `1px solid ${BDR}` }}
            >
              {[
                { title: "Allocation", items: [
                    { label: "Equity",     val: "78%",       color: GAIN    },
                    { label: "Debt",       val: "12%",       color: MUTED   },
                    { label: "Cash",       val: "10%",       color: MUTED   },
                  ]
                },
                { title: "Exposure", items: [
                    { label: "Large-cap",  val: "65%",       color: "#111318" },
                    { label: "Mid-cap",    val: "25%",       color: "#111318" },
                    { label: "Small-cap",  val: "10%",       color: "#111318" },
                  ]
                },
                { title: "Risk", items: [
                    { label: "Profile",    val: "Moderate",  color: "#111318" },
                    { label: "Beta",       val: "0.87",      color: "#111318" },
                    { label: "Sharpe",     val: "1.24",      color: GAIN      },
                  ]
                },
              ].map((col, ci, arr) => (
                <div key={col.title} style={{
                  padding: "24px 28px",
                  borderRight: ci < arr.length - 1 ? `1px solid ${BDR}` : "none",
                }}>
                  <div style={{ fontSize: 10, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 16 }}>{col.title}</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {col.items.map((it) => (
                      <div key={it.label} style={{ display: "flex", justifyContent: "space-between" }}>
                        <span style={{ fontSize: 12, color: TEXT2 }}>{it.label}</span>
                        <span className="sl-mono" style={{ fontSize: 12, fontWeight: 700, color: it.color }}>{it.val}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {/* Positions table */}
            <div style={{ padding: "24px 40px" }}>
              <div style={{ fontSize: 10, color: MUTED, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 16 }}>Open Positions</div>
              <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr", padding: "8px 0", borderBottom: `1px solid rgba(17,19,24,0.07)`, marginBottom: 4 }}>
                {["Symbol","Price","Change","Value","Weight"].map((h) => (
                  <div key={h} style={{ fontSize: 9, color: MUTED, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase" }}>{h}</div>
                ))}
              </div>
              {POSITIONS.map((p, i, arr) => (
                <div key={p.sym} style={{
                  display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr",
                  padding: "12px 0",
                  borderBottom: i < arr.length - 1 ? `1px solid rgba(17,19,24,0.05)` : "none",
                  alignItems: "center",
                }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#111318", letterSpacing: "0.03em" }}>{p.sym}</div>
                  <div className="sl-mono" style={{ fontSize: 12, color: TEXT2 }}>₹{p.price}</div>
                  <div className="sl-mono" style={{ fontSize: 12, fontWeight: 700, color: p.gain ? GAIN : LOSS }}>{p.pct}</div>
                  <div className="sl-mono" style={{ fontSize: 12, color: TEXT2 }}>₹{p.value}</div>
                  <div className="sl-mono" style={{ fontSize: 12, color: MUTED }}>{p.weight}</div>
                </div>
              ))}
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════
   ECOSYSTEM
═══════════════════════════════════════════════════════════ */
function EcosystemSection() {
  const NODES = [
    { label: "Market Data",         sub: "NSE · Yahoo Finance · Live feeds",      active: false },
    { label: "Market Intelligence", sub: "Indices, breadth, sector, volatility",  active: false },
    { label: "Portfolio Context",   sub: "Holdings, risk, watchlist, history",     active: false },
    { label: "AI Analysis",         sub: "Pattern, momentum, sentiment synthesis", active: false },
    { label: "Action",              sub: "Decision made with full context",        active: true  },
  ];

  return (
    <section id="ecosystem" style={{ padding: "140px 32px", background: PANELBG }}>
      <div style={{ maxWidth: 900, margin: "0 auto" }}>
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
          style={{ marginBottom: 80 }}
        >
          <div className="sl-eyebrow" style={{ marginBottom: 20 }}>Connected</div>
          <h2 className="sl-h2" style={{ marginBottom: 20 }}>
            Everything connected<br />to the decision.
          </h2>
          <p className="sl-body" style={{ maxWidth: 400 }}>
            StockAssist connects every signal that matters — market data, news, portfolio context and AI reasoning — into one coherent, continuously updated view.
          </p>
        </motion.div>

        <div style={{ maxWidth: 560, margin: "0 auto" }}>
          {NODES.map((node, i) => (
            <motion.div
              key={node.label}
              initial={{ opacity: 0, x: -20 }}
              whileInView={{ opacity: 1, x: 0 }}
              viewport={{ once: true, margin: "-40px" }}
              transition={{ duration: 0.6, delay: i * 0.1, ease: [0.16, 1, 0.3, 1] }}
              style={{ display: "flex", alignItems: "flex-start", gap: 20 }}
            >
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flexShrink: 0, width: 24 }}>
                <div style={{
                  width: 20, height: 20, borderRadius: "50%", flexShrink: 0,
                  background: node.active ? GAIN : "transparent",
                  border: `2px solid ${node.active ? GAIN : "rgba(17,19,24,0.15)"}`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  zIndex: 1,
                }}>
                  {node.active && <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#FFFFFF" }} />}
                </div>
                {i < NODES.length - 1 && (
                  <svg width="2" height="48" style={{ display: "block", margin: "4px 0" }}>
                    <line x1="1" y1="0" x2="1" y2="48" stroke="rgba(17,19,24,0.1)" strokeWidth="2" strokeDasharray="4 3" />
                  </svg>
                )}
              </div>
              <div style={{ paddingBottom: i < NODES.length - 1 ? 28 : 0, paddingTop: 1 }}>
                <div style={{
                  fontSize: 15, fontWeight: 700,
                  color: node.active ? "#111318" : TEXT2,
                  letterSpacing: "-0.015em", marginBottom: 3,
                  fontFamily: "'Outfit', sans-serif",
                }}>
                  {node.label}
                </div>
                <div style={{ fontSize: 12, color: MUTED }}>{node.sub}</div>
              </div>
            </motion.div>
          ))}

          {/* Broker note */}
          <motion.div
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, delay: 0.5 }}
            style={{ marginTop: 40, paddingTop: 28, borderTop: `1px solid ${BDR}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}
          >
            <div>
              <div style={{ fontSize: 11, color: MUTED, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: 4 }}>Broker Integration</div>
              <div style={{ fontSize: 13, color: TEXT2 }}>Zerodha · Upstox · AngelOne</div>
            </div>
            <span style={{ fontSize: 10, fontWeight: 700, color: MUTED, letterSpacing: "0.14em", textTransform: "uppercase", padding: "4px 12px", borderRadius: 4, border: `1px solid ${BDR}` }}>
              Coming soon
            </span>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════
   PHILOSOPHY
═══════════════════════════════════════════════════════════ */
function PhilosophySection() {
  return (
    <section style={{
      padding: "160px 32px",
      background: "#F5F5F2",
      borderTop: `1px solid ${BDR}`,
      borderBottom: `1px solid ${BDR}`,
    }}>
      <div style={{ maxWidth: 800, margin: "0 auto", textAlign: "center" }}>
        <motion.div
          initial={{ opacity: 0, y: 36 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
        >
          <h2 style={{
            fontFamily: "'Outfit', sans-serif",
            fontSize: "clamp(2.8rem, 5vw, 4.5rem)",
            fontWeight: 700, lineHeight: 1.08, letterSpacing: "-0.04em",
            color: "#111318", marginBottom: 32,
          }}>
            Less noise.<br />Better decisions.
          </h2>
          <p className="sl-body" style={{ maxWidth: 400, margin: "0 auto", fontSize: 16 }}>
            StockAssist is designed to reduce the distance between what happens in the market and what you understand about it.
          </p>
        </motion.div>
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════
   FINAL CTA
═══════════════════════════════════════════════════════════ */
function FinalCTASection() {
  return (
    <section style={{ padding: "160px 32px", background: PANELBG, position: "relative" }}>
      {/* Very subtle green wash at bottom */}
      <div aria-hidden="true" style={{
        position: "absolute", bottom: 0, left: "50%", transform: "translateX(-50%)",
        width: 600, height: 280, pointerEvents: "none",
        background: "radial-gradient(ellipse 80% 60% at 50% 100%, rgba(22,163,74,0.05) 0%, transparent 70%)",
      }} />
      <div style={{ maxWidth: 700, margin: "0 auto", textAlign: "center", position: "relative" }}>
        <motion.div
          initial={{ opacity: 0, y: 36 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="sl-eyebrow" style={{ marginBottom: 28 }}>Get started</div>
          <h2 style={{
            fontFamily: "'Outfit', sans-serif",
            fontSize: "clamp(2.2rem, 4vw, 3.4rem)",
            fontWeight: 700, lineHeight: 1.12, letterSpacing: "-0.035em",
            color: "#111318", marginBottom: 20,
          }}>
            Make every market<br />decision with context.
          </h2>
          <p className="sl-body" style={{ marginBottom: 48, fontSize: 17 }}>
            Start with the information that matters.
          </p>
          <Link
            to="/register"
            data-testid="light-cta-final-btn"
            className="sl-btn-primary"
            style={{ padding: "15px 36px", fontSize: 16 }}
          >
            Get started
            <ArrowRight size={18} />
          </Link>
        </motion.div>
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════
   FOOTER
═══════════════════════════════════════════════════════════ */
function Footer({ onNavClick }) {
  const linkStyle = {
    display: "block", fontSize: 13, color: MUTED,
    textDecoration: "none", marginBottom: 10,
    transition: "color 0.15s", cursor: "pointer",
  };

  return (
    <footer style={{ borderTop: `1px solid ${BDR}`, background: SECTBG, padding: "56px 32px 40px" }}>
      <div style={{ maxWidth: 1280, margin: "0 auto" }}>
        <div
          className="sl-footer-grid"
          style={{ display: "grid", gridTemplateColumns: "2.5fr 1fr 1fr 1fr", gap: 48, marginBottom: 56 }}
        >
          {/* Brand */}
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
              <SLLogo size={22} />
              <span style={{ fontSize: 14, fontWeight: 700, color: "#686B70", fontFamily: "'Outfit', sans-serif", letterSpacing: "-0.02em" }}>STOCKASSIST</span>
            </div>
            <p style={{ fontSize: 13, lineHeight: 1.75, color: "#9A9EA6", maxWidth: 280 }}>
              Market data, portfolio intelligence and AI-assisted analysis for the Indian stock market.
            </p>
          </div>

          {/* Product */}
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: "#C8CAD0", textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 18 }}>Product</div>
            {[
              { label: "Markets",      href: "#market-context" },
              { label: "Intelligence", href: "#ai-analysis"    },
              { label: "Portfolio",    href: "#portfolio"      },
              { label: "How it works", href: "#ecosystem"      },
            ].map((l) => (
              <a key={l.label} href={l.href} onClick={(e) => onNavClick(e, l.href)} style={linkStyle}
                onMouseEnter={(e) => { e.currentTarget.style.color = "#111318"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = MUTED; }}>
                {l.label}
              </a>
            ))}
          </div>

          {/* Company */}
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: "#C8CAD0", textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 18 }}>Company</div>
            {["About", "Contact"].map((l) => (
              <span key={l} style={{ ...linkStyle, cursor: "default" }} title="Coming soon">{l}</span>
            ))}
          </div>

          {/* Legal */}
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: "#C8CAD0", textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 18 }}>Legal</div>
            {["Privacy Policy", "Terms of Service"].map((l) => (
              <span key={l} style={{ ...linkStyle, cursor: "default" }} title="Coming soon">{l}</span>
            ))}
            <a href="#risk-disclosure-light" style={linkStyle}
              onMouseEnter={(e) => { e.currentTarget.style.color = "#111318"; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = MUTED; }}>
              Risk Disclosure
            </a>
          </div>
        </div>

        {/* Bottom bar */}
        <div id="risk-disclosure-light" style={{ borderTop: `1px solid ${BDR}`, paddingTop: 28, display: "flex", flexDirection: "column", gap: 16 }}>
          <p style={{ fontSize: 11, lineHeight: 1.7, color: "#C8CAD0", maxWidth: 780 }}>
            <span style={{ fontWeight: 700, color: MUTED }}>Risk Disclosure: </span>
            StockAssist provides market information and AI-assisted analysis for informational purposes only. It does not provide guaranteed returns or personalised investment advice. Investments are subject to market risks. Please read all scheme-related documents carefully and consult a SEBI-registered financial advisor before making investment decisions.
          </p>
          <p style={{ fontSize: 11, color: "#C8CAD0" }}>© 2026 StockAssist. All rights reserved.</p>
        </div>
      </div>
    </footer>
  );
}

/* ═══════════════════════════════════════════════════════════
   MAIN EXPORT
═══════════════════════════════════════════════════════════ */
export default function LandingLight() {
  const [liveData,    setLiveData]    = useState(null);
  const [navScrolled, setNavScrolled] = useState(false);
  const rootRef = useRef(null);
  const heroRef = useRef(null);

  /* Override body/html background synchronously (before paint) so the app's
     dark ThemeContext (which sets data-theme="dark" on <html>) can't bleed
     its blue-indigo --bg through at the page edges. */
  useLayoutEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtmlBg = html.style.background;
    const prevBodyBg = body.style.background;
    const prevHtmlBgColor = html.style.backgroundColor;
    const prevBodyBgColor = body.style.backgroundColor;
    html.style.setProperty("background", "#F5F5F2", "important");
    html.style.setProperty("background-color", "#F5F5F2", "important");
    body.style.setProperty("background", "#F5F5F2", "important");
    body.style.setProperty("background-color", "#F5F5F2", "important");
    return () => {
      html.style.background = prevHtmlBg;
      html.style.backgroundColor = prevHtmlBgColor;
      body.style.background = prevBodyBg;
      body.style.backgroundColor = prevBodyBgColor;
    };
  }, []);

  /* Inject CSS once (fallback for cases where <style> tag may be stripped) */
  useEffect(() => {
    const id = "sl-landing-light-styles";
    if (!document.getElementById(id)) {
      const el = document.createElement("style");
      el.id = id;
      el.textContent = LIGHT_STYLES;
      document.head.appendChild(el);
    }
  }, []);

  /* Live market data */
  useEffect(() => {
    api.get("/market/overview").then((r) => setLiveData(r.data)).catch(() => {});
  }, []);

  /* Nav scroll state */
  useEffect(() => {
    const onScroll = () => setNavScrolled(window.scrollY > 30);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  /* GSAP parallax on hero terminal */
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const ctx = gsap.context(() => {
      if (!heroRef.current) return;
      gsap.to(".sl-hero-term-wrapper", {
        scrollTrigger: { trigger: heroRef.current, start: "top top", end: "bottom top", scrub: 1.4 },
        y: 45, ease: "none",
      });
    }, rootRef);
    return () => ctx.revert();
  }, []);

  /* Smooth anchor scroll */
  const handleNavClick = (e, href) => {
    const target = document.querySelector(href);
    if (!target) return;
    e.preventDefault();
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  };

  return (
    <MotionConfig reducedMotion="user">
      <div ref={rootRef} className="sl-root" data-testid="light-landing-page" style={{ minHeight: "100vh" }}>
        <style>{LIGHT_STYLES}</style>

        {/* ── NAVIGATION ── */}
        <Navigation scrolled={navScrolled} onNavClick={handleNavClick} />

        {/* ── HERO ── */}
        <HeroSection liveData={liveData} heroRef={heroRef} onNavClick={handleNavClick} />

        {/* ── TICKER ── */}
        <TickerBar />

        {/* ── MARKET CONTEXT ── */}
        <MarketContextSection liveData={liveData} />

        <div className="sl-divider" />

        {/* ── AI ANALYSIS ── */}
        <AIAnalysisSection />

        <div className="sl-divider" />

        {/* ── PORTFOLIO ── */}
        <PortfolioSection />

        <div className="sl-divider" />

        {/* ── ECOSYSTEM ── */}
        <EcosystemSection />

        <div className="sl-divider" />

        {/* ── PHILOSOPHY ── */}
        <PhilosophySection />

        {/* ── FINAL CTA ── */}
        <FinalCTASection />

        {/* ── FOOTER ── */}
        <Footer onNavClick={handleNavClick} />
      </div>
    </MotionConfig>
  );
}
