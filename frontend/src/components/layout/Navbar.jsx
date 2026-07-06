import { Bell, Sun, Moon, Menu } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useTheme } from "../../context/ThemeContext";
import { useState, useEffect } from "react";
import api from "../../services/api";
import SearchBox from "./SearchBox";

export default function Navbar({ onNotificationClick, onMenuClick }) {
  const { user } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [unreadCount, setUnreadCount] = useState(0);

  useEffect(() => {
    if (!user) return;
    const fetch = () => api.get("/notifications/unread-count").then(r => setUnreadCount(r.data.count || 0)).catch(() => {});
    fetch();
    const interval = setInterval(fetch, 30000);
    return () => clearInterval(interval);
  }, [user]);

  return (
    <header
      data-testid="navbar"
      className="h-16 flex items-center justify-between px-4 sm:px-6 sticky top-0 z-30 glass-nav"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      {/* Left: Hamburger (mobile) + Search */}
      <div className="flex items-center gap-3 flex-1">
        {/* Mobile hamburger */}
        <button
          data-testid="mobile-menu-btn"
          onClick={onMenuClick}
          className="p-2 rounded-xl transition-all lg:hidden"
          style={{ color: "var(--text-secondary)" }}
        >
          <Menu size={20} />
        </button>

        {/* Global stock search with autocomplete */}
        <SearchBox />
      </div>

      {/* Right: Controls */}
      <div className="flex items-center gap-1.5">
        {/* Theme Toggle */}
        <button
          data-testid="theme-toggle-btn"
          onClick={toggleTheme}
          className="p-2.5 rounded-xl transition-all"
          style={{ color: "var(--text-secondary)" }}
          title={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
          onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}
        >
          {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
        </button>

        {/* Notifications */}
        <button
          data-testid="navbar-notifications-btn"
          onClick={() => { onNotificationClick(); setUnreadCount(0); }}
          className="p-2.5 rounded-xl transition-all relative"
          style={{ color: "var(--text-secondary)" }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}
        >
          <Bell size={18} />
          {unreadCount > 0 && (
            <span
              className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 rounded-full flex items-center justify-center text-[9px] font-bold px-1"
              style={{ background: "var(--loss)", color: "#fff" }}
            >
              {unreadCount > 9 ? "9+" : unreadCount}
            </span>
          )}
        </button>

        {/* User Avatar */}
        {user && (
          <div className="flex items-center gap-2.5 pl-2.5 ml-1.5" style={{ borderLeft: "1px solid var(--border)" }}>
            <div
              className="w-9 h-9 rounded-full flex items-center justify-center text-[13px] font-bold font-display"
              style={{
                background: "linear-gradient(135deg, var(--ai-accent), #A78BFA)",
                color: "#FFFFFF",
              }}
            >
              {user.name?.[0]?.toUpperCase() || "U"}
            </div>
            <span className="text-[15px] font-medium hidden lg:block" style={{ color: "var(--text-primary)" }}>
              {user.name}
            </span>
          </div>
        )}
      </div>
    </header>
  );
}
