import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
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

function AppRouter() {
  const location = useLocation();
  if (location.hash?.includes("session_id=")) {
    return <AuthCallback />;
  }

  return (
    <Suspense fallback={<PageFallback />}>
    <Routes>
      <Route path="/" element={<PublicRoute><Landing /></PublicRoute>} />
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/register" element={<PublicRoute><Register /></PublicRoute>} />
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
