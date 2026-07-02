import { Bell, Sun, Moon, Menu } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useTheme } from "../../context/ThemeContext";
import { useState, useEffect } from "react";
import api from "../../services/api";

export default function Navbar({ onNotificationClick, onMenuClick }) {
  const { user } = useAuth();
  const { theme, toggleTheme, displayMode, toggleDisplayMode } = useTheme();
  const [unreadCount, setUnreadCount] = useState(0);

  useEffect(() => {
    if (!user) return;
    const fetch = () => api.get("/notifications/unread-count").then(r => setUnreadCount(r.data.count || 0)).catch(() => {});
    fetch();
    const interval = setInterval(fetch, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, [user]);

  return (
    <header data-testid="navbar"
      className="h-16 flex items-center justify-between px-4 sm:px-6 sticky top-0 z-30 border-b"
      style={{
        background: theme === "light" ? "rgba(255,255,255,0.7)" : "rgba(9,9,11,0.7)",
        borderColor: "var(--border)",
        backdropFilter: "blur(20px) saturate(1.6)",
        WebkitBackdropFilter: "blur(20px) saturate(1.6)",
      }}>
      {/* Left: Hamburger + Date + Status */}
      <div className="flex items-center gap-3">
        {/* Mobile hamburger */}
        <button
          data-testid="mobile-menu-btn"
          onClick={onMenuClick}
          className="p-2 rounded-xl transition-all lg:hidden"
          style={{ color: "var(--text-secondary)" }}
        >
          <Menu size={20} />
        </button>

        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: "var(--gain)" }} />
          <span className="text-xs font-mono" style={{ color: "var(--gain)" }}>LIVE</span>
        </div>
        
        {/* iOS-style Segmented Mode Control */}
        <div className="segment-control ml-2 hidden xs:inline-flex">
          <button 
            data-testid="mode-toggle-beginner" 
            onClick={() => displayMode !== "beginner" && toggleDisplayMode()}
            className={`segment-btn ${displayMode === "beginner" ? "active" : ""}`}
          >
            Beginner
          </button>
          <button 
            data-testid="mode-toggle-advanced" 
            onClick={() => displayMode !== "advanced" && toggleDisplayMode()}
            className={`segment-btn ${displayMode === "advanced" ? "active" : ""}`}
          >
            Advanced
          </button>
        </div>

        <span className="text-xs font-mono hidden sm:block" style={{ color: "var(--text-muted)" }}>
          {new Date().toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" })}
        </span>
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-1.5 sm:gap-2">
        {/* Theme Toggle */}
        <button data-testid="theme-toggle-btn" onClick={toggleTheme}
          className="p-2 rounded-xl transition-all hover:-translate-y-px"
          style={{ color: "var(--text-secondary)" }}
          title={`Switch to ${theme === "light" ? "dark" : "light"} mode`}>
          {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
        </button>

        {/* Notifications */}
        <button data-testid="navbar-notifications-btn" onClick={() => { onNotificationClick(); setUnreadCount(0); }}
          className="p-2 rounded-xl transition-all hover:-translate-y-px relative" style={{ color: "var(--text-secondary)" }}>
          <Bell size={18} />
          {unreadCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 rounded-full flex items-center justify-center text-[9px] font-bold px-1"
              style={{ background: "var(--loss)", color: "#fff" }}>
              {unreadCount > 9 ? "9+" : unreadCount}
            </span>
          )}
        </button>

        {/* User */}
        {user && (
          <div className="flex items-center gap-2 pl-2 sm:pl-3 ml-1 border-l" style={{ borderColor: "var(--border)" }}>
            <div className="w-8 h-8 rounded-xl flex items-center justify-center text-xs font-semibold"
              style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
              {user.name?.[0]?.toUpperCase() || "U"}
            </div>
            <span className="text-sm font-medium hidden sm:block" style={{ color: "var(--text-primary)" }}>{user.name}</span>
          </div>
        )}
      </div>
    </header>
  );
}
