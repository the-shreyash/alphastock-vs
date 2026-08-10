import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion } from "framer-motion";
import { useAuth } from "../context/AuthContext";
import { Eye, EyeOff, ArrowRight } from "lucide-react";
import APLogo from "../components/APLogo";
import { startGoogleLogin } from "../services/googleAuth";
import { resolveApiErrorMessage } from "../utils/apiError";

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
    <div className="min-h-screen flex" style={{ background: "var(--bg)" }} data-testid="login-page">
      {/* Left - Branding */}
      <div className="hidden lg:flex lg:w-1/2 relative items-center justify-center p-12" style={{ background: "var(--bg-surface)" }}>
        <motion.div className="relative z-10 max-w-md"
          initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
          <APLogo size={56} className="mb-8" />
          <h1 className="page-title mb-4" style={{ fontSize: "var(--fs-display)" }}>
            AlphaPartner
          </h1>
          <p className="body-lg">
            Your Personal AI Trading Banker. Dual AI debate system powered by Claude & Gemini for smarter decisions in Indian stock markets.
          </p>
          <div className="mt-8 flex gap-8">
            {[{ n: "50+", l: "Stocks Scanned" }, { n: "2 AI", l: "Models Debating" }, { n: "24/7", l: "Monitoring" }].map((s) => (
              <div key={s.l}>
                <div className="text-2xl font-semibold font-mono" style={{ color: "var(--text-primary)" }}>{s.n}</div>
                <div className="caption">{s.l}</div>
              </div>
            ))}
          </div>
        </motion.div>
      </div>

      {/* Right - Form */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-8">
        <motion.div className="w-full max-w-sm glass-card p-8"
          initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, delay: 0.1 }}>
          <div className="lg:hidden mb-8 flex items-center gap-3">
            <APLogo size={40} />
            <span className="card-title">AlphaPartner</span>
          </div>

          <p className="eyebrow mb-1">Welcome back</p>
          <h2 className="page-title mb-8">Sign In</h2>

          {error && (
            <div data-testid="login-error" role="alert" className="mb-4 p-3 rounded-xl text-sm" style={{ background: "rgba(244,63,94,0.08)", color: "var(--loss)", border: "1px solid rgba(244,63,94,0.2)" }}>
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="stat-label block mb-1.5" htmlFor="login-email">Email</label>
              <input id="login-email" data-testid="login-email-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
                className="w-full rounded-xl px-4 py-3 text-sm focus:outline-none transition-all"
                style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                placeholder="trader@example.com" />
            </div>
            <div>
              <label className="stat-label block mb-1.5" htmlFor="login-password">Password</label>
              <div className="relative">
                <input id="login-password" data-testid="login-password-input" type={showPw ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} required
                  className="w-full rounded-xl px-4 py-3 text-sm focus:outline-none transition-all pr-10"
                  style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  placeholder="Enter password" />
                <button type="button" onClick={() => setShowPw(!showPw)} aria-label={showPw ? "Hide password" : "Show password"} className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }}>
                  {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
            <button data-testid="login-submit-btn" type="submit" disabled={loading} className="btn-primary btn-lg btn-block">
              {loading ? "Signing in..." : "Sign In"} {!loading && <ArrowRight size={16} />}
            </button>
          </form>

          <div className="mt-6 flex items-center gap-3">
            <div className="flex-1 h-px" style={{ background: "var(--border)" }} />
            <span className="caption uppercase tracking-widest">or</span>
            <div className="flex-1 h-px" style={{ background: "var(--border)" }} />
          </div>

          <button data-testid="google-login-btn" type="button" onClick={handleGoogleLogin} className="btn-ghost btn-lg btn-block mt-4">
            <svg width="16" height="16" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" /><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" /><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" /><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" /></svg>
            Continue with Google
          </button>

          <p className="text-sm mt-6 text-center" style={{ color: "var(--text-muted)" }}>
            No account? <Link to="/register" className="font-medium hover:underline" style={{ color: "var(--text-primary)" }} data-testid="register-link">Create one</Link>
          </p>
        </motion.div>
      </div>
    </div>
  );
}
