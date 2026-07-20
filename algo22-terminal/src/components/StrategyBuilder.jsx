import React, { useState, useCallback } from 'react';
import ReactFlow, { MiniMap, Controls, Background, addEdge, applyNodeChanges, applyEdgeChanges } from 'reactflow';
import 'reactflow/dist/style.css';
import { Search, Play, Save, Plus, AlertTriangle, ShieldCheck, Download, Code, Layers } from 'lucide-react';

const NODE_CATEGORIES = {
  Indicators: ['RSI', 'MACD', 'Bollinger Bands', 'EMA'],
  'ML Models': ['Random Forest', 'LSTM', 'XGBoost', 'Deep Q-Network'],
  Risk: ['Max Drawdown Guard', 'Position Sizer', 'Trailing Stop', 'Exposure Limit'],
  Execution: ['Limit Order', 'Market Order', 'TWAP', 'Iceberg'],
  Exchange: ['Binance Stream', 'OKX Stream', 'Bybit Engine'],
  Portfolio: ['Balance Checker', 'Margin Utilization']
};

const TEMPLATES = [
  { name: 'Trend Following', desc: 'Moving Average Crossover with Risk Management' },
  { name: 'Mean Reversion', desc: 'RSI divergence with Bollinger Bands' },
  { name: 'Machine Learning', desc: 'LSTM predictive model on BTC/USDT' },
];

const initialNodes = [
  { id: '1', position: { x: 250, y: 50 }, data: { label: 'Binance Data Stream' }, type: 'input', style: { background: '#1A222C', color: '#E6EDF3', border: '1px solid #202938', borderRadius: '8px', padding: '10px' } },
  { id: '2', position: { x: 250, y: 150 }, data: { label: 'LSTM (TensorFlow)' }, style: { background: '#1A222C', color: '#E6EDF3', border: '1px solid #2962FF', borderRadius: '8px', padding: '10px', boxShadow: '0 0 10px rgba(41, 98, 255, 0.2)' } },
  { id: '3', position: { x: 250, y: 250 }, data: { label: 'Max Drawdown Guard' }, style: { background: '#1A222C', color: '#E6EDF3', border: '1px solid #FFB74D', borderRadius: '8px', padding: '10px' } },
  { id: '4', position: { x: 250, y: 350 }, data: { label: 'TWAP Execution' }, type: 'output', style: { background: '#1A222C', color: '#E6EDF3', border: '1px solid #26A69A', borderRadius: '8px', padding: '10px' } },
];

const initialEdges = [
  { id: 'e1-2', source: '1', target: '2', animated: true, style: { stroke: '#2962FF' } },
  { id: 'e2-3', source: '2', target: '3', animated: true, style: { stroke: '#2962FF' } },
  { id: 'e3-4', source: '3', target: '4', animated: true, style: { stroke: '#2962FF' } },
];

