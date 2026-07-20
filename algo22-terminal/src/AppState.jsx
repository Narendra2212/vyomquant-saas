import React, { createContext, useContext, useState } from 'react';

export const AppStateContext = createContext(null);
export const useAppState = () => useContext(AppStateContext);

export const AppStateProvider = ({ children }) => {
  const [demoMode, setDemoMode] = useState(false);
  const [uiMode, setUiMode] = useState('pro');
  
  return (
    <AppStateContext.Provider value={{ demoMode, setDemoMode, uiMode, setUiMode }}>
      {children}
    </AppStateContext.Provider>
  );
};
