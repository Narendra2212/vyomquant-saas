import React from 'react'

export function Card({ children, className = '', elevated = false, onClick }) {
  return (
    <div onClick={onClick} className={`${elevated ? 'card-elevated' : 'card-surface'} p-6 ${className}`}>
      {children}
    </div>
  )
}
