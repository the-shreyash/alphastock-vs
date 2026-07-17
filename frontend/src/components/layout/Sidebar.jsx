import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { LayoutDashboard, Target, TrendingUp, Briefcase, Settings, LogOut, Newspaper, BookOpen, X, FlaskConical, Sun, Search, Globe, Brain, LineChart, Eye, Sparkles, PanelLeftClose, PanelLeftOpen, Shield } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import APLogo from "../APLogo";

// Primary order (Priority 10 — Sidebar Improvements):
// Home, Markets, Stock Scanner, Morning Report, AI Workspace, Portfolio,
// Trading, Journal, News, Settings.
// `sub: true` entries are secondary items grouped under the primary above them
// (Watchlist → Markets, Investment Advisor + SIP Advisor → AI Workspace,
// Paper Trading → Trading).
// Backtesting is no longer a top-level entry — it lives inside AI Workspace →
// Strategy Builder (its /backtesting route still works for deep links).
const NAV_ITEMS = [
  // — Discover —
  { to: "/dashboard", icon: LayoutDashboard, label: "Home" },
  { to: "/markets", icon: Globe, label: "Markets" },
  { to: "/watchlist", icon: Eye, label: "Watchlist", sub: true },
  { to: "/picks", icon: Target, label: "Stock Scanner" },
  { to: "/morning-report", icon: Sun, label: "Morning Report" },
  { type: "divider" },
  // — AI —
  { to: "/assistant", icon: Brain, label: "AI Workspace" },
  { to: "/advisor", icon: Sparkles, label: "Investment Advisor", sub: true },
  { to: "/sip", icon: LineChart, label: "SIP Advisor", sub: true },
  { type: "divider" },
  // — Trade —
  { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
  { to: "/trades", icon: TrendingUp, label: "Trading" },
  { to: "/paper-trading", icon: FlaskConical, label: "Paper Trading", sub: true },
  { to: "/journal", icon: BookOpen, label: "Journal" },
  { type: "divider" },
  { to: "/news", icon: Newspaper, label: "News" },
  { to: "/admin", icon: Shield, label: "Admin Portal", adminOnly: true },
  { to: "/settings", icon: Settings, label: "Settings" },
];

// Widths (kept in sync with Layout.jsx main-content margin)
export const SIDEBAR_COLLAPSED_W = 76;
export const SIDEBAR_EXPANDED_W = 280;
export const SIDEBAR_MOBILE_W = 268;

// Reusable label that fades / slides in only when the sidebar is open.
function Label({ open, children, className = "" }) {
  return (
    <motion.span
      animate={{ opacity: open ? 1 : 0, x: open ? 0 : -6 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className={`whitespace-nowrap ${className}`}
      style={{ pointerEvents: open ? "auto" : "none" }}
    >
      {children}
    </motion.span>
  );
}

export default function Sidebar({ collapsed, setCollapsed, onClose, isMobile }) {
  const { logout, user } = useAuth();
  const location = useLocation();
  // Hover-expand is the primary interaction. `collapsed` (owned by Layout)
  // acts as a pin: when false the sidebar stays open; when true it lives in
  // hover-expand mode — narrow by default, widening while the pointer is over it.
  const [hovered, setHovered] = useState(false);

  const open = isMobile ? true : (collapsed ? hovered : true);
  const width = isMobile
    ? SIDEBAR_MOBILE_W
    : collapsed
      ? (hovered ? SIDEBAR_EXPANDED_W : SIDEBAR_COLLAPSED_W)
      : SIDEBAR_EXPANDED_W;

  const handleLogout = () => {
    if (onClose) onClose();
    logout();
  };

  return (
    <motion.aside
      data-testid="sidebar"
      onMouseEnter={() => !isMobile && setHovered(true)}
      onMouseLeave={() => !isMobile && setHovered(false)}
      initial={false}
      animate={{ width }}
      transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
      className="fixed left-0 top-0 h-screen z-40 flex flex-col overflow-hidden"
      style={{
        background: "var(--nav-bg)",
        backdropFilter: "blur(var(--glass-blur)) saturate(var(--glass-saturate))",
        WebkitBackdropFilter: "blur(var(--glass-blur)) saturate(var(--glass-saturate))",
        borderRight: "1px solid var(--border)",
      }}
    >
      {/* Logo */}
      <div className="h-16 flex items-center justify-between px-4 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-2.5 min-w-0">
          <APLogo size={30} className="shrink-0" />
          <Label open={open} className="card-title !text-[16px] font-display">
            StockAssist AI
          </Label>
        </div>
        {isMobile && (
          <button onClick={onClose} className="p-1.5 rounded-lg transition-all shrink-0" style={{ color: "var(--text-muted)" }} data-testid="close-mobile-sidebar"
            onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
            <X size={18} />
          </button>
        )}
      </div>

      {/* Search — only when expanded */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="px-3 pt-3 pb-1 shrink-0 overflow-hidden"
          >
            <div className="relative">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
              <input
                type="text"
                placeholder="Search..."
                className="search-input w-full"
                style={{ padding: "9px 12px 9px 34px", borderRadius: "12px", fontSize: "13px" }}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Nav */}
      <nav className="flex-1 py-2 px-2.5 overflow-y-auto overflow-x-hidden">
        <div className="space-y-1">
          {NAV_ITEMS.map((item, idx) => {
            // Hide admin-only items from non-admin users
            if (item.adminOnly && !["admin", "super_admin"].includes(user?.role)) return null;
            if (item.type === "divider") {
              return <div key={`div-${idx}`} className="my-2 mx-2" style={{ borderBottom: "1px solid var(--border)" }} />;
            }
            const isActive = location.pathname === item.to || (item.to === "/dashboard" && location.pathname === "/");
            const isSub = !!item.sub;
            // Sub-items read as secondary: indented + smaller + muted when the
            // rail is expanded. In collapsed rail mode we keep the primary padding
            // so every icon stays vertically aligned.
            const layout = isSub
              ? (open ? "pl-7 pr-3.5 py-2 text-[13px]" : "px-3.5 py-2.5 text-[13px]")
              : "px-3.5 py-2.5 text-[14px]";
            return (
              <NavLink
                key={item.to}
                to={item.to}
                data-testid={`nav-${item.label.toLowerCase().replace(/\s/g, "-")}`}
                onClick={() => isMobile && onClose?.()}
                className={`group/sidebar flex items-center gap-3 rounded-xl font-medium transition-colors ${layout}`}
                style={{
                  background: isActive ? "var(--ai-accent)" : "transparent",
                  color: isActive ? "#FFFFFF" : (isSub ? "var(--text-muted)" : "var(--text-secondary)"),
                  boxShadow: isActive ? "0 4px 14px rgba(99, 102, 241, 0.35)" : "none",
                }}
                onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = "var(--hover)"; }}
                onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = "transparent"; }}
              >
                <item.icon size={isSub ? 18 : 19} strokeWidth={isActive ? 2.1 : 1.7} className="shrink-0 transition-transform duration-200 group-hover/sidebar:translate-x-1" />
                <Label open={open}>{item.label}</Label>
              </NavLink>
            );
          })}
        </div>
      </nav>

      {/* AI Status + Footer */}
      <div className="px-3 pb-3 shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              {/* AI Status */}
              <div className="mt-3 mb-2 px-3 py-2.5 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                <span className="stat-label block mb-1">AI Status</span>
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full" style={{ background: "var(--gain)", boxShadow: "0 0 6px var(--gain)" }} />
                  <span className="text-[12px] font-semibold" style={{ color: "var(--gain)" }}>ACTIVE</span>
                </div>
              </div>

              {/* User info */}
              {user && (
                <div className="px-3 py-2 mb-1.5 rounded-xl" style={{ background: "var(--hover)" }}>
                  <p className="text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{user.name}</p>
                  <p className="text-[11px] truncate" style={{ color: "var(--text-muted)" }}>{user.email}</p>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        <button
          data-testid="sidebar-logout-btn"
          onClick={handleLogout}
          className="flex items-center gap-3 px-3.5 py-2.5 w-full text-[14px] rounded-xl transition-colors font-medium mt-1"
          style={{ color: "var(--loss)" }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--loss-bg)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}
        >
          <LogOut size={18} className="shrink-0" />
          <Label open={open}>Logout</Label>
        </button>

        {!isMobile && (
          <button
            data-testid="sidebar-toggle-btn"
            onClick={() => setCollapsed(!collapsed)}
            title={collapsed ? "Pin sidebar open" : "Collapse to hover mode"}
            className="flex items-center gap-3 px-3.5 py-2.5 w-full text-[13px] rounded-xl transition-colors font-medium mt-0.5"
            style={{ color: "var(--text-muted)" }}
            onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          >
            {collapsed ? <PanelLeftOpen size={18} className="shrink-0" /> : <PanelLeftClose size={18} className="shrink-0" />}
            <Label open={open}>{collapsed ? "Pin open" : "Collapse"}</Label>
          </button>
        )}
      </div>
    </motion.aside>
  );
}
