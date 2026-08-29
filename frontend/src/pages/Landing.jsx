import { Link } from "react-router-dom";
import { useState, useEffect, useRef } from "react";
import { ArrowRight, ArrowUpRight, Plus, Minus, TrendingUp, TrendingDown, ArrowDown, Sun, Moon } from "lucide-react";
import { motion } from "framer-motion";
import { useTheme } from "../context/ThemeContext";
import api from "../services/api";

/* ─────────────────────────────────────────────
   INLINE STYLES — landing-only tokens
   Avoids shipping unused CSS; scoped to <LandingPage>
   Supports dynamic light and dark theme modes.
   ───────────────────────────────────────────── */
const LANDING_CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@300;400;500;600&display=swap');

  .sa-landing * { box-sizing: border-box; }

  /* Theme variables initialization */
  .sa-landing {
    --sa-bg: #F8F9FC;
    --sa-surface: #FFFFFF;
    --sa-surface-elevated: #F1F3F9;
    --sa-border: rgba(0, 0, 0, 0.055);
    --sa-text-primary: #1A1D29;
    --sa-text-secondary: #515568ff;
    --sa-text-muted: #9CA3B8;
    --sa-accent: #6366F1;
    --sa-accent-soft: rgba(99, 102, 241, 0.06);
    --sa-gain: #00C48C;
    --sa-gain-bg: rgba(0, 196, 140, 0.06);
    --sa-loss: #FF6B6B;
    --sa-loss-bg: rgba(255, 107, 107, 0.06);
    --sa-card-bg-market: #FFFFFF;
    --sa-card-bg-chart: #FFFFFF;
    --sa-card-bg-insight: rgba(99, 102, 241, 0.03);
    --sa-card-bg-metric: rgba(0, 196, 140, 0.03);
    --sa-flow-bg: #FFFFFF;
    --sa-faq-bg: #FFFFFF;
    --sa-break-text: #E2E8F0;
    --sa-break-highlight: #0F172A;
    --sa-nav-bg: rgba(248, 249, 252, 0.85);
  }

  [data-theme="dark"] .sa-landing {
    --sa-bg: #09090e;
    --sa-surface: #0d1117;
    --sa-surface-elevated: #161a22;
    --sa-border: rgba(255, 255, 255, 0.07);
    --sa-text-primary: #F0F2F5;
    --sa-text-secondary: #888a96;
    --sa-text-muted: #4a4f5e;
    --sa-accent: #818CF8;
    --sa-accent-soft: rgba(129, 140, 248, 0.12);
    --sa-gain: #4ade80;
    --sa-gain-bg: rgba(74, 222, 128, 0.15);
    --sa-loss: #f87171;
    --sa-loss-bg: rgba(248, 113, 113, 0.15);
    --sa-card-bg-market: #111318;
    --sa-card-bg-chart: #0d1117;
    --sa-card-bg-insight: #161a22;
    --sa-card-bg-metric: #1a2218;
    --sa-flow-bg: #0f1117;
    --sa-faq-bg: #0f1117;
    --sa-break-text: #2a2d38;
    --sa-break-highlight: #3d4150;
    --sa-nav-bg: rgba(9, 10, 14, 0.85);
  }

  /* NAV */
  .sa-nav {
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    padding: 0 2.5rem;
    height: 60px;
    display: flex; align-items: center; justify-content: space-between;
    background: var(--sa-nav-bg);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--sa-border);
    transition: background-color 0.3s, border-color 0.3s;
  }
  .sa-nav-logo {
    font-family: 'Inter', sans-serif;
    font-weight: 600; font-size: 15px; letter-spacing: -0.01em;
    color: var(--sa-text-primary);
  }
  .sa-nav-links { display: flex; align-items: center; gap: 2rem; }
  .sa-nav-link {
    font-family: 'Inter', sans-serif;
    font-size: 14px; font-weight: 400; color: var(--sa-text-secondary);
    text-decoration: none;
    transition: color 1s, transform 0.2s ease-in-out;
  }
  .sa-nav-link:hover { color: var(--sa-text-primary); transform: translateX(2px); }
  .sa-nav-actions { display: flex; align-items: center; gap: 12px; }
  .sa-btn-ghost {
    font-family: 'Inter', sans-serif;
    font-size: 13px; font-weight: 500; color: var(--sa-text-secondary);
    background: none; border: none; cursor: pointer;
    text-decoration: none; padding: 6px 12px;
    transition: color 0.2s;
  }
  .sa-btn-ghost:hover { color: var(--sa-text-primary); }
  .sa-btn-primary {
    font-family: 'Inter', sans-serif;
    font-size: 13px; font-weight: 500; color: var(--sa-bg);
    background: var(--sa-text-primary); border: none; cursor: pointer;
    text-decoration: none; padding: 8px 18px;
    border-radius: 100px; transition: background 0.2s, color 0.3s, transform 0.15s;
  }
  .sa-btn-primary:hover { opacity: 0.9; transform: translateY(-1px); }

  .sa-theme-toggle {
    background: none; border: none; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    width: 32px; height: 32px; border-radius: 50%;
    color: var(--sa-text-secondary); transition: all 0.2s;
  }
  .sa-theme-toggle:hover {
    color: var(--sa-text-primary);
    background: rgba(46, 45, 45, 0.08);
  }

  /* HERO */
  .sa-hero {
    padding: 130px 2.5rem 0;
    max-width: 1200px; margin: 0 auto;
  }
  .sa-hero-headline {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(3rem, 6vw, 5.5rem);
    font-weight: 550; font-style: normal;
    line-height: 1.08; letter-spacing: -0.025em;
    color: var(--sa-text-secondary); text-align: center;
    margin-bottom: 1.5rem;
  }
  .sa-hero-headline em {
    font-style: italic; color: var(--sa-text-primary);
  }
  .sa-hero-sub {
    font-family: 'Inter', sans-serif;
    font-size: 16px; font-weight: 400; color: var(--sa-text-secondary);
    text-align: center; max-width: 460px; margin: 0 auto 2.5rem;
    line-height: 1.65;
  }
  .sa-hero-ctas {
    display: flex; align-items: center; justify-content: center;
    gap: 12px; margin-bottom: 4rem;
  }
  .sa-cta-primary {
    display: inline-flex; align-items: center; gap: 8px;
    font-family: 'Inter', sans-serif;
    font-size: 14px; font-weight: 500; color: var(--sa-bg);
    background: var(--sa-text-primary); text-decoration: none;
    padding: 11px 24px; border-radius: 100px;
    transition: all 0.2s;
  }
  .sa-cta-primary:hover { opacity: 0.9; transform: translateY(-1px); }
  .sa-cta-secondary {
    display: inline-flex; align-items: center; gap: 8px;
    font-family: 'Inter', sans-serif;
    font-size: 14px; font-weight: 400; color: var(--sa-text-secondary);
    text-decoration: none; padding: 11px 20px;
    border-radius: 100px; border: 1px solid var(--sa-border);
    transition: all 0.2s;
  }
  .sa-cta-secondary:hover { color: var(--sa-text-primary); border-color: var(--sa-text-secondary); }

  /* MOSAIC GRID */
  .sa-mosaic {
    display: grid;
    grid-template-columns: 220px 1fr 220px;
    grid-template-rows: 220px 220px;
    gap: 10px;
    padding-bottom: 0;
  }
  .sa-tile {
    border-radius: 18px;
    overflow: hidden;
    position: relative;
    transition: background-color 0.3s, border-color 0.3s;
  }
  .sa-tile-market {
    grid-column: 1; grid-row: 1 / 3;
    background: var(--sa-card-bg-market);
    border: 1px solid var(--sa-border);
    padding: 24px;
    display: flex; flex-direction: column; justify-content: space-between;
  }
  .sa-tile-chart {
    grid-column: 2; grid-row: 1 / 3;
    background: var(--sa-card-bg-chart);
    border: 1px solid var(--sa-border);
    padding: 28px 28px 0;
    overflow: hidden;
  }
  .sa-tile-insight {
    grid-column: 3; grid-row: 1;
    background: var(--sa-card-bg-insight);
    border: 1px solid var(--sa-border);
    padding: 22px;
  }
  .sa-tile-metric {
    grid-column: 3; grid-row: 2;
    background: var(--sa-card-bg-metric);
    border: 1px solid var(--sa-border);
    padding: 22px;
    display: flex; flex-direction: column; justify-content: space-between;
  }

  /* LABEL / EYEBROW */
  .sa-eyebrow {
    font-family: 'Inter', sans-serif;
    font-size: 10px; font-weight: 600;
    letter-spacing: 0.15em; text-transform: uppercase;
    color: var(--sa-text-muted);
  }
  .sa-eyebrow-green { color: var(--sa-gain); }
  .sa-eyebrow-dim { color: var(--sa-text-muted); opacity: 0.8; }

  /* INDEX ROWS */
  .sa-idx { display: flex; flex-direction: column; gap: 14px; }
  .sa-idx-row {
    display: flex; align-items: center; justify-content: space-between;
  }
  .sa-idx-name {
    font-family: 'Inter', sans-serif;
    font-size: 11px; font-weight: 500; color: var(--sa-text-secondary);
    letter-spacing: 0.03em;
  }
  .sa-idx-val {
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px; font-weight: 600; color: var(--sa-text-primary);
  }
  .sa-idx-pct-up {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; font-weight: 500; color: var(--sa-gain);
  }
  .sa-idx-pct-dn {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; font-weight: 500; color: var(--sa-loss);
  }

  /* CHART TILE */
  .sa-chart-header { margin-bottom: 18px; }
  .sa-chart-val {
    font-family: 'Instrument Serif', serif;
    font-size: 2.8rem; letter-spacing: -0.03em; color: var(--sa-text-primary);
    line-height: 1;
  }
  .sa-chart-sub {
    font-family: 'Inter', sans-serif;
    font-size: 12px; color: var(--sa-gain); font-weight: 500; margin-top: 4px;
  }

  /* INSIGHT TILE */
  .sa-insight-tag {
    display: inline-block;
    font-family: 'Inter', sans-serif;
    font-size: 10px; font-weight: 600;
    letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--sa-accent); background: var(--sa-accent-soft);
    padding: 3px 10px; border-radius: 100px;
    margin-bottom: 14px;
  }
  .sa-insight-text {
    font-family: 'Inter', sans-serif;
    font-size: 13px; font-weight: 400; color: var(--sa-text-secondary);
    line-height: 1.6;
  }
  .sa-insight-bold { color: var(--sa-text-primary); font-weight: 500; }

  /* METRIC TILE */
  .sa-metric-big {
    font-family: 'Instrument Serif', serif;
    font-size: 2rem; letter-spacing: -0.02em; color: var(--sa-gain);
    line-height: 1;
  }
  .sa-metric-label {
    font-family: 'Inter', sans-serif;
    font-size: 11px; color: var(--sa-text-secondary); font-weight: 500;
  }
  .sa-demo-tag {
    display: inline-block;
    font-family: 'Inter', sans-serif;
    font-size: 9px; font-weight: 600; letter-spacing: 0.1em;
    text-transform: uppercase; color: var(--sa-text-secondary);
    background: var(--sa-border);
    padding: 2px 8px; border-radius: 100px;
  }

  /* ─── SECTION WRAPPERS ─── */
  .sa-section { padding: 120px 2.5rem; }
  .sa-section-narrow { padding: 80px 2.5rem; }
  .sa-container { max-width: 1200px; margin: 0 auto; }
  .sa-container-sm { max-width: 860px; margin: 0 auto; }

  /* ─── MARKET INTELLIGENCE ─── */
  .sa-intel-grid {
    display: grid;
    grid-template-columns: 1fr 1.1fr;
    gap: 80px;
    align-items: start;
  }
  .sa-intel-headline {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(2.4rem, 4.5vw, 3.8rem);
    font-weight: 400; line-height: 1.1;
    letter-spacing: -0.025em; color: var(--sa-text-primary);
    margin-bottom: 1.5rem;
  }
  .sa-intel-body {
    font-family: 'Inter', sans-serif;
    font-size: 15px; font-weight: 400;
    color: var(--sa-text-secondary); line-height: 1.7;
    max-width: 380px;
  }
  .sa-intel-panel {
    background: var(--sa-surface);
    border: 1px solid var(--sa-border);
    border-radius: 20px; overflow: hidden;
  }
  .sa-intel-panel-header {
    padding: 20px 24px;
    border-bottom: 1px solid var(--sa-border);
    display: flex; align-items: center; justify-content: space-between;
  }
  .sa-intel-panel-body { padding: 20px 24px; }
  .sa-sector-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 0; border-bottom: 1px solid var(--sa-border);
  }
  .sa-sector-row:last-child { border-bottom: none; }
  .sa-sector-name {
    font-family: 'Inter', sans-serif;
    font-size: 12px; font-weight: 500; color: var(--sa-text-secondary);
  }
  .sa-sector-bar-track {
    flex: 1; height: 2px; margin: 0 16px;
    background: var(--sa-border); border-radius: 2px;
    position: relative; overflow: hidden;
  }
  .sa-sector-bar-fill {
    position: absolute; left: 0; top: 0; height: 100%;
    border-radius: 2px;
  }
  .sa-sector-pct {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; font-weight: 500; min-width: 40px; text-align: right;
  }

  /* ─── EDITORIAL BREAK ─── */
  .sa-break {
    padding: 160px 2.5rem;
    text-align: center;
  }
  .sa-break-big {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(3.5rem, 8vw, 7rem);
    font-weight: 400; font-style: italic;
    line-height: 1.0; letter-spacing: -0.03em;
    color: var(--sa-break-text); margin-bottom: 2rem;
  }
  .sa-break-big em { font-style: normal; color: var(--sa-break-highlight); }
  .sa-break-sub {
    font-family: 'Inter', sans-serif;
    font-size: 15px; color: var(--sa-break-highlight); line-height: 1.7;
    max-width: 400px; margin: 0 auto;
  }

  /* ─── AI SECTION ─── */
  .sa-ai-grid {
    display: grid;
    grid-template-columns: 360px 1fr;
    gap: 80px; align-items: start;
  }
  .sa-ai-headline {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(2rem, 3.5vw, 3rem);
    font-weight: 400; line-height: 1.15;
    letter-spacing: -0.02em; color: var(--sa-text-primary);
    margin-bottom: 1rem;
  }
  .sa-ai-body {
    font-family: 'Inter', sans-serif;
    font-size: 14px; color: var(--sa-text-secondary); line-height: 1.7;
  }
  .sa-analysis-panel {
    background: var(--sa-surface);
    border: 1px solid var(--sa-border);
    border-radius: 20px; overflow: hidden;
  }
  .sa-analysis-header {
    padding: 18px 22px;
    border-bottom: 1px solid var(--sa-border);
    display: flex; align-items: center; gap: 10px;
  }
  .sa-analysis-body { padding: 22px; }
  .sa-analysis-section { margin-bottom: 20px; }
  .sa-analysis-label {
    font-family: 'Inter', sans-serif;
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--sa-text-muted); margin-bottom: 8px;
  }
  .sa-analysis-text {
    font-family: 'Inter', sans-serif;
    font-size: 13px; color: var(--sa-text-secondary); line-height: 1.6;
  }
  .sa-analysis-text strong { color: var(--sa-text-primary); font-weight: 500; }
  .sa-analysis-divider {
    height: 1px; background: var(--sa-border); margin: 16px 0;
  }
  .sa-pulse {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--sa-gain);
    animation: sa-pulse-anim 2s ease-in-out infinite;
  }
  @keyframes sa-pulse-anim {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(0.8); }
  }
  .sa-live-dot {
    display: inline-flex; align-items: center; gap: 5px;
    font-family: 'Inter', sans-serif;
    font-size: 10px; font-weight: 600; color: var(--sa-gain);
    letter-spacing: 0.08em;
  }

  /* ─── PORTFOLIO ─── */
  .sa-port-grid {
    display: grid;
    grid-template-columns: 1fr 360px;
    gap: 60px; align-items: start;
  }
  .sa-port-headline {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(2rem, 3.5vw, 3rem);
    font-weight: 400; line-height: 1.15;
    letter-spacing: -0.02em; color: var(--sa-text-primary);
    margin-bottom: 1rem;
  }
  .sa-port-value {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(2.8rem, 5vw, 4.5rem);
    line-height: 1; letter-spacing: -0.03em;
    color: var(--sa-text-primary); margin-bottom: 4px;
  }
  .sa-port-pnl {
    font-family: 'Inter', sans-serif;
    font-size: 13px; color: var(--sa-gain); font-weight: 500;
    margin-bottom: 32px;
  }
  .sa-alloc-row {
    display: flex; align-items: center;
    gap: 14px; margin-bottom: 16px;
  }
  .sa-alloc-name {
    font-family: 'Inter', sans-serif;
    font-size: 12px; font-weight: 500; color: var(--sa-text-secondary);
    width: 90px; flex-shrink: 0;
  }
  .sa-alloc-track {
    flex: 1; height: 3px;
    background: var(--sa-border); border-radius: 2px;
    position: relative; overflow: hidden;
  }
  .sa-alloc-fill {
    position: absolute; left: 0; top: 0; height: 100%;
    border-radius: 2px; background: var(--sa-text-secondary);
    opacity: 0.4;
  }
  .sa-alloc-pct {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; color: var(--sa-text-muted); width: 30px; text-align: right;
  }
  .sa-port-panel {
    background: var(--sa-surface);
    border: 1px solid var(--sa-border);
    border-radius: 20px; padding: 28px;
  }

  /* ─── FLOW SECTION ─── */
  .sa-flow { text-align: center; }
  .sa-flow-headline {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(2.2rem, 4vw, 3.5rem);
    font-weight: 400; line-height: 1.1;
    letter-spacing: -0.025em; color: var(--sa-text-primary);
    margin-bottom: 4rem;
  }
  .sa-flow-steps { display: flex; flex-direction: column; align-items: center; gap: 0; }
  .sa-flow-step {
    display: flex; align-items: center; gap: 24px;
    padding: 22px 32px;
    background: var(--sa-flow-bg);
    border: 1px solid var(--sa-border);
    border-radius: 14px;
    width: 100%; max-width: 480px;
    margin-bottom: 0;
    position: relative;
    text-align: left;
  }
  .sa-flow-connector {
    width: 1px; height: 28px;
    background: var(--sa-border);
    margin: 0 auto;
  }
  .sa-flow-num {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; font-weight: 600; color: var(--sa-text-muted);
    letter-spacing: 0.05em; flex-shrink: 0;
  }
  .sa-flow-title {
    font-family: 'Inter', sans-serif;
    font-size: 14px; font-weight: 500; color: var(--sa-text-primary);
  }
  .sa-flow-desc {
    font-family: 'Inter', sans-serif;
    font-size: 12px; color: var(--sa-text-secondary); line-height: 1.5;
    margin-top: 2px;
  }
  .sa-flow-soon {
    font-family: 'Inter', sans-serif;
    font-size: 10px; font-weight: 600;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--sa-text-muted); margin-left: auto;
  }

  /* ─── FAQ ─── */
  .sa-faq-headline {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(2.2rem, 4vw, 3.5rem);
    font-weight: 400; line-height: 1.1;
    letter-spacing: -0.025em; color: var(--sa-text-primary);
    text-align: center; margin-bottom: 0.75rem;
  }
  .sa-faq-sub {
    font-family: 'Inter', sans-serif;
    font-size: 14px; color: var(--sa-text-secondary);
    text-align: center; margin-bottom: 3rem;
  }
  .sa-faq-item {
    background: var(--sa-faq-bg);
    border: 1px solid var(--sa-border);
    border-radius: 14px; margin-bottom: 8px;
    overflow: hidden;
    transition: border-color 0.2s;
  }
  .sa-faq-item:hover { border-color: var(--sa-text-muted); }
  .sa-faq-trigger {
    display: flex; align-items: center; justify-content: space-between;
    width: 100%; padding: 20px 24px;
    background: none; border: none; cursor: pointer;
    text-align: left;
  }
  .sa-faq-question {
    font-family: 'Inter', sans-serif;
    font-size: 14px; font-weight: 500; color: var(--sa-text-primary);
  }
  .sa-faq-icon {
    color: var(--sa-text-muted); transition: transform 0.2s;
    flex-shrink: 0; margin-left: 16px;
  }
  .sa-faq-icon-open { color: var(--sa-text-secondary); }
  .sa-faq-answer {
    font-family: 'Inter', sans-serif;
    font-size: 13px; color: var(--sa-text-secondary); line-height: 1.7;
    padding: 0 24px 20px;
  }

  /* ─── FINAL CTA ─── */
  .sa-final-cta { text-align: center; }
  .sa-final-headline {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(3rem, 7vw, 6.5rem);
    font-weight: 400; line-height: 1.0;
    letter-spacing: -0.035em; color: var(--sa-break-text);
    margin-bottom: 1.5rem;
  }
  .sa-final-headline em { font-style: italic; color: var(--sa-break-highlight); }
  .sa-final-sub {
    font-family: 'Inter', sans-serif;
    font-size: 15px; color: var(--sa-break-highlight); line-height: 1.6;
    max-width: 420px; margin: 0 auto 2.5rem;
  }

  /* ─── FOOTER ─── */
  .sa-footer {
    padding: 60px 2.5rem 32px;
    border-top: 1px solid var(--sa-border);
  }
  .sa-footer-top {
    display: grid;
    grid-template-columns: 1fr auto auto auto;
    gap: 60px; margin-bottom: 60px;
    align-items: start;
  }
  .sa-footer-brand {
    font-family: 'Instrument Serif', serif;
    font-size: 1.8rem; letter-spacing: -0.02em;
    color: var(--sa-break-text); line-height: 1;
    margin-bottom: 0.75rem;
  }
  .sa-footer-tagline {
    font-family: 'Inter', sans-serif;
    font-size: 13px; color: var(--sa-break-text); line-height: 1.6;
    max-width: 240px;
  }
  .sa-footer-col-title {
    font-family: 'Inter', sans-serif;
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--sa-text-muted); margin-bottom: 16px;
  }
  .sa-footer-link {
    display: block;
    font-family: 'Inter', sans-serif;
    font-size: 13px; color: var(--sa-break-text);
    text-decoration: none; margin-bottom: 10px;
    transition: color 0.2s;
  }
  .sa-footer-link:hover { color: var(--sa-text-secondary); }
  .sa-footer-bottom {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 24px; flex-wrap: wrap;
    padding-top: 32px;
    border-top: 1px solid var(--sa-border);
  }
  .sa-footer-copy {
    font-family: 'Inter', sans-serif;
    font-size: 11px; color: var(--sa-break-text);
  }
  .sa-footer-disclaimer {
    font-family: 'Inter', sans-serif;
    font-size: 11px; color: var(--sa-break-text);
    max-width: 640px; line-height: 1.6;
    opacity: 0.85;
  }

  /* ─── RESPONSIVE ─── */
  @media (max-width: 900px) {
    .sa-mosaic {
      grid-template-columns: 1fr 1fr;
      grid-template-rows: auto auto auto;
    }
    .sa-tile-market { grid-column: 1; grid-row: 1; }
    .sa-tile-chart { grid-column: 1 / 3; grid-row: 2; min-height: 220px; }
    .sa-tile-insight { grid-column: 2; grid-row: 1; }
    .sa-tile-metric { grid-column: 1 / 3; grid-row: 3; min-height: 120px; }
    .sa-intel-grid { grid-template-columns: 1fr; gap: 48px; }
    .sa-ai-grid { grid-template-columns: 1fr; gap: 48px; }
    .sa-port-grid { grid-template-columns: 1fr; gap: 48px; }
    .sa-footer-top { grid-template-columns: 1fr 1fr; gap: 40px; }
    .sa-nav-links { display: none; }
  }
  @media (max-width: 600px) {
    .sa-hero { padding: 100px 1.25rem 0; }
    .sa-section { padding: 80px 1.25rem; }
    .sa-section-narrow { padding: 60px 1.25rem; }
    .sa-break { padding: 100px 1.25rem; }
    .sa-nav { padding: 0 1.25rem; }
    .sa-footer { padding: 48px 1.25rem 24px; }
    .sa-mosaic { grid-template-columns: 1fr; grid-template-rows: auto; }
    .sa-tile-market { grid-column: 1; grid-row: 1; min-height: 180px; }
    .sa-tile-chart { grid-column: 1; grid-row: 2; min-height: 200px; }
    .sa-tile-insight { grid-column: 1; grid-row: 3; }
    .sa-tile-metric { grid-column: 1; grid-row: 4; }
    .sa-footer-top { grid-template-columns: 1fr; }
  }
