import React, { useState, useEffect } from 'react';
import { get, post } from '../api';

const RiskCommandCenter = () => {
  const [portfolioExposure, setPortfolioExposure] = useState({ btc: 45, eth: 30, usdt: 25 });
  const [exchangeExposure, setExchangeExposure] = useState({ binance: 60, okx: 25, bybit: 15 });
  const [marginUtilization, setMarginUtilization] = useState(42.5);
  const [drawdown, setDrawdown] = useState(1.2);
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [circuitBreakers, setCircuitBreakers] = useState({
    latencySpike: false,
    exchangeDisconnect: false,
    drawdownBreach: false
  });

  useEffect(() => {
    let active = true;

    const fetchData = async () => {
      try {
        // Fetch risk settings
        const settings = await get('/api/risk/settings');
        if (!active) return;
        if (settings) {
          const isHalted = settings.kill_switches && settings.kill_switches.length > 0;
          setKillSwitchActive(isHalted);
        }

        // Fetch account health
        const health = await get('/api/risk/account-health');
        if (!active) return;
        if (health) {
          const rawDrawdown = health.current_drawdown_pct || 0.0;
          const ddVal = Math.abs(rawDrawdown) > 1 
            ? Math.abs(rawDrawdown) 
            : Math.abs(rawDrawdown) * 100;
          setDrawdown(parseFloat(ddVal.toFixed(2)));

          // If daily_pnl_pct or other limits are tripped
          setCircuitBreakers(prev => ({
            ...prev,
            drawdownBreach: ddVal > 5.0
          }));
        }

        // Fetch portfolio allocation
        const allocation = await get('/api/portfolio/allocation');
        if (!active) return;
        if (allocation && Array.isArray(allocation)) {
          const newExp = {};
          allocation.forEach(item => {
            if (item.asset) {
              const rawPct = item.pct || 0.0;
              const pctVal = Math.abs(rawPct) > 1 ? rawPct : rawPct * 100;
              newExp[item.asset.toLowerCase()] = parseFloat(pctVal.toFixed(1));
            }
          });
          setPortfolioExposure(prev => ({
            ...prev,
            ...newExp
          }));
        }
      } catch (err) {
        console.error("Error fetching Risk Command Center data:", err);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 5000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const toggleKillSwitch = async () => {
    try {
      if (!killSwitchActive) {
        await post('/api/risk/kill-switch', { scope: 'user', confirm_code: 'KILL' });
        setKillSwitchActive(true);
        alert("GLOBAL KILL SWITCH ENGAGED! ALL ALGORITHMIC TRADING HALTED.");
      } else {
        alert("Please contact administrator or re-enable bots via Fleet console to resume trading.");
      }
    } catch (err) {
      console.error("Kill switch failed:", err);
      alert(`Kill switch activation failed: ${err.message || err}`);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#0a0a0a', color: '#fff', fontFamily: 'JetBrains Mono, monospace', padding: '16px', gap: '16px' }}>
      
      {/* HEADER & KILL SWITCH */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #333', paddingBottom: '16px' }}>
        <h2 style={{ margin: 0, color: '#ff4444' }}>RISK COMMAND CENTER</h2>
        <button 
          onClick={toggleKillSwitch}
          style={{
            background: killSwitchActive ? '#ff0000' : '#330000',
            color: '#fff',
            border: '2px solid #ff0000',
            padding: '12px 24px',
            fontSize: '16px',
            fontWeight: 'bold',
            cursor: 'pointer',
            borderRadius: '4px',
            textTransform: 'uppercase'
          }}
        >
          {killSwitchActive ? 'SYSTEM HALTED - CLICK TO RESUME' : 'ENGAGE GLOBAL KILL SWITCH'}
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px', flex: 1 }}>
        
        {/* LEFT COLUMN: Exposures */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={panelStyle}>
            <h3>Portfolio Exposure</h3>
            <div style={barStyle('BTC', portfolioExposure.btc, '#f7931a')} />
            <div style={barStyle('ETH', portfolioExposure.eth, '#627eea')} />
            <div style={barStyle('USDT', portfolioExposure.usdt, '#26a17b')} />
          </div>

          <div style={panelStyle}>
            <h3>Exchange Exposure</h3>
            <div style={barStyle('Binance', exchangeExposure.binance, '#f3ba2f')} />
            <div style={barStyle('OKX', exchangeExposure.okx, '#ffffff')} />
            <div style={barStyle('Bybit', exchangeExposure.bybit, '#f7a600')} />
          </div>
        </div>

        {/* MIDDLE COLUMN: Health & Utilization */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={panelStyle}>
            <h3>Margin Utilization</h3>
            <div style={{ fontSize: '48px', fontWeight: 'bold', color: marginUtilization > 80 ? '#ff4444' : '#00ff00', textAlign: 'center', padding: '24px 0' }}>
              {marginUtilization}%
            </div>
            <div style={{ textAlign: 'center', color: '#888', fontSize: '12px' }}>Warning Threshold: 80%</div>
          </div>

          <div style={panelStyle}>
            <h3>Drawdown Monitor</h3>
            <div style={{ fontSize: '48px', fontWeight: 'bold', color: drawdown > 5 ? '#ff4444' : '#00ff00', textAlign: 'center', padding: '24px 0' }}>
              -{drawdown}%
            </div>
            <div style={{ textAlign: 'center', color: '#888', fontSize: '12px' }}>Hard Stop Threshold: -5.0%</div>
          </div>
        </div>

        {/* RIGHT COLUMN: Circuit Breakers & Correlation */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={panelStyle}>
            <h3>Circuit Breakers</h3>
            <div style={breakerStyle('Latency Spike > 500ms', circuitBreakers.latencySpike)} />
            <div style={breakerStyle('Exchange WebSocket Disconnect', circuitBreakers.exchangeDisconnect)} />
            <div style={breakerStyle('Drawdown Breach', circuitBreakers.drawdownBreach)} />
          </div>

          <div style={panelStyle}>
            <h3>Correlation Matrix (1h)</h3>
            {/* Mock heatmap grid */}
            <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr 1fr 1fr', gap: '4px', fontSize: '12px', marginTop: '12px' }}>
              <div></div><div style={{textAlign:'center'}}>BTC</div><div style={{textAlign:'center'}}>ETH</div><div style={{textAlign:'center'}}>SOL</div>
              <div>BTC</div><div style={matrixCell(1.0)}>1.00</div><div style={matrixCell(0.85)}>0.85</div><div style={matrixCell(0.72)}>0.72</div>
              <div>ETH</div><div style={matrixCell(0.85)}>0.85</div><div style={matrixCell(1.0)}>1.00</div><div style={matrixCell(0.81)}>0.81</div>
              <div>SOL</div><div style={matrixCell(0.72)}>0.72</div><div style={matrixCell(0.81)}>0.81</div><div style={matrixCell(1.0)}>1.00</div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
};

// --- Internal Styles for standalone rendering ---
const panelStyle = {
  background: '#1a1a1a',
  border: '1px solid #333',
  padding: '16px',
  borderRadius: '4px',
  flex: 1
};

const barStyle = (label, pct, color) => (
  <div style={{ marginBottom: '12px' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px' }}>
      <span>{label}</span>
      <span>{pct}%</span>
    </div>
    <div style={{ height: '8px', background: '#333', borderRadius: '4px', overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color }} />
    </div>
  </div>
);

const breakerStyle = (label, isTripped) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '12px', background: isTripped ? 'rgba(255,0,0,0.1)' : 'rgba(0,255,0,0.05)', border: `1px solid ${isTripped ? '#ff0000' : '#003300'}`, marginBottom: '8px', borderRadius: '4px', fontSize: '12px' }}>
    <span>{label}</span>
    <span style={{ color: isTripped ? '#ff4444' : '#00ff00', fontWeight: 'bold' }}>{isTripped ? 'TRIPPED' : 'OK'}</span>
  </div>
);

const matrixCell = (val) => ({
  background: val === 1 ? '#004400' : val > 0.8 ? '#003300' : '#111',
  border: '1px solid #333',
  padding: '8px 4px',
  textAlign: 'center',
  color: val === 1 ? '#00ff00' : '#aaa'
});

export default RiskCommandCenter;
