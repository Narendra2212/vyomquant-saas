import React, { useMemo } from 'react';

/**
 * 🔴 STEP 6 — STRATEGY RISK INDICATOR
 * 
 * CRITICAL: Prevents dangerous strategy deployment
 * 
 * Risk Score Formula: riskScore = signalsPerMinute * leverage
 * 
 * Risk Levels:
 * - Low (green): riskScore <= 5
 * - Medium (yellow): riskScore <= 15
 * - High (red): riskScore > 15 - BLOCK DEPLOYMENT
 */

// Risk score thresholds
const RISK_THRESHOLDS = {
  LOW: 5,       // Green - safe
  MEDIUM: 15,   // Yellow - caution
  CRITICAL: 25  // Red - block
};

const StrategyRiskIndicator = ({ 
  signalsPerMinute = 0, 
  leverage = 1, 
  onRiskChange,
  deploymentBlocked 
}) => {
  // 🔴 STEP 6: Calculate risk score
  const riskScore = useMemo(() => {
    const score = signalsPerMinute * leverage;
    return Math.round(score * 10) / 10; // Round to 1 decimal
  }, [signalsPerMinute, leverage]);

  // 🔴 STEP 6: Determine risk level and colors
  const riskLevel = useMemo(() => {
    if (riskScore <= RISK_THRESHOLDS.LOW) return 'LOW';
    if (riskScore <= RISK_THRESHOLDS.MEDIUM) return 'MEDIUM';
    return 'HIGH';
  }, [riskScore]);

  const riskColor = useMemo(() => {
    switch (riskLevel) {
      case 'LOW': return '#00C853';      // Green
      case 'MEDIUM': return '#FF9800';   // Yellow/Orange
      case 'HIGH': return '#FF5252';     // Red
      default: return '#6b7280';
    }
  }, [riskLevel]);

  const riskBgColor = useMemo(() => {
    switch (riskLevel) {
      case 'LOW': return '#00C85320';    // Green 20%
      case 'MEDIUM': return '#FF980020'; // Yellow 20%
      case 'HIGH': return '#FF525220';   // Red 20%
      default: return '#2a2a3e';
    }
  }, [riskLevel]);

  // 🔴 STEP 6: Check if deployment should be blocked
  const isBlocked = useMemo(() => {
    const blocked = riskScore > RISK_THRESHOLDS.MEDIUM;
    if (onRiskChange) {
      onRiskChange({
        score: riskScore,
        level: riskLevel,
        blocked: blocked,
        reason: blocked ? 'Risk score exceeds safe threshold (>15)' : null
      });
    }
    return blocked;
  }, [riskScore, riskLevel, onRiskChange]);

  // Format risk description
  const getRiskDescription = () => {
    switch (riskLevel) {
      case 'LOW':
        return 'Low risk strategy. Safe to deploy.';
      case 'MEDIUM':
        return 'Medium risk. Monitor closely after deployment.';
      case 'HIGH':
        return 'HIGH RISK! Deployment blocked. Reduce signals or leverage.';
      default:
        return '';
    }
  };

  return (
    <div style={{
      backgroundColor: '#1a1a2e',
      borderRadius: '12px',
      padding: '20px',
      border: `2px solid ${isBlocked ? '#FF5252' : riskColor}`,
      marginBottom: '20px'
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '16px'
      }}>
        <span style={{
          fontFamily: 'monospace',
          fontSize: '11px',
          fontWeight: 900,
          letterSpacing: '2px',
          color: '#6b7280'
        }}>
          STRATEGY RISK ASSESSMENT
        </span>
        <div style={{
          padding: '4px 12px',
          borderRadius: '4px',
          backgroundColor: riskBgColor,
          color: riskColor,
          fontFamily: 'monospace',
          fontSize: '12px',
          fontWeight: 900
        }}>
          {riskLevel} RISK
        </div>
      </div>

      {/* Risk Score Display */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: '20px'
      }}>
        <div style={{
          textAlign: 'center'
        }}>
          <div style={{
            fontSize: '48px',
            fontWeight: 900,
            fontFamily: 'monospace',
            color: riskColor,
            lineHeight: 1
          }}>
            {riskScore}
          </div>
          <div style={{
            fontSize: '10px',
            fontFamily: 'monospace',
            color: '#6b7280',
            marginTop: '4px'
          }}>
            RISK SCORE
          </div>
        </div>
      </div>

      {/* Risk Formula */}
      <div style={{
        backgroundColor: '#0f0f1e',
        borderRadius: '8px',
        padding: '12px',
        marginBottom: '16px',
        fontFamily: 'monospace',
        fontSize: '11px'
      }}>
        <div style={{
          color: '#6b7280',
          marginBottom: '8px'
        }}>
          FORMULA: signalsPerMin × leverage
        </div>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: '12px'
        }}>
          <div style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ color: '#ffffff', fontWeight: 600 }}>{signalsPerMinute}</div>
            <div style={{ color: '#6b7280', fontSize: '9px' }}>Signals/Min</div>
          </div>
          <div style={{ color: '#6b7280' }}>×</div>
          <div style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ color: '#ffffff', fontWeight: 600 }}>{leverage}x</div>
            <div style={{ color: '#6b7280', fontSize: '9px' }}>Leverage</div>
          </div>
          <div style={{ color: '#6b7280' }}>=</div>
          <div style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ color: riskColor, fontWeight: 700 }}>{riskScore}</div>
            <div style={{ color: '#6b7280', fontSize: '9px' }}>Score</div>
          </div>
        </div>
      </div>

      {/* Risk Gauge */}
      <div style={{ marginBottom: '16px' }}>
        <div style={{
          height: '8px',
          backgroundColor: '#0f0f1e',
          borderRadius: '4px',
          overflow: 'hidden',
          position: 'relative'
        }}>
          {/* Risk zones */}
          <div style={{
            position: 'absolute',
            left: 0,
            width: '20%',
            height: '100%',
            backgroundColor: '#00C85340'
          }} />
          <div style={{
            position: 'absolute',
            left: '20%',
            width: '40%',
            height: '100%',
            backgroundColor: '#FF980040'
          }} />
          <div style={{
            position: 'absolute',
            left: '60%',
            width: '40%',
            height: '100%',
            backgroundColor: '#FF525240'
          }} />
          
          {/* Current position indicator */}
          <div style={{
            position: 'absolute',
            left: `${Math.min((riskScore / 30) * 100, 100)}%`,
            top: 0,
            bottom: 0,
            width: '4px',
            backgroundColor: riskColor,
            transform: 'translateX(-50%)',
            boxShadow: `0 0 10px ${riskColor}`
          }} />
        </div>
        
        {/* Legend */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontFamily: 'monospace',
          fontSize: '9px',
          color: '#6b7280',
          marginTop: '4px'
        }}>
          <span style={{ color: '#00C853' }}>0 (Low)</span>
          <span style={{ color: '#FF9800' }}>15 (Med)</span>
          <span style={{ color: '#FF5252' }}>30+ (High)</span>
        </div>
      </div>

      {/* Risk Description */}
      <div style={{
        fontFamily: 'monospace',
        fontSize: '11px',
        color: riskColor,
        textAlign: 'center',
        marginBottom: isBlocked ? '12px' : 0
      }}>
        {getRiskDescription()}
      </div>

      {/* 🔴 STEP 6: BLOCK DEPLOYMENT WARNING */}
      {isBlocked && (
        <div style={{
          backgroundColor: '#FF525220',
          border: '1px solid #FF525240',
          borderRadius: '8px',
          padding: '16px',
          textAlign: 'center'
        }}>
          <div style={{
            fontSize: '24px',
            marginBottom: '8px'
          }}>
            🚫
          </div>
          <div style={{
            fontFamily: 'monospace',
            fontSize: '12px',
            fontWeight: 900,
            color: '#FF5252',
            marginBottom: '4px'
          }}>
            DEPLOYMENT BLOCKED
          </div>
          <div style={{
            fontFamily: 'monospace',
            fontSize: '10px',
            color: '#6b7280'
          }}>
            Risk score ({riskScore}) exceeds safe threshold (15).<br />
            Reduce signals per minute or leverage to continue.
          </div>
        </div>
      )}

      {/* Threshold Guide */}
      <div style={{
        marginTop: '16px',
        paddingTop: '16px',
        borderTop: '1px solid #2a2a3e',
        fontFamily: 'monospace',
        fontSize: '9px',
        color: '#6b7280'
      }}>
        <div style={{ marginBottom: '4px', fontWeight: 600 }}>RISK THRESHOLDS:</div>
        <div style={{ display: 'flex', gap: '16px' }}>
          <span style={{ color: '#00C853' }}>● ≤5 Low</span>
          <span style={{ color: '#FF9800' }}>● ≤15 Medium</span>
          <span style={{ color: '#FF5252' }}>● >15 BLOCKED</span>
        </div>
      </div>
    </div>
  );
};

export default StrategyRiskIndicator;
export { RISK_THRESHOLDS };
