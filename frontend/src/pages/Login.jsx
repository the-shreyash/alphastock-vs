import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion } from "framer-motion";
import { useAuth } from "../context/AuthContext";
import { Eye, EyeOff, ArrowRight } from "lucide-react";
import { resolveApiErrorMessage } from "../utils/apiError";
import { startGoogleLogin } from "../services/googleAuth";

/* ─────────────────────────────────────────────
   LOGIN SPECIFIC CSS
   ───────────────────────────────────────────── */
const LOGIN_CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@300;400;500;600&display=swap');

  .sa-auth * { box-sizing: border-box; }

  /* Theme variables initialization */
  .sa-auth {
    --sa-bg: #F8F9FC;
    --sa-surface: #FFFFFF;
    --sa-surface-elevated: #F1F3F9;
    --sa-border: rgba(0, 0, 0, 0.055);
    --sa-text-primary: #1A1D29;
    --sa-text-secondary: #5E6278;
    --sa-text-muted: #9CA3B8;
    --sa-accent: #6366F1;
    --sa-accent-soft: rgba(99, 102, 241, 0.06);
    --sa-gain: #00C48C;
    --sa-loss: #FF6B6B;
  }

  [data-theme="dark"] .sa-auth {
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
    --sa-loss: #f87171;
  }

  .sa-auth-left {
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 3rem;
    background: var(--sa-surface);
    border-right: 1px solid var(--sa-border);
    transition: background-color 0.3s, border-color 0.3s;
  }
  .sa-auth-headline {
    font-family: 'Instrument Serif', serif;
    font-size: clamp(2.5rem, 5vw, 4.5rem);
    font-weight: 400; line-height: 1.1;
    letter-spacing: -0.02em; color: var(--sa-text-primary);
    margin-bottom: 1.5rem;
  }
  .sa-auth-headline em {
    font-style: italic; color: var(--sa-text-secondary);
  }
  .sa-auth-sub {
    font-family: 'Inter', sans-serif;
    font-size: 15px; color: var(--sa-text-secondary);
    line-height: 1.65; max-width: 380px;
    margin-bottom: 3rem;
  }
  .sa-auth-card {
    background: var(--sa-surface);
    border: 1px solid var(--sa-border);
    border-radius: 20px;
    padding: 2.5rem;
    width: 100%; max-width: 400px;
    transition: background-color 0.3s, border-color 0.3s;
  }
  .sa-auth-input {
    width: 100%;
    border-radius: 12px;
    padding: 12px 16px;
    font-family: 'Inter', sans-serif;
    font-size: 14px;
    background: var(--sa-surface-elevated);
    border: 1px solid var(--sa-border);
    color: var(--sa-text-primary);
    transition: all 0.2s;
  }
  .sa-auth-input:focus {
    outline: none;
    border-color: var(--sa-text-secondary);
  }
  .sa-auth-label {
    font-family: 'Inter', sans-serif;
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.05em; text-transform: uppercase;
    color: var(--sa-text-muted);
    margin-bottom: 8px; display: block;
  }
  .sa-auth-btn-primary {
    width: 100%;
    font-family: 'Inter', sans-serif;
    font-size: 14px; font-weight: 500;
    padding: 12px; border-radius: 12px;
    background: var(--sa-text-primary);
    color: var(--sa-bg); border: none;
    cursor: pointer; display: flex; align-items: center;
    justify-content: center; gap: 8px;
    transition: all 0.2s;
  }
  .sa-auth-btn-primary:hover {
    opacity: 0.9; transform: translateY(-1px);
  }
  .sa-auth-btn-google {
    width: 100%;
    font-family: 'Inter', sans-serif;
    font-size: 14px; font-weight: 500;
    padding: 12px; border-radius: 12px;
    background: var(--sa-surface);
    color: var(--sa-text-primary);
    border: 1px solid var(--sa-border);
    cursor: pointer; display: flex; align-items: center;
    justify-content: center; gap: 10px;
    transition: all 0.2s;
  }
  .sa-auth-btn-google:hover {
    background: var(--sa-surface-elevated);
  }
  .sa-auth-nav-logo {
    font-family: 'Inter', sans-serif;
    font-weight: 600; font-size: 14px;
    color: var(--sa-text-primary);
    display: inline-flex; align-items: center;
    margin-bottom: 40px;
  }
