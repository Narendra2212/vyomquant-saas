import React, { useState } from 'react'
import { ChevronDown } from 'lucide-react'

export function Accordion({ items, defaultOpen = [] }) {
  const [openItems, setOpenItems] = useState(new Set(defaultOpen))
  const toggle = (index) => {
    const next = new Set(openItems)
    if (next.has(index)) next.delete(index)
    else next.add(index)
    setOpenItems(next)
  }
  return (
    <div className="space-y-3">
      {items.map((item, index) => {
        const isOpen = openItems.has(index)
        return (
          <div key={index} className="card-surface overflow-hidden transition-all duration-250">
            <button onClick={() => toggle(index)} className="w-full flex items-center justify-between p-5 text-left">
              <span className={`font-medium text-sm transition-colors ${isOpen ? 'text-accent-cyan' : 'text-text-primary'}`}>
                {item.question}
              </span>
              <ChevronDown className={`w-4 h-4 text-text-muted transition-transform duration-200 flex-shrink-0 ml-4 ${isOpen ? 'rotate-180' : ''}`} />
            </button>
            <div className="overflow-hidden transition-all duration-250" style={{ maxHeight: isOpen ? '500px' : '0' }}>
              <div className="px-5 pb-5 text-text-secondary text-sm leading-relaxed font-mono">
                {item.answer}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
