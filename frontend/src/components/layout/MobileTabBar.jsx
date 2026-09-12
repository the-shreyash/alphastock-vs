import { Link, useLocation } from "react-router-dom";
import { LayoutDashboard, Globe, Brain, Briefcase, TrendingUp } from "lucide-react";

/**
 * Bottom navigation for small screens.
 *
 * WHY THE RAIL IS NOT ENOUGH ON MOBILE
 * ------------------------------------
 * Below 1024px the sidebar collapses into a drawer behind a hamburger, so every
 * navigation on a phone costs two taps and hides the page while it is open.
 * That is acceptable for the long tail of destinations; it is not acceptable
 * for the five screens a user moves between constantly.
 *
 * These five are chosen by traffic, not by symmetry with the rail: everything
 * else stays one tap further away in the drawer, which is the right trade for a
 * surface this small.
 *
 * The bar sits at the bottom because that is where a thumb is. It is hidden
 * from `lg` up, where the persistent rail takes over.
 */
const ITEMS = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Home" },
  { to: "/markets", icon: Globe, label: "Markets" },
  { to: "/assistant", icon: Brain, label: "AI" },
  { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
  { to: "/trades", icon: TrendingUp, label: "Trading" },
];

/** Height in px, excluding the safe-area inset. Layout reserves this much. */
export const MOBILE_TAB_BAR_H = 60;

export default function MobileTabBar() {
  const { pathname } = useLocation();
  const matches = (to) => pathname === to || (to === "/dashboard" && pathname === "/");

  return (
    <nav
      data-testid="mobile-tab-bar"
      aria-label="Primary"
      className="fixed bottom-0 left-0 right-0 z-40 flex lg:hidden glass-nav"
      style={{
        height: MOBILE_TAB_BAR_H,
        // Keeps the row clear of the iOS home indicator without adding dead
        // space on devices that have none.
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
        borderTop: "1px solid var(--border)",
        borderBottom: "none",
        borderLeft: "none",
        borderRight: "none",
      }}
    >
      {ITEMS.map((item) => {
        const active = matches(item.to);
        const Icon = item.icon;
        return (
          <Link
            key={item.to}
            to={item.to}
            data-testid={`mobile-nav-${item.label.toLowerCase()}`}
            aria-current={active ? "page" : undefined}
            className="relative flex-1 flex flex-col items-center justify-center gap-1 transition-colors"
            style={{ color: active ? "var(--nav-active-fg)" : "var(--text-muted)" }}
          >
            {/* The active marker is a bar above the icon rather than a filled
                pill: it reads at a glance without turning the bar into the
                loudest element on the screen. */}
            <span
              aria-hidden="true"
              className="absolute top-0 rounded-b-full transition-all"
              style={{
                width: active ? 22 : 0,
                height: 2.5,
                background: "var(--nav-active-marker)",
              }}
            />
            <Icon size={19} strokeWidth={active ? 2.2 : 1.7} />
            <span className="text-[10px] font-medium" style={{ fontWeight: active ? 600 : 500 }}>
              {item.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
