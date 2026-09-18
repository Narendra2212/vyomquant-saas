import React, { useState, useEffect } from 'react';
import { 
  Server, Brain, Activity, Play, Rocket, 
  CheckCircle, ChevronRight, X 
} from 'lucide-react';
import { useAppState } from '../AppState';

import { token } from '../design/tokens';


const STEPS = [
  { id: 1, title: 'Connect Exchange', icon: Server, desc: 'Link Binance or Bybit' },
  { id: 2, title: 'Select Strategy', icon: Brain, desc: 'Choose a template or use AI' },
  { id: 3, title: 'Backtest', icon: Activity, desc: 'Verify historical performance' },
  { id: 4, title: 'Paper Trade', icon: Play, desc: 'Simulate with live data' },
  { id: 5, title: 'Deploy', icon: Rocket, desc: 'Go live with capital' }
];

export default function FirstTradeWizard({ onComplete }) {
  const appState = useAppState();
  
  // Try to load persisted progress
  const [currentStep, setCurrentStep] = useState(() => {
    const saved = localStorage.getItem('algo22_wizard_step');
    return saved ? parseInt(saved, 10) : 1;
  });

  const [isVisible, setIsVisible] = useState(() => {
    return localStorage.getItem('algo22_wizard_completed') !== 'true';
  });

  useEffect(() => {
    localStorage.setItem('algo22_wizard_step', currentStep.toString());
  }, [currentStep]);

  if (!isVisible) return null;

  const handleNext = () => {
    if (currentStep < 5) {
      setCurrentStep(prev => prev + 1);
    } else {
      handleComplete();
    }
  };

  const handleComplete = () => {
    localStorage.setItem('algo22_wizard_completed', 'true');
    setIsVisible(false);
    if (onComplete) onComplete();
  };

  return (
    <div style={{
      background: token.surface.raised,
      border: `1px solid ${token.line.default}`,
      borderRadius: 12,
      padding: 24,
      marginBottom: 24,
      boxShadow: "0 4px 20px rgba(0,0,0,0.3)"
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h2 style={{ color: token.content.primary, fontSize: 18, fontWeight: 700, marginBottom: 4 }}>First Trade Wizard</h2>
          <p style={{ color: token.content.secondary, fontSize: 13 }}>Complete these steps to deploy your first automated strategy.</p>
        </div>
        <button 
          onClick={handleComplete}
          style={{ background: "transparent", border: "none", color: token.content.muted, cursor: "pointer", padding: 4 }}
        >
          <X size={20} />
        </button>
      </div>

      {/* Progress Bar Container */}
      <div style={{ display: "flex", justifyContent: "space-between", position: "relative", marginBottom: 32 }}>
        {/* Connecting Line */}
        <div style={{
          position: "absolute",
          top: 16,
          left: 20,
          right: 20,
          height: 2,
          background: token.surface.inset,
          zIndex: 0
        }}>
          <div style={{
            height: "100%",
            background: token.brand.base,
            width: `${((currentStep - 1) / 4) * 100}%`,
            transition: "width 0.4s ease"
          }} />
        </div>

        {/* Steps */}
        {STEPS.map((step, index) => {
          const isCompleted = step.id < currentStep;
          const isActive = step.id === currentStep;
          
          return (
            <div key={step.id} style={{ 
              display: "flex", 
              flexDirection: "column", 
              alignItems: "center", 
              zIndex: 1,
              width: 80
            }}>
              <div style={{
                width: 32,
                height: 32,
                borderRadius: "50%",
                // `undefined` is what the completed arm has always rendered: this read was
                // `C.success`, and the shim declares no `success` key, so a completed step has
                // never had a fill of its own — it shows the card surface through, and the
                // CheckCircle glyph is what tells it apart. Preserved rather than repaired,
                // because giving it a hue is a design decision and this is a retoken.
                background: isCompleted ? undefined : (isActive ? token.brand.base : token.surface.inset),
                border: `2px solid ${token.surface.raised}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: (isCompleted || isActive) ? "#fff" : token.content.muted,
                marginBottom: 8,
                transition: "all 0.3s ease",
                boxShadow: isActive ? `0 0 10px ${token.brand.base}80` : "none"
              }}>
                {isCompleted ? <CheckCircle size={16} /> : <step.icon size={16} />}
              </div>
              <div style={{ 
                color: isActive ? token.content.primary : token.content.muted, 
                fontSize: 11, 
                fontWeight: isActive ? 700 : 500,
                textAlign: "center",
                fontFamily: "monospace"
              }}>
                {step.title}
              </div>
            </div>
          );
        })}
      </div>

      {/* Active Step Content */}
      <div style={{
        background: token.surface.canvas,
        border: `1px solid ${token.line.strong}`,
        borderRadius: 8,
        padding: 20,
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center"
      }}>
        <div>
          <div style={{ color: token.brand.base, fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>
            Step {currentStep} of 5
          </div>
          <h3 style={{ color: token.content.primary, fontSize: 16, fontWeight: 600, marginBottom: 4 }}>
            {STEPS[currentStep - 1].title}
          </h3>
          <p style={{ color: token.content.secondary, fontSize: 13 }}>
            {STEPS[currentStep - 1].desc}
          </p>
        </div>
        
        <button
          onClick={handleNext}
          style={{
            background: token.brand.base,
            color: "#fff",
            border: "none",
            borderRadius: 6,
            padding: "8px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 6,
            transition: "background 0.2s"
          }}
          onMouseEnter={(e) => e.target.style.background = token.brand.hover}
          onMouseLeave={(e) => e.target.style.background = token.brand.base}
        >
          {currentStep === 5 ? "Finish Setup" : "Complete Step"} <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
