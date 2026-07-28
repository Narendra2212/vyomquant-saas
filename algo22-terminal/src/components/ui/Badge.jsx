import React from 'react';

const variants = {
  cyan: 'bg-[#00D4FF]/15 text-[#00D4FF] border border-[#00D4FF]/30',
  gold: 'bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/30',
  profit: 'bg-[#26A69A]/15 text-[#26A69A] border border-[#26A69A]/30',
  green: 'bg-[#26A69A]/15 text-[#26A69A] border border-[#26A69A]/30',
  loss: 'bg-[#EF5350]/15 text-[#EF5350] border border-[#EF5350]/30',
  red: 'bg-[#EF5350]/15 text-[#EF5350] border border-[#EF5350]/30',
  muted: 'bg-[#151821] text-[#8B95A5] border border-[#1E2530]',
};

export function Badge({ children, variant = 'cyan', c, className = '' }) {
  const activeVariant = c || variant;
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded text-xs font-mono font-medium uppercase tracking-wider ${variants[activeVariant] || variants.cyan} ${className}`}>
      {children}
    </span>
  );
}
