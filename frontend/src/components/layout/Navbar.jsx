import { Bell, Sun, Moon, Menu, Search } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useTheme } from "../../context/ThemeContext";
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import api from "../../services/api";

export default function Navbar({ onNotificationClick, onMenuClick }) {
  const { user } = useAuth();
  const { theme, toggleTheme, displayMode, toggleDisplayMode } = useTheme();
  const [unreadCount, setUnreadCount] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    if (!user) return;
    const fetch = () => api.get("/notifications/unread-count").then(r => setUnreadCount(r.data.count || 0)).catch(() => {});
    fetch();
    const interval = setInterval(fetch, 30000);
    return () => clearInterval(interval);
  }, [user]);

  const handleSearch = (e) => {
    if (e.key === "Enter" && searchQuery.trim()) {
      navigate(`/stock/${searchQuery.trim().toUpperCase()}`);
      setSearchQuery("");
    }
  };

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

        {/* Search */}
        <div className="relative hidden sm:block w-full max-w-md">
          <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
          <input
            data-testid="navbar-search"
            type="text"
            placeholder="Search stocks, news, reports..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleSearch}
            className="search-input"
            style={{ paddingLeft: "40px" }}
          />
        </div>
      </div>

      {/* Right: Controls */}
      <div className="flex items-center gap-2">
        {/* Mode Toggle — Segmented Control */}
        <div className="segment-control hidden md:inline-flex">
          <button
            data-testid="mode-toggle-beginner"
            onClick={() => displayMode !== "beginner" && toggleDisplayMode()}
            className={`segment-btn ${displayMode === "beginner" ? "active" : ""}`}
          >
            AI Beginner
          </button>
          <button
            data-testid="mode-toggle-advanced"
            onClick={() => displayMode !== "advanced" && toggleDisplayMode()}
            className={`segment-btn ${displayMode === "advanced" ? "active" : ""}`}
          >
            AI Advanced
          </button>
        </div>

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
          {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
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
          <Bell size={17} />
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
          <div className="flex items-center gap-2.5 pl-2 ml-1" style={{ borderLeft: "1px solid var(--border)" }}>
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold"
              style={{
                background: "linear-gradient(135deg, var(--ai-accent), #A78BFA)",
                color: "#FFFFFF",
              }}
            >
              {user.name?.[0]?.toUpperCase() || "U"}
            </div>
            <span className="text-sm font-medium hidden lg:block" style={{ color: "var(--text-primary)" }}>
              {user.name}
            </span>
          </div>
        )}
      </div>
    </header>
  );
}
