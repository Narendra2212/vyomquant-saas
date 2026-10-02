/**
 * ui/Card — the legacy card shell.
 *
 * THE HOVER BORDER IS `brand` NOW. It reached for Tailwind's own default `cyan-500` ramp
 * (#06B6D4) at the same `/40` alpha — a hue with no token behind it, and a different cyan
 * from the brand (#00D4FF), on every hoverable card in the application. It compiled and
 * looked approximately right, which is why it survived; `ui/Button.jsx`'s `secondary`
 * variant carried the identical mistake and was corrected the same way. Only the hue moved:
 * the alpha, the radius, the lift and the transition are untouched.
 *
 * The retired utility is deliberately NOT spelled out in full anywhere in this file.
 * Tailwind v4's content detection is a project-wide TEXT scan, so a complete class name
 * written in a comment is emitted into `dist/` as if it had a real call site — see the
 * `@source not` note in `src/index.css`.
 *
 * Everything else here is already on tokens: `bg-surface-raised` / `bg-surface-panel` and
 * `border-line-default`.
 */

import React from 'react';

export function Card({ 
  children, 
  className = '', 
  cls = '', 
  elevated = false, 
  hover = false,
  onClick,
  ...props 
}) {
  const combinedClass = `
    ${elevated ? 'bg-surface-raised shadow-md' : 'bg-surface-panel'}
    border border-line-default
    rounded-lg
    transition-all duration-300
    ${hover ? 'hover:border-brand/40 hover:-translate-y-0.5 cursor-pointer' : ''}
    ${onClick ? 'cursor-pointer' : ''}
    p-6
    ${className} ${cls}
  `.trim();

  // A card with an `onClick` is an activation surface, and a pointer-only one cannot be
  // reached without a mouse. So the clickable card carries the button role, a tab stop and
  // Enter/Space, while a non-clickable card stays a plain div rather than becoming a stray
  // tab stop. Two branches rather than a spread object, so the pairing of handler and
  // keyboard path is visible on the element itself.
  // `{...props}` is spread last in both, so a caller supplying its own role, tabIndex or
  // onKeyDown still wins.
  if (onClick) {
    return (
      <div
        role="button"
        tabIndex={0}
        onClick={onClick}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
            // Space scrolls the page by default, which would move the card out from under
            // the person who just activated it.
            event.preventDefault();
            onClick(event);
          }
        }}
        className={combinedClass}
        {...props}
      >
        {children}
      </div>
    );
  }

  return (
    <div className={combinedClass} {...props}>
      {children}
    </div>
  );
}
