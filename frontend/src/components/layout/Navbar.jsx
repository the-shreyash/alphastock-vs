import { Bell, Sun, Moon, Menu } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useTheme } from "../../context/ThemeContext";
import { useEffect } from "react";
import api from "../../services/api";
import SearchBox from "./SearchBox";
import ConnectionStatus from "./ConnectionStatus";
import { useRealtimeStore, selectUnreadCount, selectConnected } from "../../store/realtimeStore";

export default function Navbar({ onNotificationClick, onMenuClick }) {
  const { user } = useAuth();
  const { theme, toggleTheme } = useTheme();
  // Unread badge is driven live by the store: `notification.created` pushes
  // increment it in real time. We only seed it once from the server, and keep
  // a slow poll as a fallback while the socket is disconnected.
  const unreadCount = useRealtimeStore(selectUnreadCount);
  const connected = useRealtimeStore(selectConnected);
  const seedUnreadCount = useRealtimeStore((s) => s.seedUnreadCount);
  const markNotificationsRead = useRealtimeStore((s) => s.markNotificationsRead);

  useEffect(() => {
    if (!user) return undefined;
    const fetch = () => api.get("/notifications/unread-count").then(r => seedUnreadCount(r.data.count || 0)).catch(() => {});
    fetch(); // seed on mount
    // Fallback poll only while the live push path is unavailable.
    if (connected) return undefined;
    const interval = setInterval(fetch, 30000);
    return () => clearInterval(interval);
  }, [user, connected, seedUnreadCount]);

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
        {/* Real-time connection status */}
        <ConnectionStatus />

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
          onClick={() => { onNotificationClick(); markNotificationsRead(); }}
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
