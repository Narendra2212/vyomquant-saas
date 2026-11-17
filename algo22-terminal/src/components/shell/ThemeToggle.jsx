/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/components/shell/ThemeToggle.jsx — the theme control in the top bar
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THEME-01. The only control that changes the theme.
 *
 * ---------------------------------------------------------------------------
 * IT TAKES NO `theme` PROP, FOR THE REASON ConnectionStatusIndicator TAKES NO
 * `status` PROP
 * ---------------------------------------------------------------------------
 * A caller that can pass the current theme in is a caller that can pass the
 * wrong one, and this control's entire job is to tell the truth about which
 * theme is active. It reads `useTheme()` itself. There is no code path through
 * which a call site can make the button disagree with the document.
 *
 * ---------------------------------------------------------------------------
 * WHY A BUTTON AND NOT A SWITCH
 * ---------------------------------------------------------------------------
 * `role="switch"` describes a control that turns one named thing on and off.
 * "Light mode: on" is a reading of the state that is only correct if dark is
 * understood as the absence of something, and dark is this product's default —
 * neither theme is the "off" position. So it is a plain button whose accessible
 * name states what activating it will DO ("Switch to light theme"), which is
 * what a screen-reader user needs in order to decide whether to press it. The
 * current theme is conveyed by `aria-pressed` being absent and by the name
 * naming the other theme, not by colour, which a theme control obviously cannot
 * rely on.
 *
 * The icon shows the theme you would GET, matching the label. Showing the
 * current theme's icon instead is the common mistake and leaves the button
 * ambiguous: a sun could equally mean "it is light" or "make it light".
 *
 * ---------------------------------------------------------------------------
 * NO TRANSITION ON THE SWITCH
 * ---------------------------------------------------------------------------
 * Deliberately no cross-fade. Every colour in the document changes at once, and
 * animating that is both expensive on a page with live tables and charts and
 * actively unpleasant — the intermediate frames are colours that exist in
 * neither theme. The switch is instant; `prefers-reduced-motion` is therefore
 * moot here rather than respected by accident.
 */
import { Moon, Sun } from 'lucide-react';

import { token } from '../../design/tokens';
import { useTheme } from '../../design/theme';

export function ThemeToggle({ compact = false }) {
  const { theme, toggleTheme } = useTheme();
  const goingTo = theme === 'light' ? 'dark' : 'light';

  // The icon of the theme the press would produce — see the header.
  const Icon = goingTo === 'light' ? Sun : Moon;
  const label = `Switch to ${goingTo} theme`;

  return (
    <button
      type="button"
      onClick={toggleTheme}
      title={label}
      aria-label={label}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: compact ? 0 : 6,
        height: 32,
        width: compact ? 32 : undefined,
        padding: compact ? 0 : '0 10px',
        background: token.surface.raised,
        border: `1px solid ${token.line.default}`,
        borderRadius: token.radius.md,
        color: token.content.secondary,
        cursor: 'pointer',
        fontFamily: token.font.mono,
        fontSize: token.text.micro,
        fontWeight: 600,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        transition: token.transition.fast,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = token.content.primary;
        e.currentTarget.style.borderColor = token.line.strong;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = token.content.secondary;
        e.currentTarget.style.borderColor = token.line.default;
      }}
    >
      {/* aria-hidden: the button's accessible name already says everything this
          glyph says, and an unlabelled duplicate is noise in a screen reader. */}
      <Icon size={14} aria-hidden="true" />
      {!compact && <span>{goingTo}</span>}
    </button>
  );
}

export default ThemeToggle;
