import React, { useState, useEffect, useRef } from "react";
import { Bot, X, Send, RotateCcw } from "lucide-react";
import { useCopilot } from "../contexts/CopilotContext";
import { C, Spinner } from "./ui-legacy/primitives";
import { Button } from "./ui/Button";

export default function CopilotChat() {
  const [isOpen, setIsOpen] = useState(false);
  const [inputText, setInputText] = useState("");
  const { messages, isStreaming, sendMessage, startNewSession } = useCopilot();
  const messagesEndRef = useRef(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isStreaming, isOpen]);

  const handleSend = (e) => {
    e.preventDefault();
    if (!inputText.trim() || isStreaming) return;
    sendMessage(inputText.trim());
    setInputText("");
  };

  return (
    <>
      {/* Floating Action Button */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            width: 50,
            height: 50,
            borderRadius: "50%",
            background: `linear-gradient(135deg, ${C.cyan}, ${C.blue})`,
            color: "#000",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "none",
            cursor: "pointer",
            boxShadow: `0 4px 20px ${C.cyan}40`,
            zIndex: 1000,
            transition: "transform 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
          }}
          className="hover:scale-110"
          title="Open AI Copilot"
        >
          <Bot size={24} strokeWidth={2.5} />
        </button>
      )}

      {/* Drawer */}
      <div
        style={{
          position: "fixed",
          top: 0,
          right: isOpen ? 0 : -400,
          width: 380,
          height: "100vh",
          background: C.bg1,
          borderLeft: `1px solid ${C.border}`,
          boxShadow: isOpen ? "-10px 0 30px rgba(0,0,0,0.5)" : "none",
          transition: "right 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
          zIndex: 1000,
          display: "flex",
          flexDirection: "column",
          fontFamily: "'IBM Plex Mono', 'Fira Code', monospace",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px", borderBottom: `1px solid ${C.border}`, background: C.bg2 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ background: `${C.cyan}20`, padding: 8, borderRadius: 8 }}>
              <Bot size={18} color={C.cyan} />
            </div>
            <div>
              <div style={{ color: C.t1, fontSize: 13, fontWeight: 700 }}>VyomQuant Copilot</div>
              <div style={{ color: C.t3, fontSize: 9, letterSpacing: 1, textTransform: "uppercase" }}>AI Trading Assistant</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button onClick={startNewSession} style={{ background: "transparent", border: "none", color: C.t2, cursor: "pointer", padding: 4 }} title="New Session">
              <RotateCcw size={16} className="hover:text-cyan-400" />
            </button>
            <button onClick={() => setIsOpen(false)} style={{ background: "transparent", border: "none", color: C.t2, cursor: "pointer", padding: 4 }} title="Close">
              <X size={20} className="hover:text-red-400" />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px", display: "flex", flexDirection: "column", gap: 16 }}>
          {messages.length === 0 ? (
            <div style={{ margin: "auto", textAlign: "center", color: C.t3 }}>
              <Bot size={32} style={{ margin: "0 auto 12px", opacity: 0.5 }} />
              <div style={{ fontSize: 12, marginBottom: 4 }}>How can I help you today?</div>
              <div style={{ fontSize: 9, opacity: 0.7 }}>Try asking me to build a strategy or explain market conditions.</div>
            </div>
          ) : (
            messages.map((m, i) => (
              <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: m.role === "user" ? "flex-end" : "flex-start" }}>
                <div style={{
                  maxWidth: "85%",
                  background: m.role === "user" ? C.bg3 : C.bg4,
                  border: `1px solid ${m.role === "user" ? C.border : C.cyan + "30"}`,
                  padding: "10px 14px",
                  borderRadius: 12,
                  borderBottomRightRadius: m.role === "user" ? 2 : 12,
                  borderBottomLeftRadius: m.role === "assistant" ? 2 : 12,
                  color: m.role === "user" ? C.t2 : C.t1,
                  fontSize: 11,
                  lineHeight: 1.5,
                  whiteSpace: "pre-wrap"
                }}>
                  {m.content}
                </div>
                <div style={{ fontSize: 8, color: C.t4, marginTop: 4, padding: "0 4px" }}>
                  {m.role === "user" ? "YOU" : "COPILOT"}
                </div>
              </div>
            ))
          )}
          
          {isStreaming && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.t3, fontSize: 11, padding: "8px 0" }}>
              <Spinner size={12} color={C.cyan} />
              Generating response...
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div style={{ padding: "16px", borderTop: `1px solid ${C.border}`, background: C.bg2 }}>
          <form onSubmit={handleSend} style={{ display: "flex", gap: 8 }}>
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="Ask Copilot..."
              style={{
                flex: 1,
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 8,
                padding: "10px 14px",
                color: C.t1,
                fontSize: 12,
                fontFamily: "monospace",
                outline: "none"
              }}
              className="focus:border-cyan-500/50 transition-colors"
            />
            <button
              type="submit"
              disabled={isStreaming || !inputText.trim()}
              style={{ 
                background: isStreaming || !inputText.trim() ? C.bg4 : C.cyan, 
                color: isStreaming || !inputText.trim() ? C.t4 : "#000",
                border: "none", 
                borderRadius: 8, 
                padding: "0 12px", 
                cursor: isStreaming || !inputText.trim() ? "not-allowed" : "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center"
              }}
            >
              <Send size={16} />
            </button>
          </form>
        </div>
      </div>
    </>
  );
}
