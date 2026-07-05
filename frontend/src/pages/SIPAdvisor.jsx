import { useState } from "react";
import api from "../services/api";
import { formatCurrency, formatNumber } from "../utils/formatters";
import { Calculator, TrendingUp, Brain, RefreshCw } from "lucide-react";

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
    <div data-testid="sip-advisor-page" className="space-y-4">
      <div>
        <h1 className="text-2xl font-medium text-primary tracking-tight">SIP Advisor</h1>
        <p className="text-xs text-muted">AI-powered SIP recommendations for long-term wealth building</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Form */}
        <div className="glass-card  p-4 space-y-4">
          <h3 className="text-xs text-muted uppercase tracking-widest">Your Investment Profile</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-muted uppercase block mb-1">Monthly SIP (INR)</label>
              <input data-testid="sip-amount-input" type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: Number(e.target.value) })} className="w-full bg-surface  px-2 py-1.5 text-sm text-primary font-mono focus:outline-none focus:border-zinc-600" />
            </div>
            <div>
              <label className="text-[10px] text-muted uppercase block mb-1">Time Horizon (Years)</label>
              <input data-testid="sip-years-input" type="number" value={form.years} onChange={(e) => setForm({ ...form, years: Number(e.target.value) })} className="w-full bg-surface  px-2 py-1.5 text-sm text-primary font-mono focus:outline-none focus:border-zinc-600" />
            </div>
          </div>
          <div>
            <label className="text-[10px] text-muted uppercase block mb-1">Investment Goal</label>
            <select data-testid="sip-goal-select" value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} className="w-full bg-surface  px-2 py-1.5 text-sm text-primary focus:outline-none focus:border-zinc-600">
              <option>Wealth Creation</option>
              <option>Retirement Planning</option>
              <option>Child Education</option>
              <option>Tax Saving (ELSS)</option>
              <option>House Down Payment</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-muted uppercase block mb-1">Risk Tolerance</label>
              <select data-testid="sip-risk-select" value={form.risk} onChange={(e) => setForm({ ...form, risk: e.target.value })} className="w-full bg-surface  px-2 py-1.5 text-sm text-primary focus:outline-none focus:border-zinc-600">
                <option value="conservative">Conservative</option>
                <option value="moderate">Moderate</option>
                <option value="aggressive">Aggressive</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-muted uppercase block mb-1">Age</label>
              <input data-testid="sip-age-input" type="number" value={form.age} onChange={(e) => setForm({ ...form, age: Number(e.target.value) })} className="w-full bg-surface  px-2 py-1.5 text-sm text-primary font-mono focus:outline-none focus:border-zinc-600" />
            </div>
          </div>
          <div className="flex gap-2">
            <button data-testid="sip-calculate-btn" onClick={handleCalculate} className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-zinc-800 text-primary text-xs rounded-xl bg-hover:hover transition-colors">
              <Calculator size={14} /> Calculate
            </button>
            <button data-testid="sip-ai-recommend-btn" onClick={handleAIRecommend} disabled={loading} className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium rounded-xl hover:opacity-90 transition-colors disabled:opacity-50">
              <Brain size={14} /> {loading ? "Analyzing..." : "AI Recommend"}
            </button>
          </div>
        </div>

        {/* Calculator Result */}
        <div className="space-y-4">
          {calcResult && (
            <div data-testid="sip-calc-result" className="glass-card  p-4">
              <h3 className="text-xs text-muted uppercase tracking-widest mb-3 flex items-center gap-2">
                <Calculator size={12} /> SIP Projection
              </h3>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <span className="text-[10px] text-muted uppercase block">Monthly SIP</span>
                  <span className="text-lg font-mono text-primary">{formatCurrency(calcResult.monthly_sip)}</span>
                </div>
                <div>
                  <span className="text-[10px] text-muted uppercase block">Duration</span>
                  <span className="text-lg font-mono text-primary">{calcResult.years} years</span>
                </div>
                <div>
                  <span className="text-[10px] text-muted uppercase block">Total Invested</span>
                  <span className="text-lg font-mono text-primary">{formatCurrency(calcResult.total_invested)}</span>
                </div>
                <div>
                  <span className="text-[10px] text-muted uppercase block">Expected Corpus</span>
                  <span className="text-lg font-mono text-gain">{formatCurrency(calcResult.future_value)}</span>
                </div>
              </div>
              <div className="mt-3 p-2 bg-[#00C805]/5 border border-[#00C805]/20 rounded-xl">
                <span className="text-xs text-gain font-mono">Wealth Gained: {formatCurrency(calcResult.wealth_gained)}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* AI Recommendation */}
      {aiResult && (
        <div data-testid="sip-ai-result" className="glass-card  p-4">
          <h3 className="text-xs text-muted uppercase tracking-widest mb-3 flex items-center gap-2">
            <Brain size={12} className="text-ai" /> AI Fund Recommendations
          </h3>
          <div className="text-sm text-secondary leading-relaxed whitespace-pre-wrap">{aiResult}</div>
        </div>
      )}
    </div>
  );
}
