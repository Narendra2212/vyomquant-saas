import React, { useState, useRef, useMemo, useCallback } from 'react';
import { AgGridReact } from 'ag-grid-react'; // React Grid Logic
import "ag-grid-community/styles/ag-grid.css"; // Core CSS
import "ag-grid-community/styles/ag-theme-alpine.css"; // Theme

const ExecutionBlotter = () => {
  const gridRef = useRef();

  // Mock institutional execution data
  const [rowData] = useState([
    { signal: 'LONG', strategy: 'Alpha V1', exchange: 'Binance', order: 'Market', fill: '100%', latency: '45ms', status: 'FILLED', timestamp: '2026-06-03T10:00:01' },
    { signal: 'SHORT', strategy: 'LSTM Trend', exchange: 'OKX', order: 'Limit', fill: '50%', latency: '120ms', status: 'PARTIAL', timestamp: '2026-06-03T10:01:12' },
    { signal: 'LONG', strategy: 'RSI Reversion', exchange: 'Bybit', order: 'Market', fill: '0%', latency: '210ms', status: 'REJECTED', timestamp: '2026-06-03T10:05:00' },
    { signal: 'SHORT', strategy: 'Alpha V1', exchange: 'Binance', order: 'Market', fill: '100%', latency: '48ms', status: 'FILLED', timestamp: '2026-06-03T10:15:33' },
  ]);

  // Column definitions matching the spec (Pinning, Filtering, Grouping enabled)
  const [columnDefs] = useState([
    { field: 'timestamp', headerName: 'Timestamp', sortable: true, filter: 'agDateColumnFilter', pinned: 'left', width: 180 },
    { field: 'signal', headerName: 'Signal', sortable: true, filter: true, width: 100, 
      cellStyle: params => ({ color: params.value === 'LONG' ? '#00ff00' : '#ff4444', fontWeight: 'bold' }) },
    { field: 'strategy', headerName: 'Strategy', sortable: true, filter: true, enableRowGroup: true, width: 150 },
    { field: 'exchange', headerName: 'Exchange', sortable: true, filter: true, enableRowGroup: true, width: 120 },
    { field: 'order', headerName: 'Order Type', sortable: true, filter: true, width: 120 },
    { field: 'fill', headerName: 'Fill %', sortable: true, filter: 'agNumberColumnFilter', width: 100 },
    { field: 'latency', headerName: 'Latency', sortable: true, filter: true, width: 110 },
    { field: 'status', headerName: 'Status', sortable: true, filter: true, pinned: 'right', width: 120,
      cellStyle: params => {
        if (params.value === 'FILLED') return { color: '#00ff00' };
        if (params.value === 'PARTIAL') return { color: '#ffaa00' };
        if (params.value === 'REJECTED') return { color: '#ff4444' };
        return null;
      }
    },
  ]);

  const defaultColDef = useMemo(() => ({
    resizable: true,
    flex: 1,
    minWidth: 100,
  }), []);

  return (
    <div style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column', background: '#121212' }}>
      <div style={{ padding: '8px 16px', background: '#1a1a1a', borderBottom: '1px solid #333', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ color: '#fff', fontFamily: 'JetBrains Mono, monospace', fontSize: '14px', fontWeight: 'bold' }}>
          EXECUTION BLOTTER
        </div>
        <div>
          <button style={btnStyle} onClick={() => gridRef.current.api.exportDataAsCsv()}>Export CSV</button>
        </div>
      </div>
      
      {/* AG Grid container: using alpine-dark theme for terminal feel */}
      <div className="ag-theme-alpine-dark" style={{ flex: 1, width: '100%' }}>
        <AgGridReact
          ref={gridRef}
          rowData={rowData}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          rowSelection={'multiple'}
          animateRows={true}
          // Requirements: Virtualization is enabled by default in AG Grid
          // Keyboard navigation enabled by default in AG Grid
          enableRangeSelection={true} // For excel-like highlighting
          rowGroupPanelShow={'always'} // Allows drag-and-drop grouping
        />
      </div>
    </div>
  );
};

const btnStyle = {
  background: '#333',
  color: '#fff',
  border: '1px solid #555',
  padding: '4px 12px',
  cursor: 'pointer',
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: '12px'
};

export default ExecutionBlotter;
