import { useState, useEffect } from "react";
import api from "../../services/api";
import { MessageSquare, Check, X, Send, Settings } from "lucide-react";

export default function WhatsAppPanel() {
  const [status, setStatus] = useState(null);
  const [phone, setPhone] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  useEffect(() => {
    api.get("/whatsapp/status").then(({ data }) => setStatus(data)).catch(() => {});
  }, []);

  const sendTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await api.post("/whatsapp/test");
      setTestResult(data);
    } catch (err) {
      setTestResult({ success: false, message: err.message });
    } finally {
      setTesting(false);
    }
  };

  const savePhone = async () => {
    if (!phone) return;
    try {
      await api.post("/whatsapp/configure", { phone_number: phone });
      setTestResult({ success: true, source: "saved", message: "Phone number saved!" });
    } catch (err) {
      setTestResult({ success: false, message: "Failed to save" });
    }
  };

  const configured = status?.configured;

  return (
    <div data-testid="whatsapp-panel" className="card-premium p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-bold uppercase tracking-[0.15em] flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
          <MessageSquare size={12} style={{ color: configured ? "var(--gain)" : "var(--text-muted)" }} />
          WhatsApp Alerts
        </h3>
        <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg"
          style={{ background: configured ? "rgba(16,185,129,0.08)" : "var(--bg-surface)", color: configured ? "var(--gain)" : "var(--text-muted)" }}>
          {configured ? "ACTIVE" : "SETUP NEEDED"}
        </span>
      </div>

      {configured ? (
        <div className="space-y-3">
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            WhatsApp notifications are active. You'll receive AI alerts for trade entries, stop loss warnings, profit opportunities, and exit reminders.
          </p>
          <button data-testid="whatsapp-test-btn" onClick={sendTest} disabled={testing}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium transition-all"
            style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
            <Send size={12} /> {testing ? "Sending..." : "Send Test Message"}
          </button>
          {testResult && (
            <div className="flex items-center gap-2 text-xs p-2 rounded-lg"
              style={{ background: testResult.success ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)", color: testResult.success ? "var(--gain)" : "var(--loss)" }}>
              {testResult.success ? <Check size={12} /> : <X size={12} />}
              {testResult.source === "simulated" ? "Test sent (simulated mode)" : testResult.success ? "Test sent!" : testResult.message}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Enable WhatsApp alerts to receive real-time AI trading notifications on your phone.
          </p>
          <div className="p-3 rounded-xl" style={{ background: "var(--bg-surface)" }}>
            <p className="text-xs font-semibold mb-2" style={{ color: "var(--text-primary)" }}>Setup Instructions:</p>
            <ol className="text-xs space-y-1 list-decimal list-inside" style={{ color: "var(--text-secondary)" }}>
              <li>Create a Twilio account at twilio.com</li>
              <li>Enable the WhatsApp Sandbox</li>
              <li>Add these to backend .env:</li>
            </ol>
            <div className="mt-2 p-2 rounded-lg font-mono text-[10px]" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>
              TWILIO_ACCOUNT_SID=your_sid<br />
              TWILIO_AUTH_TOKEN=your_token<br />
              TWILIO_WHATSAPP_FROM=whatsapp:+14155238886<br />
              USER_WHATSAPP_TO=whatsapp:+91XXXXXXXXXX
            </div>
          </div>

          <div>
            <label className="text-[10px] font-bold uppercase tracking-widest block mb-1" style={{ color: "var(--text-muted)" }}>Your WhatsApp Number</label>
            <div className="flex gap-2">
              <input data-testid="whatsapp-phone-input" value={phone} onChange={(e) => setPhone(e.target.value)}
                placeholder="+91XXXXXXXXXX"
                className="flex-1 rounded-xl px-3 py-2 text-sm font-mono focus:outline-none"
                style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <button onClick={savePhone} className="px-4 py-2 rounded-xl text-xs font-medium"
                style={{ background: "var(--brand)", color: "var(--bg)" }}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
