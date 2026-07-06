import { useState } from "react";
import { motion } from "framer-motion";
import api from "../services/api";
import { formatCurrency, formatNumber } from "../utils/formatters";
import { Calculator, TrendingUp, Brain, RefreshCw } from "lucide-react";

const fadeUp = {
  initial: { opacity: 0, y: 16 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-60px" },
};

const inputStyle = { background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" };
const inputClass = "w-full rounded-xl px-3 py-2.5 text-sm text-primary focus:outline-none focus:border-[var(--ai-accent)] transition-colors";

export default function SIPAdvisor() {
  const [form, setForm] = useState({ amount: 5000, goal: "Wealth Creation", years: 10, risk: "moderate", age: 30, tax_bracket: "30%" });
  const [calcResult, setCalcResult] = useState(null);
  const [aiResult, setAiResult] = useState("");
  const [loading, setLoading] = useState(false);
  const [calcLoading, setCalcLoading] = useState(false);

  const handleCalculate = async () => {
    setCalcLoading(true);
    try {
      const { data } = await api.get(`/sip/calculator?amount=${form.amount}&years=${form.years}&rate=12`);
      setCalcResult(data);
    } catch (err) {
      console.error(err);
    } finally {
      setCalcLoading(false);
    }
  };

  const handleAIRecommend = async () => {
    setLoading(true);
    try {
      const { data } = await api.post("/sip/recommend", form);
      setAiResult(data.recommendation);
    } catch (err) {
      console.error(err);
      setAiResult("Unable to get AI recommendation. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div data-testid="sip-advisor-page" className="space-y-6">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
        <h1 className="page-title">SIP Advisor</h1>
        <p className="page-subtitle mt-1">AI-powered SIP recommendations for long-term wealth building</p>
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Form */}
        <motion.div className="glass-card p-6 space-y-4" {...fadeUp} transition={{ duration: 0.4 }}>
          <h3 className="eyebrow">Your Investment Profile</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="stat-label block mb-1.5">Monthly SIP (INR)</label>
              <input data-testid="sip-amount-input" type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: Number(e.target.value) })} className={`${inputClass} font-mono`} style={inputStyle} />
            </div>
            <div>
              <label className="stat-label block mb-1.5">Time Horizon (Years)</label>
              <input data-testid="sip-years-input" type="number" value={form.years} onChange={(e) => setForm({ ...form, years: Number(e.target.value) })} className={`${inputClass} font-mono`} style={inputStyle} />
            </div>
          </div>
          <div>
            <label className="stat-label block mb-1.5">Investment Goal</label>
            <select data-testid="sip-goal-select" value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} className={inputClass} style={inputStyle}>
              <option>Wealth Creation</option>
              <option>Retirement Planning</option>
              <option>Child Education</option>
              <option>Tax Saving (ELSS)</option>
              <option>House Down Payment</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="stat-label block mb-1.5">Risk Tolerance</label>
              <select data-testid="sip-risk-select" value={form.risk} onChange={(e) => setForm({ ...form, risk: e.target.value })} className={inputClass} style={inputStyle}>
                <option value="conservative">Conservative</option>
                <option value="moderate">Moderate</option>
                <option value="aggressive">Aggressive</option>
              </select>
            </div>
            <div>
              <label className="stat-label block mb-1.5">Age</label>
              <input data-testid="sip-age-input" type="number" value={form.age} onChange={(e) => setForm({ ...form, age: Number(e.target.value) })} className={`${inputClass} font-mono`} style={inputStyle} />
            </div>
          </div>
          <div className="flex gap-3 pt-1">
            <button data-testid="sip-calculate-btn" onClick={handleCalculate} className="btn-secondary flex-1">
              <Calculator size={16} /> Calculate
            </button>
            <button data-testid="sip-ai-recommend-btn" onClick={handleAIRecommend} disabled={loading} className="btn-primary flex-1">
              <Brain size={16} /> {loading ? "Analyzing..." : "AI Recommend"}
            </button>
          </div>
        </motion.div>

        {/* Calculator Result */}
        <div className="space-y-4">
          {calcResult && (
            <motion.div data-testid="sip-calc-result" className="glass-card p-6" {...fadeUp} transition={{ duration: 0.4 }}>
              <h3 className="eyebrow mb-4 flex items-center gap-2">
                <Calculator size={13} /> SIP Projection
              </h3>
              <div className="grid grid-cols-2 gap-5">
                <div>
                  <span className="stat-label block mb-1">Monthly SIP</span>
                  <span className="stat-value">{formatCurrency(calcResult.monthly_sip)}</span>
                </div>
                <div>
                  <span className="stat-label block mb-1">Duration</span>
                  <span className="stat-value">{calcResult.years} yrs</span>
                </div>
                <div>
                  <span className="stat-label block mb-1">Total Invested</span>
                  <span className="stat-value">{formatCurrency(calcResult.total_invested)}</span>
                </div>
                <div>
                  <span className="stat-label block mb-1">Expected Corpus</span>
                  <span className="stat-value" style={{ color: "var(--gain)" }}>{formatCurrency(calcResult.future_value)}</span>
                </div>
              </div>
              <div className="mt-4 p-3 rounded-xl" style={{ background: "var(--gain-bg)", border: "1px solid var(--gain-bg)" }}>
                <span className="text-sm text-gain font-mono font-semibold">Wealth Gained: {formatCurrency(calcResult.wealth_gained)}</span>
              </div>
            </motion.div>
          )}
        </div>
      </div>

      {/* AI Recommendation */}
      {aiResult && (
        <motion.div data-testid="sip-ai-result" className="glass-card p-6" {...fadeUp} transition={{ duration: 0.4 }}>
          <h3 className="eyebrow mb-4 flex items-center gap-2">
            <Brain size={13} className="text-ai" /> AI Fund Recommendations
          </h3>
          <div className="body-text whitespace-pre-wrap">{aiResult}</div>
        </motion.div>
      )}
    </div>
  );
}
