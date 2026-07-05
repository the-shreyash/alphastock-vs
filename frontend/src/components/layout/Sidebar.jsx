import { NavLink, useLocation } from "react-router-dom";
import { LayoutDashboard, Target, TrendingUp, Briefcase, MessageSquare, Calculator, Settings, LogOut, ChevronLeft, ChevronRight, Newspaper, BookOpen, X, FlaskConical, BarChart2, Sun, Search, Globe, Bell, Brain, LineChart } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import APLogo from "../APLogo";

const NAV_ITEMS = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Home" },
  { to: "/picks", icon: Target, label: "Stock Scanner" },
  { to: "/markets", icon: Globe, label: "Markets" },
  { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
  { to: "/trades", icon: TrendingUp, label: "Trading" },
  { type: "divider" },
  { to: "/paper-trading", icon: FlaskConical, label: "Paper Trading" },
  { to: "/backtesting", icon: BarChart2, label: "Backtesting" },
  { type: "divider" },
  { to: "/assistant", icon: Brain, label: "AI Workspace" },
  { to: "/journal", icon: BookOpen, label: "Journal" },
  { to: "/news", icon: Newspaper, label: "Market Intel" },
  { type: "divider" },
  { to: "/morning-report", icon: Sun, label: "Morning Report" },
  { to: "/sip", icon: LineChart, label: "SIP Advisor" },
  { to: "/settings", icon: Settings, label: "Settings" },
];

export default function Sidebar({ collapsed, setCollapsed, onClose, isMobile }) {
  const { logout, user } = useAuth();
  const location = useLocation();

  const handleLogout = () => {
    if (onClose) onClose();
    logout();
  };

  return (
    <aside
      data-testid="sidebar"
      className={`fixed left-0 top-0 h-screen z-40 flex flex-col transition-all duration-300 ${isMobile ? "w-[260px]" : collapsed ? "w-[68px]" : "w-[240px]"}`}
      style={{
        background: "var(--sidebar-bg)",
        borderRight: "1px solid var(--border)",
      }}
    >
      {/* Logo */}
      <div className="h-16 flex items-center justify-between px-4 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-2.5">
          <APLogo size={28} className="shrink-0" />
          {(!collapsed || isMobile) && (
            <span className="text-[15px] font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>
              StockAssist AI
            </span>
          )}
        </div>
        {isMobile && (
          <button onClick={onClose} className="p-1.5 rounded-lg transition-all hover:bg-white/5" style={{ color: "var(--text-muted)" }} data-testid="close-mobile-sidebar">
            <X size={18} />
          </button>
        )}
      </div>

      {/* Search */}
      {(!collapsed || isMobile) && (
        <div className="px-3 pt-3 pb-1 shrink-0">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
            <input
              type="text"
              placeholder="Search..."
              className="search-input w-full text-xs"
              style={{ padding: "8px 12px 8px 32px", borderRadius: "10px", fontSize: "12px" }}
            />
          </div>
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 py-2 px-2.5 overflow-y-auto">
        <div className="space-y-0.5">
          {NAV_ITEMS.map((item, idx) => {
            if (item.type === "divider") {
              return <div key={`div-${idx}`} className="my-2" style={{ borderBottom: "1px solid var(--border)" }} />;
            }
            const isActive = location.pathname === item.to || (item.to === "/dashboard" && location.pathname === "/");
            return (
              <NavLink
                key={item.to}
                to={item.to}
                data-testid={`nav-${item.label.toLowerCase().replace(/\s/g, "-")}`}
                onClick={() => isMobile && onClose?.()}
                className={`flex items-center gap-2.5 px-3 py-[9px] rounded-[10px] text-[13px] font-medium transition-all group ${collapsed && !isMobile ? "justify-center px-0" : ""}`}
                style={{
                  background: isActive ? "var(--ai-accent)" : "transparent",
                  color: isActive ? "#FFFFFF" : "var(--text-secondary)",
                  boxShadow: isActive ? "0 2px 8px rgba(99, 102, 241, 0.3)" : "none",
                }}
              >
                <item.icon size={17} strokeWidth={isActive ? 2 : 1.6} className="shrink-0 transition-transform group-hover:scale-105" />
                {(!collapsed || isMobile) && <span>{item.label}</span>}
              </NavLink>
            );
          })}
        </div>
      </nav>

      {/* AI Status + Footer */}
      <div className="px-3 pb-3 shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
        {/* AI Status */}
        {(!collapsed || isMobile) && (
          <div className="mt-3 mb-2 px-3 py-2.5 rounded-[10px]" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }}>AI Status</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full" style={{ background: "var(--gain)", boxShadow: "0 0 6px var(--gain)" }} />
              <span className="text-[11px] font-semibold" style={{ color: "var(--gain)" }}>ACTIVE</span>
            </div>
          </div>
        )}

        {/* User info */}
        {(!collapsed || isMobile) && user && (
          <div className="px-3 py-2 mb-1.5 rounded-[10px]" style={{ background: "var(--hover)" }}>
            <p className="text-[12px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{user.name}</p>
            <p className="text-[10px] truncate" style={{ color: "var(--text-muted)" }}>{user.email}</p>
          </div>
        )}

        <button
          data-testid="sidebar-logout-btn"
          onClick={handleLogout}
          className="flex items-center gap-2.5 px-3 py-2 w-full text-[13px] rounded-[10px] transition-all font-medium"
          style={{ color: "var(--loss)" }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--loss-bg)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}
        >
          <LogOut size={15} />
          {(!collapsed || isMobile) && <span>Logout</span>}
        </button>

        {!isMobile && (
          <button
            data-testid="sidebar-toggle-btn"
            onClick={() => setCollapsed(!collapsed)}
            className="flex items-center justify-center w-full py-2 mt-1 rounded-lg transition-all"
            style={{ color: "var(--text-muted)" }}
            onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        )}
      </div>
    </aside>
  );
}
