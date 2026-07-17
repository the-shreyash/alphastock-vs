import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Flag, Plus, ToggleLeft, ToggleRight } from "lucide-react";
import adminService from "../../services/adminService";

export default function AdminFeatureFlags() {
  const [flags, setFlags] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newFlag, setNewFlag] = useState({ name: "", description: "", enabled: false, target_plans: ["all"] });

  const load = async () => {
    try { const { data } = await adminService.getFeatureFlags(); setFlags(data.flags || []); } catch { /* */ }
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const handleToggle = async (flag) => {
    await adminService.updateFeatureFlag(flag._id, { enabled: !flag.enabled });
    load();
  };

  const handleCreate = async () => {
    if (!newFlag.name) return;
    await adminService.createFeatureFlag(newFlag);
    setShowCreate(false); setNewFlag({ name: "", description: "", enabled: false, target_plans: ["all"] }); load();
  };

  if (loading) return <div className="space-y-6"><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /><div className="space-y-3">{Array.from({length:5}).map((_,i)=><div key={i} className="h-20 rounded-2xl animate-pulse" style={{background:"var(--bg-surface)"}}/>)}</div></div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Feature Flags</h1>
          <p className="page-subtitle mt-1">Control feature availability without deployment</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium text-white" style={{ background: "linear-gradient(135deg, #6366F1, #8B5CF6)" }}>
          <Plus size={16} /> New Flag
        </button>
      </div>

      <div className="space-y-3">
        {flags.map((flag, i) => (
          <motion.div
            key={flag._id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
            className="p-5 rounded-2xl flex items-center justify-between gap-4 group transition-all duration-300"
            style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)", boxShadow: "var(--card-shadow)" }}
            onMouseEnter={e => { e.currentTarget.style.boxShadow = "var(--card-shadow-hover)"; }}
            onMouseLeave={e => { e.currentTarget.style.boxShadow = "var(--card-shadow)"; }}
          >
            <div className="flex items-center gap-4 min-w-0">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{ background: flag.enabled ? "rgba(0,214,143,0.12)" : "rgba(107,114,128,0.1)" }}>
                <Flag size={18} style={{ color: flag.enabled ? "#00D68F" : "#6B7280" }} />
              </div>
              <div className="min-w-0">
                <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{flag.name}</h4>
                <p className="text-xs mt-0.5 truncate" style={{ color: "var(--text-muted)" }}>{flag.description}</p>
                <div className="flex items-center gap-2 mt-1.5">
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded" style={{ background: "var(--border)", color: "var(--text-muted)" }}>{flag.key}</span>
                  {(flag.target_plans || []).map(p => (
                    <span key={p} className="text-[10px] px-1.5 py-0.5 rounded capitalize" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>{p}</span>
                  ))}
                </div>
              </div>
            </div>
            <button onClick={() => handleToggle(flag)} className="shrink-0 p-1 rounded-lg transition-transform hover:scale-110">
              {flag.enabled ? (
                <ToggleRight size={32} style={{ color: "#00D68F" }} />
              ) : (
                <ToggleLeft size={32} style={{ color: "#6B7280" }} />
              )}
            </button>
          </motion.div>
        ))}
      </div>

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center" onClick={() => setShowCreate(false)}>
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="w-full max-w-md p-6 rounded-2xl mx-4" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }} onClick={e => e.stopPropagation()}>
            <h3 className="card-title mb-4">Create Feature Flag</h3>
            <div className="space-y-3">
              <input value={newFlag.name} onChange={e => setNewFlag(p => ({ ...p, name: e.target.value }))} placeholder="Flag name" className="w-full px-4 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <input value={newFlag.description} onChange={e => setNewFlag(p => ({ ...p, description: e.target.value }))} placeholder="Description" className="w-full px-4 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <label className="flex items-center gap-2 text-sm cursor-pointer" style={{ color: "var(--text-secondary)" }}>
                <input type="checkbox" checked={newFlag.enabled} onChange={e => setNewFlag(p => ({ ...p, enabled: e.target.checked }))} className="rounded" /> Enabled by default
              </label>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setShowCreate(false)} className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium" style={{ background: "var(--bg-surface)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
                <button onClick={handleCreate} className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium text-white" style={{ background: "linear-gradient(135deg, #6366F1, #8B5CF6)" }}>Create</button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}
