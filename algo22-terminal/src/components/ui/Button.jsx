/**
 * ui/Button — the application's button, retokened.
 *
 * ═══ WHY THIS CHANGED ═══
 *
 * `ds/CommandButton` wraps this component, and `CommandButton` is how every authenticated page
 * renders an action — so whatever this file decides, the whole app wears. It was deciding four
 * things off-system:
 *
 * 1. FIVE RAW COLOUR LITERALS. `text-[#080A0E]` twice (which is `content-inverse`, and the
 *    colour budget's own notes say so: "`content.inverse` is `#080A0E` and is what
 *    `components/ui/Button.jsx`'s own `text-[#080A0E]` was reaching for"), `hover:bg-[#33E0FF]`
 *    (`brand-hover`), `ring-offset-[#080A0E]`, and `hover:bg-[#FCD34D]` — an amber with no token
 *    behind it at all. Its budget entry goes 5 → 0.
 *
 * 2. AN OFF-PALETTE HOVER. `secondary` used `hover:border-cyan-500/50`, which is Tailwind's
 *    default cyan ramp, not the brand. It survived because the default palette is still present
 *    in the theme, so it compiled and looked approximately right while being a different colour
 *    from every other focus and hover in the product.
 *
 * 3. MONOSPACE LABELS. Every button in the application rendered in JetBrains Mono. `App.jsx`
 *    states the rule this breaks, in the comment on the shell wrapper: the shell inherits Inter
 *    "and mono is applied locally — via `font-mono` — to numeric cells, identifiers, timestamps
 *    and code only". A button label is none of those. Buttons are sans now, which is why they
 *    stop looking like a different product from the inputs beside them.
 *
 * 4. A RADIUS NOTHING ELSE USES. `rounded-lg` (8px) is `ds/Panel`'s radius — the CARD radius.
 *    Every control in `ds/` is `rounded-sm`: `ActionControl`, `Field`, `FilterBar`,
 *    `ErrorState`'s retry, `RiskIndicator`'s track. A button at card radius beside an input at
 *    control radius is the mismatch that reads as two design systems, so this is `rounded-sm`.
 *
 * ═══ TYPE SCALE ═══
 *
 * The four sizes used raw `text-xs` / `text-sm` / `text-base` (12/14/16px), which is not the
 * app's scale. They now use the declared steps — `text-small`, `text-body`, `text-title`
 * (11/13/14px) — matching `ds/DataTable`'s cells and `ds/Field`'s inputs. Padding is unchanged,
 * so no layout moves; only the glyph size comes onto the system.
 *
 * ═══ WHAT IS DELIBERATELY UNCHANGED ═══
 *
 * Every variant and size key, the `v`/`sz`/`cls`/`icon` aliases, the `to`/`href` polymorphism,
 * the hover/press/focus state handlers and the computed `aria-label`. This is a retokening, not a
 * behaviour change. `portfolio-rendering.test.jsx` documents that `ds/CommandButton` spreads `to`
 * here and gets an unconditional react-router `<Link>` back; that path is untouched.
 *
 * The transition drops from 300ms to 150ms, which is the one behavioural nudge: `tokens.css`
 * declares `--transition-fast: 120ms` and `--transition-base: 180ms`, and 300ms on a button that
 * also lifts and scales read as sluggish against every other control in the shell.
 */

import React, { useState } from 'react';
import { Link } from 'react-router-dom';

const variants = {
  primary: 'bg-brand text-content-inverse font-semibold hover:bg-brand-hover',
  outline:
    'bg-transparent border border-line-default text-brand hover:bg-surface-raised hover:border-line-strong',
  ghost: 'bg-transparent text-content-secondary hover:text-content-primary hover:bg-surface-raised',
  danger:
    'bg-status-loss-wash border border-status-loss/30 text-status-loss hover:bg-status-loss/20',
  success:
    'bg-status-profit-wash border border-status-profit/30 text-status-profit hover:bg-status-profit/20',
  gold: 'bg-status-warning text-content-inverse font-semibold hover:bg-status-warning/90',
  secondary:
    'bg-surface-inset text-content-primary border border-line-default hover:border-line-strong',
};

const sizes = {
  xs: 'px-3 py-1.5 text-small',
  sm: 'px-4 py-2 text-body',
  md: 'px-5 py-2.5 text-body',
  lg: 'px-6 py-3 text-title',
};

export function Button({
  children,
  variant = 'primary',
  v,
  size = 'md',
  sz,
  to,
  href,
  onClick,
  disabled = false,
  Icon,
  icon,
  className = '',
  cls = '',
  'aria-label': ariaLabel,
  ...props
}) {
  const [isHovered, setIsHovered] = useState(false);
  const [isPressed, setIsPressed] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  const activeVariant = v || variant;
  const activeSize = sz || size;
  const iconComponent = Icon || icon;

  const calculatedAriaLabel = ariaLabel || (typeof children === 'string' ? children : undefined);

  const combinedClass = `
    inline-flex items-center justify-center
    rounded-sm font-medium
    transition-all duration-150
    ${variants[activeVariant] || variants.primary}
    ${sizes[activeSize] || sizes.md}
    ${disabled ? 'opacity-40 cursor-not-allowed pointer-events-none' : 'cursor-pointer'}
    ${isPressed ? 'scale-95' : ''}
    ${isHovered && !disabled ? '-translate-y-px' : ''}
    ${isFocused ? 'ring-2 ring-brand ring-offset-2 ring-offset-surface-canvas' : 'focus:outline-none'}
    ${className} ${cls}
  `.trim();

  const iconElement = iconComponent ? (
    <span className="mr-1.5 inline-flex items-center">
      {React.createElement(iconComponent, {
        size: activeSize === 'xs' ? 12 : activeSize === 'sm' ? 14 : activeSize === 'md' ? 16 : 18,
        className: iconComponent.displayName === 'Loader2' ? 'animate-spin' : '',
        'aria-hidden': true,
      })}
    </span>
  ) : null;

  const buttonProps = {
    ...props,
    className: combinedClass,
    disabled,
    onClick,
    'aria-label': calculatedAriaLabel,
    tabIndex: disabled ? -1 : 0,
    onMouseEnter: () => setIsHovered(true),
    onMouseLeave: () => { setIsHovered(false); setIsPressed(false); },
    onMouseDown: () => setIsPressed(true),
    onMouseUp: () => setIsPressed(false),
    onFocus: () => setIsFocused(true),
    onBlur: () => setIsFocused(false),
  };

  if (to) return <Link to={to} {...buttonProps}>{iconElement}{children}</Link>;
  if (href) return <a href={href} {...buttonProps}>{iconElement}{children}</a>;
  return <button type="button" {...buttonProps}>{iconElement}{children}</button>;
}
