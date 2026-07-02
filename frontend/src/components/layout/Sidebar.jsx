import { NavLink, useLocation } from "react-router-dom";
import { LayoutDashboard, Target, TrendingUp, Briefcase, MessageSquare, Calculator, Settings, LogOut, ChevronLeft, ChevronRight, Newspaper, BookOpen, X, FlaskConical, BarChart2, Sun } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import APLogo from "../APLogo";

const NAV_GROUPS = [
  {
    title: "Dashboard & Telemetry",
    items: [
      { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
      { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
      { to: "/trades", icon: TrendingUp, label: "Trades" },
    ]
  },
  {
    title: "AI Intelligence",
    items: [
      { to: "/picks", icon: Target, label: "AI Picks" },
      { to: "/morning-report", icon: Sun, label: "Morning Report" },
      { to: "/news", icon: Newspaper, label: "News" },
    ]
  },
  {
    title: "Simulations",
    items: [
      { to: "/paper-trading", icon: FlaskConical, label: "Paper Trading" },
      { to: "/backtesting", icon: BarChart2, label: "Backtesting" },
      { to: "/journal", icon: BookOpen, label: "Journal" },
    ]
  },
  {
    title: "Assistance",
    items: [
      { to: "/assistant", icon: MessageSquare, label: "AI Chat" },
      { to: "/sip", icon: Calculator, label: "SIP Advisor" },
      { to: "/settings", icon: Settings, label: "Settings" },
    ]
  }
];

export default function Sidebar({ collapsed, setCollapsed, onClose, isMobile }) {
  const { logout, user } = useAuth();
  const location = useLocation();

  const handleLogout = () => {
    if (onClose) onClose();
    logout();
  };

  return (
    <aside data-testid="sidebar" className={`fixed left-0 top-0 h-screen z-40 flex flex-col transition-all duration-300 border-r ${isMobile ? "w-[260px]" : collapsed ? "w-[68px]" : "w-[240px]"}`}
      style={{ background: "var(--bg)", borderColor: "var(--border)" }}>
      {/* Logo */}
      <div className="h-16 flex items-center justify-between px-5 border-b shrink-0" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-3">
          <APLogo size={32} className="shrink-0 text-blue-500" />
          {(!collapsed || isMobile) && (
            <span className="text-base font-semibold tracking-tight text-white font-display">
              StockAssist AI
            </span>
          )}
        </div>
        {isMobile && (
          <button onClick={onClose} className="p-1.5 rounded-lg transition-all hover:opacity-70" style={{ color: "var(--text-muted)" }} data-testid="close-mobile-sidebar">
            <X size={18} />
          </button>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 py-4 px-3 overflow-y-auto space-y-5">
        {NAV_GROUPS.map((group, gIdx) => (
          <div key={gIdx} className="space-y-1.5">
            {(!collapsed || isMobile) && (
              <div className="px-3 text-[10px] font-bold uppercase tracking-[0.15em]" style={{ color: "var(--text-muted)" }}>
                {group.title}
              </div>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const isActive = location.pathname === item.to || (item.to === "/dashboard" && location.pathname === "/");
                return (
                  <NavLink key={item.to} to={item.to} data-testid={`nav-${item.label.toLowerCase().replace(/\s/g, "-")}`}
                    onClick={() => isMobile && onClose?.()}
                    className={`flex items-center gap-3 px-3 py-2 rounded-xl text-sm font-medium transition-all group ${collapsed && !isMobile ? "justify-center" : ""}`}
                    style={{ background: isActive ? "var(--ai-accent-soft)" : "transparent", color: isActive ? "var(--ai-accent)" : "var(--text-secondary)" }}>
                    <item.icon size={16} strokeWidth={1.8} className="shrink-0 transition-transform group-hover:scale-105" />
                    {(!collapsed || isMobile) && <span>{item.label}</span>}
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="p-3 border-t shrink-0" style={{ borderColor: "var(--border)" }}>
        {(!collapsed || isMobile) && user && (
          <div className="px-3 py-2 mb-1 bg-white/[0.02] border border-white/[0.04] rounded-xl">
            <p className="text-xs font-semibold truncate text-white">{user.name}</p>
            <p className="text-[10px] truncate" style={{ color: "var(--text-muted)" }}>{user.email}</p>
          </div>
        )}
        <button data-testid="sidebar-logout-btn" onClick={handleLogout}
          className="flex items-center gap-3 px-3 py-2 w-full text-sm rounded-xl transition-all hover:bg-rose-500/5 hover:opacity-100"
          style={{ color: "var(--loss)" }}>
          <LogOut size={16} />
          {(!collapsed || isMobile) && <span>Logout</span>}
        </button>
        {!isMobile && (
          <button data-testid="sidebar-toggle-btn" onClick={() => setCollapsed(!collapsed)}
            className="flex items-center justify-center w-full py-2 mt-1 rounded-lg transition-all hover:bg-white/[0.02]"
            style={{ color: "var(--text-muted)" }}>
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        )}
      </div>
    </aside>
  );
}
