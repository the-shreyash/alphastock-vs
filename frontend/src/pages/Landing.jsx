import { Link } from "react-router-dom";
import { useState, useEffect, useRef } from "react";
import { motion, MotionConfig } from "framer-motion";
import {
  ArrowRight, Shield, Brain, TrendingUp, BarChart3, Zap, LineChart,
  Bot, ChevronRight, ScanSearch, Lock, Database, Activity, Cpu,
  Eye, Newspaper, Layers, Check, Globe,
} from "lucide-react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import api from "../services/api";

gsap.registerPlugin(ScrollTrigger);

/* ─────────────────────────────────────────────────────────
   LANDING-SPECIFIC CSS (injected once, scoped via class)
───────────────────────────────────────────────────────── */
const LANDING_STYLES = `
  .lp-root {
    background: #080912;
    color: #F0F2F5;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    overflow-x: hidden;
  }
  /* ── Orb animations ── */
  @keyframes lp-orb-drift {
    0%, 100% { transform: translate(0, 0) scale(1); }
    33%       { transform: translate(40px, -30px) scale(1.08); }
    66%       { transform: translate(-25px, 20px) scale(0.96); }
  }
  @keyframes lp-orb-drift-2 {
    0%, 100% { transform: translate(0, 0) scale(1); }
    40%       { transform: translate(-50px, 30px) scale(1.05); }
    70%       { transform: translate(30px, -20px) scale(0.98); }
  }
  @keyframes lp-particle {
    0%   { opacity: 0; transform: translateY(0) scale(0.5); }
    20%  { opacity: 1; }
    80%  { opacity: 0.6; }
    100% { opacity: 0; transform: translateY(-120px) scale(1.2); }
  }
  @keyframes lp-pulse-ring {
    0%   { transform: scale(0.85); opacity: 0.8; }
    70%  { transform: scale(1.15); opacity: 0; }
    100% { transform: scale(1.15); opacity: 0; }
  }
  @keyframes lp-glow-breathe {
    0%, 100% { opacity: 0.5; }
    50%       { opacity: 1; }
  }
  @keyframes lp-float-y {
    0%, 100% { transform: translateY(0px); }
    50%       { transform: translateY(-10px); }
  }
  @keyframes lp-scan-line {
    0%   { transform: translateY(0); opacity: 0.7; }
    100% { transform: translateY(220px); opacity: 0; }
  }
  @keyframes lp-ticker {
    0%   { transform: translateX(0); }
    100% { transform: translateX(-50%); }
  }
  @keyframes lp-flow-path {
    0%   { stroke-dashoffset: 300; opacity: 0; }
    20%  { opacity: 1; }
    100% { stroke-dashoffset: 0; opacity: 0.5; }
  }
  @keyframes lp-blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.3; }
  }
  .lp-orb-1 { animation: lp-orb-drift 18s ease-in-out infinite; }
  .lp-orb-2 { animation: lp-orb-drift-2 22s ease-in-out infinite; }
  .lp-orb-3 { animation: lp-orb-drift 26s ease-in-out infinite reverse; }
  .lp-float { animation: lp-float-y 4.5s ease-in-out infinite; }
  .lp-glow  { animation: lp-glow-breathe 3s ease-in-out infinite; }
  .lp-blink { animation: lp-blink 2.2s ease-in-out infinite; }

  /* ── Nav ── */
  .lp-nav {
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    backdrop-filter: blur(24px) saturate(1.6);
    -webkit-backdrop-filter: blur(24px) saturate(1.6);
    background: rgba(8, 9, 18, 0.72);
    border-bottom: 1px solid rgba(130,141,235,0.08);
    transition: background 0.3s ease;
  }
  /* ── Cards ── */
  .lp-card {
    background: rgba(18, 21, 40, 0.75);
    backdrop-filter: blur(20px) saturate(1.5);
    -webkit-backdrop-filter: blur(20px) saturate(1.5);
    border: 1px solid rgba(130,141,235,0.13);
    border-radius: 18px;
    box-shadow: 0 4px 32px rgba(0,0,0,0.35);
    transition: border-color 0.3s ease, box-shadow 0.3s ease;
  }
  .lp-card:hover {
    border-color: rgba(129,140,248,0.28);
    box-shadow: 0 8px 48px rgba(129,140,248,0.12);
  }
  .lp-card-glow {
    background: rgba(18, 21, 40, 0.82);
    border: 1px solid rgba(129,140,248,0.2);
    border-radius: 18px;
    box-shadow: 0 0 40px rgba(129,140,248,0.15), 0 4px 32px rgba(0,0,0,0.4);
  }
  /* ── Buttons ── */
  .lp-btn-primary {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 14px 28px; border-radius: 14px;
    font-family: 'Outfit', sans-serif; font-size: 15px; font-weight: 600;
    background: linear-gradient(135deg, #6366F1, #818CF8);
    color: #fff; border: none; cursor: pointer; white-space: nowrap;
    box-shadow: 0 0 0 1px rgba(129,140,248,0.3), 0 4px 20px rgba(99,102,241,0.4);
    transition: all 0.25s cubic-bezier(0.16,1,0.3,1);
    text-decoration: none;
  }
  .lp-btn-primary:hover {
    transform: translateY(-2px);
    box-shadow: 0 0 0 1px rgba(129,140,248,0.5), 0 8px 32px rgba(99,102,241,0.55);
    filter: brightness(1.08);
  }
  .lp-btn-ghost {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 14px 28px; border-radius: 14px;
    font-family: 'Outfit', sans-serif; font-size: 15px; font-weight: 600;
    background: rgba(129,140,248,0.07);
    color: rgba(240,242,245,0.85);
    border: 1px solid rgba(130,141,235,0.2); cursor: pointer; white-space: nowrap;
    transition: all 0.25s cubic-bezier(0.16,1,0.3,1);
    text-decoration: none;
  }
  .lp-btn-ghost:hover {
    background: rgba(129,140,248,0.14);
    border-color: rgba(129,140,248,0.4);
    transform: translateY(-1px);
  }
  /* ── Text ── */
  .lp-eyebrow {
    font-size: 11px; font-weight: 700; letter-spacing: 0.18em;
    text-transform: uppercase; color: #818CF8;
  }
  .lp-h2 {
    font-family: 'Outfit', sans-serif; font-size: clamp(2rem, 3.5vw, 3rem);
    font-weight: 700; line-height: 1.1; letter-spacing: -0.025em;
    color: #F0F2F5;
  }
  .lp-body {
    font-size: 15px; line-height: 1.7; color: rgba(240,242,245,0.6);
  }
  /* ── Dividers ── */
  .lp-section-divider {
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(130,141,235,0.15), transparent);
  }
  /* ── Ticker ── */
  .lp-ticker-track {
    display: flex; width: max-content;
    animation: lp-ticker 55s linear infinite;
  }
  .lp-ticker-track:hover { animation-play-state: paused; }

  /* ── Badge ── */
  .lp-badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 14px; border-radius: 99px;
    font-size: 11px; font-weight: 600;
    background: rgba(129,140,248,0.1);
    border: 1px solid rgba(129,140,248,0.25);
    color: #818CF8;
  }
  .lp-badge-live {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 3px 10px; border-radius: 99px;
    font-size: 10px; font-weight: 700;
    font-family: 'JetBrains Mono', monospace; text-transform: uppercase;
    background: rgba(0,214,143,0.1); color: #00D68F;
    border: 1px solid rgba(0,214,143,0.2);
  }
  /* ── Flow ── */
  .lp-flow-path {
    stroke-dasharray: 300;
    animation: lp-flow-path 3s ease-in-out infinite;
  }
  /* ── Particle ── */
  .lp-particle {
    position: absolute; border-radius: 50%;
    animation: lp-particle ease-in-out infinite;
    pointer-events: none;
  }
  /* ── Scan line ── */
  .lp-scan-line {
    position: absolute; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(129,140,248,0.6), transparent);
    animation: lp-scan-line 3.5s ease-in-out infinite;
    pointer-events: none;
  }
  /* ── Anchor offset: the nav is fixed at 64px, so an anchored section would
       otherwise scroll to sit underneath it. ── */
  .lp-root section[id] { scroll-margin-top: 84px; }

  /* ── Reduced motion ── */
  @media (prefers-reduced-motion: reduce) {
    .lp-orb-1, .lp-orb-2, .lp-orb-3,
    .lp-float, .lp-glow, .lp-blink,
    .lp-ticker-track,
    .lp-particle,
    .lp-scan-line { animation: none !important; }
  }
`;

/* ─────────────────────────────────────────────────────────
   LOGO
───────────────────────────────────────────────────────── */
function APLogo({ size = 32 }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 512 512" aria-label="StockAssist logo">
      <defs>
        <linearGradient id="lp-logo-g" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#4F46E5" />
          <stop offset="100%" stopColor="#818CF8" />
        </linearGradient>
      </defs>
      <rect width="512" height="512" rx="110" fill="url(#lp-logo-g)" />
      <rect x="80"  y="340" width="44" height="96"  rx="6" fill="#fff" opacity="0.45" />
      <rect x="140" y="300" width="44" height="136" rx="6" fill="#fff" opacity="0.58" />
      <rect x="200" y="250" width="44" height="186" rx="6" fill="#fff" opacity="0.70" />
      <rect x="260" y="200" width="44" height="236" rx="6" fill="#fff" opacity="0.82" />
      <rect x="320" y="155" width="44" height="281" rx="6" fill="#fff" opacity="0.91" />
      <rect x="380" y="115" width="44" height="321" rx="6" fill="#fff" />
      <polyline points="102,340 162,300 222,248 282,198 342,153 402,113"
        stroke="#fff" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" opacity="0.9" />
      <circle cx="402" cy="113" r="9" fill="#fff" />
    </svg>
  );
}

