/**
 * src/design/theme.jsx — THEME-01. The runtime owner of the active theme.
 *
 * WHAT THEMING MEANS IN THIS CODEBASE
 * ---------------------------------------------------------------------------
 * Nothing here holds a colour. `src/styles/tokens.css` declares the dark theme
 * in its `@theme static` block and the light theme in a `[data-theme="light"]`
 * block, and `src/design/tokens.js` mirrors every colour as a `var(--…)`
 * reference. So switching the theme is exactly one DOM write — the attribute on
 * <html> — and the browser re-resolves every colour in the document, including
 * the ones sitting in inline `style` attributes and in recharts' `fill` and
 * `stroke` props.
 *
 * That is why there is no theme-aware component, no duplicated page and no
 * colour prop threaded through the tree. A component that reads
 * `token.surface.panel` is already theme-aware because that string is
 * `var(--color-surface-panel)`.
 *
 * WHY A STORED CHOICE BEATS prefers-color-scheme
 * ---------------------------------------------------------------------------
 * The system preference is consulted ONCE, for a user who has never chosen.
 * After that the stored value wins. A trader who selects dark on a machine
 * configured for light meant it, and a bare
 * `@media (prefers-color-scheme: light)` would silently override that on every
 * load with no way to opt out. The OS listener below therefore only follows the
 * system while nothing is stored.
 *
 * WHY DARK IS THE ABSENCE OF THE ATTRIBUTE
 * ---------------------------------------------------------------------------
 * Dark is the product's identity and the baseline every contrast figure in
 * tokens.css was measured against. Expressing it as "no attribute" means the
 * `@theme` block remains the default for any document that never runs this code
 * at all — a server-rendered error page, a Tauri splash, a test that mounts a
 * component without the provider.
 *
 * THE DUPLICATION WITH public/theme-boot.js IS DELIBERATE
 * ---------------------------------------------------------------------------
 * `STORAGE_KEY` and the resolution order also exist in `public/theme-boot.js`,
 * which has to run as a classic synchronous script before the bundle exists in
 * order to prevent a flash of the wrong theme. It cannot import from here. The
 * two are held to the same values by `tests/unit/design/themeBoot.test.js`,
 * which parses that file rather than trusting it.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

/** Must match public/theme-boot.js. Pinned by tests/unit/design/themeBoot.test.js. */
export const STORAGE_KEY = 'vq.theme';

export const THEMES = Object.freeze(['dark', 'light']);

/** The browser-chrome colour per theme: --color-surface-canvas in each. */
const THEME_COLOR = Object.freeze({ dark: '#080A0E', light: '#F4F6F8' });

const ThemeContext = createContext(null);

const isValid = (value) => THEMES.includes(value);

/** The stored choice, or null. Never throws: storage is blockable. */
export function readStoredTheme() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return isValid(value) ? value : null;
  } catch {
    return null;
  }
}

/** The system preference, defaulting to dark. Never throws. */
export function readSystemTheme() {
  try {
    if (window.matchMedia?.('(prefers-color-scheme: light)')?.matches) return 'light';
  } catch {
    /* matchMedia is absent in jsdom and in some embedded webviews. */
  }
  return 'dark';
}

/**
 * Write the theme to the document.
 *
 * Exported because `theme-boot.js` has already done this once before React
 * mounted; the provider calls it again only when the value actually changes, so
 * a mount with the already-correct theme performs no DOM write.
 */
export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'light') root.setAttribute('data-theme', 'light');
  else root.removeAttribute('data-theme');

  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', THEME_COLOR[theme] ?? THEME_COLOR.dark);
}

export function ThemeProvider({ children }) {
  // Initialised from the DOM, not from storage. `theme-boot.js` has already
  // resolved and applied the theme synchronously, so the attribute is the
  // authority at mount: reading storage again here could disagree with what the
  // user is currently looking at if storage became unreadable between the two.
  const [theme, setThemeState] = useState(() => {
    if (typeof document === 'undefined') return 'dark';
    return document.documentElement.getAttribute('data-theme') === 'light'
      ? 'light'
      : 'dark';
  });

  const setTheme = useCallback((next) => {
    if (!isValid(next)) return;
    setThemeState((current) => {
      if (current === next) return current;
      applyTheme(next);
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // A blocked store means the choice does not survive a reload. The theme
        // still changes for this session, which is better than refusing to
        // switch, and there is nothing honest to show the user about it.
      }
      return next;
    });
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'light' ? 'dark' : 'light');
  }, [theme, setTheme]);

  // Follow the OS only while the user has made no choice of their own.
  useEffect(() => {
    if (readStoredTheme() !== null) return undefined;
    let mql;
    try {
      mql = window.matchMedia?.('(prefers-color-scheme: light)');
    } catch {
      return undefined;
    }
    if (!mql?.addEventListener) return undefined;

    const onChange = (event) => {
      if (readStoredTheme() !== null) return; // chose while we were listening
      const next = event.matches ? 'light' : 'dark';
      setThemeState((current) => {
        if (current === next) return current;
        applyTheme(next);
        return next;
      });
    };
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  const value = useMemo(
    () => ({ theme, setTheme, toggleTheme, isLight: theme === 'light' }),
    [theme, setTheme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/**
 * The active theme and the controls to change it.
 *
 * Usable outside the provider on purpose: it falls back to reading the document
 * attribute and a no-op setter, so a component rendered in a test without the
 * provider does not throw. Theming is presentation — a missing provider should
 * not be able to break a page.
 */
export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (ctx) return ctx;
  const theme =
    typeof document !== 'undefined' &&
    document.documentElement.getAttribute('data-theme') === 'light'
      ? 'light'
      : 'dark';
  return {
    theme,
    isLight: theme === 'light',
    setTheme: () => {},
    toggleTheme: () => {},
  };
}

export default ThemeProvider;
