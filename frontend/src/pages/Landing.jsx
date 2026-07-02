import { Link } from "react-router-dom";
import { ArrowRight, Shield, Brain, TrendingUp, BarChart3, Zap, Users } from "lucide-react";

// Inline AlphaPartner SVG logo — no external CDN dependency
const APLogo = ({ size = 32, className = "" }) => (
  <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 512 512" className={className} aria-label="AlphaPartner logo">
    <defs>
      <linearGradient id="ap-bg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#09090B" />
        <stop offset="100%" stopColor="#18181B" />
      </linearGradient>
      <linearGradient id="ap-accent" x1="0%" y1="100%" x2="100%" y2="0%">
        <stop offset="0%" stopColor="#4F46E5" />
        <stop offset="100%" stopColor="#818CF8" />
      </linearGradient>
    </defs>
    <rect width="512" height="512" rx="96" fill="url(#ap-bg)" />
    <rect width="508" height="508" x="2" y="2" rx="94" fill="none" stroke="#6366F1" strokeWidth="2" strokeOpacity="0.4" />
    <rect x="80" y="340" width="44" height="96" rx="6" fill="url(#ap-accent)" opacity="0.5" />
    <rect x="140" y="300" width="44" height="136" rx="6" fill="url(#ap-accent)" opacity="0.65" />
    <rect x="200" y="260" width="44" height="176" rx="6" fill="url(#ap-accent)" opacity="0.75" />
    <rect x="260" y="210" width="44" height="226" rx="6" fill="url(#ap-accent)" opacity="0.85" />
    <rect x="320" y="160" width="44" height="276" rx="6" fill="url(#ap-accent)" />
    <rect x="380" y="120" width="44" height="316" rx="6" fill="url(#ap-accent)" />
    <polyline points="102,340 162,300 222,258 282,208 342,158 402,118"
      stroke="#818CF8" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" opacity="0.9" />
    <circle cx="402" cy="118" r="9" fill="#A5B4FC" />
  </svg>
);

const FEATURES = [
  { icon: Brain, title: "Dual AI Analysis", desc: "Claude & Gemini debate every trade — bullish vs risk-aware — giving you balanced, intelligent insights before you act." },
  { icon: TrendingUp, title: "Top 3 Daily Picks", desc: "Every morning at 9:15 AM, AI scans 50+ NSE stocks and surfaces the 3 highest-confidence setups with entry, SL, and targets." },
  { icon: BarChart3, title: "Real-Time Monitoring", desc: "Live WebSocket streaming, sector heatmaps, FII/DII flows, and automatic alerts when your trades hit key levels." },
  { icon: Zap, title: "Smart Notifications", desc: "AI watches your portfolio 24/7 and sends alerts on WhatsApp — buy signals, stop loss warnings, exit reminders." },
  { icon: Users, title: "Beginner & Advanced", desc: "New to trading? Get plain-English explanations for every recommendation. Advanced? Access raw data and technical indicators." },
  { icon: Shield, title: "Risk Management", desc: "Built-in position sizing, daily loss limits, and risk-reward validation ensure your capital is always protected." },
];

const STEPS = [
  { num: "01", title: "Sign Up", desc: "Create your free account in seconds. Set your capital, risk level, and preferences." },
  { num: "02", title: "AI Analyzes", desc: "Our dual AI system scans the market, identifies patterns, and generates high-confidence stock picks." },
  { num: "03", title: "You Decide", desc: "Review AI recommendations with full explanations. Approve trades with one click via Zerodha." },
  { num: "04", title: "AI Monitors", desc: "Once you're in a trade, AI monitors every second — alerting you on momentum shifts, target proximity, and exit timing." },
];

