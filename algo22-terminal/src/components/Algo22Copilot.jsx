import React, { useState, useEffect, useRef } from 'react';
import { Bot, Send, X, Zap, Code } from 'lucide-react';

const Algo22Copilot = ({ isOpen, onToggle }) => {
  const [messages, setMessages] = useState([
    { role: 'assistant', text: 'VyomQuant Copilot initialized. I can help you build strategies, debug code, or explain market events. Try saying: "Build an RSI crossover strategy for SOL".' }
  ]);
  const [input, setInput] = useState('');
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isOpen]);

  useEffect(() => {
    const handleIntent = (e) => {
      const detail = e.detail || {};
      const intent = detail.intent || detail.type;
      
      let systemMsg = "I'm looking into that for you.";
      
      if (intent === 'explain_metric') {
        systemMsg = `Let me explain the ${detail.contextData?.metric} metric (currently ${detail.contextData?.value}). ${detail.contextData?.description || 'This is a key performance indicator for your portfolio.'}`;
      } else if (intent === 'explain_risk') {
        systemMsg = `Your ${detail.contextData?.topic} score is ${detail.contextData?.score}. This takes into account your drawdown limits and position sizing across all active strategies.`;
      } else if (intent === 'explain_strategy') {
        systemMsg = `The strategy ${detail.contextData?.name} is currently ${detail.contextData?.status} with a P&L of ${detail.contextData?.pnl}%.`;
      } else if (intent === 'BUILD_RSI_STRATEGY') {
        systemMsg = "Loading the RSI Template into your Builder now.";
      }

      setMessages(prev => [...prev, { role: 'assistant', text: systemMsg }]);
    };

    window.addEventListener('copilot-intent', handleIntent);
    return () => window.removeEventListener('copilot-intent', handleIntent);
  }, []);

  const handleSend = (e) => {
    e.preventDefault();
    if (!input.trim()) return;

    const userText = input.trim();
    setMessages(prev => [...prev, { role: 'user', text: userText }]);
    setInput('');

    setTimeout(() => {
      let reply = "I've analyzed your request.";
      let intent = null;

      if (userText.toLowerCase().includes('rsi') || userText.toLowerCase().includes('strategy') || userText.toLowerCase().includes('buy')) {
        reply = "I've generated a foundational strategy for you. Sending it to the Strategy Builder now.";
        intent = 'BUILD_RSI_STRATEGY';
      } else if (userText.toLowerCase().includes('debug')) {
        reply = "Looking at your last execution log, the error 'Insufficient Balance' occurred because paper trading allocation exceeded your mock USDT balance. Try reducing position size.";
      } else {
        reply = "I am a simulated UI copilot for this phase. But I'm ready to assist with quant analysis!";
      }

      setMessages(prev => [...prev, { role: 'assistant', text: reply }]);

      if (intent) {
        window.dispatchEvent(new CustomEvent('copilot-intent', { detail: { type: intent, prompt: userText } }));
      }
    }, 800);
  };

  if (!isOpen) {
    return (
      <div 
        onClick={onToggle}
        className="fixed bottom-6 right-6 w-14 h-14 bg-[#2962FF] rounded-full shadow-[0_0_30px_rgba(41,98,255,0.4)] flex items-center justify-center cursor-pointer hover:scale-110 transition-transform z-50 group"
      >
        <Bot size={24} className="text-white group-hover:animate-pulse" />
      </div>
    );
  }

  return (
    <div className="fixed top-0 right-0 h-full w-[350px] bg-[#080A0D] border-l border-[#202938] shadow-2xl flex flex-col z-50 transform transition-transform duration-300">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-[#202938] bg-[#131722]">
        <div className="flex items-center gap-2 text-[#E6EDF3] font-bold font-mono">
          <Bot size={20} className="text-[#2962FF]" />
          <span>VyomQuant Copilot</span>
        </div>
        <button onClick={onToggle} className="text-[#8B949E] hover:text-[#E6EDF3] transition-colors">
          <X size={20} />
        </button>
      </div>

      {/* Chat Area */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4 font-mono text-sm">
        {messages.map((msg, idx) => (
          <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] p-3 rounded-xl leading-relaxed ${
              msg.role === 'user' 
                ? 'bg-[#2962FF] text-white rounded-br-sm shadow-[0_0_15px_rgba(41,98,255,0.2)]' 
                : 'bg-[#1A222C] text-[#E6EDF3] border border-[#202938] rounded-bl-sm'
            }`}>
              {msg.text}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="p-4 bg-[#131722] border-t border-[#202938]">
        <form onSubmit={handleSend} className="relative">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask Copilot..."
            className="w-full bg-[#080A0D] border border-[#202938] rounded-lg py-3 pl-4 pr-12 text-[#E6EDF3] focus:outline-none focus:border-[#2962FF] font-mono text-sm transition-colors"
          />
          <button 
            type="submit"
            disabled={!input.trim()}
            className="absolute right-2 top-2 p-1.5 text-[#2962FF] hover:bg-[#2962FF] hover:text-white rounded disabled:opacity-50 transition-colors"
          >
            <Send size={18} />
          </button>
        </form>
        <div className="flex justify-between items-center mt-3 px-1 text-[10px] text-[#8B949E] font-mono uppercase tracking-widest">
          <span className="flex items-center gap-1"><Zap size={10}/> GPT-4 Turbo</span>
          <span className="flex items-center gap-1"><Code size={10}/> Context Active</span>
        </div>
      </div>
    </div>
  );
};

export default Algo22Copilot;
