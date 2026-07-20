import React from 'react';
import Card from './Card';

const StatCard = ({ label, value, icon: Icon, color = 'cyan', delta = null, className = '' }) => {
  const colors = {
    cyan: 'from-cyan/10 to-transparent',
    green: 'from-green/10 to-transparent',
    red: 'from-red/10 to-transparent',
    purple: 'from-purple/10 to-transparent',
    orange: 'from-orange/10 to-transparent',
    gold: 'from-gold/10 to-transparent',
  };

  const textColors = {
    cyan: 'text-cyan',
    green: 'text-green',
    red: 'text-red',
    purple: 'text-purple',
    orange: 'text-orange',
    gold: 'text-gold',
  };

  return (
    <Card className={`p-4 relative overflow-hidden hover:shadow-glow transition-all duration-300 ${className}`}>
      <div className={`absolute top-0 right-0 w-20 h-20 bg-gradient-to-br ${colors[color]} rounded-full blur-2xl opacity-50`} />
      <div className="flex items-center justify-between mb-3">
        <span className="text-text-3 text-[10px] font-mono font-bold tracking-widest uppercase">
          {label}
        </span>
        {Icon && <Icon size={16} className={textColors[color]} />}
      </div>
      <div className="text-text-1 text-2xl font-black font-mono">
        {value}
      </div>
      {delta !== null && (
        <div className={`text-xs font-mono mt-1 ${delta >= 0 ? 'text-green' : 'text-red'}`}>
          {delta >= 0 ? '▲' : '▼'} {Math.abs(delta)}%
        </div>
      )}
    </Card>
  );
};

export default StatCard;
