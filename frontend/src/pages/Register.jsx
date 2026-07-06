import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion } from "framer-motion";
import { useAuth } from "../context/AuthContext";
import { ArrowRight, Eye, EyeOff } from "lucide-react";
import APLogo from "../components/APLogo";

function formatApiError(detail) {
  if (detail == null) return "Something went wrong.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((e) => e?.msg || JSON.stringify(e)).join(" ");
  return String(detail);
}

export default function Register() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { register } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault(); setError("");
    if (password.length < 6) { setError("Password must be at least 6 characters"); return; }
    setLoading(true);
    try { await register(name, email, password); navigate("/dashboard"); }
    catch (err) { setError(formatApiError(err.response?.data?.detail) || err.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen flex" style={{ background: "var(--bg)" }} data-testid="register-page">
      <div className="hidden lg:flex lg:w-1/2 relative items-center justify-center p-12" style={{ background: "var(--bg-surface)" }}>
        <motion.div className="relative z-10 max-w-md"
          initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
          <APLogo size={56} className="mb-8" />
          <h1 className="page-title mb-4" style={{ fontSize: "var(--fs-display)" }}>AlphaPartner</h1>
          <p className="body-lg">
            Join the future of AI-powered Indian stock trading. Dual AI debate system for smarter decisions.
          </p>
        </motion.div>
      </div>
      <div className="flex-1 flex items-center justify-center p-6 sm:p-8">
        <motion.div className="w-full max-w-sm glass-card p-8"
          initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, delay: 0.1 }}>
          <p className="eyebrow mb-1">Get Started</p>
          <h2 className="page-title mb-8">Create Account</h2>
          {error && <div data-testid="register-error" className="mb-4 p-3 rounded-xl text-sm" style={{ background: "rgba(244,63,94,0.08)", color: "var(--loss)" }}>{error}</div>}
          <form onSubmit={handleSubmit} className="space-y-4">
            {[{ id: "register-name-input", label: "Name", type: "text", val: name, set: setName, ph: "Your name" },
            { id: "register-email-input", label: "Email", type: "email", val: email, set: setEmail, ph: "trader@example.com" }
            ].map((f) => (
              <div key={f.id}>
                <label className="stat-label block mb-1.5">{f.label}</label>
                <input data-testid={f.id} type={f.type} value={f.val} onChange={(e) => f.set(e.target.value)} required placeholder={f.ph}
                  className="w-full rounded-xl px-4 py-3 text-sm focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              </div>
            ))}
            <div>
              <label className="stat-label block mb-1.5">Password</label>
              <div className="relative">
                <input data-testid="register-password-input" type={showPw ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} required placeholder="Min 6 characters"
                  className="w-full rounded-xl px-4 py-3 text-sm focus:outline-none pr-10" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }}>
                  {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
            <button data-testid="register-submit-btn" type="submit" disabled={loading} className="btn-primary btn-lg btn-block">
              {loading ? "Creating..." : "Create Account"} {!loading && <ArrowRight size={16} />}
            </button>
          </form>
          <div className="mt-6 flex items-center gap-3">
            <div className="flex-1 h-px" style={{ background: "var(--border)" }} />
            <span className="caption uppercase tracking-widest">or</span>
            <div className="flex-1 h-px" style={{ background: "var(--border)" }} />
          </div>
          <button data-testid="google-register-btn" type="button" onClick={() => {
            window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?client_id=${encodeURIComponent(process.env.REACT_APP_GOOGLE_CLIENT_ID || '')}&redirect_uri=${encodeURIComponent(window.location.origin + '/auth/google/callback')}&response_type=code&scope=openid%20email%20profile&prompt=select_account`;
          }} className="btn-ghost btn-lg btn-block mt-4">
            <svg width="16" height="16" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" /><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" /><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" /><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" /></svg>
            Continue with Google
          </button>
          <p className="text-sm mt-6 text-center" style={{ color: "var(--text-muted)" }}>
            Already have an account? <Link to="/login" className="font-medium hover:underline" style={{ color: "var(--text-primary)" }} data-testid="login-link">Sign in</Link>
          </p>
        </motion.div>
      </div>
    </div>
  );
}
