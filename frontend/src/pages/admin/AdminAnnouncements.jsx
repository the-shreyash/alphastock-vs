import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Megaphone, Plus, Edit3, Trash2, Info, Wrench, Sparkles, ShieldAlert, Tag } from "lucide-react";
import adminService from "../../services/adminService";

const TYPE_ICONS = { info: Info, maintenance: Wrench, feature: Sparkles, security: ShieldAlert, promotion: Tag };
const TYPE_COLORS = { info: "#6366F1", maintenance: "#F59E0B", feature: "#00D68F", security: "#FF6B6B", promotion: "#EC4899" };

export default function AdminAnnouncements() {
  const [announcements, setAnnouncements] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ title: "", body: "", type: "info", target: "all", status: "active" });

  const load = async () => {
    try { const { data } = await adminService.getAnnouncements(); setAnnouncements(data.announcements || []); } catch { /* */ }
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    if (!form.title) return;
    if (editing) { await adminService.updateAnnouncement(editing, form); }
    else { await adminService.createAnnouncement(form); }
    setShowForm(false); setEditing(null); setForm({ title: "", body: "", type: "info", target: "all", status: "active" }); load();
  };

  const handleEdit = (ann) => {
    setForm({ title: ann.title, body: ann.body, type: ann.type, target: ann.target, status: ann.status });
    setEditing(ann._id); setShowForm(true);
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this announcement?")) return;
    await adminService.deleteAnnouncement(id); load();
  };

  if (loading) return <div className="space-y-6"><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /></div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Announcements</h1>
          <p className="page-subtitle mt-1">Platform-wide notifications and updates</p>
        </div>
        <button onClick={() => { setEditing(null); setForm({ title: "", body: "", type: "info", target: "all", status: "active" }); setShowForm(true); }} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium text-white" style={{ background: "linear-gradient(135deg, #6366F1, #8B5CF6)" }}>
          <Plus size={16} /> New Announcement
        </button>
      </div>

      {announcements.length === 0 ? (
        <div className="text-center py-16 rounded-2xl" style={{ background: "var(--bg-card-glass)", border: "1px solid var(--border)" }}>
          <Megaphone size={40} style={{ color: "var(--text-muted)", margin: "0 auto" }} />
          <p className="text-sm mt-3" style={{ color: "var(--text-muted)" }}>No announcements yet</p>
        </div>
      ) : (
        <div className="space-y-3">
          {announcements.map((ann, i) => {
            const TypeIcon = TYPE_ICONS[ann.type] || Info;
            const color = TYPE_COLORS[ann.type] || "#6B7280";
            return (
              <motion.div
                key={ann._id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
                className="p-5 rounded-2xl transition-all duration-300"
                style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)", boxShadow: "var(--card-shadow)" }}
                onMouseEnter={e => e.currentTarget.style.boxShadow = "var(--card-shadow-hover)"}
                onMouseLeave={e => e.currentTarget.style.boxShadow = "var(--card-shadow)"}
              >
                <div className="flex items-start gap-4">
                  <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{ background: `${color}15` }}>
                    <TypeIcon size={18} style={{ color }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{ann.title}</h4>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize" style={{ background: `${color}15`, color }}>{ann.type}</span>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize" style={{ background: ann.status === "active" ? "rgba(0,214,143,0.1)" : "rgba(107,114,128,0.1)", color: ann.status === "active" ? "#00D68F" : "#6B7280" }}>{ann.status}</span>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>→ {ann.target}</span>
                    </div>
                    <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{ann.body}</p>
                    <span className="text-[10px] mt-1 block" style={{ color: "var(--text-muted)" }}>{new Date(ann.created_at).toLocaleString()}</span>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button onClick={() => handleEdit(ann)} className="p-1.5 rounded-lg transition-all" style={{ color: "var(--text-muted)" }}
                      onMouseEnter={e => { e.currentTarget.style.color = "#6366F1"; e.currentTarget.style.background = "rgba(99,102,241,0.1)"; }}
                      onMouseLeave={e => { e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.background = "transparent"; }}>
                      <Edit3 size={15} />
                    </button>
                    <button onClick={() => handleDelete(ann._id)} className="p-1.5 rounded-lg transition-all" style={{ color: "var(--text-muted)" }}
                      onMouseEnter={e => { e.currentTarget.style.color = "#FF6B6B"; e.currentTarget.style.background = "rgba(255,107,107,0.1)"; }}
                      onMouseLeave={e => { e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.background = "transparent"; }}>
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              </motion.div>
            );
          })}
        </div>
      )}

      {/* Create/Edit Modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center" onClick={() => setShowForm(false)}>
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="w-full max-w-lg p-6 rounded-2xl mx-4" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }} onClick={e => e.stopPropagation()}>
            <h3 className="card-title mb-4">{editing ? "Edit" : "Create"} Announcement</h3>
            <div className="space-y-3">
              <input value={form.title} onChange={e => setForm(p => ({ ...p, title: e.target.value }))} placeholder="Title" className="w-full px-4 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <textarea value={form.body} onChange={e => setForm(p => ({ ...p, body: e.target.value }))} placeholder="Announcement body..." rows={3} className="w-full px-4 py-2.5 rounded-xl text-sm outline-none resize-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <div className="grid grid-cols-3 gap-3">
                <select value={form.type} onChange={e => setForm(p => ({ ...p, type: e.target.value }))} className="px-3 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                  <option value="info">Info</option><option value="maintenance">Maintenance</option><option value="feature">Feature</option><option value="security">Security</option><option value="promotion">Promotion</option>
                </select>
                <select value={form.target} onChange={e => setForm(p => ({ ...p, target: e.target.value }))} className="px-3 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                  <option value="all">All Users</option><option value="free">Free</option><option value="pro">Pro</option><option value="elite">Elite</option><option value="admins">Admins</option>
                </select>
                <select value={form.status} onChange={e => setForm(p => ({ ...p, status: e.target.value }))} className="px-3 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                  <option value="active">Active</option><option value="draft">Draft</option><option value="archived">Archived</option>
                </select>
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setShowForm(false)} className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium" style={{ background: "var(--bg-surface)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
                <button onClick={handleSave} className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium text-white" style={{ background: "linear-gradient(135deg, #6366F1, #8B5CF6)" }}>{editing ? "Update" : "Create"}</button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}
