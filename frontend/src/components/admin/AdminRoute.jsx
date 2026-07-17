import { Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";

/**
 * AdminRoute guard — only allows users with role admin/super_admin.
 * Non-admin users are redirected to /dashboard.
 */
export default function AdminRoute({ children }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg)" }}>
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 rounded-full animate-spin" style={{ borderColor: "var(--border)", borderTopColor: "var(--ai-accent)" }} />
          <span className="text-xs font-mono uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Loading</span>
        </div>
      </div>
    );
  }

  if (!user || user === false) return <Navigate to="/login" replace />;
  if (!["admin", "super_admin"].includes(user.role)) return <Navigate to="/dashboard" replace />;

  return children;
}
