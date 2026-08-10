import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ThemeProvider } from "./context/ThemeContext";
import { RealtimeProvider } from "./context/RealtimeProvider";
import Layout from "./components/layout/Layout";
import "./App.css";

/*
 * Route-level code splitting (Sprint R9 lazy loading). Every page loads as its
 * own chunk on first navigation instead of shipping the entire app in one
 * bundle — the initial load carries only the shell + the visited page. The
 * auth pages and Landing are split too: a logged-in user deep-linking into the
 * dashboard never downloads them.
 */
const Landing = lazy(() => import("./pages/Landing"));
const Login = lazy(() => import("./pages/Login"));
const Register = lazy(() => import("./pages/Register"));
const AuthCallback = lazy(() => import("./pages/AuthCallback"));
const BrokerCallback = lazy(() => import("./pages/BrokerCallback"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const StockPicks = lazy(() => import("./pages/StockPicks"));
const StockDetail = lazy(() => import("./pages/StockDetail"));
const TradeMonitor = lazy(() => import("./pages/TradeMonitor"));
const Portfolio = lazy(() => import("./pages/Portfolio"));
const AIAssistant = lazy(() => import("./pages/AIAssistant"));
const SIPAdvisor = lazy(() => import("./pages/SIPAdvisor"));
const InvestmentAdvisor = lazy(() => import("./pages/InvestmentAdvisor"));
const Settings = lazy(() => import("./pages/Settings"));
const News = lazy(() => import("./pages/News"));
const TradeJournal = lazy(() => import("./pages/TradeJournal"));
const PaperTrading = lazy(() => import("./pages/PaperTrading"));
const Backtesting = lazy(() => import("./pages/Backtesting"));
const MorningReport = lazy(() => import("./pages/MorningReport"));
const Markets = lazy(() => import("./pages/Markets"));
const Watchlist = lazy(() => import("./pages/Watchlist"));

/* Admin Portal (Sprint 11) — lazy-loaded, never shipped to non-admin users. */
const AdminLayout = lazy(() => import("./components/admin/AdminLayout"));
const AdminRoute = lazy(() => import("./components/admin/AdminRoute"));
const AdminDashboard = lazy(() => import("./pages/admin/AdminDashboard"));
const AdminUsers = lazy(() => import("./pages/admin/AdminUsers"));
const AdminPayments = lazy(() => import("./pages/admin/AdminPayments"));
const AdminAI = lazy(() => import("./pages/admin/AdminAI"));
const AdminAPIs = lazy(() => import("./pages/admin/AdminAPIs"));
const AdminAnalytics = lazy(() => import("./pages/admin/AdminAnalytics"));
const AdminLogs = lazy(() => import("./pages/admin/AdminLogs"));
const AdminSupport = lazy(() => import("./pages/admin/AdminSupport"));
const AdminFeatureFlags = lazy(() => import("./pages/admin/AdminFeatureFlags"));
const AdminAnnouncements = lazy(() => import("./pages/admin/AdminAnnouncements"));
const AdminSystemHealth = lazy(() => import("./pages/admin/AdminSystemHealth"));

/* Suspense fallback matching the app's existing loading language. */
function PageFallback() {
  return (
    <div className="min-h-[50vh] flex items-center justify-center" style={{ background: "var(--bg)" }}>
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 rounded-full animate-spin" style={{ borderColor: "var(--border)", borderTopColor: "var(--ai-accent)" }} />
        <span className="text-xs font-mono uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Loading</span>
      </div>
    </div>
  );
}

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg)" }}>
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 rounded-full animate-spin" style={{ borderColor: "var(--border)", borderTopColor: "var(--ai-accent)" }} />
          <span className="text-xs font-mono uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Loading</span>
        </div>
      </div>
    );
  }
  if (user === false) return <Navigate to="/login" replace />;
  return children;
}

function PublicRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user && user !== false) return <Navigate to="/dashboard" replace />;
  return children;
}

/*
 * Exported for tests (PH3.2): the route table and its guards are the security
 * boundary of the SPA, so the routing tests must exercise *this* declaration
 * rather than a copy. `App` still owns the BrowserRouter, which lets a test
 * mount AppRouter inside a MemoryRouter and drive navigation without touching
 * window.history.
 */
export function AppRouter() {
  return (
    <Suspense fallback={<PageFallback />}>
    <Routes>
      <Route path="/" element={<PublicRoute><Landing /></PublicRoute>} />
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/register" element={<PublicRoute><Register /></PublicRoute>} />
      <Route path="/auth/google/callback" element={<AuthCallback />} />
      <Route path="/broker/callback" element={<BrokerCallback />} />
      <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="markets" element={<Markets />} />
        <Route path="watchlist" element={<Watchlist />} />
        <Route path="picks" element={<StockPicks />} />
        <Route path="stock/:symbol" element={<StockDetail />} />
        <Route path="trades" element={<TradeMonitor />} />
        <Route path="portfolio" element={<Portfolio />} />
        <Route path="assistant" element={<AIAssistant />} />
        <Route path="sip" element={<SIPAdvisor />} />
        <Route path="advisor" element={<InvestmentAdvisor />} />
        <Route path="news" element={<News />} />
        <Route path="journal" element={<TradeJournal />} />
        <Route path="settings" element={<Settings />} />
        <Route path="paper-trading" element={<PaperTrading />} />
        <Route path="backtesting" element={<Backtesting />} />
        <Route path="morning-report" element={<MorningReport />} />
      </Route>
      {/* Admin Portal (Sprint 11) — isolated layout with its own sidebar */}
      <Route path="/admin" element={<ProtectedRoute><AdminRoute><AdminLayout /></AdminRoute></ProtectedRoute>}>
        <Route index element={<Navigate to="/admin/dashboard" replace />} />
        <Route path="dashboard" element={<AdminDashboard />} />
        <Route path="users" element={<AdminUsers />} />
        <Route path="payments" element={<AdminPayments />} />
        <Route path="ai" element={<AdminAI />} />
        <Route path="apis" element={<AdminAPIs />} />
        <Route path="analytics" element={<AdminAnalytics />} />
        <Route path="logs" element={<AdminLogs />} />
        <Route path="support" element={<AdminSupport />} />
        <Route path="feature-flags" element={<AdminFeatureFlags />} />
        <Route path="announcements" element={<AdminAnnouncements />} />
        <Route path="system-health" element={<AdminSystemHealth />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
  );
}

function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <RealtimeProvider>
            <AppRouter />
          </RealtimeProvider>
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}

export default App;
