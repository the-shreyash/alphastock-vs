import { useState, useEffect, Suspense } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import Sidebar, { SIDEBAR_COLLAPSED_W, SIDEBAR_EXPANDED_W, SIDEBAR_MOBILE_W } from "./Sidebar";
import Navbar from "./Navbar";
import NotificationPanel from "../notifications/NotificationPanel";
import NotificationToast from "../notifications/NotificationToast";
import AIQuickAction from "../AIQuickAction";

export default function Layout() {
  // Hover-expand is the primary interaction: the sidebar starts collapsed
  // (narrow) and widens on hover. Clicking the toggle pins it open.
  const [collapsed, setCollapsed] = useState(true);
  const [showNotifs, setShowNotifs] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isDesktop, setIsDesktop] = useState(window.innerWidth >= 1024);
  const location = useLocation();

  // Close mobile sidebar on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  // Track viewport size
  useEffect(() => {
    const handleResize = () => {
      const desktop = window.innerWidth >= 1024;
      setIsDesktop(desktop);
      if (desktop) setMobileOpen(false);
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const mainMargin = isDesktop ? (collapsed ? SIDEBAR_COLLAPSED_W : SIDEBAR_EXPANDED_W) : 0;

  return (
    <div className="min-h-screen transition-colors duration-300" style={{ background: "var(--bg)" }} data-testid="app-layout">
      {/* Desktop sidebar */}
      {isDesktop && (
        <Sidebar collapsed={collapsed} setCollapsed={setCollapsed} />
      )}

      {/* Mobile sidebar overlay */}
      <AnimatePresence>
        {mobileOpen && !isDesktop && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40"
              onClick={() => setMobileOpen(false)}
              data-testid="mobile-sidebar-backdrop"
            />
            <motion.div
              initial={{ x: -SIDEBAR_MOBILE_W }}
              animate={{ x: 0 }}
              exit={{ x: -SIDEBAR_MOBILE_W }}
              transition={{ type: "spring", damping: 28, stiffness: 320 }}
              className="fixed left-0 top-0 h-screen z-50"
            >
              <Sidebar collapsed={false} setCollapsed={() => {}} onClose={() => setMobileOpen(false)} isMobile />
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Main content */}
      <div className="transition-all duration-300" style={{ marginLeft: mainMargin }}>
        <Navbar
          onNotificationClick={() => setShowNotifs(!showNotifs)}
          onMenuClick={() => setMobileOpen(true)}
        />
        <main className="page-container min-h-[calc(100vh-64px)]">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25, ease: "easeOut" }}
            >
              {/* Nested Suspense (Sprint R9 lazy loading): a lazily loaded
                  page chunk suspends only the content region — the sidebar,
                  navbar and toast host never unmount during navigation. */}
              <Suspense
                fallback={
                  <div className="min-h-[50vh] flex items-center justify-center">
                    <div className="w-8 h-8 border-2 rounded-full animate-spin" style={{ borderColor: "var(--border)", borderTopColor: "var(--ai-accent)" }} />
                  </div>
                }
              >
                <Outlet />
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      {showNotifs && <NotificationPanel onClose={() => setShowNotifs(false)} />}

      {/* Live toast host (Sprint R8): notification.created + news.breaking
          pushes slide in on every authenticated page. */}
      <NotificationToast />

      {/* Context-aware floating AI companion — rendered once so it appears
          on every authenticated page (never on Login/Register/Landing, which
          live outside this Layout). */}
      <AIQuickAction />
    </div>
  );
}