// Dashboard preview — SVG placeholder (no CDN dependency)
const DashboardPreview = () => (
  <div className="mt-16 rounded-2xl overflow-hidden border" style={{ borderColor: "var(--border)", background: "var(--bg-surface)", minHeight: 240 }}>
    <div className="p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="h-3 w-3 rounded-full" style={{ background: "var(--loss)" }} />
        <div className="h-3 w-3 rounded-full" style={{ background: "#F59E0B" }} />
        <div className="h-3 w-3 rounded-full" style={{ background: "var(--gain)" }} />
        <div className="ml-4 text-xs font-mono" style={{ color: "var(--text-muted)" }}>AlphaPartner Dashboard</div>
      </div>
      <div className="grid grid-cols-3 gap-4 mb-6">
        {[["NIFTY 50", "23,412", "+0.84%", true], ["BANK NIFTY", "54,280", "+1.12%", true], ["India VIX", "14.2", "-0.6", false]].map(([l, v, c, up]) => (
          <div key={l} className="p-3 rounded-xl" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
            <div className="text-[10px] uppercase tracking-widest mb-1" style={{ color: "var(--text-muted)" }}>{l}</div>
            <div className="text-base font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{v}</div>
            <div className="text-xs font-mono" style={{ color: up ? "var(--gain)" : "var(--loss)" }}>{c}%</div>
          </div>
        ))}
      </div>
      <div className="space-y-2">
        {[["RELIANCE", "Bullish breakout", 87], ["HDFC BANK", "Support hold", 79], ["INFY", "Momentum strong", 82]].map(([sym, reason, conf]) => (
          <div key={sym} className="flex items-center justify-between p-3 rounded-xl" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
            <div>
              <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{sym}</span>
              <span className="text-xs ml-2" style={{ color: "var(--text-muted)" }}>{reason}</span>
            </div>
            <span className="text-xs font-mono px-2 py-0.5 rounded-lg" style={{ background: "rgba(16,185,129,0.1)", color: "var(--gain)" }}>{conf}% conf</span>
          </div>
        ))}
      </div>
    </div>
  </div>
);

