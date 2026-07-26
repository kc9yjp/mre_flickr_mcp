// Theme + text-size selection: persisted to localStorage, applied as a
// `data-theme` attribute and a `--font-size-base` custom property on <html>
// so styles.css can react to them everywhere (including dockview's chrome).

export interface ThemeDef {
  id: string;
  label: string;
  mode: "light" | "dark";
}

export const THEMES: ThemeDef[] = [
  { id: "slate", label: "Slate", mode: "dark" },
  { id: "dracula", label: "Dracula", mode: "dark" },
  { id: "daylight", label: "Daylight", mode: "light" },
  { id: "paper", label: "Paper", mode: "light" },
  { id: "mist", label: "Mist", mode: "light" },
];

export interface FontSizeDef {
  id: string;
  label: string;
  px: number;
}

export const FONT_SIZES: FontSizeDef[] = [
  { id: "sm", label: "Small", px: 14 },
  { id: "md", label: "Medium", px: 16 },
  { id: "lg", label: "Large", px: 18 },
  { id: "xl", label: "X-Large", px: 20 },
];

const THEME_KEY = "workbench-theme-v1";
const FONT_SIZE_KEY = "workbench-font-size-v1";
const DEFAULT_THEME = "slate";
const DEFAULT_FONT_SIZE = "md";

export function getStoredTheme(): string {
  const v = localStorage.getItem(THEME_KEY);
  return v && THEMES.some((t) => t.id === v) ? v : DEFAULT_THEME;
}

export function getStoredFontSize(): string {
  const v = localStorage.getItem(FONT_SIZE_KEY);
  return v && FONT_SIZES.some((f) => f.id === v) ? v : DEFAULT_FONT_SIZE;
}

export function applyTheme(themeId: string) {
  document.documentElement.setAttribute("data-theme", themeId);
  localStorage.setItem(THEME_KEY, themeId);
}

export function applyFontSize(sizeId: string) {
  const size = FONT_SIZES.find((f) => f.id === sizeId) ?? FONT_SIZES[1];
  document.documentElement.style.setProperty("--font-size-base", `${size.px}px`);
  localStorage.setItem(FONT_SIZE_KEY, sizeId);
}

// Call once at startup, before the first paint, to avoid a flash of the
// default theme/size.
export function initThemePreferences() {
  applyTheme(getStoredTheme());
  applyFontSize(getStoredFontSize());
}
