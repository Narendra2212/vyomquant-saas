import React from 'react';

const Button = ({ 
  children, 
  variant = 'primary', 
  size = 'md', 
  icon: Icon, 
  onClick, 
  disabled = false, 
  className = '',
  style = {}
}) => {
  const variants = {
    primary: 'bg-cyan text-black font-bold hover:bg-cyan-90 hover:shadow-glow active:scale-95',
    secondary: 'bg-bg-3 text-text-1 border border-border hover:border-cyan-500/50 hover:-translate-y-0.5 active:scale-95',
    ghost: 'bg-transparent text-text-2 hover:text-cyan hover:bg-cyan/5 active:scale-95',
    outline: 'bg-transparent text-cyan border border-cyan/50 hover:bg-cyan/10 hover:shadow-glow active:scale-95',
    danger: 'bg-red/20 text-red border border-red/50 hover:bg-red/30 hover:-translate-y-0.5 active:scale-95',
  };

  const sizes = {
    xs: 'px-3 py-1.5 text-xs',
    sm: 'px-4 py-2 text-sm',
    md: 'px-5 py-2.5 text-sm',
    lg: 'px-6 py-3 text-base',
  };

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        rounded-lg font-mono font-semibold
        transition-all duration-300
        active:scale-95
        disabled:opacity-50 disabled:cursor-not-allowed
        flex items-center justify-center gap-2
        ${variants[variant]}
        ${sizes[size]}
        ${className}
      `}
      style={style}
    >
      {Icon && <Icon size={size === 'xs' ? 14 : size === 'sm' ? 16 : 18} />}
      {children}
    </button>
  );
};

export default Button;
