import React from 'react';
import { Link } from 'react-router-dom';

const variants = {
  primary: 'btn-primary',
  ghost: 'btn-ghost',
  gold: 'btn-gold',
  outline: 'inline-flex items-center justify-center px-4 py-2 rounded-lg border border-[#1E2530] text-[#00D4FF] hover:bg-[#151821] hover:border-[#2A3441] transition-all font-mono font-medium text-sm',
  danger: 'inline-flex items-center justify-center px-4 py-2 rounded-lg bg-[#EF5350]/15 border border-[#EF5350]/30 text-[#EF5350] font-semibold text-sm transition-all duration-200 hover:-translate-y-px hover:shadow-[0_0_30px_rgba(239,83,80,0.25)] active:translate-y-0',
  success: 'inline-flex items-center justify-center px-4 py-2 rounded-lg bg-[#26A69A]/15 border border-[#26A69A]/30 text-[#26A69A] font-semibold text-sm transition-all duration-200 hover:-translate-y-px hover:shadow-[0_0_30px_rgba(38,166,154,0.25)] active:translate-y-0',
};

export function Button({
  children,
  variant = 'primary',
  v,
  to,
  href,
  onClick,
  disabled,
  Icon,
  className = '',
  cls = '',
  ...props
}) {
  const activeVariant = v || variant;
  const combinedClass = `${variants[activeVariant] || variants.primary} ${className} ${cls} ${disabled ? 'opacity-40 cursor-not-allowed pointer-events-none' : ''}`.trim();
  const iconElement = Icon ? <Icon className="w-4 h-4 mr-1.5 inline-block" /> : null;

  if (to) return <Link to={to} className={combinedClass} {...props}>{iconElement}{children}</Link>;
  if (href) return <a href={href} className={combinedClass} {...props}>{iconElement}{children}</a>;
  return <button onClick={onClick} disabled={disabled} className={combinedClass} {...props}>{iconElement}{children}</button>;
}
