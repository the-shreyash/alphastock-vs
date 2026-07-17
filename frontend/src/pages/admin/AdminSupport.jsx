import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { LifeBuoy, MessageCircle, Plus, Send, ChevronLeft, ChevronRight } from "lucide-react";
import adminService from "../../services/adminService";

const PRIORITY_COLORS = { high: "#FF6B6B", medium: "#F59E0B", low: "#00D68F" };
const STATUS_COLORS = { open: "#6366F1", in_progress: "#F59E0B", resolved: "#00D68F", closed: "#6B7280" };

export default function AdminSupport() {
  const [tickets, setTickets] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [reply, setReply] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [newTicket, setNewTicket] = useState({ subject: "", message: "", priority: "medium" });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await adminService.getTickets({ page, limit: 15, status: statusFilter });
      setTickets(data.tickets); setTotal(data.total); setPages(data.pages);
    } catch { /* */ }
    setLoading(false);
  }, [page, statusFilter]);

  useEffect(() => { load(); }, [load]);

  const handleReply = async () => {
    if (!selected || !reply.trim()) return;
    await adminService.updateTicket(selected._id, { reply });
    setReply(""); load();
    setSelected(prev => prev ? { ...prev, messages: [...(prev.messages || []), { from: "admin", text: reply, at: new Date().toISOString() }] } : null);
  };

  const handleStatusChange = async (ticketId, status) => {
    await adminService.updateTicket(ticketId, { status });
    load();
  };

  const handleCreate = async () => {
    await adminService.createTicket(newTicket);
    setShowCreate(false); setNewTicket({ subject: "", message: "", priority: "medium" }); load();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Support Center</h1>
          <p className="page-subtitle mt-1">{total} tickets</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium text-white" style={{ background: "linear-gradient(135deg, #6366F1, #8B5CF6)" }}>
          <Plus size={16} /> New Ticket
        </button>
      </div>

      {/* Filter */}
      <div className="flex gap-2">
        {["", "open", "in_progress", "resolved", "closed"].map(s => (
          <button key={s} onClick={() => { setStatusFilter(s); setPage(1); }} className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
            style={{ background: statusFilter === s ? "var(--ai-accent-soft)" : "transparent", color: statusFilter === s ? "var(--ai-accent)" : "var(--text-muted)", border: `1px solid ${statusFilter === s ? "var(--ai-accent)" : "var(--border)"}` }}>
            {s || "All"}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Ticket List */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="lg:col-span-2 rounded-2xl overflow-hidden" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
          {loading ? <div className="p-6 space-y-3">{Array.from({length:4}).map((_,i) => <div key={i} className="h-16 rounded-xl animate-pulse" style={{background:"var(--border)"}}/>)}</div> : tickets.length === 0 ? (
            <div className="p-12 text-center"><LifeBuoy size={32} style={{ color: "var(--text-muted)", margin: "0 auto" }} /><p className="text-sm mt-3" style={{ color: "var(--text-muted)" }}>No tickets found</p></div>
          ) : (
            <div className="divide-y" style={{ borderColor: "var(--border-subtle)" }}>
              {tickets.map(t => (
                <div key={t._id} className="px-4 py-3 cursor-pointer transition-colors" onClick={() => setSelected(t)}
                  style={{ background: selected?._id === t._id ? "var(--hover)" : "transparent" }}
                  onMouseEnter={e => { if (selected?._id !== t._id) e.currentTarget.style.background = "var(--hover)"; }}
                  onMouseLeave={e => { if (selected?._id !== t._id) e.currentTarget.style.background = "transparent"; }}>
                  <div className="flex items-center justify-between">
                    <h4 className="text-sm font-medium truncate" style={{ color: "var(--text-primary)" }}>{t.subject || "No subject"}</h4>
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold" style={{ background: `${PRIORITY_COLORS[t.priority] || "#6B7280"}15`, color: PRIORITY_COLORS[t.priority] || "#6B7280" }}>{t.priority}</span>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize" style={{ background: `${STATUS_COLORS[t.status] || "#6B7280"}15`, color: STATUS_COLORS[t.status] || "#6B7280" }}>{t.status?.replace("_", " ")}</span>
                    </div>
                  </div>
                  <div className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>{new Date(t.created_at).toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}
          {pages > 1 && <div className="flex items-center justify-between px-4 py-3" style={{ borderTop: "1px solid var(--border)" }}><span className="text-xs" style={{ color: "var(--text-muted)" }}>Page {page}/{pages}</span><div className="flex gap-1"><button disabled={page<=1} onClick={()=>setPage(p=>p-1)} className="p-1.5 rounded-lg disabled:opacity-30" style={{color:"var(--text-secondary)"}}><ChevronLeft size={16}/></button><button disabled={page>=pages} onClick={()=>setPage(p=>p+1)} className="p-1.5 rounded-lg disabled:opacity-30" style={{color:"var(--text-secondary)"}}><ChevronRight size={16}/></button></div></div>}
        </motion.div>

        {/* Detail Panel */}
        <motion.div initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} className="rounded-2xl overflow-hidden flex flex-col" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)", minHeight: 400 }}>
          {selected ? (
            <>
              <div className="p-4" style={{ borderBottom: "1px solid var(--border)" }}>
                <h4 className="card-title text-sm">{selected.subject || "Ticket"}</h4>
                <div className="flex gap-2 mt-2">
                  {["open", "in_progress", "resolved", "closed"].map(s => (
                    <button key={s} onClick={() => handleStatusChange(selected._id, s)} className="px-2 py-1 rounded text-[10px] font-semibold capitalize transition-all"
                      style={{ background: selected.status === s ? `${STATUS_COLORS[s]}25` : "transparent", color: STATUS_COLORS[s], border: `1px solid ${STATUS_COLORS[s]}30` }}>
                      {s.replace("_", " ")}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-4 space-y-3" style={{ maxHeight: 300 }}>
                {(selected.messages || []).map((m, i) => (
                  <div key={i} className={`flex ${m.from === "admin" ? "justify-end" : "justify-start"}`}>
                    <div className="max-w-[85%] px-3 py-2 rounded-xl text-xs" style={{ background: m.from === "admin" ? "var(--ai-accent-soft)" : "var(--bg-surface)", color: "var(--text-primary)" }}>
                      {m.text}
                      <div className="text-[9px] mt-1" style={{ color: "var(--text-muted)" }}>{new Date(m.at).toLocaleTimeString()}</div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="p-3 flex gap-2" style={{ borderTop: "1px solid var(--border)" }}>
                <input value={reply} onChange={e => setReply(e.target.value)} placeholder="Reply..." className="flex-1 px-3 py-2 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} onKeyDown={e => e.key === "Enter" && handleReply()} />
                <button onClick={handleReply} className="p-2 rounded-xl" style={{ background: "linear-gradient(135deg, #6366F1, #8B5CF6)" }}><Send size={16} className="text-white" /></button>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center p-6">
              <div className="text-center"><MessageCircle size={28} style={{ color: "var(--text-muted)", margin: "0 auto" }} /><p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>Select a ticket to view</p></div>
            </div>
          )}
        </motion.div>
      </div>

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center" onClick={() => setShowCreate(false)}>
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="w-full max-w-md p-6 rounded-2xl mx-4" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }} onClick={e => e.stopPropagation()}>
            <h3 className="card-title mb-4">Create Ticket</h3>
            <div className="space-y-3">
              <input value={newTicket.subject} onChange={e => setNewTicket(p => ({ ...p, subject: e.target.value }))} placeholder="Subject" className="w-full px-4 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <textarea value={newTicket.message} onChange={e => setNewTicket(p => ({ ...p, message: e.target.value }))} placeholder="Message..." rows={3} className="w-full px-4 py-2.5 rounded-xl text-sm outline-none resize-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <select value={newTicket.priority} onChange={e => setNewTicket(p => ({ ...p, priority: e.target.value }))} className="w-full px-4 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                <option value="low">Low Priority</option><option value="medium">Medium Priority</option><option value="high">High Priority</option>
              </select>
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
