import React from 'react';

export function Card({ children, className = '', cls = '', elevated = false, onClick, ...props }) {
  const combinedClass = `${className} ${cls}`.trim();
  return (
    <div onClick={onClick} className={`${elevated ? 'card-elevated' : 'card-surface'} p-6 ${combinedClass}`} {...props}>
      {children}
    </div>
  );
}
