import React, { useState, useEffect, useRef } from 'react';

const COMMANDS = [
  { id: 'builder', label: 'Open Strategy Builder', action: () => console.log('Opening Strategy Builder...') },
  { id: 'portfolio', label: 'Open Portfolio', action: () => console.log('Opening Portfolio...') },
  { id: 'risk', label: 'Open Risk Center', action: () => console.log('Opening Risk Center...') },
  { id: 'monitor', label: 'Open Monitoring', action: () => console.log('Opening Monitoring...') },
  { id: 'exchange', label: 'Open Exchange Settings', action: () => console.log('Opening Exchange Settings...') },
  { id: 'deploy', label: 'Deploy Bot', action: () => console.log('Deploying Bot...') },
  { id: 'stop', label: 'Stop Bot', action: () => console.log('Stopping Bot...') },
  { id: 'kill', label: 'Enable Kill Switch', action: () => alert('KILL SWITCH ENGAGED! ALL TRADING HALTED.'), isDanger: true }
];

const CommandPalette = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef(null);

  // Filter commands (basic fuzzy search by converting to lowercase)
  const filteredCommands = COMMANDS.filter(c => 
    c.label.toLowerCase().includes(query.toLowerCase()) || 
    c.id.toLowerCase().includes(query.toLowerCase())
  );

  useEffect(() => {
    const handleKeyDown = (e) => {
      // Toggle with Ctrl+K or Cmd+K
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        setIsOpen((prev) => !prev);
      }
      
      if (!isOpen) return;

      if (e.key === 'Escape') {
        setIsOpen(false);
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % filteredCommands.length);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (filteredCommands[selectedIndex]) {
          filteredCommands[selectedIndex].action();
          setIsOpen(false);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, filteredCommands, selectedIndex]);

  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  if (!isOpen) return null;

  return (
    <div style={overlayStyle} onClick={() => setIsOpen(false)}>
      <div style={paletteStyle} onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search commands... (e.g. 'Deploy')"
          style={inputStyle}
        />
        <div style={listStyle}>
          {filteredCommands.length > 0 ? (
            filteredCommands.map((cmd, index) => (
              <div 
                key={cmd.id} 
                style={itemStyle(index === selectedIndex, cmd.isDanger)}
                onClick={() => {
                  cmd.action();
                  setIsOpen(false);
                }}
                onMouseEnter={() => setSelectedIndex(index)}
              >
                {cmd.label}
              </div>
            ))
          ) : (
            <div style={{ padding: '12px', color: '#666', textAlign: 'center' }}>No commands found.</div>
          )}
        </div>
      </div>
    </div>
  );
};

// Internal inline styles for the demo shell to ensure zero external dependencies
const overlayStyle = {
  position: 'fixed',
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  backgroundColor: 'rgba(0,0,0,0.7)',
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'flex-start',
  paddingTop: '15vh',
  zIndex: 99999,
  fontFamily: 'JetBrains Mono, monospace'
};

const paletteStyle = {
  width: '600px',
  backgroundColor: '#1a1a1a',
  border: '1px solid #333',
  borderRadius: '8px',
  boxShadow: '0 10px 30px rgba(0,0,0,0.5)',
  overflow: 'hidden',
  display: 'flex',
  flexDirection: 'column'
};

const inputStyle = {
  padding: '16px',
  fontSize: '16px',
  color: '#fff',
  backgroundColor: 'transparent',
  border: 'none',
  borderBottom: '1px solid #333',
  outline: 'none',
  width: '100%',
  boxSizing: 'border-box'
};

const listStyle = {
  maxHeight: '350px',
  overflowY: 'auto',
  padding: '8px'
};

const itemStyle = (isSelected, isDanger) => ({
  padding: '12px 16px',
  color: isDanger && isSelected ? '#ff4444' : isSelected ? '#fff' : '#aaa',
  backgroundColor: isSelected ? '#2d2d2d' : 'transparent',
  cursor: 'pointer',
  borderRadius: '4px',
  display: 'flex',
  alignItems: 'center',
  fontWeight: isSelected ? 'bold' : 'normal'
});

export default CommandPalette;
