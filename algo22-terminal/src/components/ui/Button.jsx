import React from 'react'
import { Link } from 'react-router-dom'

const variants = {
  primary: 'btn-primary',
  ghost: 'btn-ghost',
  gold: 'btn-gold',
  danger: 'inline-flex items-center justify-center px-6 py-3 rounded-lg bg-accent-loss text-text-inverse font-semibold text-sm transition-all duration-200 hover:-translate-y-px hover:shadow-[0_0_30px_rgba(239,68,68,0.25)] active:translate-y-0',
}

export function Button({ children, variant = 'primary', to, href, onClick, disabled, className = '', ...props }) {
  const classes = `${variants[variant] || variants.primary} ${className} ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`
  if (to) return <Link to={to} className={classes} {...props}>{children}</Link>
  if (href) return <a href={href} className={classes} {...props}>{children}</a>
  return <button onClick={onClick} disabled={disabled} className={classes} {...props}>{children}</button>
}
