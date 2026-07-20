import React, { useEffect } from 'react';

const OperatorMode = () => {
  useEffect(() => {
    const handleGlobalShortcuts = (e) => {
      // Ignore if typing in an input
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        // Exception: ESC can still trigger kill switch even if typing
        if (e.key === 'Escape') {
          console.warn('KILL SWITCH ENGAGED VIA ESC!');
          alert('KILL SWITCH ENGAGED! ALL ALGORITHMIC TRADING HALTED.');
          return;
        }
        return;
      }

      // ESC = Kill Switch
      if (e.key === 'Escape') {
        e.preventDefault();
        console.warn('KILL SWITCH ENGAGED VIA ESC!');
        alert('KILL SWITCH ENGAGED! ALL ALGORITHMIC TRADING HALTED.');
      }
      
      // Ctrl+Shift+K = Global Halt
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        console.warn('GLOBAL HALT ENGAGED!');
        alert('GLOBAL HALT ENGAGED! ALL SYSTEMS PAUSED.');
      }
      
      // Ctrl+B = Bot Manager
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b' && !e.shiftKey) {
        e.preventDefault();
        console.log('Navigating to Bot Manager...');
      }

      // Ctrl+R = Risk Center
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r' && !e.shiftKey) {
        e.preventDefault();
        console.log('Navigating to Risk Center...');
      }

      // Ctrl+P = Portfolio
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'p' && !e.shiftKey) {
        e.preventDefault();
        console.log('Navigating to Portfolio...');
      }

      // Ctrl+O = Orders
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'o' && !e.shiftKey) {
        e.preventDefault();
        console.log('Navigating to Orders...');
      }
    };

    window.addEventListener('keydown', handleGlobalShortcuts);
    return () => window.removeEventListener('keydown', handleGlobalShortcuts);
  }, []);

  return null; // This component handles side-effects only
};

export default OperatorMode;
