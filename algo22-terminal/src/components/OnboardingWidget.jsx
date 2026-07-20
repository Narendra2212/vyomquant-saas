import React, { useState, useEffect } from 'react';
import { CheckCircle, Circle, Zap, Target, BookOpen, Layers, Bot } from 'lucide-react';

const OnboardingWidget = ({ go, onComplete }) => {
  const [progress, setProgress] = useState({
    exchange: false,
    strategy: false,
    backtest: false,
    paper: false,
    deploy: false
  });

  useEffect(() => {
    const saved = localStorage.getItem('algo22_onboarding');
    if (saved) {
      const parsed = JSON.parse(saved);
      setProgress(parsed);
    }
  }, []);

  const updateProgress = (key) => {
    const next = { ...progress, [key]: true };
    setProgress(next);
    localStorage.setItem('algo22_onboarding', JSON.stringify(next));
  };

  const steps = [
    { id: 'exchange', title: 'Connect Exchange', desc: 'Securely link API keys.', icon: Zap, action: () => { updateProgress('exchange'); go('exchange'); } },
    { id: 'strategy', title: 'Create Strategy', desc: 'Use node builder or templates.', icon: Layers, action: () => { updateProgress('strategy'); go('builder'); } },
    { id: 'backtest', title: 'Run Backtest', desc: 'Simulate tick data.', icon: BookOpen, action: () => { updateProgress('backtest'); go('strategies'); } },
    { id: 'paper', title: 'Start Paper Trading', desc: 'Test logic risk-free.', icon: Target, action: () => { updateProgress('paper'); go('strategies'); } },
    { id: 'deploy', title: 'Deploy First Bot', desc: 'Go live and let it trade.', icon: Bot, action: () => { updateProgress('deploy'); go('strategies'); } }
  ];

  const completedCount = Object.values(progress).filter(Boolean).length;
  const percentage = (completedCount / steps.length) * 100;

  useEffect(() => {
    if (percentage === 100 && onComplete) {
      onComplete();
    }
  }, [percentage, onComplete]);

  if (percentage === 100) return null;

  return (
    <div className="bg-[#131722] border border-[#2962FF]/40 rounded-xl p-6 shadow-[0_0_40px_rgba(41,98,255,0.08)] mb-6 relative overflow-hidden group hover:border-[#2962FF]/60 transition-all duration-300">
      <div className="absolute top-0 right-0 w-64 h-64 bg-[#2962FF] opacity-[0.03] group-hover:opacity-[0.06] rounded-full blur-3xl -mr-10 -mt-10 pointer-events-none transition-opacity duration-500"></div>
      
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center mb-6 z-10 relative">
        <div>
          <h2 className="text-xl font-bold font-mono text-[#E6EDF3] flex items-center gap-2">
            <Zap className="text-[#2962FF]" size={20} /> Mission Control
          </h2>
          <p className="text-[#8B949E] text-sm mt-1">Complete your initialization sequence to unlock all platform features.</p>
        </div>
        <div className="mt-4 md:mt-0 flex items-center gap-4 bg-[#080A0D] border border-[#202938] px-4 py-2 rounded-lg">
          <div className="text-xs text-[#8B949E] font-mono tracking-widest">STATUS</div>
          <div className="font-mono text-[#26A69A] font-bold text-lg">{Math.round(percentage)}%</div>
        </div>
      </div>

      <div className="w-full bg-[#080A0D] h-1.5 rounded-full mb-8 overflow-hidden border border-[#202938]">
        <div className="h-full bg-gradient-to-r from-[#00d4ff] to-[#0055ff] transition-all duration-1000 ease-out" style={{ width: `${percentage}%` }}></div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-5 gap-4 relative z-10">
        {steps.map((step, idx) => {
          const isCompleted = progress[step.id];
          const isNext = !isCompleted && (idx === 0 || progress[steps[idx-1].id]);
          
          return (
            <div 
              key={step.id}
              onClick={() => {
                 if (isNext || isCompleted) {
                   step.action();
                 }
              }}
              className={`p-4 rounded-xl border transition-all duration-300 ${
                isCompleted ? 'bg-[#080A0D]/50 border-[#26A69A]/30 opacity-70 cursor-pointer hover:bg-[#080A0D]' :
                isNext ? 'bg-[#1A222C] border-[#2962FF] shadow-[0_0_20px_rgba(41,98,255,0.15)] cursor-pointer hover:-translate-y-1' :
                'bg-[#080A0D]/30 border-[#202938] opacity-40 cursor-not-allowed'
              }`}
            >
              <div className="flex justify-between items-start mb-4">
                <div className={`p-2 rounded-lg ${isCompleted ? 'bg-[#26A69A]/10 text-[#26A69A]' : isNext ? 'bg-[#2962FF]/20 text-[#2962FF]' : 'bg-[#202938] text-[#8B949E]'}`}>
                  <step.icon size={18} />
                </div>
                {isCompleted ? <CheckCircle size={18} className="text-[#26A69A]" /> : <Circle size={18} className={isNext ? 'text-[#2962FF]/50' : 'text-[#8B949E]/30'} />}
              </div>
              <h3 className={`font-bold text-sm mb-1 ${isCompleted ? 'text-[#E6EDF3]' : isNext ? 'text-white' : 'text-[#8B949E]'}`}>{step.title}</h3>
              <p className="text-[#8B949E] text-xs leading-relaxed">{step.desc}</p>
            </div>
          )
        })}
      </div>
    </div>
  );
};

export default OnboardingWidget;