export default function Landing() {
  return (
    <div className="min-h-screen" style={{ background: "var(--bg)" }} data-testid="landing-page">
      {/* Nav */}
      <nav className="fixed top-0 left-0 right-0 z-50 backdrop-blur-xl border-b" style={{ background: "var(--bg)", opacity: 0.97, borderColor: "var(--border)" }}>
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <APLogo size={32} />
            <span className="text-lg font-semibold tracking-tight" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>AlphaPartner</span>
          </div>
          <div className="flex items-center gap-3">
            <Link to="/login" data-testid="landing-login-btn" className="px-4 py-2 text-sm font-medium rounded-lg transition-all hover:opacity-80" style={{ color: "var(--text-secondary)" }}>
              Sign In
            </Link>
            <Link to="/register" data-testid="landing-signup-btn" className="px-5 py-2 text-sm font-medium rounded-lg transition-all hover:-translate-y-px" style={{ background: "var(--brand)", color: "var(--bg)" }}>
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative pt-32 pb-20 px-6 overflow-hidden">
        <div className="absolute inset-0 opacity-[0.04]" style={{ backgroundImage: "radial-gradient(circle at 1px 1px, var(--text-muted) 1px, transparent 0)", backgroundSize: "32px 32px" }} />
        <div className="max-w-6xl mx-auto relative">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium mb-6" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
              <Zap size={12} /> Powered by Dual AI — Claude & Gemini
            </div>
            <h1 className="text-5xl sm:text-6xl lg:text-7xl font-medium tracking-tight leading-[1.05] mb-6" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
              Your Personal<br />
              <span style={{ color: "var(--ai-accent)" }}>AI Trading</span> Partner
            </h1>
            <p className="text-lg sm:text-xl leading-relaxed mb-8 max-w-xl" style={{ color: "var(--text-secondary)" }}>
              AlphaPartner watches the Indian stock market so you don't have to. AI-powered analysis, real-time alerts, and smart trade execution — all in one platform.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link to="/register" data-testid="hero-cta-btn" className="inline-flex items-center gap-2 px-7 py-3.5 text-base font-medium rounded-xl transition-all hover:-translate-y-px hover:shadow-lg" style={{ background: "var(--brand)", color: "var(--bg)" }}>
                Start Trading Smarter <ArrowRight size={18} />
              </Link>
              <a href="#how-it-works" className="inline-flex items-center gap-2 px-7 py-3.5 text-base font-medium rounded-xl border transition-all hover:-translate-y-px" style={{ borderColor: "var(--border)", color: "var(--text-primary)" }}>
                See How It Works
              </a>
            </div>
          </div>
          <DashboardPreview />
        </div>
      </section>

      {/* Features */}
      <section className="py-20 px-6" style={{ background: "var(--bg-surface)" }}>
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <p className="text-xs uppercase tracking-[0.2em] font-bold mb-3" style={{ color: "var(--ai-accent)" }}>Features</p>
            <h2 className="text-3xl sm:text-4xl font-medium tracking-tight" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
              Everything You Need to Trade Confidently
            </h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {FEATURES.map((f) => (
              <div key={f.title} className="p-6 rounded-2xl border transition-all hover:-translate-y-1" style={{ background: "var(--bg-elevated)", borderColor: "var(--border)" }}>
                <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-4" style={{ background: "var(--ai-accent-soft)" }}>
                  <f.icon size={20} style={{ color: "var(--ai-accent)" }} />
                </div>
                <h3 className="text-base font-semibold mb-2" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>{f.title}</h3>
                <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How It Works */}
      <section id="how-it-works" className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <p className="text-xs uppercase tracking-[0.2em] font-bold mb-3" style={{ color: "var(--gain)" }}>How It Works</p>
            <h2 className="text-3xl sm:text-4xl font-medium tracking-tight" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
              Four Simple Steps
            </h2>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
            {STEPS.map((s) => (
              <div key={s.num}>
                <span className="text-4xl font-bold font-mono" style={{ color: "var(--border)" }}>{s.num}</span>
                <h3 className="text-lg font-semibold mt-3 mb-2" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>{s.title}</h3>
                <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA + Disclaimer */}
      <section className="py-20 px-6" style={{ background: "var(--bg-surface)" }}>
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-3xl sm:text-4xl font-medium tracking-tight mb-4" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
            Ready to Trade Smarter?
          </h2>
          <p className="text-base leading-relaxed mb-8" style={{ color: "var(--text-secondary)" }}>
            Join thousands of Indian traders who use AI to make better decisions. Start with as little as INR 15,000.
          </p>
          <Link to="/register" data-testid="cta-final-btn" className="inline-flex items-center gap-2 px-8 py-4 text-base font-medium rounded-xl transition-all hover:-translate-y-px hover:shadow-lg" style={{ background: "var(--brand)", color: "var(--bg)" }}>
            Create Free Account <ArrowRight size={18} />
          </Link>
          <div className="mt-12 p-5 rounded-xl border flex items-start gap-3 text-left" style={{ borderColor: "var(--border)", background: "var(--bg-elevated)" }}>
            <Shield size={20} className="shrink-0 mt-0.5" style={{ color: "var(--text-muted)" }} />
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--text-muted)" }}>Important Disclaimer</p>
              <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                AlphaPartner is an AI-powered analysis tool. We do not promise or guarantee profits. Stock market investments are subject to market risks. Our AI helps you make informed decisions through data-driven analysis, but all trading decisions are ultimately yours. Past performance does not indicate future results. Please consult a SEBI-registered financial advisor before making investment decisions.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-8 px-6 border-t" style={{ borderColor: "var(--border)" }}>
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <APLogo size={24} />
            <span className="text-sm font-medium" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>AlphaPartner</span>
          </div>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>© 2026 AlphaPartner. AI-Powered Indian Stock Market Analysis. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
