/**
 * CopilotChat.jsx — Floating Drawer & Chat UI for AI Copilot
 * 
 * NOTE: Intentionally DORMANT / UNMOUNTED. Preserved for future Copilot UI restoration.
 * All chat controls, message rendering, auto-scroll, and session management
 * remain fully implemented and intact.
 */

import React, { useState, useEffect, useRef } from "react";
import { Bot, X, Send, RotateCcw } from "lucide-react";
import { useCopilot } from "../contexts/CopilotContext";
import { Spinner } from "./ui-legacy/primitives";
import { token } from "../design/tokens";
import { Button } from "./ui/Button";

export default function CopilotChat() {
  const [isOpen, setIsOpen] = useState(false);
  const [inputText, setInputText] = useState("");
  const { messages, isStreaming, sendMessage, startNewSession } = useCopilot();
  const messagesEndRef = useRef(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (typeof messagesEndRef.current?.scrollIntoView === 'function') {
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
            background: `linear-gradient(135deg, ${token.brand.base}, ${token.brand.base})`,
            color: "#000",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "none",
            cursor: "pointer",
            boxShadow: `0 4px 20px ${token.brand.base}40`,
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
          background: token.surface.panel,
          borderLeft: `1px solid ${token.line.default}`,
          boxShadow: isOpen ? "-10px 0 30px rgba(0,0,0,0.5)" : "none",
          transition: "right 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
          zIndex: 1000,
          display: "flex",
          flexDirection: "column",
          fontFamily: "'IBM Plex Mono', 'Fira Code', monospace",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px", borderBottom: `1px solid ${token.line.default}`, background: token.surface.raised }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ background: `${token.brand.base}20`, padding: 8, borderRadius: 8 }}>
              <Bot size={18} color={token.brand.base} />
            </div>
            <div>
              <div style={{ color: token.content.primary, fontSize: 13, fontWeight: 700 }}>VyomQuant Copilot</div>
              <div style={{ color: token.content.muted, fontSize: 9, letterSpacing: 1, textTransform: "uppercase" }}>AI Trading Assistant</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button onClick={startNewSession} style={{ background: "transparent", border: "none", color: token.content.secondary, cursor: "pointer", padding: 4 }} title="New Session">
              <RotateCcw size={16} className="hover:text-cyan-400" />
            </button>
            <button onClick={() => setIsOpen(false)} style={{ background: "transparent", border: "none", color: token.content.secondary, cursor: "pointer", padding: 4 }} title="Close">
              <X size={20} className="hover:text-red-400" />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px", display: "flex", flexDirection: "column", gap: 16 }}>
          {messages.length === 0 ? (
            <div style={{ margin: "auto", textAlign: "center", color: token.content.muted }}>
              <Bot size={32} style={{ margin: "0 auto 12px", opacity: 0.5 }} />
              <div style={{ fontSize: 12, marginBottom: 4 }}>How can I help you today?</div>
              <div style={{ fontSize: 9, opacity: 0.7 }}>Try asking me to build a strategy or explain market conditions.</div>
            </div>
          ) : (
            messages.map((m, i) => (
              <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: m.role === "user" ? "flex-end" : "flex-start" }}>
                <div style={{
                  maxWidth: "85%",
                  background: m.role === "user" ? token.surface.inset : token.surface.inset,
                  border: `1px solid ${m.role === "user" ? token.line.default : token.brand.base + "30"}`,
                  padding: "10px 14px",
                  borderRadius: 12,
                  borderBottomRightRadius: m.role === "user" ? 2 : 12,
                  borderBottomLeftRadius: m.role === "assistant" ? 2 : 12,
                  color: m.role === "user" ? token.content.secondary : token.content.primary,
                  fontSize: 11,
                  lineHeight: 1.5,
                  whiteSpace: "pre-wrap"
                }}>
                  {m.content}
                </div>
                <div style={{ fontSize: 8, color: token.content.muted, marginTop: 4, padding: "0 4px" }}>
                  {m.role === "user" ? "YOU" : "COPILOT"}
                </div>
              </div>
            ))
          )}
          
          {isStreaming && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: token.content.muted, fontSize: 11, padding: "8px 0" }}>
              <Spinner size={12} color={token.brand.base} />
              Generating response...
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div style={{ padding: "16px", borderTop: `1px solid ${token.line.default}`, background: token.surface.raised }}>
          <form onSubmit={handleSend} style={{ display: "flex", gap: 8 }}>
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="Ask Copilot..."
              style={{
                flex: 1,
                background: token.surface.inset,
                border: `1px solid ${token.line.default}`,
                borderRadius: 8,
                padding: "10px 14px",
                color: token.content.primary,
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
                background: isStreaming || !inputText.trim() ? token.surface.inset : token.brand.base, 
                color: isStreaming || !inputText.trim() ? token.content.muted : "#000",
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
