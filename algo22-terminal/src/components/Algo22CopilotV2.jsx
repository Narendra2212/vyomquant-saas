import React, { useState, useRef, useEffect } from "react";
import { useCopilot } from "../contexts/CopilotContext";

/**
 * Algo22Copilot — upgraded from mockup to full streaming LLM integration.
 * Integrates with FastAPI backend via CopilotContext.
 */
export default function Algo22Copilot() {
  const {
    messages,
    isStreaming,
    sendMessage,
    startNewSession,
    generateDAG,
    dagSuggestion,
    currentSessionId,
  } = useCopilot();

  const [input, setInput] = useState("");
  const [mode, setMode] = useState("chat"); // chat | dag
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming, dagSuggestion]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;
    const text = input.trim();
    setInput("");

    if (mode === "dag") {
      await sendMessage(text, "generate_dag");
      setMode("chat");
    } else {
      let intent = null;
      const lower = text.toLowerCase();
      if (lower.includes("sharpe") || lower.includes("drawdown") || lower.includes("backtest")) {
        intent = "explain_metric";
      } else if (lower.includes("dag") || lower.includes("node") || lower.includes("strategy")) {
        intent = "explain_dag_node";
      }
      await sendMessage(text, intent);
    }
  };

  const handleQuickAction = (action) => {
    const prompts = {
      explain_sharpe: "Explain the Sharpe ratio in my current backtest.",
      explain_dag: "Explain the current DAG structure.",
      suggest_indicator: "Suggest an indicator for a mean reversion strategy.",
      generate_dag: "Generate a momentum strategy DAG using RSI and MACD.",
    };
    const text = prompts[action];
    if (action === "generate_dag") {
      setMode("dag");
    }
    setInput(text);
  };

  return (
    <div className="copilot-container">
      <header className="copilot-header">
        <h2>VyomQuant AI Copilot</h2>
        <div className="copilot-actions">
          <button onClick={() => setMode(mode === "dag" ? "chat" : "dag")}>
            {mode === "dag" ? "DAG Mode" : "Chat Mode"}
          </button>
          <button onClick={startNewSession}>New Chat</button>
        </div>
      </header>

      {currentSessionId && (
        <div className="session-badge">Session: {currentSessionId.slice(0, 8)}</div>
      )}

      <div className="copilot-messages">
        {messages.length === 0 && (
          <div className="copilot-welcome">
            <p>Welcome to VyomQuant Copilot. How can I help you today?</p>
            <div className="quick-actions">
              <button onClick={() => handleQuickAction("explain_sharpe")}>Explain Sharpe Ratio</button>
              <button onClick={() => handleQuickAction("explain_dag")}>Explain DAG</button>
              <button onClick={() => handleQuickAction("suggest_indicator")}>Suggest Indicator</button>
              <button onClick={() => handleQuickAction("generate_dag")}>Generate DAG</button>
            </div>
          </div>
        )}

        {messages.map((msg, idx) => (
          <div key={idx} className={`message ${msg.role}`}>
            <div className="message-content">{msg.content}</div>
          </div>
        ))}

        {isStreaming && (
          <div className="message assistant streaming">
            <div className="message-content">▌</div>
          </div>
        )}

        {dagSuggestion && (
          <div className="dag-suggestion">
            <strong>DAG Suggestion Ready</strong>
            <pre>{JSON.stringify(dagSuggestion, null, 2)}</pre>
            <button onClick={() => window.dispatchEvent(new CustomEvent("copilot-dag-apply", { detail: dagSuggestion }))}>
              Apply to Canvas
            </button>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <form className="copilot-input" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={mode === "dag" ? "Describe your trading idea..." : "Ask the Copilot..."}
          disabled={isStreaming}
        />
        <button type="submit" disabled={isStreaming || !input.trim()}>
          {isStreaming ? "..." : "Send"}
        </button>
      </form>
    </div>
  );
}
