import { createContext, useContext, useState, useEffect } from "react";

const ThemeContext = createContext(null);

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => localStorage.getItem("ap-theme") || "light");
  const [displayMode, setDisplayMode] = useState(() => localStorage.getItem("ap-display-mode") || "advanced");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("ap-theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("ap-display-mode", displayMode);
  }, [displayMode]);

  const toggleTheme = () => setTheme((t) => (t === "light" ? "dark" : "light"));
  const toggleDisplayMode = () => setDisplayMode((m) => (m === "beginner" ? "advanced" : "beginner"));

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme, displayMode, toggleDisplayMode, setDisplayMode }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be inside ThemeProvider");
  return ctx;
}
