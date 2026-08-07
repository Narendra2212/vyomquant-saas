import React, { useState } from 'react';
import { Link } from 'react-router-dom';

const variants = {
  primary: 'bg-accent-cyan text-[#080A0E] font-semibold hover:bg-[#33E0FF] transition-all',
  outline: 'bg-transparent border border-border-default text-accent-cyan hover:bg-bg-elevated hover:border-border-active transition-all',
  ghost: 'bg-transparent text-text-secondary hover:text-text-primary hover:bg-bg-elevated transition-all',
  danger: 'bg-accent-loss-dim border border-accent-loss/30 text-accent-loss hover:bg-accent-loss/20 transition-all',
  success: 'bg-accent-profit-dim border border-accent-profit/30 text-accent-profit hover:bg-accent-profit/20 transition-all',
  gold: 'bg-accent-gold text-[#080A0E] font-semibold hover:bg-[#FCD34D] transition-all',
  secondary: 'bg-bg-3 text-text-primary border border-border hover:border-cyan-500/50 transition-all',
};

const sizes = {
  xs: 'px-3 py-1.5 text-xs',
  sm: 'px-4 py-2 text-sm',
  md: 'px-5 py-2.5 text-sm',
  lg: 'px-6 py-3 text-base',
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
    rounded-lg font-mono font-medium
    transition-all duration-300
    ${variants[activeVariant] || variants.primary}
    ${sizes[activeSize] || sizes.md}
    ${disabled ? 'opacity-40 cursor-not-allowed pointer-events-none' : 'cursor-pointer'}
    ${isPressed ? 'scale-95' : ''}
    ${isHovered && !disabled ? '-translate-y-px' : ''}
    ${isFocused ? 'ring-2 ring-accent-cyan ring-offset-2 ring-offset-[#080A0E]' : 'focus:outline-none'}
    ${className} ${cls}
  `.trim();

  const iconElement = iconComponent ? (
    <span className="mr-1.5 inline-flex items-center">
      {React.createElement(iconComponent, { 
        size: activeSize === 'xs' ? 12 : activeSize === 'sm' ? 14 : activeSize === 'md' ? 16 : 18,
        className: iconComponent.displayName === 'Loader2' ? 'animate-spin' : '',
        'aria-hidden': true 
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
