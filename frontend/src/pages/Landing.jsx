import { Link } from "react-router-dom";
import { useState, useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { ArrowRight, Shield, Brain, TrendingUp, BarChart3, Zap, LineChart, Search, Bot, Play, ChevronRight, ScanSearch, MessagesSquare, MousePointerClick, GraduationCap, CandlestickChart, Building2, Newspaper, CalendarDays, Sparkles, Landmark, Wallet, Check, Target, Users, Eye } from "lucide-react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useTheme } from "../context/ThemeContext";
import api from "../services/api";
import heroImg from "../assets/hero-trading-desk.png";
import aboutImg from "../assets/about-mumbai-skyline.png";

gsap.registerPlugin(ScrollTrigger);

const APLogo = ({ size = 32, className = "" }) => (
  <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 512 512" className={className} aria-label="StockAssist AI logo">
    <defs>
      <linearGradient id="ap-bg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#4F46E5" />
        <stop offset="100%" stopColor="#818CF8" />
      </linearGradient>
    </defs>
    <rect width="512" height="512" rx="110" fill="url(#ap-bg)" />
    <rect x="80" y="340" width="44" height="96" rx="6" fill="#FFFFFF" opacity="0.5" />
    <rect x="140" y="300" width="44" height="136" rx="6" fill="#FFFFFF" opacity="0.6" />
    <rect x="200" y="260" width="44" height="176" rx="6" fill="#FFFFFF" opacity="0.7" />
    <rect x="260" y="210" width="44" height="226" rx="6" fill="#FFFFFF" opacity="0.8" />
    <rect x="320" y="160" width="44" height="276" rx="6" fill="#FFFFFF" opacity="0.9" />
    <rect x="380" y="120" width="44" height="316" rx="6" fill="#FFFFFF" />
    <polyline points="102,340 162,300 222,258 282,208 342,158 402,118" stroke="#FFFFFF" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" opacity="0.9" />
    <circle cx="402" cy="118" r="9" fill="#FFFFFF" />
  </svg>
);

const FEATURES = [
  { icon: Brain, title: "AI Market Analysis", desc: "Dual AI engines debate every trade — giving you balanced, intelligent insights." },
  { icon: Search, title: "Real-time Scanning", desc: "Live scanning of 50+ NSE stocks with AI-powered pattern recognition." },
  { icon: LineChart, title: "Smart Portfolio", desc: "AI-monitored portfolio with automatic rebalancing suggestions." },
  { icon: BarChart3, title: "Paper Trading", desc: "Practice strategies risk-free with ₹10L virtual capital." },
  { icon: TrendingUp, title: "Backtesting", desc: "Test strategies against historical data before risking real capital." },
  { icon: Bot, title: "AI Chat Assistant", desc: "Ask anything about markets — get instant, data-backed answers." },
];

const BROKERS = ["ZERODHA", "upstox", "AngelOne", "Groww", "FYERS"];

const NAV_LINKS = ["Features", "Pricing", "How it Works", "Resources", "About"];

// ── How It Works: the actual product flow ──────────────────────────
const HOW_STEPS = [
  { icon: ScanSearch, title: "Scan the market", desc: "AI continuously scans 50+ NSE stocks in real time, surfacing setups with genuine momentum, volume and pattern signals." },
  { icon: MessagesSquare, title: "Claude & Gemini debate", desc: "Two independent AI engines analyze each setup and debate it to a synthesized call — with confidence score, risk and reward reasoning." },
  { icon: MousePointerClick, title: "Review & execute", desc: "You read the full reasoning, then act — risk-free in paper trading, or live through your connected broker." },
  { icon: GraduationCap, title: "Monitor & coach", desc: "AI tracks your open positions and coaches you after every closed trade, so each decision makes you sharper." },
];

// ── Resources: the real data & AI sources this product uses ─────────
const RESOURCES = [
  { icon: CandlestickChart, name: "Yahoo Finance", role: "Live market data, quotes & historical prices" },
  { icon: Building2, name: "NSE", role: "Official National Stock Exchange data" },
  { icon: LineChart, name: "TradingView", role: "Advanced charting & technical indicators" },
  { icon: Newspaper, name: "News API", role: "Market news & sentiment signals" },
  { icon: CalendarDays, name: "Economic Calendar", role: "Macro events, results & earnings dates" },
  { icon: Sparkles, name: "Claude (Anthropic)", role: "Dual-AI analysis & trade reasoning engine" },
  { icon: Sparkles, name: "Gemini (Google)", role: "Dual-AI analysis & debate engine" },
  { icon: Landmark, name: "Zerodha Kite", role: "Live broker order execution" },
  { icon: Wallet, name: "Upstox", role: "Live broker order execution" },
  { icon: Wallet, name: "Groww", role: "Broker integration", tag: "Soon" },
];

// ── About: principle chips drawn from project vision ────────────────
const ABOUT_CHIPS = [
  { icon: Target, label: "Every call explained" },
  { icon: Users, label: "Beginner to pro" },
  { icon: Eye, label: "Full transparency" },
];

// ── Pricing: illustrative tiers (numbers are placeholders) ──────────
const PRICING_TIERS = [
  {
    name: "Starter", tagline: "For learning the ropes", price: "Free", period: "", cta: "Get Started",
    features: [
      "Paper trading with ₹10L virtual capital",
      "Daily AI stock picks (limited)",
      "Live market overview & watchlist",
      "Basic single-AI trade analysis",
    ],
  },
  {
    name: "Pro", tagline: "For active swing & intraday traders", price: "₹499", period: "/month", popular: true, cta: "Start Free Trial",
    features: [
      "Everything in Starter",
      "Unlimited dual-AI (Claude + Gemini) analysis",
      "Real-time scanning of 50+ NSE stocks",
      "Backtesting engine & portfolio monitoring",
      "Email & WhatsApp alerts",
    ],
  },
  {
    name: "Elite", tagline: "For serious, full-time traders", price: "₹1,499", period: "/month", cta: "Go Elite",
    features: [
      "Everything in Pro",
      "Live broker execution (Zerodha & Upstox)",
      "AI trade coaching after every closed trade",
      "Advanced backtesting & priority AI compute",
      "Priority support",
    ],
  },
];

export default function Landing() {
  const { theme } = useTheme();
  const [liveData, setLiveData] = useState(null);
  const [aiScore] = useState(72);
  const rootRef = useRef(null);

  useEffect(() => {
    api.get("/market/overview").then(r => setLiveData(r.data)).catch(() => {});
  }, []);

  // GSAP scroll-driven reveal for the About section.
  // Scoped via gsap.context so ctx.revert() kills every ScrollTrigger
  // created here on unmount — no leaks across SPA route changes.
  // (How It Works / Resources / Pricing use framer-motion's whileInView
  // instead — same reliable IntersectionObserver-based reveal used
  // throughout the rest of the app; GSAP ScrollTrigger's pixel-position
  // triggers for those 3 sections proved unreliable once real images
  // above them shifted page layout after mount.)
  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from(".about-reveal", {
        scrollTrigger: { trigger: "#about", start: "top 75%" },
        y: 32, opacity: 0, duration: 0.7, stagger: 0.18, ease: "power3.out",
      });
    }, rootRef);
    return () => ctx.revert();
  }, []);

  return (
    <div ref={rootRef} className="min-h-screen" style={{ background: "var(--bg)" }} data-testid="landing-page">
      {/* ====== NAVIGATION ====== */}
      <nav className="fixed top-0 left-0 right-0 z-50 glass-nav" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <div className="flex items-center gap-2.5">
              <APLogo size={28} />
              <span className="text-base font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>StockAssist AI</span>
            </div>
            <div className="hidden lg:flex items-center gap-6">
              {NAV_LINKS.map(link => (
                <a key={link} href={`#${link.toLowerCase().replace(/\s/g, '-')}`} className="text-[13px] font-medium transition-colors hover:opacity-80" style={{ color: "var(--text-secondary)" }}>
                  {link}
                </a>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Link to="/login" data-testid="landing-login-btn" className="text-[13px] font-medium transition-all hover:opacity-80 px-4 py-2" style={{ color: "var(--text-secondary)" }}>
              Login
            </Link>
            <Link to="/register" data-testid="landing-signup-btn" className="btn-primary text-[13px] py-2.5 px-5">
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {/* ====== HERO ====== */}
      <section className="relative pt-28 pb-16 px-6 overflow-hidden">
        {/* Subtle grid pattern */}
        <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: "radial-gradient(circle at 1px 1px, var(--text-muted) 1px, transparent 0)", backgroundSize: "40px 40px" }} />
        {/* Gradient orbs */}
        <div className="absolute top-20 left-1/4 w-[500px] h-[500px] rounded-full opacity-[0.06]" style={{ background: "radial-gradient(circle, var(--ai-accent), transparent 70%)" }} />
        <div className="absolute bottom-0 right-1/4 w-[400px] h-[400px] rounded-full opacity-[0.04]" style={{ background: "radial-gradient(circle, #10B981, transparent 70%)" }} />

        <div className="max-w-7xl mx-auto relative">
          {/* Pill badge */}
          <div className="flex justify-center mb-6">
            <div className="badge-ai text-[11px]">
              <Zap size={12} /> AI-Powered Trading Operating System
            </div>
          </div>

          {/* Headline */}
          <div className="text-center max-w-4xl mx-auto mb-8">
            <h1 className="text-5xl sm:text-6xl lg:text-[72px] font-semibold tracking-tight leading-[1.05] mb-6 font-display" style={{ color: "var(--text-primary)" }}>
              Your AI Trading{" "}
              <br className="hidden sm:block" />
              <span className="bg-clip-text text-transparent" style={{ backgroundImage: "linear-gradient(135deg, var(--ai-accent), #A78BFA)" }}>Partner That Never Sleeps.</span>
            </h1>
            <p className="text-lg leading-relaxed max-w-2xl mx-auto" style={{ color: "var(--text-secondary)" }}>
              Real-time market intelligence, AI trade reasoning, portfolio insights, paper trading, and continuous monitoring — all in one intelligent <span className="font-semibold" style={{ color: "var(--text-primary)" }}>platform.</span>
            </p>
          </div>

          {/* CTAs */}
          <div className="flex flex-wrap justify-center gap-3 mb-16">
            <Link to="/register" data-testid="hero-cta-btn" className="btn-primary px-7 py-3.5 text-[15px]">
              Start Free Trial <ArrowRight size={16} />
            </Link>
            <a href="#how-it-works" className="btn-ghost px-7 py-3.5 text-[15px]">
              <Play size={14} /> Watch Live Demo
            </a>
          </div>

          {/* ====== HERO PANELS ====== */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-6xl mx-auto">
            {/* Left: AI Activity Panel */}
            <div className="glass-card p-6" style={{ borderRadius: "var(--card-radius-lg)" }}>
              <div className="space-y-3 mb-5">
                {[
                  { status: "done", text: "AI Active", color: "var(--gain)" },
                  { status: "done", text: "Scanning 52 stocks", color: "var(--ai-accent)" },
                  { status: "loading", text: "Analyzing patterns", color: "var(--ai-accent)" },
                  { status: "done", text: "Generating insights", color: "var(--ai-accent)" },
                  { status: "done", text: "Updated 3 sec ago", color: "var(--text-muted)" },
                ].map((item, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <div className={`w-2 h-2 rounded-full shrink-0 ${item.status === "loading" ? "animate-pulse" : ""}`} style={{ background: item.color }} />
                    <span className="text-[13px] font-medium" style={{ color: item.color }}>{item.text}</span>
                  </div>
                ))}
              </div>
              {/* Mini chart placeholder */}
              <div className="rounded-xl overflow-hidden" style={{ background: "var(--bg-surface)", height: 120, border: "1px solid var(--border)" }}>
                <svg viewBox="0 0 400 120" className="w-full h-full">
                  <defs>
                    <linearGradient id="chart-grad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--ai-accent)" stopOpacity="0.3" />
                      <stop offset="100%" stopColor="var(--ai-accent)" stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  <path d="M0,80 Q50,60 100,65 T200,45 T300,55 T400,30 L400,120 L0,120 Z" fill="url(#chart-grad)" />
                  <path d="M0,80 Q50,60 100,65 T200,45 T300,55 T400,30" fill="none" stroke="var(--ai-accent)" strokeWidth="2" />
                </svg>
              </div>
            </div>

            {/* Right: Markets Open Panel */}
            <div className="glass-card p-6" style={{ borderRadius: "var(--card-radius-lg)" }}>
              <div className="flex items-center justify-between mb-5">
                <div>
                  <span className="text-xs font-semibold" style={{ color: "var(--text-muted)" }}>Markets Open</span>
                  <p className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>
                    {new Date().toLocaleDateString("en-IN", { month: "short", day: "numeric" })} · {new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
                  </p>
                </div>
                <span className="badge-live">LIVE</span>
              </div>
              <div className="space-y-3">
                {[
                  { label: "NIFTY 50", value: liveData?.nifty?.value, pct: liveData?.nifty?.change_pct },
                  { label: "SENSEX", value: liveData?.sensex?.value, pct: liveData?.sensex?.change_pct },
                  { label: "BANK NIFTY", value: liveData?.bank_nifty?.value, pct: liveData?.bank_nifty?.change_pct },
                ].map(idx => (
                  <div key={idx.label} className="flex items-center justify-between py-2.5 px-3 rounded-xl" style={{ background: "var(--hover)" }}>
                    <span className="text-[12px] font-semibold" style={{ color: "var(--text-secondary)" }}>{idx.label}</span>
                    <div className="flex items-center gap-3">
                      <span className="text-[14px] font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                        {idx.value ? Number(idx.value).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—"}
                      </span>
                      <span className="text-[12px] font-mono font-medium" style={{ color: (idx.pct ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
                        {idx.pct != null ? `${idx.pct >= 0 ? "+" : ""}${idx.pct.toFixed(2)}%` : "—"}
                      </span>
                    </div>
                  </div>
                ))}
              </div>

              {/* AI Score */}
              <div className="mt-5 flex items-center gap-4">
                <div className="relative w-16 h-16">
                  <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                    <circle cx="18" cy="18" r="14" fill="none" stroke="var(--border)" strokeWidth="3" />
                    <circle cx="18" cy="18" r="14" fill="none" stroke="var(--gain)" strokeWidth="3" strokeLinecap="round"
                      strokeDasharray={`${aiScore * 0.88} ${88 - aiScore * 0.88}`} className="score-gauge-circle" />
                  </svg>
                  <span className="absolute inset-0 flex items-center justify-center text-sm font-bold font-mono" style={{ color: "var(--gain)" }}>{aiScore}</span>
                </div>
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>AI Confidence</p>
                  <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>Market conditions favorable</p>
                </div>
              </div>
            </div>
          </div>

          {/* ====== HERO PRODUCT PREVIEW (image slot: hero-trading-desk.png) ====== */}
          <div className="mt-16 max-w-5xl mx-auto">
            <div data-testid="hero-image-slot" className="glass-card overflow-hidden relative" style={{ borderRadius: "var(--card-radius-lg)" }}>
              <img
                src={heroImg}
                alt="A trader's desk at night lit by live market charts"
                loading="lazy"
                className="w-full object-cover"
                style={{ height: 380, display: "block" }}
              />
              <div className="absolute inset-0 pointer-events-none" style={{ background: "linear-gradient(180deg, transparent 55%, rgba(9,10,20,0.55) 100%)" }} />
              <div className="absolute bottom-5 left-6 right-6 flex items-center justify-between flex-wrap gap-2">
                <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-3 py-1.5 rounded-full" style={{ background: "rgba(255,255,255,0.14)", color: "#fff", backdropFilter: "blur(8px)" }}>
                  <Zap size={12} /> AI Active — Markets Never Sleep
                </span>
              </div>
            </div>
          </div>

          {/* ====== TRUST BAR ====== */}
          <div className="mt-14 text-center">
            <p className="text-[11px] uppercase tracking-[0.15em] font-medium mb-5" style={{ color: "var(--text-muted)" }}>Trusted by traders and investors worldwide</p>
            <div className="flex items-center justify-center gap-3 flex-wrap">
              {BROKERS.map(name => (
                <span key={name} className="px-4 py-2 rounded-full text-[13px] font-semibold tracking-wide font-display transition-colors" style={{ color: "var(--text-secondary)", background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                  {name}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ====== FEATURES ====== */}
      <section id="features" className="py-20 px-6" style={{ background: "var(--bg-surface)" }}>
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <p className="text-[11px] uppercase tracking-[0.2em] font-bold mb-3" style={{ color: "var(--ai-accent)" }}>Features</p>
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>
              Everything You Need to Trade Smarter
            </h2>
            <p className="text-sm mt-3 max-w-lg mx-auto" style={{ color: "var(--text-secondary)" }}>
              Powerful tools and AI insights to help you make better trading decisions.
            </p>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
            {FEATURES.map((f) => (
              <div key={f.title} className="glass-card p-5 text-center group cursor-pointer" style={{ borderRadius: "var(--card-radius)" }}>
                <div className="w-12 h-12 rounded-xl flex items-center justify-center mx-auto mb-3 transition-transform group-hover:scale-110" style={{ background: "var(--ai-accent-soft)" }}>
                  <f.icon size={22} style={{ color: "var(--ai-accent)" }} />
                </div>
                <h3 className="text-[13px] font-semibold mb-1 font-display" style={{ color: "var(--text-primary)" }}>{f.title}</h3>
                <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ====== HOW IT WORKS ====== */}
      <section id="how-it-works" className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <p className="eyebrow mb-3" style={{ color: "var(--ai-accent)" }}>How It Works</p>
            <h2 className="section-title font-display">From Scan to Smarter Trade</h2>
            <p className="body-text mt-3 max-w-lg mx-auto">Four steps that turn raw market data into a decision you can actually trust — end to end.</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {HOW_STEPS.map((step, i) => (
              <motion.div
                key={step.title}
                className="glass-card p-6 relative"
                style={{ borderRadius: "var(--card-radius)" }}
                initial={{ opacity: 0, y: 40 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ duration: 0.5, delay: i * 0.12, ease: "easeOut" }}
              >
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
                    <step.icon size={20} style={{ color: "var(--ai-accent)" }} />
                  </div>
                  <span className="text-[11px] font-mono font-bold tracking-wide" style={{ color: "var(--text-muted)" }}>STEP {i + 1}</span>
                </div>
                <h3 className="card-title mb-2">{step.title}</h3>
                <p className="body-text text-[13px]">{step.desc}</p>
                {i < HOW_STEPS.length - 1 && (
                  <ChevronRight size={18} className="hidden lg:block absolute top-8 -right-4 z-10" style={{ color: "var(--text-muted)" }} />
                )}
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ====== RESOURCES / "Powered By" ====== */}
      <section id="resources" className="py-20 px-6" style={{ background: "var(--bg-surface)" }}>
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <p className="eyebrow mb-3" style={{ color: "var(--ai-accent)" }}>Powered By</p>
            <h2 className="section-title font-display">Our Data &amp; AI Sources</h2>
            <p className="body-text mt-3 max-w-lg mx-auto">
              Full transparency on where every quote, chart, headline and recommendation actually comes from — no black boxes.
            </p>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
            {RESOURCES.map((r, i) => (
              <motion.div
                key={r.name}
                className="glass-card p-5 text-center"
                style={{ borderRadius: "var(--card-radius)" }}
                initial={{ opacity: 0, y: 28 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ duration: 0.4, delay: i * 0.05, ease: "easeOut" }}
              >
                <div className="w-11 h-11 rounded-xl flex items-center justify-center mx-auto mb-3" style={{ background: "var(--ai-accent-soft)" }}>
                  <r.icon size={20} style={{ color: "var(--ai-accent)" }} />
                </div>
                <h3 className="text-[13px] font-semibold font-display mb-1 flex items-center justify-center gap-1.5 flex-wrap" style={{ color: "var(--text-primary)" }}>
                  {r.name}
                  {r.tag && (
                    <span className="badge-ai" style={{ fontSize: 9, padding: "1px 7px" }}>{r.tag}</span>
                  )}
                </h3>
                <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>{r.role}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ====== PRICING ====== */}
      <section id="pricing" className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <p className="eyebrow mb-3" style={{ color: "var(--ai-accent)" }}>Pricing</p>
            <h2 className="section-title font-display">Simple, Transparent Plans</h2>
            <p className="body-text mt-3 max-w-lg mx-auto">Start free, upgrade when you're ready for the full power of dual-AI analysis and live execution.</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-5xl mx-auto items-stretch">
            {PRICING_TIERS.map((tier, i) => (
              <motion.div
                key={tier.name}
                className="glass-card p-7 flex flex-col relative"
                style={{
                  borderRadius: "var(--card-radius-lg)",
                  borderColor: tier.popular ? "var(--ai-accent)" : undefined,
                  boxShadow: tier.popular ? "0 0 0 1px var(--ai-accent), var(--card-shadow-hover)" : undefined,
                }}
                initial={{ opacity: 0, y: 50, scale: 0.97 }}
                whileInView={{ opacity: 1, y: 0, scale: 1 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ duration: 0.5, delay: i * 0.1, ease: "easeOut" }}
              >
                {tier.popular && (
                  <span className="badge-ai absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap">Most Popular</span>
                )}
                <h3 className="card-title mb-1">{tier.name}</h3>
                <p className="text-[13px] mb-5" style={{ color: "var(--text-muted)" }}>{tier.tagline}</p>
                <div className="mb-6 flex items-baseline gap-1.5">
                  <span className="stat-value" style={{ fontSize: "2rem" }}>{tier.price}</span>
                  {tier.period && <span className="text-sm" style={{ color: "var(--text-muted)" }}>{tier.period}</span>}
                </div>
                <ul className="space-y-3 mb-8 flex-1">
                  {tier.features.map(f => (
                    <li key={f} className="flex items-start gap-2 text-[13px]" style={{ color: "var(--text-secondary)" }}>
                      <Check size={15} className="shrink-0 mt-0.5" style={{ color: "var(--gain)" }} />
                      {f}
                    </li>
                  ))}
                </ul>
                <Link to="/register" data-testid={`pricing-cta-${tier.name.toLowerCase()}`} className={`btn-block ${tier.popular ? "btn-primary" : "btn-secondary"}`}>
                  {tier.cta}
                </Link>
              </motion.div>
            ))}
          </div>
          <p className="text-center text-[11px] mt-10" style={{ color: "var(--text-muted)" }}>
            * Pricing shown is illustrative and subject to change ahead of launch. Final plans, limits and features will be confirmed at general availability.
          </p>
        </div>
      </section>

      {/* ====== ABOUT ====== */}
      <section id="about" className="py-20 px-6" style={{ background: "var(--bg-surface)" }}>
        <div className="max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
          <div className="about-reveal">
            <p className="eyebrow mb-3" style={{ color: "var(--ai-accent)" }}>About Us</p>
            <h2 className="section-title font-display mb-5">The Smartest AI Trading Partner — Not a Replacement</h2>
            <p className="body-text mb-4">
              StockAssist AI is an AI-powered trading operating system built for the Indian stock market — combining artificial intelligence, live market data, chart analysis, portfolio management, broker integration, paper trading, backtesting, news intelligence and educational guidance into one seamless experience.
            </p>
            <p className="body-text mb-4">
              Our goal isn't to replace traders — it's to become the smartest AI trading partner you have. Every recommendation our AI makes explains itself: why a stock was selected, its confidence score, the technical and fundamental reasoning behind it, the risk and reward, news impact, sector performance and current market conditions.
            </p>
            <p className="body-text mb-6">
              Built for the full spectrum of the market — beginner traders taking their first position, swing and intraday traders chasing setups, long-term investors compounding wealth, students learning to read a chart, and professionals who want a fast, tireless second opinion. The AI is here to educate, not just to signal buy or sell.
            </p>
            <div className="flex flex-wrap gap-3">
              {ABOUT_CHIPS.map(c => (
                <div key={c.label} className="flex items-center gap-2 px-4 py-2 rounded-full" style={{ background: "var(--ai-accent-soft)" }}>
                  <c.icon size={14} style={{ color: "var(--ai-accent)" }} />
                  <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>{c.label}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="about-reveal" data-testid="about-image-slot">
            <div className="glass-card overflow-hidden" style={{ borderRadius: "var(--card-radius-lg)", aspectRatio: "4 / 3" }}>
              <img
                src={aboutImg}
                alt="Mumbai financial district skyline at dusk"
                loading="lazy"
                className="w-full h-full object-cover"
              />
            </div>
          </div>
        </div>
      </section>

      {/* ====== CTA ====== */}
      <section className="py-20 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight mb-4 font-display" style={{ color: "var(--text-primary)" }}>
            Ready to Trade Smarter?
          </h2>
          <p className="text-base leading-relaxed mb-8" style={{ color: "var(--text-secondary)" }}>
            Join thousands of Indian traders who use AI to make better decisions. Start with as little as ₹15,000.
          </p>
          <Link to="/register" data-testid="cta-final-btn" className="btn-primary px-8 py-4 text-base">
            Create Free Account <ArrowRight size={18} />
          </Link>
          <div className="mt-12 glass-card p-5 text-left" style={{ borderRadius: "var(--card-radius)" }}>
            <div className="flex items-start gap-3">
              <Shield size={20} className="shrink-0 mt-0.5" style={{ color: "var(--text-muted)" }} />
              <div>
                <p className="text-[10px] font-bold uppercase tracking-widest mb-1" style={{ color: "var(--text-muted)" }}>Important Disclaimer</p>
                <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                  StockAssist AI is an AI-powered analysis tool. We do not promise or guarantee profits. Stock market investments are subject to market risks. Our AI helps you make informed decisions through data-driven analysis, but all trading decisions are ultimately yours. Past performance does not indicate future results.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ====== FOOTER ====== */}
      <footer className="py-8 px-6" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <APLogo size={22} />
            <span className="text-sm font-semibold font-display" style={{ color: "var(--text-primary)" }}>StockAssist AI</span>
          </div>
          <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>© 2026 StockAssist AI. AI-Powered Indian Stock Market Analysis. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
