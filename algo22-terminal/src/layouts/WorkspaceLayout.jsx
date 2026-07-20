import React, { useState, useEffect } from 'react';
import GridLayout from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

// Import the migrated institutional components
import StrategyBuilder from '../components/StrategyBuilder';
import ExecutionBlotter from '../components/ExecutionBlotter';
import RiskCommandCenter from '../components/RiskCommandCenter';
import MonitoringTerminal from '../components/MonitoringTerminal';
import EventLogPanel from '../components/EventLogPanel'; // existing component

const DEFAULT_LAYOUT = [
  { i: 'leftPanel', x: 0, y: 0, w: 2, h: 8 },
  { i: 'centerPanel', x: 2, y: 0, w: 6, h: 5 },
  { i: 'rightPanel', x: 8, y: 0, w: 4, h: 5 },
  { i: 'bottomPanel', x: 2, y: 5, w: 10, h: 3 },
];

const DEFAULT_STATE = {
  layout: DEFAULT_LAYOUT,
  activePanels: ['leftPanel', 'centerPanel', 'rightPanel', 'bottomPanel'],
  theme: 'terminal-dark'
};

const WorkspaceLayout = () => {
  const [workspaceState, setWorkspaceState] = useState(() => {
    try {
      const saved = localStorage.getItem('aerora_workspace_state');
      if (saved) return JSON.parse(saved);
    } catch (e) {
      console.warn("Failed to load layout from local storage", e);
    }
    return DEFAULT_STATE;
  });

  useEffect(() => {
    localStorage.setItem('aerora_workspace_state', JSON.stringify(workspaceState));
  }, [workspaceState]);

  const handleLayoutChange = (newLayout) => {
    setWorkspaceState(prev => ({
      ...prev,
      layout: newLayout
    }));
  };

  const panelStyle = {
    background: '#121212',
    border: '1px solid #333',
    color: '#00ff00',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden'
  };

  const headerStyle = {
    background: '#1a1a1a',
    padding: '4px 8px',
    fontSize: '12px',
    fontWeight: 'bold',
    borderBottom: '1px solid #333',
    cursor: 'move',
    color: '#fff',
    fontFamily: 'JetBrains Mono, monospace'
  };

  const contentStyle = {
    flex: 1,
    overflow: 'auto',
    position: 'relative'
  };

  return (
    <div className={`workspace-container ${workspaceState.theme}`} style={{ background: '#000', minHeight: '100vh', padding: '8px' }}>
      <GridLayout 
        className="layout" 
        layout={workspaceState.layout} 
        cols={12} 
        rowHeight={100} 
        width={1600}
        onLayoutChange={handleLayoutChange}
        draggableHandle=".panel-header"
      >
        
        {/* LEFT: Strategy Explorer & Portfolio Explorer */}
        <div key="leftPanel" style={panelStyle}>
          <div className="panel-header" style={headerStyle}>EXPLORER</div>
          <div style={{ ...contentStyle, padding: '8px', fontSize: '12px', fontFamily: 'JetBrains Mono, monospace' }}>
            <div style={{ marginBottom: '16px' }}>
              <div style={{ color: '#888', marginBottom: '8px' }}>STRATEGIES</div>
              <div style={{ padding: '4px', background: '#1a1a1a', borderLeft: '2px solid #00ff00' }}>Alpha V1 (Running)</div>
              <div style={{ padding: '4px', background: '#1a1a1a' }}>LSTM Trend (Paused)</div>
            </div>
            <div>
              <div style={{ color: '#888', marginBottom: '8px' }}>PORTFOLIO</div>
              <div style={{ padding: '4px' }}>Binance: $45,210</div>
              <div style={{ padding: '4px' }}>Bybit: $12,400</div>
            </div>
          </div>
        </div>

        {/* CENTER: Strategy Builder & Signal Tracing */}
        <div key="centerPanel" style={panelStyle}>
          <div className="panel-header" style={headerStyle}>STRATEGY BUILDER / CHARTING</div>
          <div style={{ ...contentStyle, overflow: 'hidden' }}>
            {/* Embedded StrategyBuilder Elite */}
            <StrategyBuilder />
          </div>
        </div>

        {/* RIGHT: Orders, Positions, Risk */}
        <div key="rightPanel" style={panelStyle}>
          <div className="panel-header" style={headerStyle}>RISK COMMAND CENTER</div>
          <div style={contentStyle}>
            {/* Embedded Risk Command Center */}
            <RiskCommandCenter />
          </div>
        </div>

        {/* BOTTOM: Logs, Execution, Monitoring */}
        <div key="bottomPanel" style={panelStyle}>
          <div className="panel-header" style={headerStyle}>EXECUTION & MONITORING TERMINAL</div>
          <div style={{ display: 'flex', height: '100%', width: '100%' }}>
            <div style={{ flex: 1, borderRight: '1px solid #333' }}>
              <ExecutionBlotter />
            </div>
            <div style={{ flex: 1 }}>
              <MonitoringTerminal />
            </div>
          </div>
        </div>

      </GridLayout>
    </div>
  );
};

export default WorkspaceLayout;
