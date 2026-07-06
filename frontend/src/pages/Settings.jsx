import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { motion } from "framer-motion";
import api from "../services/api";
import { useAuth } from "../context/AuthContext";
import { Save, User, Shield, ShieldAlert, Bell, Link2, ExternalLink, Database, Check, X, Wifi, MessageSquare, Mail, Workflow, Clock } from "lucide-react";

const fadeUp = {
  initial: { opacity: 0, y: 16 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-60px" },
};

export default function SettingsPage() {
  const { user, checkAuth } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [form, setForm] = useState({ name: "", capital: 100000, risk_level: "moderate", max_daily_loss: 5000, max_trades_per_day: 3, telegram_chat_id: "" });
  const [notifPrefs, setNotifPrefs] = useState({ trade_alerts: true, portfolio_alerts: true, exit_reminder: true, email_alerts: false, telegram_alerts: false });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stopResult, setStopResult] = useState(null);
  const [zerodhaStatus, setZerodhaStatus] = useState(null);
  const [dataSources, setDataSources] = useState(null);
  const [zerodhaMessage, setZerodhaMessage] = useState(null);
  const [webhookLogs, setWebhookLogs] = useState(null);

  useEffect(() => {
    if (user) {
      setForm({
        name: user.name || "",
        capital: user.capital || 100000,
        risk_level: user.risk_level || "moderate",
        max_daily_loss: user.max_daily_loss || 5000,
        max_trades_per_day: user.max_trades_per_day || 3,
        telegram_chat_id: user.telegram_chat_id || "",
      });
      if (user.notification_prefs) {
        setNotifPrefs(prev => ({ ...prev, ...user.notification_prefs }));
      }
    }
    api.get("/zerodha/status").then(({ data }) => setZerodhaStatus(data)).catch(() => {});
    api.get("/data-sources").then(({ data }) => setDataSources(data)).catch(() => {});
    api.get("/webhooks/logs").then(({ data }) => setWebhookLogs(data)).catch(() => {});

    // Handle Zerodha redirect callback
    const zerodhaParam = searchParams.get("zerodha");
    if (zerodhaParam === "connected") {
      setZerodhaMessage({ type: "success", text: "Zerodha connected successfully! Live data is now flowing." });
      api.get("/zerodha/status").then(({ data }) => setZerodhaStatus(data)).catch(() => {});
      searchParams.delete("zerodha");
      setSearchParams(searchParams, { replace: true });
    } else if (zerodhaParam === "failed") {
      const detail = searchParams.get("error");
      setZerodhaMessage({ type: "error", text: detail ? `Zerodha login failed: ${detail}` : "Zerodha login failed. Please try again." });
      searchParams.delete("zerodha");
      searchParams.delete("error");
      setSearchParams(searchParams, { replace: true });
    } else if (zerodhaParam === "cancelled") {
      setZerodhaMessage({ type: "error", text: "Zerodha login was cancelled." });
      searchParams.delete("zerodha");
      setSearchParams(searchParams, { replace: true });
    }
  }, [user]);

  const handleSave = async () => {
    setSaving(true); setSaved(false);
    try {
      await api.put("/settings", { ...form, notification_prefs: notifPrefs });
      setSaved(true); checkAuth();
      setTimeout(() => setSaved(false), 3000);
    } catch (err) { console.error(err); }
    finally { setSaving(false); }
  };

  const connectZerodha = async () => {
    try {
      const { data } = await api.get("/zerodha/login-url");
      if (data.url) {
        window.location.href = data.url;
      } else {
        setZerodhaMessage({ type: "error", text: data.message || "Zerodha API key not configured" });
      }
    } catch (err) {
      setZerodhaMessage({ type: "error", text: "Failed to get Zerodha login URL" });
    }
  };

  const handleEmergencyStop = async () => {
    if (!window.confirm("WARNING: This will instantly cancel all pending orders and liquidate ALL active positions at market price. Are you sure you want to trigger Emergency Stop?")) {
      return;
    }
    setStopping(true);
    setStopResult(null);
    try {
      const { data } = await api.post("/zerodha/emergency-stop");
      setStopResult({ type: "success", text: data.message || "Emergency stop executed successfully." });
    } catch (err) {
      console.error(err);
      const errMsg = err.response?.data?.detail || "Failed to execute emergency stop.";
      setStopResult({ type: "error", text: errMsg });
    } finally {
      setStopping(false);
    }
  };

  const inputStyle = { background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" };

  return (
    <div data-testid="settings-page" className="space-y-6 max-w-3xl">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
        <h1 className="page-title">Settings</h1>
        <p className="page-subtitle mt-1">Configure your trading preferences and integrations</p>
      </motion.div>

      {/* Zerodha Connection — Top Priority */}
      <motion.div className="glass-card p-6" {...fadeUp} transition={{ duration: 0.4 }}>
        <h3 className="eyebrow flex items-center gap-2 mb-4">
          <Link2 size={13} /> Zerodha Kite Connect
        </h3>

        {zerodhaMessage && (
          <div className="flex items-center gap-2 p-3 rounded-xl mb-3" style={{
            background: zerodhaMessage.type === "success" ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)",
            color: zerodhaMessage.type === "success" ? "var(--gain)" : "var(--loss)"
          }}>
            {zerodhaMessage.type === "success" ? <Check size={14} /> : <X size={14} />}
            <span className="text-sm">{zerodhaMessage.text}</span>
          </div>
        )}

        {zerodhaStatus && (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <div className={`w-3 h-3 rounded-full ${zerodhaStatus.connected ? "animate-pulse" : ""}`}
                style={{ background: zerodhaStatus.connected ? "var(--gain)" : zerodhaStatus.configured ? "#F59E0B" : "var(--text-muted)" }} />
              <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{zerodhaStatus.message}</span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg ml-auto"
                style={{ background: zerodhaStatus.connected ? "rgba(16,185,129,0.08)" : "var(--bg-surface)", color: zerodhaStatus.connected ? "var(--gain)" : "var(--text-muted)" }}>
                {zerodhaStatus.mode?.toUpperCase()}
              </span>
            </div>

            {zerodhaStatus.connected ? (
              <div className="p-3 rounded-xl flex items-center gap-2" style={{ background: "rgba(16,185,129,0.05)" }}>
                <Wifi size={14} style={{ color: "var(--gain)" }} />
                <span className="text-sm" style={{ color: "var(--gain)" }}>Live trading active. Your Zerodha account data is synced.</span>
              </div>
            ) : (
              <button data-testid="zerodha-login-btn" onClick={connectZerodha} className="btn-primary btn-block">
                <ExternalLink size={16} /> Connect Zerodha Account
              </button>
            )}

            {!zerodhaStatus.connected && (
              <p className="caption leading-relaxed">
                Clicking "Connect" will redirect you to Zerodha's secure login page. After login, you'll be redirected back here and your account data will sync automatically.
              </p>
            )}
          </div>
        )}
      </motion.div>

      {/* Profile */}
      <motion.div className="glass-card p-6 space-y-3" {...fadeUp} transition={{ duration: 0.4, delay: 0.05 }}>
        <h3 className="eyebrow flex items-center gap-2"><User size={13} /> Profile</h3>
        <div>
          <label className="stat-label block mb-1.5">Name</label>
          <input data-testid="settings-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none" style={inputStyle} />
        </div>
        <div>
          <label className="stat-label block mb-1.5">Email</label>
          <input value={user?.email || ""} disabled className="w-full rounded-xl px-3 py-2.5 text-sm cursor-not-allowed" style={{ ...inputStyle, opacity: 0.5 }} />
        </div>
      </motion.div>

      {/* Risk */}
      <motion.div className="glass-card p-6 space-y-3" {...fadeUp} transition={{ duration: 0.4, delay: 0.1 }}>
        <h3 className="eyebrow flex items-center gap-2"><Shield size={13} /> Risk Management</h3>
        <div className="grid grid-cols-2 gap-3">
          {[
            { id: "settings-capital-input", label: "Trading Capital (INR)", key: "capital", type: "number" },
            { id: "settings-maxloss-input", label: "Max Daily Loss (INR)", key: "max_daily_loss", type: "number" },
            { id: "settings-maxtrades-input", label: "Max Trades/Day", key: "max_trades_per_day", type: "number" },
          ].map((f) => (
            <div key={f.id}>
              <label className="stat-label block mb-1.5">{f.label}</label>
              <input data-testid={f.id} type={f.type} value={form[f.key]} onChange={(e) => setForm({ ...form, [f.key]: f.type === 'number' ? Number(e.target.value) : e.target.value })}
                className="w-full rounded-xl px-3 py-2.5 text-sm font-mono focus:outline-none" style={inputStyle} />
            </div>
          ))}
          <div>
            <label className="stat-label block mb-1.5">Risk Level</label>
            <select data-testid="settings-risk-select" value={form.risk_level} onChange={(e) => setForm({ ...form, risk_level: e.target.value })}
              className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none" style={inputStyle}>
              <option value="conservative">Conservative</option>
              <option value="moderate">Moderate</option>
              <option value="aggressive">Aggressive</option>
            </select>
          </div>
        </div>
      </motion.div>

      {/* Notifications — Personal Only */}
      <motion.div className="glass-card p-6 space-y-3" {...fadeUp} transition={{ duration: 0.4, delay: 0.15 }}>
        <h3 className="eyebrow flex items-center gap-2"><Bell size={13} /> Notifications</h3>
        <p className="body-text">Only alerts relevant to your trades and portfolio. No spam.</p>
        {[
          { key: "trade_alerts", label: "Trade Alerts", desc: "Entry confirmations, SL/target hits" },
          { key: "portfolio_alerts", label: "Portfolio Monitoring", desc: "AI detects risk or opportunity in your holdings" },
          { key: "exit_reminder", label: "Exit Reminder", desc: "3:10 PM reminder to close intraday positions" },
          { key: "email_alerts", label: "Email Notifications", desc: "Receive alerts via email (configure SendGrid/SMTP in backend)" },
          { key: "telegram_alerts", label: "Telegram Notifications", desc: "Receive real-time alerts via Telegram bot" },
        ].map(({ key, label, desc }) => (
          <label key={key} className="flex items-center justify-between py-2 cursor-pointer">
            <div>
              <span className="text-sm font-medium block" style={{ color: "var(--text-primary)" }}>{label}</span>
              <span className="caption">{desc}</span>
            </div>
            <button data-testid={`notif-toggle-${key}`} onClick={() => setNotifPrefs({ ...notifPrefs, [key]: !notifPrefs[key] })}
              className="w-10 h-5 rounded-full relative transition-all"
              style={{ background: notifPrefs[key] ? "var(--gain)" : "var(--border)" }}>
              <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform" style={{ left: notifPrefs[key] ? "22px" : "2px" }} />
            </button>
          </label>
        ))}
        {notifPrefs.telegram_alerts && (
          <div className="pt-2 border-t border-dashed transition-all" style={{ borderColor: "var(--border)" }}>
            <label className="stat-label block mb-1.5">
              Telegram Chat ID
            </label>
            <input
              data-testid="settings-telegram-chat-id-input"
              type="text"
              placeholder="e.g. 123456789"
              value={form.telegram_chat_id || ""}
              onChange={(e) => setForm({ ...form, telegram_chat_id: e.target.value })}
              className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none font-mono"
              style={inputStyle}
            />
            <p className="caption mt-1.5">
              To get your chat ID, start a chat with your Telegram Bot and send <code>/start</code>.
            </p>
          </div>
        )}
      </motion.div>

      {/* Data Sources */}
      <motion.div className="glass-card p-6" {...fadeUp} transition={{ duration: 0.4, delay: 0.2 }}>
        <h3 className="eyebrow flex items-center gap-2 mb-3"><Database size={13} /> Connected Services</h3>
        {dataSources && (
          <div className="space-y-2">
            {[
              { label: "Yahoo Finance (Market Data)", status: "LIVE", active: true },
              { label: "Alpha Vantage (Technical)", status: dataSources.alpha_vantage?.mode?.toUpperCase(), active: dataSources.alpha_vantage?.configured },
              { label: "Zerodha (Trading)", status: dataSources.zerodha?.mode?.toUpperCase(), active: dataSources.zerodha?.connected },
              { label: "AI (Claude + Gemini)", status: dataSources.ai?.configured ? "ACTIVE" : "OFF", active: dataSources.ai?.configured },
              { label: "Gemini Direct", status: dataSources.gemini_direct?.mode?.toUpperCase(), active: dataSources.gemini_direct?.configured },
              { label: "WhatsApp (Twilio)", status: dataSources.whatsapp?.mode?.toUpperCase(), active: dataSources.whatsapp?.configured },
              { label: "Email (SendGrid/SMTP)", status: dataSources.email?.mode?.toUpperCase(), active: dataSources.email?.configured },
              { label: "Telegram (Bot)", status: dataSources.telegram?.mode?.toUpperCase(), active: dataSources.telegram?.configured },
            ].map((s) => (
              <div key={s.label} className="flex items-center justify-between py-1.5 border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                <span className="text-sm" style={{ color: "var(--text-secondary)" }}>{s.label}</span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg"
                  style={{ background: s.active ? "rgba(16,185,129,0.08)" : "var(--bg-surface)", color: s.active ? "var(--gain)" : "var(--text-muted)" }}>
                  {s.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </motion.div>

      {/* n8n Automation */}
      <motion.div data-testid="n8n-automation-panel" className="glass-card p-6" {...fadeUp} transition={{ duration: 0.4, delay: 0.25 }}>
        <h3 className="eyebrow flex items-center gap-2 mb-1">
          <Workflow size={13} /> n8n Automation
        </h3>
        <p className="body-text mb-3">
          Scheduled workflows that trigger market scans, summaries, and digests via n8n. Shows the last run of each webhook.
        </p>
        <div className="space-y-2">
          {[
            { key: "morning_scan", label: "Morning Scan", schedule: "08:55 IST · Mon–Fri" },
            { key: "evening_summary", label: "Evening Summary", schedule: "15:35 IST · Mon–Fri" },
            { key: "weekly_review", label: "Weekly Review", schedule: "Sun 10:00 IST" },
            { key: "news_digest", label: "News Digest", schedule: "09:30 & 13:00 IST · Mon–Fri" },
          ].map((wf) => {
            const log = webhookLogs?.[wf.key];
            const lastRun = log ? new Date(log.triggered_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : null;
            const ok = log?.status === "success";
            return (
              <div key={wf.key} data-testid={`n8n-workflow-${wf.key}`} className="flex items-center justify-between gap-3 py-1.5 border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                <div className="min-w-0">
                  <span className="text-sm block" style={{ color: "var(--text-secondary)" }}>{wf.label}</span>
                  <span className="caption">{wf.schedule}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[10px] font-mono flex items-center gap-1" style={{ color: "var(--text-muted)" }}>
                    <Clock size={10} /> {lastRun || "Never run"}
                  </span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg"
                    style={{
                      background: log ? (ok ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)") : "var(--bg-surface)",
                      color: log ? (ok ? "var(--gain)" : "var(--loss)") : "var(--text-muted)",
                    }}>
                    {log ? (ok ? "OK" : "ERROR") : "IDLE"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </motion.div>

      {/* Emergency Controls */}
      <motion.div className="glass-card p-6 border border-red-500/20 space-y-4" {...fadeUp} transition={{ duration: 0.4, delay: 0.3 }}>
        <h3 className="eyebrow flex items-center gap-2" style={{ color: "var(--loss)" }}>
          <ShieldAlert size={15} /> Emergency Controls
        </h3>

        {stopResult && (
          <div className="flex items-start gap-2 p-3 rounded-xl text-sm" style={{
            background: stopResult.type === "success" ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)",
            color: stopResult.type === "success" ? "var(--gain)" : "var(--loss)"
          }}>
            {stopResult.type === "success" ? <Check size={14} className="mt-0.5" /> : <X size={14} className="mt-0.5" />}
            <span>{stopResult.text}</span>
          </div>
        )}

        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <span className="text-sm font-medium block" style={{ color: "var(--text-primary)" }}>Emergency Liquidate & Halt</span>
            <span className="caption">
              Cancels all pending orders and closes all open positions at market price immediately.
            </span>
          </div>
          <button
            data-testid="emergency-stop-btn"
            onClick={handleEmergencyStop}
            disabled={stopping}
            className="btn-lg shrink-0 text-white"
            style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8,
              fontFamily: "Outfit, sans-serif", fontWeight: 700, letterSpacing: "0.03em", textTransform: "uppercase",
              background: "linear-gradient(135deg, #EF4444 0%, #B91C1C 100%)",
              boxShadow: "0 4px 14px 0 rgba(239, 68, 68, 0.4)",
              transition: "all 0.25s cubic-bezier(0.16, 1, 0.3, 1)",
            }}
          >
            {stopping ? "Processing..." : "ACTIVATE EMERGENCY STOP"}
          </button>
        </div>
      </motion.div>

      {/* Save */}
      <button data-testid="settings-save-btn" onClick={handleSave} disabled={saving} className="btn-primary btn-lg">
        <Save size={16} /> {saving ? "Saving..." : saved ? "Saved!" : "Save Settings"}
      </button>
    </div>
  );
}
