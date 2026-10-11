"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "signalforge:theme";

const ThemeContext = createContext<{ theme: Theme; setTheme: (theme: Theme) => void } | null>(null);

function applyThemeClass(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

/**
 * Light is the default and requires no stored preference; only "dark" is
 * ever persisted. The blocking inline script in layout.tsx (see
 * THEME_INIT_SCRIPT) already applies the right class to <html> before this
 * component mounts / before paint, so there is no flash of the wrong theme
 * — this provider's job is just to keep React state in sync with that
 * already-applied DOM class and to persist future changes.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("light");

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing from the blocking inline script's already-applied DOM class (see layout.tsx) after mount; a lazy initializer would run during hydration itself and reintroduce the mismatch this avoids.
    setThemeState(document.documentElement.classList.contains("dark") ? "dark" : "light");
  }, []);

  function setTheme(next: Theme) {
    setThemeState(next);
    applyThemeClass(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage unavailable (private mode, etc.) — theme still applies for this session, just won't persist.
    }
  }

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within a ThemeProvider");
  return ctx;
}

/** Runs before hydration (via next/script strategy="beforeInteractive") so
 * the correct theme class is already on <html> at first paint — the
 * standard technique to avoid a light-then-dark flash on reload. */
export const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = window.localStorage.getItem("${STORAGE_KEY}");
    if (stored === "dark") document.documentElement.classList.add("dark");
  } catch (e) {}
})();
`;
