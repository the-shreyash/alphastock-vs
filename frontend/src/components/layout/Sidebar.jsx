import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  LayoutDashboard, Globe, Brain, Eye, Briefcase, TrendingUp, FlaskConical,
  Newspaper, Settings, LogOut, X, Search, Shield, Target, Sun, LineChart,
  Sparkles, BookOpen, PanelLeftClose, PanelLeftOpen,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import APLogo from "../APLogo";

/**
 * Primary navigation — nine top-level destinations, each owning the pages that
 * belong to it.
 *
 * WHY NESTING RATHER THAN A FLAT LIST
 * -----------------------------------
 * The rail previously carried sixteen flat entries, which is more than the eye
 * can scan and gave equal visual weight to "Portfolio" and "SIP Advisor". The
 * nine here are the product's actual top-level concepts; everything else is a
 * *view within* one of them and is revealed when that section is active.
 *
 * No route was removed. Every previous entry still has a home and every URL
 * still resolves — deep links into /sip, /backtesting, /journal and the rest
 * keep working exactly as before.
 */
const NAV_ITEMS = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  {
    to: "/markets",
    icon: Globe,
    label: "Markets",
    children: [
      { to: "/picks", icon: Target, label: "Stock Scanner" },
      { to: "/morning-report", icon: Sun, label: "Morning Report" },
    ],
  },
  {
    to: "/assistant",
    icon: Brain,
    label: "AI Insights",
    children: [
      { to: "/advisor", icon: Sparkles, label: "Investment Advisor" },
      { to: "/sip", icon: LineChart, label: "SIP Advisor" },
    ],
  },
  { to: "/watchlist", icon: Eye, label: "Watchlist" },
  { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
  {
    to: "/trades",
    icon: TrendingUp,
    label: "Trading",
    children: [
      { to: "/paper-trading", icon: FlaskConical, label: "Paper Trading" },
      { to: "/journal", icon: BookOpen, label: "Journal" },
    ],
  },
  // Research is strategy research — the backtester. Market intelligence
  // (Scanner, Morning Report) belongs under Markets, above.
  { to: "/backtesting", icon: FlaskConical, label: "Research" },
  { to: "/news", icon: Newspaper, label: "News" },
  { to: "/admin", icon: Shield, label: "Admin Portal", adminOnly: true },
  { to: "/settings", icon: Settings, label: "Settings" },
];

// Widths (kept in sync with Layout.jsx main-content margin)
export const SIDEBAR_COLLAPSED_W = 76;
export const SIDEBAR_EXPANDED_W = 280;
export const SIDEBAR_MOBILE_W = 268;

/** Flat list of every routable path the rail knows about, parents + children. */
export const NAV_PATHS = NAV_ITEMS.flatMap((i) => [i.to, ...(i.children ?? []).map((c) => c.to)]);

const testId = (label) => `nav-${label.toLowerCase().replace(/\s/g, "-")}`;

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

/**
 * A single rail entry.
 *
 * The active treatment is a subtle tinted surface plus a short accent bar on
 * the leading edge — not a saturated filled pill. A loud fill makes the rail
 * the most colourful thing on screen, which competes with the market data that
 * is supposed to hold the user's attention.
 */
function NavItem({ item, open, isActive, isChild, onNavigate }) {
  const Icon = item.icon;
  return (
    /*
     * A plain Link rather than a NavLink, deliberately.
     *
     * NavLink computes its own active state and writes `aria-current` from it,
     * overriding anything passed in. That gave the rail two competing notions
     * of "current page": NavLink's exact path match, and this component's
     * `matches()` — which also has to map "/" onto the dashboard. The two
     * disagreed on the root path, so a user on "/" saw a highlighted Dashboard
     * that assistive technology was told was not the current page.
     *
     * One source of truth is worth more than NavLink's convenience here.
     */
    <Link
      to={item.to}
      data-testid={testId(item.label)}
      onClick={onNavigate}
      aria-current={isActive ? "page" : undefined}
      className={`group/sidebar relative flex items-center gap-3 rounded-xl font-medium transition-colors ${
        isChild ? (open ? "pl-9 pr-3.5 py-1.5 text-[13px]" : "px-3.5 py-2 text-[13px]") : "px-3.5 py-2.5 text-[14px]"
      }`}
      style={{
        background: isActive ? "var(--nav-active-bg)" : "transparent",
        color: isActive
          ? "var(--nav-active-fg)"
          : isChild
            ? "var(--text-muted)"
            : "var(--text-secondary)",
        fontWeight: isActive ? 600 : 500,
      }}
      onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = "var(--hover)"; }}
      onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = "transparent"; }}
    >
      {isActive && (
        <span
          aria-hidden="true"
          className="absolute left-0 top-1/2 -translate-y-1/2 rounded-r-full"
          style={{ width: 3, height: isChild ? 14 : 18, background: "var(--nav-active-marker)" }}
        />
      )}
      <Icon
        size={isChild ? 17 : 19}
        strokeWidth={isActive ? 2.1 : 1.7}
        className="shrink-0"
        style={isActive ? { color: "var(--nav-active-marker)" } : undefined}
      />
      <Label open={open}>{item.label}</Label>
    </Link>
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

  const path = location.pathname;
  const matches = (to) => path === to || (to === "/dashboard" && path === "/");

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
                aria-label="Search"
                className="search-input w-full"
                style={{ padding: "9px 12px 9px 34px", borderRadius: "12px", fontSize: "13px" }}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Nav */}
      <nav className="flex-1 py-2 px-2.5 overflow-y-auto overflow-x-hidden" aria-label="Primary">
        <div className="space-y-0.5">
          {NAV_ITEMS.map((item) => {
            // Hide admin-only items from non-admin users
            if (item.adminOnly && !["admin", "super_admin"].includes(user?.role)) return null;

            const children = item.children ?? [];
            const childActive = children.some((c) => matches(c.to));
            const isActive = matches(item.to);
            // A section reveals its views while the user is inside it. The rail
            // stays at nine lines everywhere else.
            const expanded = open && (isActive || childActive);

            return (
              <div key={item.to}>
                <NavItem
                  item={item}
                  open={open}
                  isActive={isActive}
                  onNavigate={() => isMobile && onClose?.()}
                />
                <AnimatePresence initial={false}>
                  {expanded && children.length > 0 && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.18, ease: "easeOut" }}
                      className="overflow-hidden mt-0.5 space-y-0.5"
                    >
                      {children.map((child) => (
                        <NavItem
                          key={child.to}
                          item={child}
                          open={open}
                          isActive={matches(child.to)}
                          isChild
                          onNavigate={() => isMobile && onClose?.()}
                        />
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
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