`;

/* ─────────────────────────────────────────────
   NIFTY CHART SVG — Inline product visualization
   ───────────────────────────────────────────── */
function NiftyChartSVG({ liveData }) {
  const val = liveData?.nifty?.value;
  const pct = liveData?.nifty?.change_pct;
  const isUp = (pct ?? 0.64) >= 0;

  // Synthetic-looking NIFTY path for illustration
  const path = "M0,90 C30,88 50,82 80,78 S130,72 160,68 S200,60 230,55 S270,52 300,48 S340,44 370,42 S400,38 440,35";
  const areaPath = `${path} L440,130 L0,130 Z`;

  return (
    <div className="sa-tile sa-tile-chart">
      <div className="sa-chart-header">
        <div className="sa-eyebrow sa-eyebrow-dim" style={{ marginBottom: 10 }}>NIFTY 50</div>
        <div className="sa-chart-val">
          {val ? `₹${Number(val).toLocaleString("en-IN", { maximumFractionDigits: 0 })}` : "₹25,428"}
        </div>
        <div className="sa-chart-sub">
          {isUp ? "+" : ""}{pct != null ? `${pct.toFixed(2)}%` : "+0.64%"} today {isUp ? "↑" : "↓"}
        </div>
      </div>
      <svg
        viewBox="0 0 440 130"
        style={{ width: "100%", height: "auto", display: "block", marginTop: 8 }}
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id="nifty-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isUp ? "var(--sa-gain)" : "var(--sa-loss)"} stopOpacity="0.18" />
            <stop offset="100%" stopColor={isUp ? "var(--sa-gain)" : "var(--sa-loss)"} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill="url(#nifty-grad)" />
        <path d={path} fill="none" stroke={isUp ? "var(--sa-gain)" : "var(--sa-loss)"} strokeWidth="1.5" strokeLinecap="round" />
        {/* Small dot at end */}
        <circle cx="440" cy="35" r="3" fill={isUp ? "var(--sa-gain)" : "var(--sa-loss)"} />
      </svg>
    </div>
  );
}

/* ─────────────────────────────────────────────
   PORTFOLIO MINI CHART
   ───────────────────────────────────────────── */
function PortfolioMiniChart() {
  const path = "M0,70 C20,68 40,60 70,52 S120,44 150,40 S190,36 220,30 S260,28 300,25";
  const area = `${path} L300,90 L0,90 Z`;
  return (
    <svg viewBox="0 0 300 90" style={{ width: "100%", display: "block" }} preserveAspectRatio="none">
      <defs>
        <linearGradient id="port-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--sa-gain)" stopOpacity="0.2" />
          <stop offset="100%" stopColor="var(--sa-gain)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#port-grad)" />
      <path d={path} fill="none" stroke="var(--sa-gain)" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

/* ─────────────────────────────────────────────
   FAQ ITEM
   ───────────────────────────────────────────── */
const FAQS = [
  {
    q: "What is StockAssist?",
    a: "StockAssist is a financial intelligence platform that brings market data, portfolio context and AI-assisted analysis together in one place. It helps investors and traders understand what is happening in the market before deciding what to do.",
  },
  {
    q: "How does StockAssist analyze market information?",
    a: "StockAssist uses dual AI engines — Claude (Anthropic) and Gemini (Google) — that independently analyze market setups and synthesize a reasoned view. Every analysis includes the underlying data, confidence level, and the reasoning behind it. There are no black boxes.",
  },
  {
    q: "Can I connect my broker?",
    a: "Yes. StockAssist currently supports Zerodha and Upstox for live broker connectivity. Groww integration is in progress and coming soon. Broker connections are used to view your portfolio and facilitate order execution — StockAssist does not trade automatically.",
  },
  {
    q: "Does StockAssist execute trades automatically?",
    a: "No. StockAssist does not execute trades automatically or without your explicit action. You review the analysis and context, then decide whether to act. Any order execution requires your deliberate confirmation through your connected broker.",
  },
  {
    q: "Is StockAssist investment advice?",
    a: "No. StockAssist provides market data, AI-assisted analysis and portfolio context for informational purposes only. It is not registered as an investment advisor and does not provide personalized investment advice. All investment decisions remain yours. Markets involve risk and past analysis does not guarantee future results.",
  },
];

function FAQItem({ q, a }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sa-faq-item">
      <button
        className="sa-faq-trigger"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
      >
        <span className="sa-faq-question">{q}</span>
        <span className={`sa-faq-icon ${open ? "sa-faq-icon-open" : ""}`}>
          {open ? <Minus size={15} /> : <Plus size={15} />}
        </span>
      </button>
      {open && <div className="sa-faq-answer">{a}</div>}
    </div>
  );
}

/* ─────────────────────────────────────────────
   ANIMATION VARIANTS
   ───────────────────────────────────────────── */
const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.15,
      delayChildren: 0.1,
    },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 30 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { type: "spring", stiffness: 100, damping: 15 },
  },
};

const sectionVariants = {
  hidden: { opacity: 0, y: 40 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.7, ease: [0.25, 1, 0.5, 1] },
  },
};

/* ─────────────────────────────────────────────
   MAIN COMPONENT
   ───────────────────────────────────────────── */
export default function Landing() {
  const { theme, toggleTheme } = useTheme();
  const [liveData, setLiveData] = useState(null);

  useEffect(() => {
    api.get("/market/overview").then(r => setLiveData(r.data)).catch(() => {});
  }, []);

  /* helper: format index values */
  const fmt = (v) => v ? Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : null;
  const fmtPct = (p, fallback) => {
    if (p != null) return { val: `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`, up: p >= 0 };
    return { val: fallback, up: true };
  };

  const niftyPct = fmtPct(liveData?.nifty?.change_pct, "+0.64%");
  const sensexPct = fmtPct(liveData?.sensex?.change_pct, "+0.51%");
  const bankPct = fmtPct(liveData?.bank_nifty?.change_pct, "+0.82%");

  return (
    <div
      className="sa-landing"
      style={{ background: "var(--sa-bg)", minHeight: "100vh", transition: "background-color 0.3s, color 0.3s" }}
      data-testid="landing-page"
    >
      <style>{LANDING_CSS}</style>

      {/* ══════════════════════════════════════
          NAVIGATION
          ══════════════════════════════════════ */}
      <motion.nav 
        className="sa-nav"
        initial={{ y: -60, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
      >
        <span className="sa-nav-logo">StockAssist</span>

        <div className="sa-nav-links">
          <a href="#markets" className="sa-nav-link">Markets</a>
          <a href="#intelligence" className="sa-nav-link">Intelligence</a>
          <a href="#portfolio" className="sa-nav-link">Portfolio</a>
          <a href="#how-it-works" className="sa-nav-link">How it works</a>
        </div>

        <div className="sa-nav-actions">
          <button
            onClick={toggleTheme}
            className="sa-theme-toggle"
            aria-label="Toggle theme"
          >
            {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
          </button>
          <Link
            to="/login"
            className="sa-btn-ghost"
            data-testid="landing-login-btn"
          >
            Log in
          </Link>
          <Link
            to="/register"
            className="sa-btn-primary"
            data-testid="landing-signup-btn"
          >
            Get started
          </Link>
        </div>
      </motion.nav>

      {/* ══════════════════════════════════════
          HERO
          ══════════════════════════════════════ */}
      <section className="sa-hero" id="markets">
        <motion.div
          initial="hidden"
          animate="visible"
          variants={containerVariants}
        >
          {/* Headline */}
          <motion.h1 className="sa-hero-headline" variants={itemVariants}>
            Understand the market.<br />
            <em>Then make your move.</em>
          </motion.h1>

          {/* Sub */}
          <motion.p className="sa-hero-sub" variants={itemVariants}>
            StockAssist brings market data, portfolio context and AI-assisted analysis together
            so you can understand what is happening before you decide what to do.
          </motion.p>

          {/* CTAs */}
          <motion.div className="sa-hero-ctas" variants={itemVariants}>
            <Link
              to="/register"
              className="sa-cta-primary"
              data-testid="hero-cta-btn"
            >
              Explore StockAssist <ArrowRight size={14} />
            </Link>
            <a href="#how-it-works" className="sa-cta-secondary">
              See how it works
            </a>
          </motion.div>
        </motion.div>

        {/* ── MOSAIC GRID ── */}
        <motion.div 
          className="sa-mosaic"
          initial="hidden"
          animate="visible"
          variants={containerVariants}
        >
          {/* LEFT TILE — Market Snapshot */}
          <motion.div className="sa-tile sa-tile-market" variants={itemVariants}>
            <div>
              <div className="sa-eyebrow sa-eyebrow-dim" style={{ marginBottom: 20 }}>
                MARKET SNAPSHOT
              </div>
              <div className="sa-idx">
                {[
                  {
                    label: "NIFTY 50",
                    val: fmt(liveData?.nifty?.value) ?? "25,428",
                    pct: niftyPct,
                  },
                  {
                    label: "SENSEX",
                    val: fmt(liveData?.sensex?.value) ?? "83,514",
                    pct: sensexPct,
                  },
                  {
                    label: "BANK NIFTY",
                    val: fmt(liveData?.bank_nifty?.value) ?? "54,212",
                    pct: bankPct,
                  },
                ].map(idx => (
                  <div key={idx.label}>
                    <div className="sa-idx-name">{idx.label}</div>
                    <div className="sa-idx-row" style={{ marginTop: 4 }}>
                      <span className="sa-idx-val">{idx.val}</span>
                      <span className={idx.pct.up ? "sa-idx-pct-up" : "sa-idx-pct-dn"}>
                        {idx.pct.val}
                      </span>
                    </div>
                    <div
                      style={{
                        height: 1,
                        background: "var(--sa-border)",
                        marginTop: 12,
                        marginBottom: 2,
                      }}
                    />
                  </div>
                ))}
              </div>
            </div>
            <div className="sa-eyebrow" style={{ fontSize: 9 }}>
              {liveData ? "LIVE DATA" : "ILLUSTRATIVE VALUES"}
            </div>
          </motion.div>

          {/* CENTER TILE — NIFTY Chart */}
          <motion.div variants={itemVariants} style={{ display: "contents" }}>
            <NiftyChartSVG liveData={liveData} />
          </motion.div>

          {/* TOP RIGHT — AI Insight */}
          <motion.div className="sa-tile sa-tile-insight" id="intelligence" variants={itemVariants}>
            <div className="sa-insight-tag">AI INSIGHT</div>
            <p className="sa-insight-text">
              <span className="sa-insight-bold">Banking and IT</span> are leading today's move.{" "}
              Improving breadth suggests the advance is broader than a handful of large-cap names.
            </p>
            <div
              className="sa-live-dot"
              style={{ marginTop: 20 }}
            >
              <span className="sa-pulse" />
              Live analysis
            </div>
          </motion.div>

          {/* BOTTOM RIGHT — Metric */}
          <motion.div className="sa-tile sa-tile-metric" variants={itemVariants}>
            <div>
              <div className="sa-eyebrow sa-eyebrow-dim" style={{ marginBottom: 12 }}>
                MARKET BREADTH
              </div>
              <div className="sa-metric-big">68%</div>
              <div className="sa-metric-label">Advances · NSE</div>
            </div>
            <span className="sa-demo-tag">Illustrative</span>
          </motion.div>

        </motion.div>
      </section>

      {/* ══════════════════════════════════════
          MARKET INTELLIGENCE SECTION
          ══════════════════════════════════════ */}
      <motion.section 
        className="sa-section" 
        style={{ paddingTop: 140 }}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={sectionVariants}
      >
        <div className="sa-container">
          <div className="sa-intel-grid">

            {/* LEFT — Editorial text */}
            <div>
              <div
                className="sa-eyebrow"
                style={{ marginBottom: 24 }}
              >
                MARKET INTELLIGENCE
              </div>
              <h2 className="sa-intel-headline">
                Know what moved.<br />
                <span style={{ color: "var(--sa-text-muted)" }}>Understand why.</span>
              </h2>
              <p className="sa-intel-body">
                Markets move for a reason. StockAssist brings price movement, market context and
                relevant data together so you can see beyond the number.
              </p>
              <a
                href="#intelligence"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  marginTop: 32,
                  fontFamily: "Inter, sans-serif",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "var(--sa-text-primary)",
                  textDecoration: "none",
                }}
              >
                Open intelligence <ArrowUpRight size={13} />
              </a>
            </div>

            {/* RIGHT — Product panel */}
            <div
              className="sa-intel-panel"
              data-testid="hero-image-slot"
            >
              <div className="sa-intel-panel-header">
                <span
                  className="sa-eyebrow"
                >
                  SECTOR MOVEMENT
                </span>
                <span
                  style={{
                    fontFamily: "Inter, sans-serif",
                    fontSize: 10,
                    color: "var(--sa-text-muted)",
                  }}
                >
                  Today · NSE
                </span>
              </div>
              <div className="sa-intel-panel-body">
                {[
                  { name: "Banking", pct: "+1.24%", w: 72, up: true },
                  { name: "IT", pct: "+0.91%", w: 58, up: true },
                  { name: "FMCG", pct: "+0.33%", w: 30, up: true },
                  { name: "Auto", pct: "−0.18%", w: 22, up: false },
                  { name: "Pharma", pct: "−0.44%", w: 38, up: false },
                  { name: "Metals", pct: "+0.67%", w: 46, up: true },
                ].map(s => (
                  <div className="sa-sector-row" key={s.name}>
                    <span className="sa-sector-name">{s.name}</span>
                    <div className="sa-sector-bar-track">
                      <div
                        className="sa-sector-bar-fill"
                        style={{
                          width: `${s.w}%`,
                          background: s.up
                            ? "var(--sa-gain)"
                            : "var(--sa-loss)",
                          opacity: 0.7
                        }}
                      />
                    </div>
                    <span
                      className="sa-sector-pct"
                      style={{ color: s.up ? "var(--sa-gain)" : "var(--sa-loss)" }}
                    >
                      {s.pct}
                    </span>
                  </div>
                ))}
                <div
                  style={{
                    marginTop: 20,
                    padding: "14px 16px",
                    background: "var(--sa-surface-elevated)",
                    borderRadius: 10,
                  }}
                >
                  <div
                    className="sa-eyebrow"
                    style={{ marginBottom: 8 }}
                  >
                    MARKET CONTEXT
                  </div>
                  <p
                    style={{
                      fontFamily: "Inter, sans-serif",
                      fontSize: 12,
                      color: "var(--sa-text-secondary)",
                      lineHeight: 1.6,
                    }}
                  >
                    FII inflows turning positive this week.
                    RBI stance stable. Earnings season underway.
                  </p>
                </div>
                <div
                  style={{
                    marginTop: 8,
                    fontFamily: "Inter, sans-serif",
                    fontSize: 10,
                    color: "var(--sa-text-muted)",
                    textAlign: "right",
                  }}
                >
                  Illustrative values · For demonstration
                </div>
              </div>
            </div>

          </div>
        </div>
      </motion.section>

      {/* ══════════════════════════════════════
          EDITORIAL BREAK
          ══════════════════════════════════════ */}
      <motion.section 
        className="sa-break"
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={sectionVariants}
      >
        <h2 className="sa-break-big">
          Less noise.<br />
          <em>More context.</em>
        </h2>
        <p className="sa-break-sub">
          Because the hardest part of investing isn't finding information.
          It's knowing what matters.
        </p>
      </motion.section>

      {/* ══════════════════════════════════════
          AI ASSISTANCE SECTION
          ══════════════════════════════════════ */}
      <motion.section 
        className="sa-section" 
        style={{ paddingTop: 60 }}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={sectionVariants}
      >
        <div className="sa-container">
          <div className="sa-ai-grid">

            {/* LEFT — Label + headline */}
            <div>
              <div
                className="sa-eyebrow"
                style={{ marginBottom: 24 }}
              >
                AI ASSISTANCE
              </div>
              <h2 className="sa-ai-headline">
                An intelligent layer<br />
                over the market.
              </h2>
              <p className="sa-ai-body">
                Not a chatbot. Not a signal service. An analytical layer that brings
                structured reasoning to what the market is doing — and what it might mean
                for your position.
              </p>
              <p
                className="sa-ai-body"
                style={{ marginTop: 16 }}
              >
                Powered by Claude (Anthropic) + Gemini (Google) — dual AI engines that
                independently analyze each situation and synthesize a view.
              </p>
            </div>

            {/* RIGHT — Analysis panel */}
            <div className="sa-analysis-panel">
              <div className="sa-analysis-header">
                <div className="sa-pulse" />
                <span
                  style={{
                    fontFamily: "Inter, sans-serif",
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--sa-text-primary)",
                    letterSpacing: "0.03em",
                  }}
                >
                  NIFTY 50 · Market Update
                </span>
                <span
                  style={{
                    marginLeft: "auto",
                    fontFamily: "JetBrains Mono, monospace",
                    fontSize: 11,
                    color: "var(--sa-text-muted)",
                  }}
                >
                  {new Date().toLocaleTimeString("en-IN", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
              <div className="sa-analysis-body">
                <div className="sa-analysis-section">
                  <div className="sa-analysis-label">SUMMARY</div>
                  <p className="sa-analysis-text">
                    <strong>Banking and IT are leading today's advance.</strong>{" "}
                    The move has broad participation — 68% of NSE stocks advancing suggests
                    this is not a narrow large-cap rally.
                  </p>
                </div>
                <div className="sa-analysis-divider" />
                <div className="sa-analysis-section">
                  <div className="sa-analysis-label">WHY IT MATTERS</div>
                  <p className="sa-analysis-text">
                    Improving breadth is a constructive signal. When gains are distributed
                    across sectors rather than concentrated, underlying participation is
                    stronger — reducing the risk of a single-sector reversal dragging the index.
                  </p>
                </div>
                <div className="sa-analysis-divider" />
                <div className="sa-analysis-section" style={{ marginBottom: 0 }}>
                  <div className="sa-analysis-label">WHAT TO WATCH</div>
                  <p className="sa-analysis-text">
                    Watch Bank Nifty breadth and volume confirmation into the close.
                    FII flow data tomorrow morning will be significant.
                  </p>
                </div>
                <div
                  style={{
                    marginTop: 20,
                    padding: "10px 14px",
                    background: "var(--sa-surface-elevated)",
                    borderRadius: 8,
                    fontFamily: "Inter, sans-serif",
                    fontSize: 10,
                    color: "var(--sa-text-muted)",
                    lineHeight: 1.5,
                  }}
                >
                  AI-generated analysis · For informational purposes only · Not investment advice
                </div>
              </div>
            </div>

          </div>
        </div>
      </motion.section>

      {/* ══════════════════════════════════════
          PORTFOLIO SECTION
          ══════════════════════════════════════ */}
      <motion.section
        className="sa-section"
        id="portfolio"
        style={{ paddingTop: 140, paddingBottom: 140 }}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={sectionVariants}
      >
        <div className="sa-container">
          <div className="sa-port-grid">

            {/* LEFT — Portfolio value */}
            <div>
              <div
                className="sa-eyebrow"
                style={{ marginBottom: 24 }}
              >
                PORTFOLIO INTELLIGENCE
              </div>
              <h2 className="sa-port-headline">
                Your investments.<br />
                One clear picture.
              </h2>
              <div
                style={{ marginTop: 36, marginBottom: 8 }}
                className="sa-port-value"
              >
                ₹12,48,320
              </div>
              <div className="sa-port-pnl">
                +₹18,420 today · +1.50%
              </div>

              {/* Mini chart */}
              <div
                style={{
                  background: "var(--sa-surface)",
                  border: "1px solid var(--sa-border)",
                  borderRadius: 14,
                  padding: "16px 20px",
                  marginBottom: 24,
                }}
              >
                <PortfolioMiniChart />
              </div>

              {/* Allocation */}
              {[
                { name: "Equity", pct: 52 },
                { name: "Technology", pct: 21 },
                { name: "Financials", pct: 18 },
                { name: "Other", pct: 9 },
              ].map(a => (
                <div className="sa-alloc-row" key={a.name}>
                  <span className="sa-alloc-name">{a.name}</span>
                  <div className="sa-alloc-track">
                    <div className="sa-alloc-fill" style={{ width: `${a.pct}%` }} />
                  </div>
                  <span className="sa-alloc-pct">{a.pct}%</span>
                </div>
              ))}
              <p
                style={{
                  fontFamily: "Inter, sans-serif",
                  fontSize: 10,
                  color: "var(--sa-text-muted)",
                  marginTop: 16,
                }}
              >
                All portfolio values shown are demo · Connect your broker to see real data
              </p>
            </div>

            {/* RIGHT — Context panel */}
            <div
              className="sa-port-panel"
              data-testid="about-image-slot"
            >
              <div
                className="sa-eyebrow sa-eyebrow-dim"
                style={{ marginBottom: 20 }}
              >
                PORTFOLIO CONTEXT
              </div>

              {[
                {
                  label: "Today's P&L",
                  val: "+₹18,420",
                  color: "var(--sa-gain)",
                },
                {
                  label: "Unrealised Gain",
                  val: "+₹1,24,200",
                  color: "var(--sa-gain)",
                },
                {
                  label: "Open Positions",
                  val: "8 stocks",
                  color: "var(--sa-text-primary)",
                },
                {
                  label: "Largest Holding",
                  val: "HDFC Bank",
                  color: "var(--sa-text-primary)",
                },
              ].map(r => (
                <div
                  key={r.label}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "14px 0",
                    borderBottom: "1px solid var(--sa-border)",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "Inter, sans-serif",
                      fontSize: 12,
                      color: "var(--sa-text-secondary)",
                      fontWeight: 500,
                    }}
                  >
                    {r.label}
                  </span>
                  <span
                    style={{
                      fontFamily: "JetBrains Mono, monospace",
                      fontSize: 13,
                      color: r.color,
                      fontWeight: 600,
                    }}
                  >
                    {r.val}
                  </span>
                </div>
              ))}

              <div
                style={{
                  marginTop: 24,
                  padding: "14px 16px",
                  background: "var(--sa-accent-soft)",
                  border: "1px solid var(--sa-border)",
                  borderRadius: 12,
                }}
              >
                <div
                  className="sa-eyebrow sa-eyebrow-green"
                  style={{ marginBottom: 8, color: "var(--sa-accent)" }}
                >
                  AI NOTE
                </div>
                <p
                  style={{
                    fontFamily: "Inter, sans-serif",
                    fontSize: 12,
                    color: "var(--sa-text-secondary)",
                    lineHeight: 1.6,
                  }}
                >
                  Your largest holdings are outperforming the index today.
                  Banking exposure is working in your favour.
                </p>
              </div>

              <div
                style={{
                  marginTop: 16,
                  padding: "10px 0 0",
                  fontFamily: "Inter, sans-serif",
                  fontSize: 10,
                  color: "var(--sa-text-muted)",
                }}
              >
                Demo portfolio · Values are illustrative only
              </div>
            </div>

          </div>
        </div>
      </motion.section>

      {/* ══════════════════════════════════════
          FROM INSIGHT TO ACTION
          ══════════════════════════════════════ */}
      <motion.section
        className="sa-section"
        id="how-it-works"
        style={{
          paddingTop: 60,
          paddingBottom: 120,
          borderTop: "1px solid var(--sa-border)",
        }}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={sectionVariants}
      >
        <div className="sa-container-sm">
          <div className="sa-flow">
            <h2 className="sa-flow-headline">
              From insight<br />
              to action.
            </h2>

            <div className="sa-flow-steps">
              {[
                {
                  num: "01",
                  title: "Market movement",
                  desc: "Live data from NSE and Yahoo Finance. Price, volume, breadth.",
                  soon: false,
                },
                {
                  num: "02",
                  title: "AI analysis",
                  desc: "Dual AI engines synthesize market context into structured reasoning.",
                  soon: false,
                },
                {
                  num: "03",
                  title: "Portfolio context",
                  desc: "How does the market move relate to your open positions and watchlist?",
                  soon: false,
                },
                {
                  num: "04",
                  title: "Your decision",
                  desc: "You read the full context. You decide whether to act.",
                  soon: false,
                },
                {
                  num: "05",
                  title: "Broker execution",
                  desc: "Execute through Zerodha or Upstox directly. Groww coming soon.",
                  soon: false,
                },
              ].map((step, i, arr) => (
                <div key={step.num} style={{ width: "100%", maxWidth: 480 }}>
                  <div className="sa-flow-step">
                    <span className="sa-flow-num">{step.num}</span>
                    <div>
                      <div className="sa-flow-title">{step.title}</div>
                      <div className="sa-flow-desc">{step.desc}</div>
                    </div>
                    {step.soon && (
                      <span className="sa-flow-soon">Soon</span>
                    )}
                  </div>
                  {i < arr.length - 1 && (
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "center",
                        alignItems: "center",
                        height: 28,
                      }}
                    >
                      <ArrowDown size={12} style={{ color: "var(--sa-text-muted)" }} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </motion.section>

      {/* ══════════════════════════════════════
          FAQ
          ══════════════════════════════════════ */}
      <motion.section
        className="sa-section"
        style={{
          paddingTop: 80,
          paddingBottom: 120,
          borderTop: "1px solid var(--sa-border)",
        }}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={sectionVariants}
      >
        <div className="sa-container-sm">
          <h2 className="sa-faq-headline">Questions</h2>
          <p className="sa-faq-sub">The answers you'll want before you start.</p>
          {FAQS.map(f => (
            <FAQItem key={f.q} q={f.q} a={f.a} />
          ))}
        </div>
      </motion.section>

      {/* ══════════════════════════════════════
          FINAL CTA
          ══════════════════════════════════════ */}
      <motion.section
        className="sa-break sa-final-cta"
        style={{ paddingTop: 100, paddingBottom: 100 }}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={sectionVariants}
      >
        <h2 className="sa-final-headline">
          Make the market<br />
          <em>easier to understand.</em>
        </h2>
        <p className="sa-final-sub">
          StockAssist brings the information together.
          You decide what to do with it.
        </p>
        <Link
          to="/register"
          className="sa-cta-primary"
          data-testid="cta-final-btn"
          style={{ display: "inline-flex" }}
        >
          Get started <ArrowRight size={14} />
        </Link>
      </motion.section>

      {/* ══════════════════════════════════════
          FOOTER
          ══════════════════════════════════════ */}
      <footer className="sa-footer">
        <div className="sa-container">
          <div className="sa-footer-top">
            <div>
              <div className="sa-footer-brand">StockAssist</div>
              <p className="sa-footer-tagline">
                Market data, portfolio context<br />
                and AI-assisted analysis.
              </p>
            </div>

            <div>
              <div className="sa-footer-col-title">Product</div>
              <a href="#markets" className="sa-footer-link">Markets</a>
              <a href="#intelligence" className="sa-footer-link">Intelligence</a>
              <a href="#portfolio" className="sa-footer-link">Portfolio</a>
              <a href="#how-it-works" className="sa-footer-link">How it works</a>
            </div>

            <div>
              <div className="sa-footer-col-title">Account</div>
              <Link to="/login" className="sa-footer-link">Log in</Link>
              <Link to="/register" className="sa-footer-link">Get started</Link>
            </div>

            <div>
              <div className="sa-footer-col-title">Legal</div>
              <a href="#" className="sa-footer-link">Privacy</a>
              <a href="#" className="sa-footer-link">Terms</a>
              <a href="#" className="sa-footer-link">Risk Disclosure</a>
            </div>
          </div>

          <div className="sa-footer-bottom">
            <span className="sa-footer-copy">
              © 2026 StockAssist. All rights reserved.
            </span>
            <p className="sa-footer-disclaimer">
              StockAssist provides market information and AI-assisted analysis for informational
              purposes only. It does not provide guaranteed returns or personalised investment advice.
              Investments are subject to market risks. Past analysis does not indicate future results.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
