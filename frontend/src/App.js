import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ThemeProvider } from "./context/ThemeContext";
import Layout from "./components/layout/Layout";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import AuthCallback from "./pages/AuthCallback";
import Dashboard from "./pages/Dashboard";
import StockPicks from "./pages/StockPicks";
import StockDetail from "./pages/StockDetail";
import TradeMonitor from "./pages/TradeMonitor";
import Portfolio from "./pages/Portfolio";
import AIAssistant from "./pages/AIAssistant";
import SIPAdvisor from "./pages/SIPAdvisor";
import Settings from "./pages/Settings";
import News from "./pages/News";
import TradeJournal from "./pages/TradeJournal";
import PaperTrading from "./pages/PaperTrading";
import Backtesting from "./pages/Backtesting";
import MorningReport from "./pages/MorningReport";
import "./App.css";

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
    <Routes>
      <Route path="/" element={<PublicRoute><Landing /></PublicRoute>} />
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/register" element={<PublicRoute><Register /></PublicRoute>} />
      <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="picks" element={<StockPicks />} />
        <Route path="stock/:symbol" element={<StockDetail />} />
        <Route path="trades" element={<TradeMonitor />} />
        <Route path="portfolio" element={<Portfolio />} />
        <Route path="assistant" element={<AIAssistant />} />
        <Route path="sip" element={<SIPAdvisor />} />
        <Route path="news" element={<News />} />
        <Route path="journal" element={<TradeJournal />} />
        <Route path="settings" element={<Settings />} />
        <Route path="paper-trading" element={<PaperTrading />} />
        <Route path="backtesting" element={<Backtesting />} />
        <Route path="morning-report" element={<MorningReport />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <AppRouter />
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}

export default App;