const StrategyBuilder = ({ onBack, onBacktest }) => {
  const [nodes, setNodes] = useState(initialNodes);
  const [edges, setEdges] = useState(initialEdges);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState('nodes');

  const onNodesChange = useCallback((changes) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback((changes) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);
  const onConnect = useCallback((params) => setEdges((eds) => addEdge({ ...params, animated: true, style: { stroke: '#2962FF' } }, eds)), []);

  const complexityScore = 85;
  const expectedLatency = '120ms';
  const expectedFrequency = '15 sig/day';

  return (
    <div className="flex h-full w-full bg-[#080A0D] text-[#E6EDF3] font-['Inter'] rounded-xl border border-[#202938] overflow-hidden shadow-2xl">
      
      {/* LEFT SIDEBAR: Palette & Templates */}
      <div className="w-[320px] bg-[#131722] border-r border-[#202938] flex flex-col z-10">
        
        {/* Header Tabs */}
        <div className="flex border-b border-[#202938]">
          <button 
            onClick={() => setActiveTab('nodes')}
            className={`flex-1 py-4 text-sm font-bold flex items-center justify-center gap-2 ${activeTab === 'nodes' ? 'text-[#2962FF] border-b-2 border-[#2962FF] bg-[#1A222C]' : 'text-[#8B949E] hover:text-[#E6EDF3]'}`}
          >
            <Code size={16} /> Nodes
          </button>
          <button 
            onClick={() => setActiveTab('templates')}
            className={`flex-1 py-4 text-sm font-bold flex items-center justify-center gap-2 ${activeTab === 'templates' ? 'text-[#2962FF] border-b-2 border-[#2962FF] bg-[#1A222C]' : 'text-[#8B949E] hover:text-[#E6EDF3]'}`}
          >
            <Layers size={16} /> Templates
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {activeTab === 'nodes' ? (
            <div className="p-4 flex flex-col gap-4">
              <div className="relative">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#8B949E]" />
                <input 
                  type="text" 
                  placeholder="Search nodes..." 
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full bg-[#080A0D] border border-[#202938] rounded-lg py-2 pl-9 pr-4 text-sm focus:outline-none focus:border-[#2962FF] transition-colors"
                />
              </div>
              
              <div className="text-xs text-[#8B949E] italic px-1 flex items-center gap-2 bg-[#2962FF]10 text-[#2962FF] p-2 rounded border border-[#2962FF]30">
                <Plus size={14}/> Drag and drop nodes to the canvas
              </div>

              {Object.entries(NODE_CATEGORIES).map(([category, items]) => {
                const filteredItems = items.filter(item => item.toLowerCase().includes(searchQuery.toLowerCase()));
                if (filteredItems.length === 0) return null;
                return (
                  <div key={category} className="mb-2">
                    <div className="text-[10px] text-[#8B949E] uppercase tracking-wider font-bold mb-2 ml-1">{category}</div>
                    <div className="flex flex-col gap-2">
                      {filteredItems.map(item => (
                        <div 
                          key={item} 
                          draggable 
                          className="bg-[#1A222C] border border-[#202938] hover:border-[#2962FF] hover:bg-[#131722] p-3 rounded-lg text-sm cursor-grab active:cursor-grabbing transition-all shadow-sm"
                        >
                          {item}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="p-4 flex flex-col gap-4">
              <div className="text-xs text-[#8B949E] mb-2">Select a preset template to kickstart your strategy.</div>
              {TEMPLATES.map((tpl, i) => (
                <div key={i} className="bg-[#1A222C] border border-[#202938] hover:border-[#26A69A] p-4 rounded-xl cursor-pointer group transition-all">
                  <h4 className="font-bold text-[#E6EDF3] group-hover:text-[#26A69A] transition-colors">{tpl.name}</h4>
                  <p className="text-xs text-[#8B949E] mt-1">{tpl.desc}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* CENTER: Canvas */}
      <div className="flex-1 relative flex flex-col">
        {/* Top toolbar */}
        <div className="absolute top-4 left-4 right-4 z-10 flex justify-between pointer-events-none">
          <div className="bg-[#131722] border border-[#202938] rounded-lg px-4 py-2 pointer-events-auto shadow-lg flex items-center gap-4">
            <span className="font-bold">Alpha Neural V2</span>
            <span className="text-xs px-2 py-1 bg-[#26A69A]20 text-[#26A69A] rounded font-mono font-bold border border-[#26A69A]30">Draft</span>
          </div>
          <div className="flex gap-2 pointer-events-auto">
            <button className="bg-[#131722] border border-[#202938] hover:bg-[#1A222C] px-4 py-2 rounded-lg text-sm font-bold transition-colors flex items-center gap-2">
              <Save size={16}/> Save
            </button>
            <button 
              onClick={() => onBacktest && onBacktest({ id: 'new', name: 'Alpha Neural V2' })}
              className="bg-[#2962FF] hover:bg-[#448AFF] text-white px-6 py-2 rounded-lg text-sm font-bold shadow-[0_0_15px_rgba(41,98,255,0.3)] transition-all flex items-center gap-2"
            >
              <Play size={16}/> Run Simulation
            </button>
          </div>
        </div>

        <ReactFlow 
          nodes={nodes} 
          edges={edges} 
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          fitView 
          className="bg-[#080A0D]"
        >
          <Background color="#202938" gap={20} size={1} />
          <Controls className="bg-[#131722] border-[#202938] fill-[#E6EDF3]" />
          <MiniMap 
            nodeColor={(node) => '#2962FF'}
            maskColor="rgba(8, 10, 13, 0.8)"
            className="bg-[#131722] border border-[#202938] rounded-lg overflow-hidden"
          />
        </ReactFlow>
      </div>

      {/* RIGHT SIDEBAR: Validation & Preview */}
      <div className="w-[320px] bg-[#131722] border-l border-[#202938] flex flex-col z-10">
        
        <div className="p-5 border-b border-[#202938]">
          <h3 className="font-bold mb-4 flex items-center gap-2"><ShieldCheck className="text-[#26A69A]" size={18}/> Validation Status</h3>
          <div className="bg-[#26A69A]10 border border-[#26A69A]30 rounded-lg p-3">
            <div className="text-sm font-bold text-[#26A69A] mb-1">Pass: All Guards Active</div>
            <p className="text-xs text-[#E6EDF3]">Max drawdown guard and exposure limits are properly connected.</p>
          </div>
          <div className="bg-[#FFB74D]10 border border-[#FFB74D]30 rounded-lg p-3 mt-3">
            <div className="text-sm font-bold text-[#FFB74D] flex items-center gap-1"><AlertTriangle size={14}/> Warning</div>
            <p className="text-xs text-[#E6EDF3]">LSTM model may cause high latency (approx. +40ms) during volatile periods.</p>
          </div>
        </div>

        <div className="p-5 border-b border-[#202938]">
          <h3 className="font-bold mb-4">Estimations</h3>
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[#080A0D] border border-[#202938] rounded-lg p-3 text-center">
              <div className="text-[10px] text-[#8B949E] uppercase tracking-wider mb-1">Complexity</div>
              <div className={`text-lg font-mono font-bold ${complexityScore > 80 ? 'text-[#FFB74D]' : 'text-[#26A69A]'}`}>{complexityScore}/100</div>
            </div>
            <div className="bg-[#080A0D] border border-[#202938] rounded-lg p-3 text-center">
              <div className="text-[10px] text-[#8B949E] uppercase tracking-wider mb-1">Latency</div>
              <div className="text-lg font-mono font-bold text-[#E6EDF3]">{expectedLatency}</div>
            </div>
            <div className="bg-[#080A0D] border border-[#202938] rounded-lg p-3 text-center col-span-2">
              <div className="text-[10px] text-[#8B949E] uppercase tracking-wider mb-1">Expected Frequency</div>
              <div className="text-lg font-mono font-bold text-[#E6EDF3]">{expectedFrequency}</div>
            </div>
          </div>
        </div>

        <div className="flex-1 p-5 flex flex-col">
          <h3 className="font-bold mb-4 flex items-center gap-2"><Download size={18}/> Execution Preview</h3>
          <div className="flex-1 bg-[#080A0D] border border-[#202938] rounded-lg p-3 font-mono text-[10px] text-[#8B949E] overflow-y-auto leading-relaxed shadow-inner">
            <span className="text-[#2962FF]">[DRY RUN INIT]</span><br/>
            10:00:00.000 - Data ingested (Binance BTC/USDT)<br/>
            10:00:00.012 - Features extracted (24 dimensions)<br/>
            10:00:00.085 - LSTM inference: <span className="text-[#26A69A] font-bold">LONG (0.84 conf)</span><br/>
            10:00:00.090 - Risk check: <span className="text-[#26A69A]">PASS</span> (DD 1.2% &lt; 5%)<br/>
            10:00:00.095 - PosSizer: Allocate 15% ($2,150)<br/>
            10:00:00.120 - <span className="text-[#E6EDF3] font-bold">Routing TWAP Order -&gt; Bybit</span><br/>
            <span className="text-[#2962FF] animate-pulse">Waiting for next tick...</span>
          </div>
        </div>

      </div>
    </div>
  );
};

export default StrategyBuilder;