`;

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (err) {
      setError(resolveApiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleLogin = async () => {
    setError("");
    try {
      await startGoogleLogin();
    } catch (err) {
      setError(resolveApiErrorMessage(err));
    }
  };

  return (
    <div
      className="min-h-screen flex sa-auth"
      style={{ background: "var(--sa-bg)" }}
      data-testid="login-page"
    >
      <style>{LOGIN_CSS}</style>

      {/* Left - Branding */}
      <div className="hidden lg:flex lg:w-1/2 sa-auth-left">
        <motion.div
          className="max-w-md"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <div className="sa-auth-nav-logo">StockAssist</div>
          <h1 className="sa-auth-headline">
            See the market clearly.<br />
            <em>Act with context.</em>
          </h1>
          <p className="sa-auth-sub">
            Your Personal AI Trading Advisor. Dual AI debate system powered by Claude & Gemini
            for smarter decisions in Indian stock markets.
          </p>
          <div className="flex gap-12">
            {[
              { n: "50+", l: "Stocks Scanned" },
              { n: "2 AI", l: "Models Debating" },
              { n: "24/7", l: "Monitoring" }
            ].map((s) => (
              <div key={s.l}>
                <div
                  style={{
                    fontFamily: "JetBrains Mono, monospace",
                    fontSize: 22,
                    fontWeight: 600,
                    color: "var(--sa-text-primary)"
                  }}
                >
                  {s.n}
                </div>
                <div
                  style={{
                    fontFamily: "Inter, sans-serif",
                    fontSize: 11,
                    color: "var(--sa-text-muted)",
                    marginTop: 4
                  }}
                >
                  {s.l}
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      </div>

      {/* Right - Form */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-8">
        <motion.div
          className="sa-auth-card"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.1 }}
        >
          <div className="lg:hidden sa-auth-nav-logo" style={{ marginBottom: 32 }}>
            StockAssist
          </div>

          <div
            style={{
              fontFamily: "Inter, sans-serif",
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              color: "var(--sa-text-muted)",
              marginBottom: 4
            }}
          >
            Welcome back
          </div>
          <h2
            style={{
              fontFamily: "Instrument Serif, serif",
              fontSize: "2.2rem",
              fontWeight: 400,
              color: "var(--sa-text-primary)",
              marginBottom: 32,
              lineHeight: 1
            }}
          >
            Sign In
          </h2>

          {error && (
            <div
              data-testid="login-error"
              role="alert"
              style={{
                background: "rgba(248,113,113,0.08)",
                color: "var(--sa-loss)",
                border: "1px solid rgba(248,113,113,0.15)",
                padding: "12px 16px",
                borderRadius: 12,
                fontSize: 13,
                marginBottom: 20,
                fontFamily: "Inter, sans-serif"
              }}
            >
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="sa-auth-label" htmlFor="login-email">Email</label>
              <input
                id="login-email"
                data-testid="login-email-input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="sa-auth-input"
                placeholder="trader@example.com"
              />
            </div>
            <div>
              <label className="sa-auth-label" htmlFor="login-password">Password</label>
              <div className="relative">
                <input
                  id="login-password"
                  data-testid="login-password-input"
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="sa-auth-input"
                  style={{ paddingRight: 40 }}
                  placeholder="Enter password"
                />
                <button
                  type="button"
                  onClick={() => setShowPw(!showPw)}
                  aria-label={showPw ? "Hide password" : "Show password"}
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--sa-text-muted)",
                    cursor: "pointer",
                    padding: 0
                  }}
                >
                  {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
            <button
              data-testid="login-submit-btn"
              type="submit"
              disabled={loading}
              className="sa-auth-btn-primary"
              style={{ marginTop: 24 }}
            >
              {loading ? "Signing in..." : "Sign In"} {!loading && <ArrowRight size={15} />}
            </button>
          </form>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              margin: "24px 0"
            }}
          >
            <div style={{ flex: 1, height: 1, background: "var(--sa-border)" }} />
            <span
              style={{
                fontFamily: "Inter, sans-serif",
                fontSize: 9,
                fontWeight: 600,
                color: "var(--sa-text-muted)",
                letterSpacing: "0.1em",
                textTransform: "uppercase"
              }}
            >
              or
            </span>
            <div style={{ flex: 1, height: 1, background: "var(--sa-border)" }} />
          </div>

          <button
            data-testid="google-login-btn"
            type="button"
            onClick={handleGoogleLogin}
            className="sa-auth-btn-google"
          >
            <svg width="15" height="15" viewBox="0 0 24 24">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
            </svg>
            Continue with Google
          </button>

          <p
            style={{
              fontFamily: "Inter, sans-serif",
              fontSize: 13,
              color: "var(--sa-text-muted)",
              marginTop: 24,
              textAlign: "center"
            }}
          >
            No account?{" "}
            <Link
              to="/register"
              style={{ color: "var(--sa-text-primary)", fontWeight: 500, textDecoration: "none" }}
              data-testid="register-link"
              className="hover:underline"
            >
              Create one
            </Link>
          </p>
        </motion.div>
      </div>
    </div>
  );
}
