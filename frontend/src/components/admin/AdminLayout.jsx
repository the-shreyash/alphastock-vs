import { useState, useEffect } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  LayoutDashboard, Users, CreditCard, Brain, Wifi, BarChart3,
  ScrollText, LifeBuoy, Flag, Megaphone, Activity, Settings,
  ArrowLeft, Shield, ChevronRight, Menu, X
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const ADMIN_NAV = [
  { to: "/admin/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/admin/users", icon: Users, label: "Users" },
  { to: "/admin/payments", icon: CreditCard, label: "Payments" },
  { type: "divider" },
  { to: "/admin/ai", icon: Brain, label: "AI Monitoring" },
  { to: "/admin/apis", icon: Wifi, label: "API Health" },
  { type: "divider" },
  { to: "/admin/analytics", icon: BarChart3, label: "Analytics" },
  { to: "/admin/logs", icon: ScrollText, label: "Audit Logs" },
  { type: "divider" },
  { to: "/admin/support", icon: LifeBuoy, label: "Support" },
  { to: "/admin/feature-flags", icon: Flag, label: "Feature Flags" },
  { to: "/admin/announcements", icon: Megaphone, label: "Announcements" },
  { type: "divider" },
  { to: "/admin/system-health", icon: Activity, label: "System Health" },
];

const SIDEBAR_W = 260;
const SIDEBAR_COLLAPSED_W = 68;

export default function AdminLayout() {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isDesktop, setIsDesktop] = useState(window.innerWidth >= 1024);

  useEffect(() => { setMobileOpen(false); }, [location.pathname]);
  useEffect(() => {
    const h = () => { const d = window.innerWidth >= 1024; setIsDesktop(d); if (d) setMobileOpen(false); };
    window.addEventListener("resize", h);
    return () => window.removeEventListener("resize", h);
  }, []);

  const sidebarWidth = isDesktop ? (collapsed ? SIDEBAR_COLLAPSED_W : SIDEBAR_W) : SIDEBAR_W;

  return (
    <div className="min-h-screen" style={{ background: "var(--bg)" }}>
      {/* Desktop sidebar */}
      {isDesktop && (
        <motion.aside
          initial={false}
          animate={{ width: sidebarWidth }}
          transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
          className="fixed left-0 top-0 h-screen z-40 flex flex-col overflow-hidden"
          style={{
            background: "linear-gradient(180deg, #0F1128 0%, #0A0C1A 100%)",
            borderRight: "1px solid rgba(99, 102, 241, 0.12)",
          }}
        >
          <SidebarContent collapsed={collapsed} setCollapsed={setCollapsed} navigate={navigate} user={user} />
        </motion.aside>
      )}

      {/* Mobile sidebar overlay */}
      <AnimatePresence>
        {mobileOpen && !isDesktop && (
          <>
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40"
              onClick={() => setMobileOpen(false)}
            />
            <motion.aside
              initial={{ x: -SIDEBAR_W }} animate={{ x: 0 }} exit={{ x: -SIDEBAR_W }}
              transition={{ type: "spring", damping: 28, stiffness: 320 }}
              className="fixed left-0 top-0 h-screen z-50 flex flex-col"
              style={{ width: SIDEBAR_W, background: "linear-gradient(180deg, #0F1128 0%, #0A0C1A 100%)" }}
            >
              <SidebarContent collapsed={false} setCollapsed={() => {}} navigate={navigate} user={user} onClose={() => setMobileOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      {/* Main content */}
      <div className="transition-all duration-300" style={{ marginLeft: isDesktop ? sidebarWidth : 0 }}>
        {/* Top bar */}
        <header className="h-16 flex items-center justify-between px-6 sticky top-0 z-30" style={{
          background: "var(--nav-bg)",
          backdropFilter: "blur(20px) saturate(1.8)",
          borderBottom: "1px solid var(--border)",
        }}>
          <div className="flex items-center gap-3">
            {!isDesktop && (
              <button onClick={() => setMobileOpen(true)} className="p-2 rounded-lg" style={{ color: "var(--text-secondary)" }}>
                <Menu size={20} />
              </button>
            )}
            <div className="flex items-center gap-2">
              <Shield size={18} style={{ color: "var(--ai-accent)" }} />
              <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>Admin Portal</span>
              <ChevronRight size={14} style={{ color: "var(--text-muted)" }} />
              <span className="text-sm capitalize" style={{ color: "var(--text-secondary)" }}>
                {location.pathname.split("/").pop()?.replace(/-/g, " ") || "Dashboard"}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate("/dashboard")}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{ color: "var(--ai-accent)", background: "var(--ai-accent-soft)" }}
              onMouseEnter={e => e.currentTarget.style.background = "rgba(99,102,241,0.2)"}
              onMouseLeave={e => e.currentTarget.style.background = "var(--ai-accent-soft)"}
            >
              <ArrowLeft size={14} /> Back to App
            </button>
          </div>
        </header>

        {/* Page content */}
        <main className="p-6 max-w-[1600px] mx-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25, ease: "easeOut" }}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}

function SidebarContent({ collapsed, setCollapsed, navigate, user, onClose }) {
  return (
    <>
      {/* Header */}
      <div className="h-16 flex items-center justify-between px-4 shrink-0" style={{ borderBottom: "1px solid rgba(99, 102, 241, 0.12)" }}>
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: "linear-gradient(135deg, #6366F1, #8B5CF6)" }}>
            <Shield size={16} className="text-white" />
          </div>
          {!collapsed && (
            <motion.span
              initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="font-semibold text-sm whitespace-nowrap"
              style={{ color: "#F0F2F5" }}
            >
              Admin Portal
            </motion.span>
          )}
        </div>
        {onClose && (
          <button onClick={onClose} className="p-1.5 rounded-lg" style={{ color: "#8B8FA3" }}>
            <X size={18} />
          </button>
        )}
      </div>

      {/* Nav items */}
      <nav className="flex-1 overflow-y-auto py-3 px-2.5 space-y-0.5" style={{ scrollbarWidth: "none" }}>
        {ADMIN_NAV.map((item, i) => {
          if (item.type === "divider") {
            return <div key={i} className="my-2 mx-2" style={{ height: 1, background: "rgba(99, 102, 241, 0.08)" }} />;
          }
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={onClose}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200 group ${isActive ? "admin-nav-active" : ""}`
              }
              style={({ isActive }) => ({
                color: isActive ? "#F0F2F5" : "#8B8FA3",
                background: isActive ? "rgba(99, 102, 241, 0.15)" : "transparent",
              })}
              onMouseEnter={e => { if (!e.currentTarget.classList.contains("admin-nav-active")) e.currentTarget.style.background = "rgba(99, 102, 241, 0.08)"; }}
              onMouseLeave={e => { if (!e.currentTarget.classList.contains("admin-nav-active")) e.currentTarget.style.background = "transparent"; }}
            >
              <Icon size={18} className="shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </NavLink>
          );
        })}
      </nav>

      {/* User info */}
      <div className="p-3 shrink-0" style={{ borderTop: "1px solid rgba(99, 102, 241, 0.08)" }}>
        <div className="flex items-center gap-2.5 px-2">
          <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0" style={{ background: "rgba(99, 102, 241, 0.2)", color: "#818CF8" }}>
            {user?.name?.charAt(0)?.toUpperCase() || "A"}
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="text-xs font-medium truncate" style={{ color: "#F0F2F5" }}>{user?.name || "Admin"}</div>
              <div className="text-[10px] truncate" style={{ color: "#4B5068" }}>{user?.role || "admin"}</div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
