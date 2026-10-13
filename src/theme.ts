export type Theme = "system" | "light" | "dark";

export function storedTheme(): Theme {
  try {
    const value = localStorage.getItem("macbot-theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme) {
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

export function saveTheme(theme: Theme) {
  applyTheme(theme);
  try {
    localStorage.setItem("macbot-theme", theme);
  } catch {
    // Storage can be unavailable; the choice still applies to this session.
  }
}
