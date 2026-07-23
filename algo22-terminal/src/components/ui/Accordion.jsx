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
        const contentId = `accordion-content-${index}`
        const buttonId = `accordion-button-${index}`
        
        return (
          <div 
            key={index} 
            className={`rounded-xl border transition-all duration-300 ${
              isOpen 
                ? 'border-accent-cyan/40 bg-bg-surface shadow-md' 
                : 'border-border-default/80 bg-bg-surface/60 hover:border-border-default hover:bg-bg-surface'
            }`}
          >
            <button 
              id={buttonId}
              aria-expanded={isOpen}
              aria-controls={contentId}
              onClick={() => toggle(index)} 
              className="w-full flex items-center justify-between p-5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-xl"
            >
              <span className={`font-semibold text-base transition-colors ${isOpen ? 'text-accent-cyan' : 'text-text-primary'}`}>
                {item.question}
              </span>
              <ChevronDown className={`w-5 h-5 text-text-muted transition-transform duration-300 flex-shrink-0 ml-4 ${isOpen ? 'rotate-180 text-accent-cyan' : ''}`} />
            </button>
            <div 
              id={contentId}
              role="region"
              aria-labelledby={buttonId}
              className="overflow-hidden transition-all duration-300 ease-in-out" 
              style={{ maxHeight: isOpen ? '600px' : '0', opacity: isOpen ? 1 : 0 }}
            >
              <div className="px-5 pb-5 pt-1 text-text-secondary text-sm leading-relaxed font-mono border-t border-border-default/40 mt-1">
                {item.answer}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
