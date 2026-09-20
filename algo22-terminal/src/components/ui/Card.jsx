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
    ${elevated ? 'bg-bg-elevated shadow-md' : 'bg-bg-surface'}
    border border-border-default
    rounded-xl
    transition-all duration-300
    ${hover ? 'hover:border-cyan-500/40 hover:-translate-y-0.5 cursor-pointer' : ''}
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
