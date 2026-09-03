import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { motion } from "framer-motion";
import api from "../services/api";
import brokerService, { brokerErrorMessage } from "../services/brokerService";
import { useAuth } from "../context/AuthContext";
import { Save, User, Shield, ShieldAlert, Bell, Link2, ExternalLink, Database, Check, X, Wifi, MessageSquare, Mail, Workflow, Clock, RefreshCw, Unplug, Zap } from "lucide-react";

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
  const [preferredBroker, setPreferredBroker] = useState("");
  const [platformSaving, setPlatformSaving] = useState(false);
  const [platformMessage, setPlatformMessage] = useState(null);
  const [stopping, setStopping] = useState(false);
  const [stopResult, setStopResult] = useState(null);
  const [brokerStatus, setBrokerStatus] = useState(null);
  const [dataSources, setDataSources] = useState(null);
  const [brokerMessage, setBrokerMessage] = useState(null);
  const [brokerBusy, setBrokerBusy] = useState({});
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
      setPreferredBroker(user.preferred_broker || "");
    }
    refreshBrokerStatus();
    api.get("/data-sources").then(({ data }) => setDataSources(data)).catch(() => {});
    api.get("/webhooks/logs").then(({ data }) => setWebhookLogs(data)).catch(() => {});

    // Handle broker OAuth redirect callbacks.
    // Legacy Zerodha format: ?zerodha=connected|failed|cancelled
    // Unified format:        ?broker=<name>&status=connected|failed|cancelled
    const zerodhaParam = searchParams.get("zerodha");
    const brokerParam = searchParams.get("broker");
    const statusParam = zerodhaParam || searchParams.get("status");
    const brokerName = brokerParam || (zerodhaParam ? "zerodha" : null);
    if (brokerName && statusParam) {
      const label = brokerName.charAt(0).toUpperCase() + brokerName.slice(1);
      if (statusParam === "connected") {
        setBrokerMessage({ type: "success", text: `${label} connected successfully! Live account data is now syncing.` });
        refreshBrokerStatus();
      } else if (statusParam === "failed") {
        const detail = searchParams.get("error");
        setBrokerMessage({ type: "error", text: detail ? `${label} login failed: ${detail}` : `${label} login failed. Please try again.` });
      } else if (statusParam === "cancelled") {
        setBrokerMessage({ type: "error", text: `${label} login was cancelled.` });
      }
      ["zerodha", "broker", "status", "error"].forEach((k) => searchParams.delete(k));
      setSearchParams(searchParams, { replace: true });
    }
  }, [user]);

  const refreshBrokerStatus = () => {
    brokerService.status().then(setBrokerStatus).catch(() => {});
  };

  const handleSave = async () => {
    setSaving(true); setSaved(false);
    try {
      await api.put("/settings", { ...form, notification_prefs: notifPrefs });
      setSaved(true); checkAuth();
      setTimeout(() => setSaved(false), 3000);
    } catch (err) { console.error(err); }
    finally { setSaving(false); }
  };

  // The user's chosen trading platform — saved immediately on selection.
  // There is deliberately NO default: quick/one-click trading stays disabled
  // until the user makes an explicit choice here.
  const selectPlatform = async (broker) => {
    setPlatformSaving(true);
    setPlatformMessage(null);
    try {
      await api.put("/settings", { preferred_broker: broker });
      setPreferredBroker(broker);
      setPlatformMessage({
        type: "success",
        text: broker
          ? `${(brokerStatus?.[broker]?.display_name) || broker} is now your trading platform.`
          : "Trading platform cleared — one-click trading is disabled.",
      });
      checkAuth();
    } catch (err) {
      setPlatformMessage({ type: "error", text: err.response?.data?.detail || "Could not save your platform choice." });
    } finally {
      setPlatformSaving(false);
    }
  };

  const connectBroker = async (broker) => {
    try {
      const data = await brokerService.getLoginUrl(broker);
      if (data.url) {
        window.location.href = data.url;
      } else {
        setBrokerMessage({ type: "error", text: data.message || `${broker} API keys not configured` });
      }
    } catch (err) {
      setBrokerMessage({ type: "error", text: brokerErrorMessage(err, `Failed to get ${broker} login URL`) });
    }
  };

  const disconnectBroker = async (broker) => {
    if (!window.confirm(`Disconnect ${broker}? Live sync and trading through this account will stop until you reconnect.`)) return;
    setBrokerBusy((b) => ({ ...b, [broker]: true }));
    try {
      await brokerService.disconnect(broker);
      setBrokerMessage({ type: "success", text: `${broker.charAt(0).toUpperCase() + broker.slice(1)} disconnected.` });
      refreshBrokerStatus();
    } catch (err) {
      setBrokerMessage({ type: "error", text: brokerErrorMessage(err, `Failed to disconnect ${broker}`) });
    } finally {
      setBrokerBusy((b) => ({ ...b, [broker]: false }));
    }
  };

  const syncBroker = async (broker) => {
    setBrokerBusy((b) => ({ ...b, [broker]: true }));
    try {
      const result = await brokerService.sync(broker);
      const s = result?.summary;
      setBrokerMessage({
        type: "success",
        text: s ? `Portfolio synced: ${s.holdings_count} holdings, ${s.positions_count} positions.` : "Portfolio synced.",
      });
      refreshBrokerStatus();
    } catch (err) {
      setBrokerMessage({ type: "error", text: brokerErrorMessage(err, `Failed to sync ${broker} portfolio`) });
    } finally {
      setBrokerBusy((b) => ({ ...b, [broker]: false }));
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

      {/* Broker Accounts — Top Priority */}
      <motion.div className="glass-card p-6" {...fadeUp} transition={{ duration: 0.4 }}>
        <h3 className="eyebrow flex items-center gap-2 mb-4">
          <Link2 size={13} /> Broker Accounts
        </h3>

        {brokerMessage && (
          <div className="flex items-center gap-2 p-3 rounded-xl mb-3" style={{
            background: brokerMessage.type === "success" ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)",
            color: brokerMessage.type === "success" ? "var(--gain)" : "var(--loss)"
          }}>
            {brokerMessage.type === "success" ? <Check size={14} /> : <X size={14} />}
            <span className="text-sm">{brokerMessage.text}</span>
          </div>
        )}

        {brokerStatus && (
          <div className="space-y-5">
            {Object.values(brokerStatus).map((b) => (
              <div key={b.broker} data-testid={`broker-card-${b.broker}`} className="space-y-3 pb-4 border-b last:border-0 last:pb-0" style={{ borderColor: "var(--border)" }}>
                <div className="flex items-center gap-3">
                  <div className={`w-3 h-3 rounded-full ${b.connected ? "animate-pulse" : ""}`}
                    style={{ background: b.connected ? "var(--gain)" : b.session_expired ? "var(--loss)" : b.configured ? "#F59E0B" : "var(--text-muted)" }} />
                  <div className="min-w-0">
                    <span className="text-sm font-semibold block" style={{ color: "var(--text-primary)" }}>{b.display_name}</span>
                    <span className="caption">{b.message}</span>
                  </div>
                  <div className="flex items-center gap-2 ml-auto shrink-0">
                    {b.streaming && (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg flex items-center gap-1"
                        style={{ background: "rgba(16,185,129,0.08)", color: "var(--gain)" }}>
                        <Wifi size={10} /> STREAM
                      </span>
                    )}
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg"
                      style={{ background: b.connected ? "rgba(16,185,129,0.08)" : "var(--bg-surface)", color: b.connected ? "var(--gain)" : "var(--text-muted)" }}>
                      {b.mode?.toUpperCase()}
                    </span>
                  </div>
                </div>

                {b.connected ? (
                  <div className="flex flex-col sm:flex-row gap-2">
                    <div className="p-3 rounded-xl flex items-center gap-2 flex-1" style={{ background: "rgba(16,185,129,0.05)" }}>
                      <Wifi size={14} style={{ color: "var(--gain)" }} />
                      <span className="text-sm" style={{ color: "var(--gain)" }}>
                        Live trading active{b.last_sync ? ` · synced ${new Date(b.last_sync).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}` : ""}
                      </span>
                    </div>
                    <button data-testid={`broker-sync-btn-${b.broker}`} onClick={() => syncBroker(b.broker)} disabled={brokerBusy[b.broker]}
                      className="btn-secondary shrink-0">
                      <RefreshCw size={14} className={brokerBusy[b.broker] ? "animate-spin" : ""} /> Sync Now
                    </button>
                    <button data-testid={`broker-disconnect-btn-${b.broker}`} onClick={() => disconnectBroker(b.broker)} disabled={brokerBusy[b.broker]}
                      className="btn-secondary shrink-0" style={{ color: "var(--loss)" }}>
                      <Unplug size={14} /> Disconnect
                    </button>
                  </div>
                ) : (
                  <button data-testid={`broker-login-btn-${b.broker}`} onClick={() => connectBroker(b.broker)}
                    disabled={!b.configured} className="btn-primary btn-block" style={!b.configured ? { opacity: 0.5, cursor: "not-allowed" } : {}}>
                    <ExternalLink size={16} /> {b.session_expired ? `Reconnect ${b.display_name}` : `Connect ${b.display_name} Account`}
                  </button>
                )}
              </div>
            ))}
            <p className="caption leading-relaxed">
              Connecting redirects you to your broker's secure login page — credentials never touch StockAssist. Broker
              sessions expire daily per exchange rules (Zerodha ~6:00 AM, Upstox ~3:30 AM IST) and will ask to reconnect.
            </p>
          </div>
        )}
      </motion.div>

      {/* Trading Platform — the user's explicit choice, no default */}
      <motion.div className="glass-card p-6" {...fadeUp} transition={{ duration: 0.4, delay: 0.03 }}>
        <h3 className="eyebrow flex items-center gap-2 mb-1">
          <Zap size={13} /> Trading Platform
        </h3>
        <p className="caption leading-relaxed mb-4">
          Choose which broker executes your trades (AI quick trades and the default in the New Trade form).
          Nothing is selected by default — one-click trading stays off until you pick a connected platform.
        </p>

        {platformMessage && (
          <div className="flex items-center gap-2 p-3 rounded-xl mb-3" style={{
            background: platformMessage.type === "success" ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)",
            color: platformMessage.type === "success" ? "var(--gain)" : "var(--loss)"
          }}>
            {platformMessage.type === "success" ? <Check size={14} /> : <X size={14} />}
            <span className="text-sm">{platformMessage.text}</span>
          </div>
        )}

        <div className="space-y-2">
          {brokerStatus && Object.values(brokerStatus).map((b) => {
            const selected = preferredBroker === b.broker;
            const selectable = b.connected;
            return (
              <button
                key={b.broker}
                type="button"
                data-testid={`platform-option-${b.broker}`}
                onClick={() => selectable && !selected && selectPlatform(b.broker)}
                disabled={!selectable || platformSaving}
                className="w-full flex items-center gap-3 p-3 rounded-xl text-left transition-all"
                style={{
                  background: selected ? "rgba(16,185,129,0.08)" : "var(--bg-surface)",
                  border: `1px solid ${selected ? "var(--gain)" : "var(--border)"}`,
                  opacity: selectable ? 1 : 0.55,
                  cursor: selectable ? "pointer" : "not-allowed",
                }}
              >
                <span className="w-4 h-4 rounded-full flex items-center justify-center shrink-0"
                  style={{ border: `2px solid ${selected ? "var(--gain)" : "var(--text-muted)"}` }}>
                  {selected && <span className="w-2 h-2 rounded-full" style={{ background: "var(--gain)" }} />}
                </span>
                <div className="min-w-0">
                  <span className="text-sm font-semibold block" style={{ color: "var(--text-primary)" }}>{b.display_name}</span>
                  <span className="caption">
                    {b.connected ? "Connected — ready to trade"
                      : b.session_expired ? "Session expired — reconnect above to select"
                      : b.configured ? "Not connected — connect above to select"
                      : "API keys not configured"}
                  </span>
                </div>
                {selected && (
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg ml-auto shrink-0"
                    style={{ background: "rgba(16,185,129,0.12)", color: "var(--gain)" }}>
                    ACTIVE PLATFORM
                  </span>
                )}
              </button>
            );
          })}

          {/* Explicit opt-out */}
          <button
            type="button"
            data-testid="platform-option-none"
            onClick={() => preferredBroker && selectPlatform("")}
            disabled={platformSaving}
            className="w-full flex items-center gap-3 p-3 rounded-xl text-left transition-all"
            style={{
              background: !preferredBroker ? "var(--hover)" : "var(--bg-surface)",
              border: `1px solid ${!preferredBroker ? "var(--text-muted)" : "var(--border)"}`,
            }}
          >
            <span className="w-4 h-4 rounded-full flex items-center justify-center shrink-0"
              style={{ border: `2px solid ${!preferredBroker ? "var(--text-primary)" : "var(--text-muted)"}` }}>
              {!preferredBroker && <span className="w-2 h-2 rounded-full" style={{ background: "var(--text-primary)" }} />}
            </span>
            <div>
              <span className="text-sm font-semibold block" style={{ color: "var(--text-primary)" }}>No platform — track only</span>
              <span className="caption">Trades are recorded and monitored, but no live orders are placed.</span>
            </div>
          </button>
        </div>
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
              // D6.1 / S2: `dataSources.zerodha` used to be whichever user's live
              // Zerodha session the server found first. `brokers` is this
              // account's own per-broker status, keyed by broker name.
              { label: "Zerodha (Trading)", status: dataSources.brokers?.zerodha?.mode?.toUpperCase(), active: dataSources.brokers?.zerodha?.connected },
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
