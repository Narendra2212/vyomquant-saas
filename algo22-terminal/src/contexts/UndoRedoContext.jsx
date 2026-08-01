/**
 * Undo/Redo System for Strategy Builder
 * 
 * PHASE F: Undo/Redo functionality
 * Supports: Move, Connect, Delete, Create, Duplicate, Parameter changes
 */

import { createContext, useContext, useState, useCallback } from 'react';

const UndoRedoContext = createContext(null);

export const useUndoRedo = () => {
  const context = useContext(UndoRedoContext);
  if (!context) throw new Error("useUndoRedo must be used within UndoRedoProvider");
  return context;
};

export const UndoRedoProvider = ({ children }) => {
  const [history, setHistory] = useState([]);
  const [currentIndex, setCurrentIndex] = useState(-1);
  const [maxHistory, setMaxHistory] = useState(100);

  const canUndo = currentIndex > 0;
  const canRedo = currentIndex < history.length - 1;

  const pushState = useCallback((state) => {
    setHistory(prev => {
      const newHistory = prev.slice(0, currentIndex + 1);
      if (newHistory.length >= maxHistory) {
        newHistory.shift();
      } else {
        setCurrentIndex(prev => Math.min(prev + 1, maxHistory - 1));
      }
      return [...newHistory, { ...state, timestamp: Date.now() }];
    });
  }, [currentIndex, maxHistory]);

  const undo = useCallback(() => {
    if (canUndo) {
      setCurrentIndex(prev => prev - 1);
      return history[currentIndex - 1];
    }
    return null;
  }, [canUndo, currentIndex, history]);

  const redo = useCallback(() => {
    if (canRedo) {
      setCurrentIndex(prev => prev + 1);
      return history[currentIndex + 1];
    }
    return null;
  }, [canRedo, currentIndex, history]);

  const reset = useCallback(() => {
    setHistory([]);
    setCurrentIndex(-1);
  }, []);

  const value = {
    history,
    currentIndex,
    canUndo,
    canRedo,
    pushState,
    undo,
    redo,
    reset
  };

  return (
    <UndoRedoContext.Provider value={value}>
      {children}
    </UndoRedoContext.Provider>
  );
};