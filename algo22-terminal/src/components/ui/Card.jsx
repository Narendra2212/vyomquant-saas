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
    ${hover ? 'hover:border-cyan-500/40 hover:shadow-glow hover:-translate-y-0.5 cursor-pointer' : ''}
    ${onClick ? 'cursor-pointer' : ''}
    p-6
    ${className} ${cls}
  `.trim();

  return (
    <div onClick={onClick} className={combinedClass} {...props}>
      {children}
    </div>
  );
}
