import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Users, Search, Shield, Ban, Trash2, Gift, UserCheck, UserX, ChevronLeft, ChevronRight } from "lucide-react";
import adminService from "../../services/adminService";

export default function AdminUsers() {
  const [users, setUsers] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [grantModal, setGrantModal] = useState(null);
  const [grantPlan, setGrantPlan] = useState("pro");
  const [grantDays, setGrantDays] = useState(30);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await adminService.getUsers({ page, limit: 15, search, role: roleFilter });
      setUsers(data.users); setTotal(data.total); setPages(data.pages);
    } catch { /* ignore */ }
    setLoading(false);
  }, [page, search, roleFilter]);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  const handleBlock = async (userId) => { await adminService.blockUser(userId); loadUsers(); };
  const handleUnblock = async (userId) => { await adminService.unblockUser(userId); loadUsers(); };
  const handleDelete = async (userId) => {
    if (!window.confirm("Permanently delete this user? This cannot be undone.")) return;
    await adminService.deleteUser(userId); loadUsers();
  };
  const handleGrant = async () => {
    if (!grantModal) return;
    await adminService.grantPlan(grantModal, { plan: grantPlan, duration_days: grantDays });
    setGrantModal(null); loadUsers();
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">User Management</h1>
        <p className="page-subtitle mt-1">{total} registered users</p>
      </div>

      {/* Search & Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
          <input
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1); }}
            placeholder="Search by name or email..."
            className="w-full pl-10 pr-4 py-2.5 rounded-xl text-sm outline-none transition-all"
            style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            onFocus={e => e.target.style.borderColor = "var(--ai-accent)"}
            onBlur={e => e.target.style.borderColor = "var(--border)"}
          />
        </div>
        <select
          value={roleFilter}
          onChange={e => { setRoleFilter(e.target.value); setPage(1); }}
          className="px-4 py-2.5 rounded-xl text-sm outline-none"
          style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
        >
          <option value="">All Roles</option>
          <option value="user">User</option>
          <option value="pro">Pro</option>
          <option value="elite">Elite</option>
          <option value="admin">Admin</option>
          <option value="super_admin">Super Admin</option>
        </select>
      </div>

      {/* Users Table */}
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }}
        className="rounded-2xl overflow-hidden"
        style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}
      >
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["User", "Email", "Role", "Status", "Created", "Actions"].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i}><td colSpan={6} className="px-4 py-4"><div className="h-5 rounded animate-pulse" style={{ background: "var(--border)" }} /></td></tr>
                ))
              ) : users.length === 0 ? (
                <tr><td colSpan={6} className="px-4 py-12 text-center text-sm" style={{ color: "var(--text-muted)" }}>No users found</td></tr>
              ) : (
                users.map(u => (
                  <tr key={u._id} className="transition-colors" style={{ borderBottom: "1px solid var(--border-subtle)" }}
                    onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
                          {u.name?.charAt(0)?.toUpperCase() || "?"}
                        </div>
                        <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{u.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm" style={{ color: "var(--text-secondary)" }}>{u.email}</td>
                    <td className="px-4 py-3"><RoleBadge role={u.role} /></td>
                    <td className="px-4 py-3">
                      {u.blocked ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold" style={{ background: "var(--loss-bg)", color: "var(--loss)" }}>Blocked</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold" style={{ background: "var(--gain-bg)", color: "var(--gain)" }}>Active</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs" style={{ color: "var(--text-muted)" }}>{u.created_at?.split("T")[0] || "—"}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <ActionBtn icon={Gift} tip="Grant Plan" onClick={() => setGrantModal(u._id)} color="#8B5CF6" />
                        {u.blocked ? (
                          <ActionBtn icon={UserCheck} tip="Unblock" onClick={() => handleUnblock(u._id)} color="#00D68F" />
                        ) : (
                          <ActionBtn icon={Ban} tip="Block" onClick={() => handleBlock(u._id)} color="#FF6B6B" />
                        )}
                        <ActionBtn icon={Trash2} tip="Delete" onClick={() => handleDelete(u._id)} color="#FF6B6B" />
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {pages > 1 && (
          <div className="flex items-center justify-between px-4 py-3" style={{ borderTop: "1px solid var(--border)" }}>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>Page {page} of {pages}</span>
            <div className="flex gap-1">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="p-1.5 rounded-lg disabled:opacity-30" style={{ color: "var(--text-secondary)" }}><ChevronLeft size={16} /></button>
              <button disabled={page >= pages} onClick={() => setPage(p => p + 1)} className="p-1.5 rounded-lg disabled:opacity-30" style={{ color: "var(--text-secondary)" }}><ChevronRight size={16} /></button>
            </div>
          </div>
        )}
      </motion.div>

      {/* Grant Plan Modal */}
      {grantModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center" onClick={() => setGrantModal(null)}>
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
            className="w-full max-w-md p-6 rounded-2xl mx-4"
            style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
            onClick={e => e.stopPropagation()}
          >
            <h3 className="card-title mb-4">Grant VIP Access</h3>
            <div className="space-y-4">
              <div>
                <label className="text-xs font-semibold uppercase tracking-wider block mb-2" style={{ color: "var(--text-muted)" }}>Plan</label>
                <select value={grantPlan} onChange={e => setGrantPlan(e.target.value)} className="w-full px-4 py-2.5 rounded-xl text-sm" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                  <option value="free">Free</option>
                  <option value="pro">Pro</option>
                  <option value="elite">Elite</option>
                  <option value="lifetime">Lifetime</option>
                  <option value="developer">Developer</option>
                  <option value="investor">Investor</option>
                  <option value="beta_tester">Beta Tester</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-semibold uppercase tracking-wider block mb-2" style={{ color: "var(--text-muted)" }}>Duration (days)</label>
                <input type="number" value={grantDays} onChange={e => setGrantDays(Number(e.target.value))} className="w-full px-4 py-2.5 rounded-xl text-sm" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setGrantModal(null)} className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium" style={{ background: "var(--bg-surface)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
                <button onClick={handleGrant} className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium text-white" style={{ background: "linear-gradient(135deg, #6366F1, #8B5CF6)" }}>Grant Access</button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}

function RoleBadge({ role }) {
  const colors = { admin: "#6366F1", super_admin: "#EC4899", pro: "#8B5CF6", elite: "#F59E0B", user: "#6B7280", lifetime: "#00D68F" };
  const c = colors[role] || colors.user;
  return <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold capitalize" style={{ background: `${c}18`, color: c }}>{role?.replace("_", " ")}</span>;
}

function ActionBtn({ icon: Icon, tip, onClick, color }) {
  return (
    <button onClick={onClick} title={tip} className="p-1.5 rounded-lg transition-all" style={{ color: "var(--text-muted)" }}
      onMouseEnter={e => { e.currentTarget.style.color = color; e.currentTarget.style.background = `${color}15`; }}
      onMouseLeave={e => { e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.background = "transparent"; }}>
      <Icon size={15} />
    </button>
  );
}
