import React from 'react';
import { Clock, ChevronRight } from 'lucide-react';

const ActionableEmptyState = ({ 
  icon: Icon, 
  title, 
  description, 
  timeEstimate, 
  actions = [], // Array of { label, onClick, variant: 'primary' | 'secondary' | 'outline' }
  color = '#2962FF'
}) => {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-6 bg-[#080A0D] border border-[#202938] rounded max-w-2xl mx-auto w-full text-center">
      <div className="mb-4">
        <Icon size={32} color={color} />
      </div>
      <h3 className="text-xl font-bold font-mono text-[#E6EDF3] mb-3">{title}</h3>
      <p className="text-[#8B949E] text-sm max-w-md mx-auto mb-8 leading-relaxed">
        {description}
      </p>
      
      <div className="flex flex-col sm:flex-row flex-wrap items-center justify-center gap-4">
        {actions.map((act, i) => (
          <button 
            key={i}
            onClick={act.onClick}
            className={`flex items-center gap-2 px-4 py-2 rounded font-mono font-bold text-sm transition-colors ${
              act.variant === 'primary' ? 'text-white' : 
              act.variant === 'secondary' ? 'bg-[#1A222C] text-[#E6EDF3] border border-[#202938] hover:bg-[#202938]' : 
              'bg-transparent border border-[#2962FF] text-[#2962FF] hover:bg-[#2962FF]20'
            }`}
            style={act.variant === 'primary' ? { backgroundColor: color } : {}}
          >
            {act.label} {act.variant === 'primary' && <ChevronRight size={16} />}
          </button>
        ))}
        
        {timeEstimate && (
          <div className="flex items-center gap-2 text-xs font-mono text-[#8B949E] px-4 py-3 bg-[#131722] rounded-lg border border-[#202938]">
            <Clock size={14} className="text-[#26A69A]" />
            EST. TIME: {timeEstimate}
          </div>
        )}
      </div>
    </div>
  );
};

export default ActionableEmptyState;
