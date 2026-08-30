import React from 'react';

const variants = {
  cyan: 'bg-[#00D4FF]/10 text-[#00D4FF] border border-[#00D4FF]/30',
  gold: 'bg-[#F59E0B]/10 text-[#F59E0B] border border-[#F59E0B]/30',
  warning: 'bg-[#F59E0B]/10 text-[#F59E0B] border border-[#F59E0B]/30',
  profit: 'bg-[#10B981]/10 text-[#10B981] border border-[#10B981]/30',
  green: 'bg-[#10B981]/10 text-[#10B981] border border-[#10B981]/30',
  success: 'bg-[#10B981]/10 text-[#10B981] border border-[#10B981]/30',
  loss: 'bg-[#EF4444]/10 text-[#EF4444] border border-[#EF4444]/30',
  red: 'bg-[#EF4444]/10 text-[#EF4444] border border-[#EF4444]/30',
  danger: 'bg-[#EF4444]/10 text-[#EF4444] border border-[#EF4444]/30',
  muted: 'bg-[#151821] text-[#8B95A5] border border-[#1E2530]',
  running: 'bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/40',
  deployed: 'bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/40',
  active: 'bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/40',
  paused: 'bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/40',
  draft: 'bg-[#151821] text-[#94A3B8] border border-[#1E2530]',
  stopped: 'bg-[#151821] text-[#64748B] border border-[#1E2530]',
  failed: 'bg-[#EF4444]/15 text-[#EF4444] border border-[#EF4444]/40',
};

const dotColors = {
  cyan: 'bg-[#00D4FF]',
  gold: 'bg-[#F59E0B]',
  warning: 'bg-[#F59E0B]',
  profit: 'bg-[#10B981]',
  green: 'bg-[#10B981]',
  success: 'bg-[#10B981]',
  loss: 'bg-[#EF4444]',
  red: 'bg-[#EF4444]',
  danger: 'bg-[#EF4444]',
  muted: 'bg-[#8B95A5]',
  running: 'bg-[#10B981]',
  deployed: 'bg-[#10B981]',
  active: 'bg-[#10B981]',
  paused: 'bg-[#F59E0B]',
  draft: 'bg-[#94A3B8]',
  stopped: 'bg-[#64748B]',
  failed: 'bg-[#EF4444]',
};

export function Badge({ children, variant = 'cyan', c, dot = false, className = '' }) {
  const activeVariant = (c || variant || 'cyan').toLowerCase();
  const badgeClass = variants[activeVariant] || variants.cyan;
  const dotColorClass = dotColors[activeVariant] || dotColors.cyan;

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-xs font-mono font-medium uppercase tracking-wider ${badgeClass} ${className}`}>
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${dotColorClass} animate-pulse`} aria-hidden="true" />}
      {children}
    </span>
  );
}

