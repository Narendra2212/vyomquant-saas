import React from 'react';

const Card = ({ children, className = '', style = {}, hover = false }) => {
  return (
    <div
      className={`bg-bg-2 border border-border rounded-xl transition-all duration-300 ${hover ? 'hover:border-cyan-500/40 hover:shadow-glow hover:-translate-y-0.5' : ''} ${className}`}
      style={style}
    >
      {children}
    </div>
  );
};

export default Card;