/* ─────────────────────────────────────────────────────────
   AMBIENT BACKGROUND
───────────────────────────────────────────────────────── */
function AmbientBackground() {
  return (
    <div aria-hidden="true" style={{ position: "absolute", inset: 0, overflow: "hidden", pointerEvents: "none" }}>
      {/* Main violet orb */}
      <div className="lp-orb-1" style={{
        position: "absolute", top: "-10%", left: "20%",
        width: 700, height: 700, borderRadius: "50%",
        background: "radial-gradient(circle, rgba(99,102,241,0.18) 0%, transparent 70%)",
      }} />
      {/* Purple-blue orb right */}
      <div className="lp-orb-2" style={{
        position: "absolute", top: "15%", right: "-5%",
        width: 550, height: 550, borderRadius: "50%",
        background: "radial-gradient(circle, rgba(129,140,248,0.12) 0%, transparent 70%)",
      }} />
      {/* Deep blue orb bottom */}
      <div className="lp-orb-3" style={{
        position: "absolute", bottom: "5%", left: "-5%",
        width: 450, height: 450, borderRadius: "50%",
        background: "radial-gradient(circle, rgba(59,130,246,0.10) 0%, transparent 70%)",
      }} />
      {/* Subtle grid */}
      <div style={{
        position: "absolute", inset: 0, opacity: 0.03,
        backgroundImage: "radial-gradient(circle at 1px 1px, rgba(240,242,245,0.8) 1px, transparent 0)",
        backgroundSize: "44px 44px",
      }} />
      {/* Noise grain */}
      <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", opacity: 0.025 }}>
        <filter id="lp-noise">
          <feTurbulence type="fractalNoise" baseFrequency="0.65" numOctaves="3" stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter="url(#lp-noise)" />
      </svg>
      {/* Floating particles */}
      {[
        { left: "15%", top: "60%", size: 3, delay: "0s",  dur: "6s",  color: "rgba(129,140,248,0.7)" },
        { left: "30%", top: "75%", size: 2, delay: "1.5s", dur: "8s",  color: "rgba(99,102,241,0.5)" },
        { left: "65%", top: "55%", size: 2, delay: "0.8s", dur: "7s",  color: "rgba(129,140,248,0.6)" },
        { left: "78%", top: "70%", size: 3, delay: "2.2s", dur: "9s",  color: "rgba(59,130,246,0.5)" },
        { left: "50%", top: "80%", size: 2, delay: "3s",   dur: "7.5s",color: "rgba(167,139,250,0.5)" },
        { left: "88%", top: "45%", size: 2, delay: "1s",   dur: "8.5s",color: "rgba(129,140,248,0.4)" },
      ].map((p, i) => (
        <div key={i} className="lp-particle" style={{
          left: p.left, top: p.top, width: p.size, height: p.size,
          background: p.color, animationDelay: p.delay, animationDuration: p.dur,
        }} />
      ))}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   DEVICE MOCKUP (SVG laptop with inline dashboard)
───────────────────────────────────────────────────────── */
function DeviceMockup({ liveData }) {
  const nifty  = liveData?.nifty;
  const sensex = liveData?.sensex;
  const bankNifty = liveData?.bank_nifty;

  const fmt = (v, sample) => v ? Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : sample;
  const fmtPct = (v, sample) => v != null ? `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%` : sample;
  const gainColor = (v) => (v ?? 0) >= 0 ? "#00D68F" : "#FF6B6B";

  return (
    <div className="lp-float" style={{ position: "relative", width: "100%", maxWidth: 820, margin: "0 auto" }}>
      {/* Glow halo behind device */}
      <div className="lp-glow" style={{
        position: "absolute", top: "10%", left: "10%", right: "10%", bottom: "5%",
        borderRadius: "50%", pointerEvents: "none",
        background: "radial-gradient(ellipse, rgba(99,102,241,0.22) 0%, transparent 70%)",
      }} />

      <svg viewBox="0 0 820 520" style={{ width: "100%", height: "auto", display: "block" }} aria-label="StockAssist dashboard preview">
        <defs>
          <linearGradient id="dev-body-g" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#1C1E30" />
            <stop offset="100%" stopColor="#0F1020" />
          </linearGradient>
          <linearGradient id="dev-screen-g" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#0A0C18" />
            <stop offset="100%" stopColor="#080A14" />
          </linearGradient>
          <linearGradient id="dev-chart-g" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#818CF8" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#818CF8" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="dev-chart-g2" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#00D68F" stopOpacity="0.3" />
            <stop offset="100%" stopColor="#00D68F" stopOpacity="0" />
          </linearGradient>
          <clipPath id="dev-screen-clip">
            <rect x="55" y="20" width="710" height="445" rx="6" />
          </clipPath>
          <filter id="dev-glow-filter">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>

        {/* ── Laptop body ── */}
        {/* Screen surround */}
        <rect x="30" y="8" width="760" height="470" rx="14" fill="url(#dev-body-g)" stroke="rgba(130,141,235,0.25)" strokeWidth="1.5" />
        {/* Screen bezel */}
        <rect x="42" y="16" width="736" height="456" rx="10" fill="#0A0C18" />
        {/* Camera dot */}
        <circle cx="410" cy="22" r="3" fill="rgba(130,141,235,0.3)" />
        {/* Keyboard base */}
        <rect x="0" y="478" width="820" height="28" rx="6" fill="url(#dev-body-g)" stroke="rgba(130,141,235,0.15)" strokeWidth="1" />
        <rect x="290" y="482" width="240" height="14" rx="7" fill="rgba(0,0,0,0.4)" />

        {/* ── Screen content (clipped) ── */}
        <g clipPath="url(#dev-screen-clip)">
          <rect x="55" y="20" width="710" height="445" fill="url(#dev-screen-g)" />

          {/* ── Sidebar ── */}
          <rect x="55" y="20" width="52" height="445" fill="rgba(8,9,20,0.9)" />
          {/* Sidebar logo */}
          <rect x="68" y="38" width="26" height="26" rx="6" fill="rgba(99,102,241,0.8)" />
          <rect x="74" y="48" width="6" height="10" rx="1" fill="white" opacity="0.8" />
          <rect x="82" y="44" width="6" height="14" rx="1" fill="white" opacity="0.9" />
          {/* Sidebar icons */}
          {[80, 108, 136, 164, 192].map((y, i) => (
            <rect key={i} x="68" y={y} width="26" height="20" rx="5"
              fill={i === 0 ? "rgba(129,140,248,0.2)" : "transparent"}
              stroke={i === 0 ? "rgba(129,140,248,0.4)" : "none"} />
          ))}

          {/* ── Top bar ── */}
          <rect x="107" y="20" width="658" height="32" fill="rgba(10,12,22,0.95)" />
          <text x="120" y="40" fill="rgba(240,242,245,0.85)" fontSize="10" fontWeight="600" fontFamily="Outfit, sans-serif">Dashboard</text>
          {/* Search bar */}
          <rect x="360" y="26" width="160" height="20" rx="6" fill="rgba(130,141,235,0.08)" stroke="rgba(130,141,235,0.15)" strokeWidth="0.5" />
          <text x="374" y="39" fill="rgba(240,242,245,0.25)" fontSize="9" fontFamily="Inter, sans-serif">Search markets...</text>
          {/* Live badge */}
          <rect x="540" y="26" width="38" height="20" rx="5" fill="rgba(0,214,143,0.12)" stroke="rgba(0,214,143,0.25)" strokeWidth="0.5" />
          <circle cx="548" cy="36" r="3" fill="#00D68F" opacity="0.9" />
          <text x="553" y="39" fill="#00D68F" fontSize="7.5" fontWeight="700" fontFamily="JetBrains Mono, monospace">LIVE</text>

          {/* ── Index Cards Row ── */}
          {[
            { x: 112, label: "NIFTY 50",   val: fmt(nifty?.value, "24,320.15"),     pct: fmtPct(nifty?.change_pct, "+0.82%"),     col: gainColor(nifty?.change_pct) },
            { x: 248, label: "SENSEX",     val: fmt(sensex?.value, "80,142.30"),    pct: fmtPct(sensex?.change_pct, "+0.74%"),    col: gainColor(sensex?.change_pct) },
            { x: 384, label: "BANK NIFTY", val: fmt(bankNifty?.value, "52,418.60"), pct: fmtPct(bankNifty?.change_pct, "+1.12%"), col: gainColor(bankNifty?.change_pct) },
            { x: 520, label: "VIX",        val: "14.82",  pct: "+0.34%",  col: "#FF6B6B" },
          ].map((c) => (
            <g key={c.label}>
              <rect x={c.x} y="57" width="128" height="56" rx="8" fill="rgba(18,21,40,0.9)" stroke="rgba(130,141,235,0.12)" strokeWidth="0.8" />
              <text x={c.x+10} y="73" fill="rgba(240,242,245,0.4)" fontSize="7.5" fontWeight="700" fontFamily="Inter, sans-serif" letterSpacing="0.08em">{c.label}</text>
              <text x={c.x+10} y="89" fill="rgba(240,242,245,0.92)" fontSize="13" fontWeight="600" fontFamily="JetBrains Mono, monospace">{c.val}</text>
              <text x={c.x+10} y="103" fill={c.col} fontSize="9" fontWeight="600" fontFamily="JetBrains Mono, monospace">{c.pct}</text>
            </g>
          ))}

          {/* ── Main chart panel ── */}
          <rect x="112" y="120" width="310" height="195" rx="10" fill="rgba(12,14,26,0.9)" stroke="rgba(130,141,235,0.12)" strokeWidth="0.8" />
          <text x="124" y="140" fill="rgba(240,242,245,0.8)" fontSize="10" fontWeight="600" fontFamily="Outfit, sans-serif">NIFTY 50 — 1D</text>
          {/* Chart line */}
          <path d="M120,270 Q140,260 165,255 T215,240 T255,245 T290,228 T330,218 T365,205 T400,195 L400,305 L120,305 Z"
            fill="url(#dev-chart-g)" />
          <path d="M120,270 Q140,260 165,255 T215,240 T255,245 T290,228 T330,218 T365,205 T400,195"
            fill="none" stroke="#818CF8" strokeWidth="1.5" strokeLinecap="round" />
          {/* Chart dot */}
          <circle cx="400" cy="195" r="3" fill="#818CF8" filter="url(#dev-glow-filter)" />
          {/* Grid lines */}
          {[145, 165, 185, 205, 225, 245, 265, 285, 305].map((y) => (
            <line key={y} x1="112" y1={y+15} x2="422" y2={y+15} stroke="rgba(130,141,235,0.05)" strokeWidth="0.5" />
          ))}
          {/* Scan line */}
          <line x1="112" y1="200" x2="422" y2="200" stroke="rgba(129,140,248,0.15)" strokeWidth="0.5" strokeDasharray="4 4" />

          {/* ── AI Insight Panel ── */}
          <rect x="430" y="120" width="335" height="195" rx="10" fill="rgba(12,14,26,0.9)" stroke="rgba(129,140,248,0.2)" strokeWidth="0.8" />
          {/* AI badge */}
          <rect x="442" y="130" width="58" height="14" rx="4" fill="rgba(129,140,248,0.12)" stroke="rgba(129,140,248,0.25)" strokeWidth="0.5" />
          <text x="452" y="141" fill="#818CF8" fontSize="7" fontWeight="700" fontFamily="JetBrains Mono, monospace">AI INSIGHT</text>
          <text x="442" y="160" fill="rgba(240,242,245,0.85)" fontSize="9" fontFamily="Inter, sans-serif">Market momentum strengthening</text>
          <text x="442" y="175" fill="rgba(240,242,245,0.5)" fontSize="8.5" fontFamily="Inter, sans-serif">as financial and tech sectors lead</text>
          <text x="442" y="190" fill="rgba(240,242,245,0.5)" fontSize="8.5" fontFamily="Inter, sans-serif">broad-based advance. Breadth</text>
          <text x="442" y="205" fill="rgba(240,242,245,0.5)" fontSize="8.5" fontFamily="Inter, sans-serif">improving across mid-cap index.</text>
          {/* Signal chips */}
          {[
            { x: 442, y: 222, label: "Bullish",     col: "#00D68F", bg: "rgba(0,214,143,0.12)" },
            { x: 492, y: 222, label: "Vol ↑",        col: "#818CF8", bg: "rgba(129,140,248,0.12)" },
            { x: 534, y: 222, label: "IT Sector",   col: "#60A5FA", bg: "rgba(96,165,250,0.12)" },
            { x: 587, y: 222, label: "News: +",     col: "#00D68F", bg: "rgba(0,214,143,0.1)" },
          ].map((c) => (
            <g key={c.label}>
              <rect x={c.x} y={c.y} width={c.label.length * 5.8 + 8} height="14" rx="4" fill={c.bg} />
              <text x={c.x + 4} y={c.y + 10} fill={c.col} fontSize="7.5" fontWeight="600" fontFamily="Inter, sans-serif">{c.label}</text>
            </g>
          ))}
          {/* AI activity dots */}
          <circle cx="750" cy="136" r="4" fill="rgba(129,140,248,0.2)" className="lp-blink" />
          <circle cx="750" cy="136" r="2.5" fill="#818CF8" className="lp-blink" />

          {/* ── Watchlist Panel ── */}
          <rect x="112" y="322" width="210" height="135" rx="10" fill="rgba(12,14,26,0.9)" stroke="rgba(130,141,235,0.12)" strokeWidth="0.8" />
          <text x="124" y="340" fill="rgba(240,242,245,0.75)" fontSize="9.5" fontWeight="600" fontFamily="Outfit, sans-serif">Watchlist</text>
          {[
            { sym: "RELIANCE", val: "2,847",  pct: "+1.24%", col: "#00D68F" },
            { sym: "INFY",     val: "1,642",  pct: "+0.86%", col: "#00D68F" },
            { sym: "HDFCBANK", val: "1,723",  pct: "-0.31%", col: "#FF6B6B" },
            { sym: "TCS",      val: "3,912",  pct: "+0.55%", col: "#00D68F" },
          ].map((s, i) => (
            <g key={s.sym}>
              <text x="124"  y={358 + i * 22} fill="rgba(240,242,245,0.75)" fontSize="8.5" fontWeight="600" fontFamily="Inter, sans-serif">{s.sym}</text>
              <text x="252"  y={358 + i * 22} fill="rgba(240,242,245,0.5)"  fontSize="8"   fontFamily="JetBrains Mono, monospace" textAnchor="end">{s.val}</text>
              <text x="318"  y={358 + i * 22} fill={s.col} fontSize="8" fontFamily="JetBrains Mono, monospace" textAnchor="end">{s.pct}</text>
            </g>
          ))}

          {/* ── Market Breadth ── */}
          <rect x="330" y="322" width="205" height="135" rx="10" fill="rgba(12,14,26,0.9)" stroke="rgba(130,141,235,0.12)" strokeWidth="0.8" />
          <text x="342" y="340" fill="rgba(240,242,245,0.75)" fontSize="9.5" fontWeight="600" fontFamily="Outfit, sans-serif">Market Breadth</text>
          <text x="342" y="360" fill="#00D68F" fontSize="20" fontWeight="700" fontFamily="JetBrains Mono, monospace">312</text>
          <text x="342" y="373" fill="rgba(0,214,143,0.6)" fontSize="8" fontFamily="Inter, sans-serif">Advancing</text>
          <text x="440" y="360" fill="#FF6B6B" fontSize="20" fontWeight="700" fontFamily="JetBrains Mono, monospace">138</text>
          <text x="440" y="373" fill="rgba(255,107,107,0.6)" fontSize="8" fontFamily="Inter, sans-serif">Declining</text>
          {/* Breadth bar */}
          <rect x="342" y="385" width="181" height="7" rx="3.5" fill="rgba(130,141,235,0.12)" />
          <rect x="342" y="385" width="119" height="7" rx="3.5" fill="#00D68F" opacity="0.75" />
          <text x="342" y="408" fill="rgba(240,242,245,0.35)" fontSize="7.5" fontFamily="Inter, sans-serif">68% stocks advancing on NSE today</text>

          {/* ── Portfolio ── */}
          <rect x="543" y="322" width="222" height="135" rx="10" fill="rgba(12,14,26,0.9)" stroke="rgba(130,141,235,0.12)" strokeWidth="0.8" />
          <text x="555" y="340" fill="rgba(240,242,245,0.75)" fontSize="9.5" fontWeight="600" fontFamily="Outfit, sans-serif">Portfolio</text>
          <text x="555" y="364" fill="rgba(240,242,245,0.92)" fontSize="18" fontWeight="700" fontFamily="JetBrains Mono, monospace">₹4,82,310</text>
          <text x="555" y="378" fill="#00D68F" fontSize="8.5" fontWeight="600" fontFamily="JetBrains Mono, monospace">+₹6,842 (+1.44%) today</text>
          {/* Mini portfolio bar */}
          <path d="M555,410 Q565,400 575,395 T595,385 T615,388 T635,380 T655,372 T665,368 L665,435 L555,435 Z"
            fill="url(#dev-chart-g2)" />
          <path d="M555,410 Q565,400 575,395 T595,385 T615,388 T635,380 T655,372 T665,368"
            fill="none" stroke="#00D68F" strokeWidth="1.2" strokeLinecap="round" />
        </g>

        {/* ── Screen edge glow ── */}
        <rect x="42" y="16" width="736" height="456" rx="10" fill="none"
          stroke="rgba(129,140,248,0.12)" strokeWidth="1.5" />
      </svg>

      {/* Floating accent cards outside the device */}
      <motion.div
        initial={{ opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ delay: 1.2, duration: 0.7, ease: "easeOut" }}
        style={{
          position: "absolute", left: "-8%", top: "28%",
          background: "rgba(12,14,26,0.92)",
          border: "1px solid rgba(0,214,143,0.25)", borderRadius: 14,
          padding: "12px 16px", backdropFilter: "blur(20px)",
          boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
        }}
      >
        <div style={{ fontSize: 9, color: "rgba(0,214,143,0.7)", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 4 }}>Portfolio Today</div>
        <div style={{ fontSize: 18, fontWeight: 700, color: "#00D68F", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>+1.44%</div>
        <div style={{ fontSize: 10, color: "rgba(240,242,245,0.5)", marginTop: 2 }}>₹6,842 gain</div>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, x: 20 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ delay: 1.4, duration: 0.7, ease: "easeOut" }}
        style={{
          position: "absolute", right: "-6%", top: "18%",
          background: "rgba(12,14,26,0.92)",
          border: "1px solid rgba(129,140,248,0.3)", borderRadius: 14,
          padding: "12px 16px", backdropFilter: "blur(20px)",
          boxShadow: "0 0 24px rgba(129,140,248,0.15), 0 4px 24px rgba(0,0,0,0.4)",
        }}
      >
        <div style={{ fontSize: 9, color: "rgba(129,140,248,0.7)", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#818CF8", display: "inline-block", animation: "lp-blink 2s infinite" }} />
          AI Analysis
        </div>
        <div style={{ fontSize: 13, fontWeight: 600, color: "#F0F2F5", fontFamily: "'Inter', sans-serif", lineHeight: 1.4, maxWidth: 140 }}>Momentum remains positive</div>
        <div style={{ fontSize: 10, color: "rgba(240,242,245,0.4)", marginTop: 3 }}>Confidence: 78/100</div>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 1.6, duration: 0.7, ease: "easeOut" }}
        style={{
          position: "absolute", right: "-4%", bottom: "1%",
          background: "rgba(12,14,26,0.92)",
          border: "1px solid rgba(130,141,235,0.2)", borderRadius: 14,
          padding: "10px 14px", backdropFilter: "blur(20px)",
          boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
        }}
      >
        <div style={{ fontSize: 9, color: "rgba(240,242,245,0.35)", fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 3 }}>Scanning</div>
        <div style={{ fontSize: 12, fontWeight: 600, color: "#F0F2F5" }}>52 NSE stocks</div>
        <div style={{ width: 80, height: 3, borderRadius: 4, background: "rgba(130,141,235,0.15)", marginTop: 6, overflow: "hidden" }}>
          <motion.div
            style={{ height: "100%", background: "linear-gradient(90deg, #6366F1, #818CF8)", borderRadius: 4 }}
            initial={{ width: "0%" }}
            animate={{ width: "78%" }}
            transition={{ delay: 2, duration: 1.2, ease: "easeOut" }}
          />
        </div>
      </motion.div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   TICKER BAR
───────────────────────────────────────────────────────── */
const TICKER_ITEMS = [
  { sym: "NIFTY 50", val: "24,320.15", pct: "+0.82%", gain: true },
  { sym: "SENSEX",   val: "80,142.30", pct: "+0.74%", gain: true },
  { sym: "BANKNIFTY",val: "52,418.60", pct: "+1.12%", gain: true },
  { sym: "RELIANCE", val: "2,847.25",  pct: "+1.24%", gain: true },
  { sym: "INFY",     val: "1,642.80",  pct: "+0.86%", gain: true },
  { sym: "TCS",      val: "3,912.40",  pct: "+0.55%", gain: true },
  { sym: "HDFCBANK", val: "1,723.10",  pct: "-0.31%", gain: false },
  { sym: "VIX",      val: "14.82",     pct: "+0.34%", gain: false },
  { sym: "WIPRO",    val: "542.60",    pct: "+0.92%", gain: true },
  { sym: "BAJFINANCE",val: "7,142.50", pct: "-0.18%", gain: false },
];

function TickerBar() {
  const doubled = [...TICKER_ITEMS, ...TICKER_ITEMS];
  return (
    <div style={{
      background: "rgba(8,9,18,0.85)", borderTop: "1px solid rgba(130,141,235,0.08)",
      borderBottom: "1px solid rgba(130,141,235,0.08)", overflow: "hidden",
      padding: "8px 0", backdropFilter: "blur(12px)",
    }}>
      <div className="lp-ticker-track">
        {doubled.map((t, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "0 32px", flexShrink: 0 }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: "rgba(240,242,245,0.55)", fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.04em" }}>{t.sym}</span>
            <span style={{ fontSize: 11, fontWeight: 500, color: "rgba(240,242,245,0.8)", fontFamily: "'JetBrains Mono', monospace" }}>{t.val}</span>
            <span style={{ fontSize: 10, fontWeight: 700, color: t.gain ? "#00D68F" : "#FF6B6B", fontFamily: "'JetBrains Mono', monospace" }}>{t.pct}</span>
            <span style={{ width: 3, height: 3, borderRadius: "50%", background: "rgba(130,141,235,0.3)", flexShrink: 0 }} />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 2 — MARKET INTELLIGENCE
───────────────────────────────────────────────────────── */
function MarketIntelligenceSection({ liveData }) {
  /*
   * `/api/market/overview` answers `{ available: false }` (with no index
   * payload) whenever the gateway cannot serve live indices. The card falls
   * back to representative sample figures in that case, so the badge has to say
   * so — labelling placeholder numbers "LIVE" would misrepresent the product to
   * a visitor who has no way to tell the difference.
   */
  const isLive = liveData?.available === true && liveData?.nifty?.value != null;
  const fmt = (v) => v ? Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "24,320";
  const fmtPct = (v) => v != null ? `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%` : "+0.82%";
  const gainColor = (v) => (v ?? 0.82) >= 0 ? "#00D68F" : "#FF6B6B";

  const cardAnim = { initial: { opacity: 0, y: 32 }, whileInView: { opacity: 1, y: 0 }, viewport: { once: true, margin: "-60px" } };

  return (
    <section id="features" style={{ padding: "120px 24px", position: "relative" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        {/* Header */}
        <div style={{ marginBottom: 72 }}>
          <div className="lp-eyebrow" style={{ marginBottom: 16 }}>Market Intelligence</div>
          <h2 className="lp-h2" style={{ maxWidth: 560, marginBottom: 20 }}>
            See the market<br />
            <span style={{ background: "linear-gradient(135deg, #818CF8, #A78BFA)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              differently.
            </span>
          </h2>
          <p className="lp-body" style={{ maxWidth: 480 }}>
            StockAssist unifies live index data, AI-generated insight, market news, and breadth signals into one coherent intelligent view — so you always know what the market is doing and why.
          </p>
        </div>

        {/* Asymmetric grid */}
        <div className="lp-mi-grid" style={{ display: "grid", gridTemplateColumns: "repeat(12, 1fr)", gap: 20 }}>
          {/* Large index card */}
          <motion.div {...cardAnim} transition={{ duration: 0.6, ease: "easeOut" }}
            style={{ gridColumn: "span 5" }} className="lp-card lp-mi-main" data-testid="mi-index-card">
            <div style={{ padding: 28 }}>
              <div className="lp-eyebrow" style={{ marginBottom: 12 }}>{isLive ? "Live Index" : "Index Preview"}</div>
              <div style={{ marginBottom: 6, display: "flex", alignItems: "flex-end", gap: 12 }}>
                <div>
                  <div style={{ fontSize: 12, color: "rgba(240,242,245,0.45)", fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4 }}>NIFTY 50</div>
                  <div style={{ fontSize: 36, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace", color: "#F0F2F5", lineHeight: 1 }}>
                    {fmt(liveData?.nifty?.value)}
                  </div>
                </div>
                <div className="lp-badge-live" data-testid="mi-live-badge"
                  style={{
                    marginBottom: 6,
                    ...(isLive ? {} : {
                      background: "rgba(130,141,235,0.1)",
                      borderColor: "rgba(130,141,235,0.2)",
                      color: "rgba(240,242,245,0.45)",
                    }),
                  }}>
                  {isLive ? "LIVE" : "SAMPLE"}
                </div>
              </div>
              <div style={{ fontSize: 15, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace", color: gainColor(liveData?.nifty?.change_pct), marginBottom: 20 }}>
                {fmtPct(liveData?.nifty?.change_pct)} today
              </div>
              {/* Mini sparkline */}
              <svg viewBox="0 0 240 60" style={{ width: "100%", height: 60 }}>
                <defs>
                  <linearGradient id="mi-chart-g" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#818CF8" stopOpacity="0.35" />
                    <stop offset="100%" stopColor="#818CF8" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <path d="M0,45 Q20,42 40,38 T80,30 T120,32 T160,22 T200,16 T240,10 L240,60 L0,60 Z" fill="url(#mi-chart-g)" />
                <path d="M0,45 Q20,42 40,38 T80,30 T120,32 T160,22 T200,16 T240,10" fill="none" stroke="#818CF8" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                {["SENSEX", "BANK NIFTY", "MIDCAP"].map((idx) => (
                  <div key={idx} style={{
                    flex: 1, padding: "8px 10px", borderRadius: 10,
                    background: "rgba(130,141,235,0.07)", border: "1px solid rgba(130,141,235,0.1)",
                    fontSize: 10, color: "rgba(240,242,245,0.5)", fontWeight: 600,
                    textAlign: "center", letterSpacing: "0.04em",
                  }}>{idx}</div>
                ))}
              </div>
            </div>
          </motion.div>

          {/* AI Insight tall card */}
          <motion.div {...cardAnim} transition={{ duration: 0.6, delay: 0.1, ease: "easeOut" }}
            style={{ gridColumn: "span 4" }} className="lp-card-glow lp-mi-ai">
            <div style={{ padding: 28, height: "100%" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20 }}>
                <div style={{
                  width: 32, height: 32, borderRadius: 10,
                  background: "rgba(129,140,248,0.15)", display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  <Brain size={16} color="#818CF8" />
                </div>
                <div>
                  <div className="lp-eyebrow">AI Insight</div>
                  <div className="lp-blink" style={{ width: 6, height: 6, borderRadius: "50%", background: "#818CF8", marginTop: 2 }} />
                </div>
              </div>
              <p style={{ fontSize: 14, lineHeight: 1.65, color: "rgba(240,242,245,0.8)", marginBottom: 20, fontStyle: "italic" }}>
                "Momentum remains positive as financial and technology stocks strengthen. Increasing breadth suggests the move is broader than a narrow group of constituents."
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {["Bullish momentum", "IT outperforming", "Breadth widening", "FII buying"].map((tag) => (
                  <span key={tag} style={{
                    padding: "4px 10px", borderRadius: 8,
                    background: "rgba(129,140,248,0.1)", border: "1px solid rgba(129,140,248,0.18)",
                    fontSize: 10, color: "#818CF8", fontWeight: 600,
                  }}>{tag}</span>
                ))}
              </div>
              <div style={{ marginTop: 24, padding: "12px 14px", borderRadius: 12, background: "rgba(0,214,143,0.06)", border: "1px solid rgba(0,214,143,0.15)" }}>
                <div style={{ fontSize: 10, color: "rgba(0,214,143,0.6)", fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 4 }}>Market Sentiment</div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ flex: 1, height: 5, borderRadius: 4, background: "rgba(130,141,235,0.15)", overflow: "hidden" }}>
                    <div style={{ width: "68%", height: "100%", background: "linear-gradient(90deg, #00D68F, #34D399)", borderRadius: 4 }} />
                  </div>
                  <span style={{ fontSize: 11, fontWeight: 700, color: "#00D68F", fontFamily: "'JetBrains Mono', monospace" }}>68 Bullish</span>
                </div>
              </div>
            </div>
          </motion.div>

          {/* News + Breadth stacked right */}
          <div className="lp-mi-side" style={{ gridColumn: "span 3", display: "flex", flexDirection: "column", gap: 20 }}>
            <motion.div {...cardAnim} transition={{ duration: 0.6, delay: 0.2, ease: "easeOut" }} className="lp-card" style={{ flex: 1 }}>
              <div style={{ padding: 20 }}>
                <div className="lp-eyebrow" style={{ marginBottom: 12 }}>Market News</div>
                {[
                  { hl: "RBI holds repo rate at 6.5% amid stable inflation", time: "2h ago", gain: true },
                  { hl: "IT sector leads with strong Q4 earnings beat", time: "4h ago", gain: true },
                  { hl: "FII net buyers for fifth consecutive session", time: "6h ago", gain: true },
                ].map((n, i) => (
                  <div key={i} style={{ borderBottom: i < 2 ? "1px solid rgba(130,141,235,0.07)" : "none", paddingBottom: i < 2 ? 10 : 0, marginBottom: i < 2 ? 10 : 0 }}>
                    <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                      <div style={{ width: 5, height: 5, borderRadius: "50%", background: n.gain ? "#00D68F" : "#FF6B6B", marginTop: 4, flexShrink: 0 }} />
                      <div>
                        <p style={{ fontSize: 11, lineHeight: 1.5, color: "rgba(240,242,245,0.7)", marginBottom: 2 }}>{n.hl}</p>
                        <span style={{ fontSize: 9, color: "rgba(240,242,245,0.3)", fontFamily: "'JetBrains Mono', monospace" }}>{n.time}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </motion.div>

            <motion.div {...cardAnim} transition={{ duration: 0.6, delay: 0.3, ease: "easeOut" }} className="lp-card" style={{ flexShrink: 0 }}>
              <div style={{ padding: 20 }}>
                <div className="lp-eyebrow" style={{ marginBottom: 10 }}>Breadth</div>
                <div style={{ display: "flex", gap: 12, alignItems: "flex-end", marginBottom: 10 }}>
                  <div><div style={{ fontSize: 24, fontWeight: 700, color: "#00D68F", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>312</div><div style={{ fontSize: 9, color: "rgba(0,214,143,0.55)", marginTop: 2 }}>Advancing</div></div>
                  <div><div style={{ fontSize: 24, fontWeight: 700, color: "#FF6B6B", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>138</div><div style={{ fontSize: 9, color: "rgba(255,107,107,0.55)", marginTop: 2 }}>Declining</div></div>
                </div>
                <div style={{ height: 6, borderRadius: 4, background: "rgba(130,141,235,0.1)", overflow: "hidden" }}>
                  <div style={{ width: "69%", height: "100%", background: "linear-gradient(90deg, #00D68F, #34D399)", borderRadius: 4 }} />
                </div>
              </div>
            </motion.div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 3 — AI INTELLIGENCE
───────────────────────────────────────────────────────── */
function AIIntelligenceSection() {
  const signals = [
    { icon: TrendingUp,  label: "Why It Moved",      desc: "Identifies the exact catalysts behind today's market shift — sector rotation, macro triggers, or FII activity." },
    { icon: Activity,    label: "Market Sentiment",   desc: "Quantified sentiment signal from news, options data, and price action — not guesswork." },
    { icon: BarChart3,   label: "Sector Strength",    desc: "Real-time comparative view of which sectors are leading and which are lagging the broader index." },
    { icon: Newspaper,   label: "News Impact",        desc: "AI parses market-moving headlines and rates their short-term impact on individual stocks and indices." },
  ];

  return (
    <section id="ai-intelligence" style={{ padding: "120px 24px", position: "relative", background: "rgba(6,7,16,0.6)" }}>
      {/* Subtle background accent */}
      <div aria-hidden="true" style={{
        position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
        width: 700, height: 700, borderRadius: "50%", pointerEvents: "none",
        background: "radial-gradient(circle, rgba(99,102,241,0.07) 0%, transparent 70%)",
      }} />

      <div style={{ maxWidth: 1200, margin: "0 auto", position: "relative" }}>
        <div className="lp-ai-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 80, alignItems: "center" }}>
          {/* Left: copy */}
          <motion.div
            initial={{ opacity: 0, x: -40 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true, margin: "-80px" }}
            transition={{ duration: 0.7, ease: "easeOut" }}
          >
            <div className="lp-eyebrow" style={{ marginBottom: 16 }}>AI Intelligence</div>
            <h2 className="lp-h2" style={{ marginBottom: 24 }}>
              Not just what<br />happened.{" "}
              <span style={{ background: "linear-gradient(135deg, #818CF8, #C4B5FD)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
                Understand why.
              </span>
            </h2>
            <p className="lp-body" style={{ marginBottom: 40, maxWidth: 440 }}>
              Most platforms show you price. StockAssist shows you reasoning. Our AI synthesises market data, sector dynamics, news sentiment, and breadth signals into one coherent explanation — updated continuously.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              {signals.map((s, i) => (
                <motion.div key={s.label}
                  initial={{ opacity: 0, x: -20 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.5, delay: i * 0.1, ease: "easeOut" }}
                  style={{ display: "flex", gap: 16 }}
                >
                  <div style={{
                    width: 40, height: 40, borderRadius: 12, flexShrink: 0,
                    background: "rgba(129,140,248,0.1)", border: "1px solid rgba(129,140,248,0.2)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                  }}>
                    <s.icon size={18} color="#818CF8" />
                  </div>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#F0F2F5", marginBottom: 3 }}>{s.label}</div>
                    <div style={{ fontSize: 12, lineHeight: 1.6, color: "rgba(240,242,245,0.5)" }}>{s.desc}</div>
                  </div>
                </motion.div>
              ))}
            </div>
          </motion.div>

          {/* Right: AI panel mockup */}
          <motion.div
            initial={{ opacity: 0, x: 40 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true, margin: "-80px" }}
            transition={{ duration: 0.7, ease: "easeOut" }}
          >
            <div className="lp-card-glow" style={{ padding: 28, position: "relative", overflow: "hidden" }}>
              {/* Scan line effect */}
              <div className="lp-scan-line" />

              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
                <div style={{
                  width: 36, height: 36, borderRadius: 12,
                  background: "linear-gradient(135deg, rgba(99,102,241,0.3), rgba(129,140,248,0.15))",
                  border: "1px solid rgba(129,140,248,0.3)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  <Brain size={18} color="#818CF8" />
                </div>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#F0F2F5" }}>Market Analysis</div>
                  <div className="lp-badge-live">Active</div>
                </div>
                <div style={{ marginLeft: "auto", textAlign: "right" }}>
                  <div style={{ fontSize: 22, fontWeight: 700, color: "#00D68F", fontFamily: "'JetBrains Mono', monospace" }}>+0.82%</div>
                  <div style={{ fontSize: 10, color: "rgba(240,242,245,0.4)" }}>NIFTY 50</div>
                </div>
              </div>

              {/* AI Summary block */}
              <div style={{ padding: "16px 18px", borderRadius: 12, background: "rgba(129,140,248,0.06)", border: "1px solid rgba(129,140,248,0.12)", marginBottom: 20 }}>
                <div className="lp-eyebrow" style={{ marginBottom: 10 }}>AI Summary</div>
                <p style={{ fontSize: 13, lineHeight: 1.7, color: "rgba(240,242,245,0.75)", fontStyle: "italic" }}>
                  "Momentum remains positive as financial and technology stocks strengthen. Increased breadth suggests the move is broader than a small group of constituents. Options data supports continued upward bias into expiry week."
                </p>
              </div>

              {/* Signal grid */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {[
                  { label: "Why It Moved", val: "FII inflows + IT earnings",   col: "#818CF8" },
                  { label: "Sentiment",    val: "Bullish — score 68/100",       col: "#00D68F" },
                  { label: "Sector",       val: "IT +1.8% · Finance +1.2%",    col: "#60A5FA" },
                  { label: "News Impact",  val: "Positive — 3 key drivers",    col: "#00D68F" },
                ].map((s) => (
                  <div key={s.label} style={{
                    padding: "12px 14px", borderRadius: 12,
                    background: "rgba(12,14,26,0.8)", border: "1px solid rgba(130,141,235,0.1)",
                  }}>
                    <div style={{ fontSize: 9, fontWeight: 700, color: "rgba(240,242,245,0.35)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 5 }}>{s.label}</div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: s.col, lineHeight: 1.4 }}>{s.val}</div>
                  </div>
                ))}
              </div>

              {/* Disclaimer */}
              <div style={{ marginTop: 16, fontSize: 9, color: "rgba(240,242,245,0.25)", lineHeight: 1.5, textAlign: "center" }}>
                Illustrative UI preview · Not live financial advice
              </div>
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 4 — SIGNAL TO DECISION (flow)
───────────────────────────────────────────────────────── */
function SignalToDecisionSection() {
  const steps = [
    { icon: Database,  label: "Market Data",         desc: "Live quotes, OHLCV, index data, market breadth, options chain" },
    { icon: Newspaper, label: "News & Events",        desc: "Market headlines, earnings, macro events, RBI decisions" },
    { icon: Brain,     label: "AI Analysis",          desc: "Dual synthesis of patterns, momentum, sector dynamics, sentiment" },
    { icon: Layers,    label: "Portfolio Context",    desc: "Your holdings, risk exposure, watchlist, trade history" },
    { icon: Zap,       label: "Actionable Insight",   desc: "Clear, reasoned market intelligence with full explanation" },
  ];

  return (
    <section id="how-it-works" style={{ padding: "120px 24px", position: "relative" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ textAlign: "center", marginBottom: 72 }}>
          <div className="lp-eyebrow" style={{ marginBottom: 16 }}>How It Works</div>
          <h2 className="lp-h2" style={{ marginBottom: 20 }}>
            Everything you need.<br />
            <span style={{ background: "linear-gradient(135deg, #818CF8, #60A5FA)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              One intelligent workspace.
            </span>
          </h2>
          <p className="lp-body" style={{ maxWidth: 520, margin: "0 auto" }}>
            StockAssist connects every signal that matters — market data, news, AI reasoning and your portfolio — into one coherent, continuously updated intelligence layer.
          </p>
        </div>

        {/* Flow steps */}
        <div style={{ display: "flex", flexDirection: "column", gap: 0, position: "relative", maxWidth: 680, margin: "0 auto" }}>
          {steps.map((step, i) => (
            <div key={step.label} style={{ position: "relative" }}>
              <motion.div
                initial={{ opacity: 0, x: -30 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true, margin: "-40px" }}
                transition={{ duration: 0.55, delay: i * 0.12, ease: "easeOut" }}
                style={{ display: "flex", alignItems: "flex-start", gap: 20, paddingBottom: i < steps.length - 1 ? 0 : 0 }}
              >
                {/* Left: icon + connector */}
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flexShrink: 0, width: 52 }}>
                  <div style={{
                    width: 52, height: 52, borderRadius: 16, flexShrink: 0,
                    background: i === steps.length - 1
                      ? "linear-gradient(135deg, rgba(99,102,241,0.4), rgba(129,140,248,0.2))"
                      : "rgba(18,21,40,0.8)",
                    border: i === steps.length - 1
                      ? "1px solid rgba(129,140,248,0.5)"
                      : "1px solid rgba(130,141,235,0.15)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    boxShadow: i === steps.length - 1 ? "0 0 24px rgba(129,140,248,0.25)" : "none",
                    position: "relative", zIndex: 1,
                  }}>
                    <step.icon size={22} color={i === steps.length - 1 ? "#818CF8" : "rgba(240,242,245,0.5)"} />
                  </div>
                  {/* Connector line */}
                  {i < steps.length - 1 && (
                    <div style={{ width: 1, flex: 1, minHeight: 40, background: "linear-gradient(180deg, rgba(129,140,248,0.25), rgba(129,140,248,0.08))", margin: "6px 0" }} />
                  )}
                </div>

                {/* Right: content */}
                <div className="lp-card" style={{
                  flex: 1, padding: "18px 22px", marginBottom: i < steps.length - 1 ? 12 : 0,
                  ...(i === steps.length - 1 ? {
                    border: "1px solid rgba(129,140,248,0.3)",
                    background: "rgba(24,28,52,0.85)",
                    boxShadow: "0 0 32px rgba(129,140,248,0.1)",
                  } : {}),
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: i === steps.length - 1 ? "#818CF8" : "#F0F2F5", marginBottom: 4 }}>{step.label}</div>
                      <div style={{ fontSize: 12, lineHeight: 1.6, color: "rgba(240,242,245,0.45)" }}>{step.desc}</div>
                    </div>
                    <ChevronRight size={16} color="rgba(240,242,245,0.2)" style={{ flexShrink: 0, transform: "rotate(90deg)" }} />
                  </div>
                </div>
              </motion.div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 5 — PRODUCT SHOWCASE
───────────────────────────────────────────────────────── */
function ProductShowcaseSection() {
  const features = [
    { icon: LineChart,  title: "Market Overview",    desc: "Live indices, sector heatmap, breadth, VIX and global context in one dashboard." },
    { icon: Brain,      title: "AI Analysis",        desc: "Continuous market narrative — what's happening and the intelligence behind it." },
    { icon: ScanSearch, title: "Stock Scanner",      desc: "Real-time scan across 50+ NSE stocks with AI-powered pattern recognition." },
    { icon: TrendingUp, title: "Portfolio Monitor",  desc: "Track P&L, exposure, and AI-generated rebalancing suggestions." },
    { icon: Bot,        title: "AI Chat",            desc: "Ask any market question and get data-backed, reasoned answers instantly." },
    { icon: BarChart3,  title: "Trade Journal",      desc: "Log, review and learn from every trade with AI coaching built in." },
  ];

  return (
    <section id="product" style={{ padding: "120px 24px", position: "relative", background: "rgba(6,7,16,0.55)" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <div style={{ textAlign: "center", marginBottom: 72 }}>
          <div className="lp-eyebrow" style={{ marginBottom: 16 }}>Product</div>
          <h2 className="lp-h2" style={{ marginBottom: 20 }}>
            Built for every layer<br />
            <span style={{ background: "linear-gradient(135deg, #818CF8, #A78BFA)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              of market intelligence.
            </span>
          </h2>
          <p className="lp-body" style={{ maxWidth: 500, margin: "0 auto" }}>
            From live index tracking to AI-driven trade reasoning — every tool you need to understand the market and act with clarity.
          </p>
        </div>

        <div className="lp-grid-3" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20 }}>
          {features.map((f, i) => (
            <motion.div key={f.title}
              initial={{ opacity: 0, y: 32 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ duration: 0.55, delay: i * 0.09, ease: "easeOut" }}
              className="lp-card"
              style={{ padding: 28, cursor: "default" }}
            >
              <div style={{
                width: 48, height: 48, borderRadius: 14, marginBottom: 18,
                background: "rgba(129,140,248,0.1)", border: "1px solid rgba(129,140,248,0.18)",
                display: "flex", alignItems: "center", justifyContent: "center",
                transition: "all 0.25s ease",
              }}>
                <f.icon size={22} color="#818CF8" />
              </div>
              <h3 style={{ fontSize: 15, fontWeight: 700, color: "#F0F2F5", marginBottom: 8, fontFamily: "'Outfit', sans-serif" }}>{f.title}</h3>
              <p style={{ fontSize: 13, lineHeight: 1.65, color: "rgba(240,242,245,0.5)" }}>{f.desc}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 6 — TRUST / SECURITY
───────────────────────────────────────────────────────── */
function TrustSection() {
  const pillars = [
    { icon: Lock,     title: "Secure Authentication",     desc: "Google OAuth 2.0 and protected sessions. Credentials never stored in plain text." },
    { icon: Shield,   title: "Encrypted Secrets",         desc: "API keys and broker credentials are encrypted at rest, isolated per user." },
    { icon: Eye,      title: "Session Protection",        desc: "Automatic session expiry, HTTPS enforcement, and CSRF protection throughout." },
    { icon: Cpu,      title: "Controlled Workflows",      desc: "All trading actions require explicit user confirmation — AI advises, you decide." },
    { icon: Database, title: "Reliable Data",             desc: "Market data flows through a monitored gateway with automated integrity checks and provider failover." },
    { icon: Globe,    title: "Transparent Architecture",  desc: "Open about our data sources, AI providers, and how every recommendation is built." },
  ];

  return (
    <section id="security" style={{ padding: "120px 24px", position: "relative" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ textAlign: "center", marginBottom: 72 }}>
          <div className="lp-eyebrow" style={{ marginBottom: 16 }}>Security & Trust</div>
          <h2 className="lp-h2" style={{ marginBottom: 20 }}>
            Built for serious<br />
            <span style={{ background: "linear-gradient(135deg, #818CF8, #60A5FA)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              market decisions.
            </span>
          </h2>
          <p className="lp-body" style={{ maxWidth: 480, margin: "0 auto" }}>
            When real capital is involved, security and transparency aren't optional. StockAssist is built with both as non-negotiable foundations.
          </p>
        </div>

        <div className="lp-grid-3" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20 }}>
          {pillars.map((p, i) => (
            <motion.div key={p.title}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ duration: 0.5, delay: i * 0.08, ease: "easeOut" }}
              className="lp-card"
              style={{ padding: "24px 26px" }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                <div style={{
                  width: 36, height: 36, borderRadius: 10, flexShrink: 0,
                  background: "rgba(129,140,248,0.08)", border: "1px solid rgba(129,140,248,0.14)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  <p.icon size={16} color="#818CF8" />
                </div>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#F0F2F5" }}>{p.title}</div>
              </div>
              <p style={{ fontSize: 12, lineHeight: 1.65, color: "rgba(240,242,245,0.45)" }}>{p.desc}</p>
            </motion.div>
          ))}
        </div>

        {/* Broker trust bar */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          style={{ marginTop: 60, textAlign: "center" }}
        >
          <p style={{ fontSize: 11, color: "rgba(240,242,245,0.3)", fontWeight: 600, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 24 }}>
            Broker integrations available
          </p>
          <div style={{ display: "flex", justifyContent: "center", gap: 12, flexWrap: "wrap" }}>
            {["ZERODHA", "Upstox", "AngelOne", "Groww", "FYERS"].map((b) => (
              <span key={b} style={{
                padding: "8px 20px", borderRadius: 99,
                background: "rgba(18,21,40,0.8)", border: "1px solid rgba(130,141,235,0.12)",
                fontSize: 12, fontWeight: 700, color: "rgba(240,242,245,0.45)",
                letterSpacing: "0.05em",
              }}>{b}</span>
            ))}
          </div>
        </motion.div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────
   PRICING SECTION
───────────────────────────────────────────────────────── */
const PRICING_TIERS = [
  {
    name: "Starter", tagline: "For learning the ropes", price: "Free", period: "", cta: "Get Started", testId: "pricing-cta-starter",
    features: ["Paper trading with ₹10L virtual capital", "Daily AI stock picks (limited)", "Live market overview & watchlist", "Basic single-AI trade analysis"],
  },
  {
    name: "Pro", tagline: "For active swing & intraday traders", price: "₹499", period: "/month", popular: true, cta: "Start Free Trial", testId: "pricing-cta-pro",
    features: ["Everything in Starter", "Unlimited dual-AI (Claude + Gemini) analysis", "Real-time scanning of 50+ NSE stocks", "Backtesting engine & portfolio monitoring", "Email & WhatsApp alerts"],
  },
  {
    name: "Elite", tagline: "For serious, full-time traders", price: "₹1,499", period: "/month", cta: "Go Elite", testId: "pricing-cta-elite",
    features: ["Everything in Pro", "Live broker execution (Zerodha & Upstox)", "AI trade coaching after every closed trade", "Advanced backtesting & priority AI compute", "Priority support"],
  },
];

function PricingSection() {
  return (
    <section id="pricing" style={{ padding: "120px 24px", position: "relative", background: "rgba(6,7,16,0.4)" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ textAlign: "center", marginBottom: 72 }}>
          <div className="lp-eyebrow" style={{ marginBottom: 16 }}>Pricing</div>
          <h2 className="lp-h2" style={{ marginBottom: 20 }}>Simple, transparent plans</h2>
          <p className="lp-body" style={{ maxWidth: 460, margin: "0 auto" }}>
            Start free. Upgrade when you're ready for the full power of dual-AI analysis and live execution.
          </p>
        </div>

        <div className="lp-grid-3" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20, alignItems: "stretch" }}>
          {PRICING_TIERS.map((tier, i) => (
            <motion.div key={tier.name}
              initial={{ opacity: 0, y: 40, scale: 0.97 }}
              whileInView={{ opacity: 1, y: 0, scale: tier.popular ? 1.03 : 1 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ duration: 0.55, delay: i * 0.1, ease: "easeOut" }}
              className={tier.popular ? "lp-card-glow" : "lp-card"}
              style={{
                padding: 32, display: "flex", flexDirection: "column", position: "relative",
              }}
            >
              {tier.popular && (
                <div style={{
                  position: "absolute", top: -12, left: "50%", transform: "translateX(-50%)",
                  background: "linear-gradient(135deg, #6366F1, #818CF8)", color: "#fff",
                  fontSize: 10, fontWeight: 700, padding: "4px 14px", borderRadius: 99,
                  whiteSpace: "nowrap", letterSpacing: "0.05em",
                }}>Most Popular</div>
              )}
              <div style={{ fontSize: 18, fontWeight: 700, color: "#F0F2F5", marginBottom: 4, fontFamily: "'Outfit', sans-serif" }}>{tier.name}</div>
              <div style={{ fontSize: 12, color: "rgba(240,242,245,0.4)", marginBottom: 24 }}>{tier.tagline}</div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 28 }}>
                <span style={{ fontSize: 36, fontWeight: 700, color: "#F0F2F5", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>{tier.price}</span>
                {tier.period && <span style={{ fontSize: 13, color: "rgba(240,242,245,0.4)" }}>{tier.period}</span>}
              </div>
              <ul style={{ listStyle: "none", padding: 0, margin: "0 0 32px 0", display: "flex", flexDirection: "column", gap: 12, flex: 1 }}>
                {tier.features.map((f) => (
                  <li key={f} style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                    <Check size={14} color="#00D68F" style={{ flexShrink: 0, marginTop: 1 }} />
                    <span style={{ fontSize: 13, color: "rgba(240,242,245,0.6)", lineHeight: 1.5 }}>{f}</span>
                  </li>
                ))}
              </ul>
              <Link to="/register" data-testid={tier.testId}
                className={tier.popular ? "lp-btn-primary" : "lp-btn-ghost"}
                style={{ justifyContent: "center", textDecoration: "none" }}
              >
                {tier.cta}
              </Link>
            </motion.div>
          ))}
        </div>

        <p style={{ textAlign: "center", fontSize: 11, color: "rgba(240,242,245,0.25)", marginTop: 36, lineHeight: 1.6 }}>
          * Pricing shown is illustrative and subject to change ahead of launch. Final plans, limits and features will be confirmed at general availability.
        </p>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 7 — FINAL CTA
───────────────────────────────────────────────────────── */
function FinalCTASection() {
  return (
    <section style={{ padding: "140px 24px", position: "relative", overflow: "hidden" }}>
      {/* Radial glow */}
      <div aria-hidden="true" style={{
        position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
        width: 800, height: 800, borderRadius: "50%", pointerEvents: "none",
        background: "radial-gradient(circle, rgba(99,102,241,0.15) 0%, transparent 65%)",
      }} />
      {/* Orbital ring */}
      <div aria-hidden="true" style={{
        position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
        width: 600, height: 600, borderRadius: "50%", pointerEvents: "none",
        border: "1px solid rgba(130,141,235,0.07)",
      }} />
      <div aria-hidden="true" style={{
        position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
        width: 900, height: 900, borderRadius: "50%", pointerEvents: "none",
        border: "1px solid rgba(130,141,235,0.04)",
      }} />

      <div style={{ maxWidth: 800, margin: "0 auto", textAlign: "center", position: "relative" }}>
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.7, ease: "easeOut" }}
        >
          <div className="lp-eyebrow" style={{ marginBottom: 24 }}>Get Started</div>
          <h2 style={{
            fontSize: "clamp(2.5rem, 5vw, 4rem)", fontWeight: 800, lineHeight: 1.08,
            letterSpacing: "-0.03em", color: "#F0F2F5", marginBottom: 24,
            fontFamily: "'Outfit', sans-serif",
          }}>
            Stop watching<br />
            <span style={{ background: "linear-gradient(135deg, #818CF8 0%, #C4B5FD 50%, #60A5FA 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              the market.
            </span><br />
            Start understanding it.
          </h2>
          <p className="lp-body" style={{ fontSize: 16, maxWidth: 500, margin: "0 auto 40px", lineHeight: 1.7 }}>
            Bring market intelligence, AI analysis and portfolio awareness into one workspace — and finally understand what the market is telling you.
          </p>
          <div style={{ display: "flex", gap: 14, justifyContent: "center", flexWrap: "wrap" }}>
            <Link to="/register" data-testid="cta-final-btn" className="lp-btn-primary" style={{ padding: "16px 36px", fontSize: 16 }}>
              Get Started <ArrowRight size={18} />
            </Link>
            <Link to="/login" className="lp-btn-ghost" style={{ padding: "16px 28px", fontSize: 15 }}>
              Sign In
            </Link>
          </div>

          {/* Disclaimer */}
          <div id="risk-disclosure" style={{
            marginTop: 56, padding: "20px 24px", borderRadius: 14, textAlign: "left",
            background: "rgba(12,14,26,0.6)", border: "1px solid rgba(130,141,235,0.1)",
            display: "flex", alignItems: "flex-start", gap: 14,
          }}>
            <Shield size={18} color="rgba(240,242,245,0.25)" style={{ flexShrink: 0, marginTop: 2 }} />
            <div>
              <div style={{ fontSize: 9, fontWeight: 800, color: "rgba(240,242,245,0.3)", textTransform: "uppercase", letterSpacing: "0.15em", marginBottom: 6 }}>Important Disclaimer</div>
              <p style={{ fontSize: 12, lineHeight: 1.65, color: "rgba(240,242,245,0.4)" }}>
                StockAssist is an AI-powered market analysis tool. We do not promise or guarantee profits. Stock market investments are subject to market risks. Our AI helps you make informed decisions through data-driven analysis, but all trading decisions are ultimately yours. Past performance does not indicate future results. Please read all scheme-related documents carefully before investing.
              </p>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────
   NAV LINKS
───────────────────────────────────────────────────────── */
const NAV_LINKS = [
  { label: "Product",        href: "#product" },
  { label: "AI Intelligence", href: "#ai-intelligence" },
  { label: "Features",       href: "#features" },
  { label: "How It Works",   href: "#how-it-works" },
  { label: "Pricing",        href: "#pricing" },
];

/* ─────────────────────────────────────────────────────────
   MAIN LANDING COMPONENT
───────────────────────────────────────────────────────── */
export default function Landing() {
  const [liveData, setLiveData] = useState(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [navScrolled, setNavScrolled] = useState(false);
  const rootRef = useRef(null);
  const heroRef = useRef(null);

  // Inject landing-specific CSS once
  useEffect(() => {
    const id = "lp-styles";
    if (!document.getElementById(id)) {
      const el = document.createElement("style");
      el.id = id;
      el.textContent = LANDING_STYLES;
      document.head.appendChild(el);
    }
    return () => { /* keep alive for HMR; server removes on next cold load */ };
  }, []);

  // Fetch live market data
  useEffect(() => {
    api.get("/market/overview").then((r) => setLiveData(r.data)).catch(() => {});
  }, []);

  // Nav scroll state
  useEffect(() => {
    const onScroll = () => setNavScrolled(window.scrollY > 40);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // GSAP parallax for hero (scoped, no leaks on SPA nav).
  // The reduced-motion @media block in LANDING_STYLES only silences CSS
  // keyframes; GSAP drives inline transforms, so it needs its own opt-out.
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const ctx = gsap.context(() => {
      if (!heroRef.current) return;
      gsap.to(".lp-hero-device", {
        scrollTrigger: { trigger: heroRef.current, start: "top top", end: "bottom top", scrub: 1.2 },
        y: 60, ease: "none",
      });
      gsap.to(".lp-hero-bg", {
        scrollTrigger: { trigger: heroRef.current, start: "top top", end: "bottom top", scrub: 2 },
        y: 80, ease: "none",
      });
    }, rootRef);
    return () => ctx.revert();
  }, []);

  /*
   * In-page anchor navigation. `scroll-margin-top` in LANDING_STYLES keeps the
   * target clear of the fixed nav; this handler only adds the smooth easing and
   * falls back to an instant jump when the visitor prefers reduced motion.
   */
  const handleNavClick = (e, href) => {
    const target = document.querySelector(href);
    if (!target) return;
    e.preventDefault();
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    setMobileMenuOpen(false);
  };

  const heroVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.15, delayChildren: 0.1 } },
  };
  const heroItem = { hidden: { opacity: 0, y: 30 }, visible: { opacity: 1, y: 0, transition: { duration: 0.7, ease: [0.16, 1, 0.3, 1] } } };
  const deviceVariant = { hidden: { opacity: 0, y: 60, scale: 0.94 }, visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 1.1, ease: [0.16, 1, 0.3, 1], delay: 0.4 } } };

  return (
    /* reducedMotion="user" makes every Framer animation below honour the OS
       setting — the CSS @media block cannot reach JS-driven transforms. */
    <MotionConfig reducedMotion="user">
    <div ref={rootRef} className="lp-root" data-testid="landing-page" style={{ minHeight: "100vh" }}>

      {/* ── NAVIGATION ── */}
      <nav className="lp-nav" style={{ ...(navScrolled ? { background: "rgba(8,9,18,0.92)" } : {}) }}>
        <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 24px", height: 64, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          {/* Logo */}
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <APLogo size={28} />
            <span style={{ fontSize: 15, fontWeight: 700, color: "#F0F2F5", fontFamily: "'Outfit', sans-serif", letterSpacing: "-0.02em" }}>StockAssist</span>
          </div>

          {/* Desktop nav links */}
          <div style={{ display: "flex", alignItems: "center", gap: 2 }} className="lp-nav-links">
            {NAV_LINKS.map((l) => (
              <a key={l.label} href={l.href} onClick={(e) => handleNavClick(e, l.href)} style={{
                padding: "7px 14px", borderRadius: 9, fontSize: 13, fontWeight: 500,
                color: "rgba(240,242,245,0.6)", textDecoration: "none",
                transition: "color 0.2s, background 0.2s",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = "#F0F2F5"; e.currentTarget.style.background = "rgba(130,141,235,0.08)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = "rgba(240,242,245,0.6)"; e.currentTarget.style.background = "transparent"; }}
              >{l.label}</a>
            ))}
          </div>

          {/* Auth actions */}
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Link to="/login" data-testid="landing-login-btn" style={{
              padding: "8px 16px", borderRadius: 10, fontSize: 13, fontWeight: 600,
              color: "rgba(240,242,245,0.65)", textDecoration: "none",
              transition: "color 0.2s",
            }}>Login</Link>
            <Link to="/register" data-testid="landing-signup-btn" className="lp-btn-primary" style={{ padding: "9px 20px", fontSize: 13 }}>
              Get Started
            </Link>
            {/* Mobile hamburger */}
            <button
              onClick={() => setMobileMenuOpen((v) => !v)}
              aria-label="Toggle menu"
              style={{
                display: "none", padding: "8px", background: "none", border: "none",
                cursor: "pointer", color: "rgba(240,242,245,0.7)",
              }}
              className="lp-mobile-menu-btn"
            >
              {mobileMenuOpen ? "✕" : "☰"}
            </button>
          </div>
        </div>

        {/* Mobile menu */}
        {mobileMenuOpen && (
          <div style={{
            background: "rgba(8,9,18,0.97)", borderTop: "1px solid rgba(130,141,235,0.1)",
            padding: "16px 24px 24px",
          }}>
            {NAV_LINKS.map((l) => (
              <a key={l.label} href={l.href} onClick={(e) => handleNavClick(e, l.href)} style={{
                display: "block", padding: "12px 0",
                fontSize: 15, fontWeight: 500, color: "rgba(240,242,245,0.7)", textDecoration: "none",
                borderBottom: "1px solid rgba(130,141,235,0.07)",
              }}>{l.label}</a>
            ))}
            <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
              <Link to="/login" style={{ flex: 1, textAlign: "center" }} className="lp-btn-ghost">Login</Link>
              <Link to="/register" style={{ flex: 1, textAlign: "center" }} className="lp-btn-primary">Get Started</Link>
            </div>
          </div>
        )}
      </nav>

      {/* ── HERO ── */}
      <section ref={heroRef} style={{ position: "relative", minHeight: "100vh", display: "flex", flexDirection: "column", justifyContent: "center", paddingTop: 80, overflow: "hidden" }}>
        <div className="lp-hero-bg" style={{ position: "absolute", inset: 0, zIndex: 0 }}>
          <AmbientBackground />
        </div>

        <div style={{ maxWidth: 1200, margin: "0 auto", padding: "60px 24px 40px", position: "relative", zIndex: 1, width: "100%" }}>
          <motion.div variants={heroVariants} initial="hidden" animate="visible">
            {/* Badge */}
            <motion.div variants={heroItem} style={{ display: "flex", justifyContent: "center", marginBottom: 32 }}>
              <div className="lp-badge">
                <Zap size={12} />
                AI-Powered Market Intelligence
              </div>
            </motion.div>

            {/* Headline */}
            <motion.div variants={heroItem} style={{ textAlign: "center", marginBottom: 28 }}>
              <h1 style={{
                fontSize: "clamp(3rem, 6.5vw, 5.5rem)", fontWeight: 800, lineHeight: 1.05,
                letterSpacing: "-0.035em", color: "#F0F2F5", fontFamily: "'Outfit', sans-serif",
                margin: "0 auto", maxWidth: 800,
              }}>
                Understand the market.{" "}
                <span style={{
                  background: "linear-gradient(135deg, #818CF8 0%, #A78BFA 50%, #60A5FA 100%)",
                  WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
                }}>
                  Before you trade.
                </span>
              </h1>
            </motion.div>

            {/* Subhead */}
            <motion.div variants={heroItem} style={{ textAlign: "center", marginBottom: 40 }}>
              <p style={{
                fontSize: "clamp(15px, 1.8vw, 18px)", lineHeight: 1.7,
                color: "rgba(240,242,245,0.58)", maxWidth: 640, margin: "0 auto",
              }}>
                StockAssist brings market data, news, AI analysis and portfolio intelligence into one place — helping you understand what is happening, why it matters, and what deserves your attention.
              </p>
            </motion.div>

            {/* CTAs */}
            <motion.div variants={heroItem} style={{ display: "flex", gap: 14, justifyContent: "center", flexWrap: "wrap", marginBottom: 72 }}>
              <Link to="/register" data-testid="hero-cta-btn" className="lp-btn-primary" style={{ padding: "15px 32px", fontSize: 15 }}>
                Explore StockAssist <ArrowRight size={17} />
              </Link>
              <a href="#how-it-works" onClick={(e) => handleNavClick(e, "#how-it-works")} className="lp-btn-ghost" style={{ padding: "15px 28px", fontSize: 15 }}>
                See How It Works
              </a>
            </motion.div>

            {/* Device mockup */}
            <motion.div variants={deviceVariant} className="lp-hero-device" data-testid="hero-image-slot"
              style={{ position: "relative", maxWidth: 860, margin: "0 auto" }}>
              <DeviceMockup liveData={liveData} />
            </motion.div>
          </motion.div>
        </div>
      </section>

      {/* ── TICKER ── */}
      <TickerBar />

      {/* ── SECTION DIVIDER ── */}
      <div className="lp-section-divider" />

      {/* ── MARKET INTELLIGENCE ── */}
      <MarketIntelligenceSection liveData={liveData} />

      <div className="lp-section-divider" />

      {/* ── AI INTELLIGENCE ── */}
      <AIIntelligenceSection />

      <div className="lp-section-divider" />

      {/* ── SIGNAL TO DECISION ── */}
      <SignalToDecisionSection />

      <div className="lp-section-divider" />

      {/* ── PRODUCT SHOWCASE ── */}
      <ProductShowcaseSection />

      <div className="lp-section-divider" />

      {/* ── TRUST / SECURITY ── */}
      <TrustSection />

      <div className="lp-section-divider" />

      {/* ── PRICING ── */}
      <PricingSection />

      <div className="lp-section-divider" />

      {/* ── FINAL CTA ── */}
      <FinalCTASection />

      {/* ── FOOTER ── */}
      <footer style={{
        borderTop: "1px solid rgba(130,141,235,0.08)",
        background: "rgba(6,7,14,0.6)",
        padding: "48px 24px 32px",
      }}>
        <div style={{ maxWidth: 1200, margin: "0 auto" }}>
          <div className="lp-footer-grid" style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr", gap: 40, marginBottom: 48 }}>
            {/* Brand col */}
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
                <APLogo size={24} />
                <span style={{ fontSize: 15, fontWeight: 700, color: "#F0F2F5", fontFamily: "'Outfit', sans-serif" }}>StockAssist</span>
              </div>
              <p style={{ fontSize: 12, lineHeight: 1.7, color: "rgba(240,242,245,0.35)", maxWidth: 280 }}>
                AI-powered market intelligence for the Indian stock market. Understand the market, monitor your portfolio, and make better decisions.
              </p>
            </div>
            {/* Product col */}
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: "rgba(240,242,245,0.4)", textTransform: "uppercase", letterSpacing: "0.12em", marginBottom: 14 }}>Product</div>
              {["Product", "Features", "Pricing", "Security"].map((l) => (
                <a key={l} href={`#${l.toLowerCase()}`} onClick={(e) => handleNavClick(e, `#${l.toLowerCase()}`)} style={{ display: "block", fontSize: 13, color: "rgba(240,242,245,0.45)", textDecoration: "none", marginBottom: 10, transition: "color 0.2s" }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = "#F0F2F5"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = "rgba(240,242,245,0.45)"; }}
                >{l}</a>
              ))}
            </div>
            {/* Company col */}
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: "rgba(240,242,245,0.4)", textTransform: "uppercase", letterSpacing: "0.12em", marginBottom: 14 }}>Company</div>
              {["About", "Blog", "Careers"].map((l) => (
                <span key={l} title="Coming soon" style={{ display: "block", fontSize: 13, color: "rgba(240,242,245,0.28)", marginBottom: 10, cursor: "default" }}>{l}</span>
              ))}
            </div>
            {/* Legal col */}
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: "rgba(240,242,245,0.4)", textTransform: "uppercase", letterSpacing: "0.12em", marginBottom: 14 }}>Legal</div>
              {["Privacy Policy", "Terms of Service"].map((l) => (
                <span key={l} title="Coming soon" style={{ display: "block", fontSize: 13, color: "rgba(240,242,245,0.28)", marginBottom: 10, cursor: "default" }}>{l}</span>
              ))}
              <a href="#risk-disclosure" onClick={(e) => handleNavClick(e, "#risk-disclosure")}
                style={{ display: "block", fontSize: 13, color: "rgba(240,242,245,0.45)", textDecoration: "none", marginBottom: 10 }}
                onMouseEnter={(e) => { e.currentTarget.style.color = "#F0F2F5"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = "rgba(240,242,245,0.45)"; }}
              >Risk Disclosure</a>
            </div>
          </div>

          {/* Bottom bar */}
          <div style={{ borderTop: "1px solid rgba(130,141,235,0.07)", paddingTop: 24, display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 16 }}>
            <p style={{ fontSize: 11, color: "rgba(240,242,245,0.25)" }}>
              © 2026 StockAssist. All rights reserved. AI-Powered Indian Stock Market Analysis.
            </p>
            <p style={{ fontSize: 10, color: "rgba(240,242,245,0.2)", maxWidth: 600, lineHeight: 1.6 }}>
              <strong style={{ color: "rgba(240,242,245,0.3)" }}>Risk Disclosure:</strong> Stock market investments are subject to market risks. StockAssist provides AI-powered analysis and does not guarantee profits or returns. Past performance is not indicative of future results. Please consult a SEBI-registered financial advisor before making investment decisions.
            </p>
          </div>
        </div>
      </footer>

      {/* ── RESPONSIVE STYLES (injected inline to stay within Landing.jsx only) ── */}
      <style>{`
        /*
         * Landing-only responsive rules, keyed on explicit .lp-* classes rather
         * than structural paths like "#features > div > div:last-child", which
         * silently stop matching the moment a wrapper element is introduced.
         *
         * The grid-column resets matter: the Market Intelligence children carry
         * inline "span 5 / 4 / 3", and a span wider than the track count makes
         * the grid generate implicit tracks. Narrowing the template alone left
         * a measured "473px 0px 0px 0px 0px" — four dead columns, with the main
         * card squeezed to 473px inside a 553px container.
         */
        @media (max-width: 1024px) {
          .lp-nav-links { display: none !important; }
          .lp-mobile-menu-btn { display: flex !important; }
          /* Tablet: 6 tracks — index card full width, AI + side cards half each. */
          .lp-mi-grid { grid-template-columns: repeat(6, 1fr) !important; }
          .lp-mi-main { grid-column: span 6 !important; }
          .lp-mi-ai   { grid-column: span 3 !important; }
          .lp-mi-side { grid-column: span 3 !important; }
          .lp-ai-grid { gap: 48px !important; }
        }
        @media (max-width: 900px) {
          .lp-grid-3 { grid-template-columns: repeat(2, 1fr) !important; }
          .lp-footer-grid { grid-template-columns: 1fr 1fr !important; }
        }
        @media (max-width: 768px) {
          .lp-mi-grid { grid-template-columns: 1fr !important; }
          .lp-mi-main, .lp-mi-ai, .lp-mi-side { grid-column: auto !important; }
          .lp-ai-grid { grid-template-columns: 1fr !important; gap: 40px !important; }
          .lp-footer-grid { grid-template-columns: 1fr !important; }
        }
        @media (max-width: 560px) {
          .lp-grid-3 { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
    </MotionConfig>
  );
}
