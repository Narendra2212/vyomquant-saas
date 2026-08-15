import React, { createContext, useContext, useState, useCallback, useRef } from "react";

const CopilotContext = createContext(null);

export const useCopilot = () => {
  const ctx = useContext(CopilotContext);
  if (!ctx) throw new Error("useCopilot must be inside CopilotProvider");
  return ctx;
};

export const CopilotProvider = ({ children, apiBaseUrl = "/api/v1/copilot" }) => {
  const [messages, setMessages] = useState([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [dagSuggestion, setDagSuggestion] = useState(null);
  const abortRef = useRef(null);

  const getAuthHeaders = () => {
    const token = sessionStorage.getItem("token") || localStorage.getItem("supabase_access_token") || "";
    return { "Content-Type": "application/json", authorization: `Bearer ${token}` };
  };

  const detectContext = () => {
    const path = window.location.pathname;
    if (path.includes("/strategy-builder")) {
      return {
        view: "strategy_builder",
        dag_snapshot: window.__VYOMQUANT_DAG_SNAPSHOT__ || null,
      };
    }
    if (path.includes("/backtest")) {
      return {
        view: "backtest",
        backtest_stats: window.__VYOMQUANT_BACKTEST_STATS__ || null,
      };
    }
    return { view: "general" };
  };

  const sendMessage = useCallback(
    async (text, intent = null) => {
      setIsStreaming(true);
      setMessages((prev) => [...prev, { role: "user", content: text }]);
      setDagSuggestion(null);

      const payload = {
        message: text,
        session_id: currentSessionId,
        context_metadata: { ...detectContext(), intent },
      };

      try {
        const response = await fetch(`${apiBaseUrl}/chat/stream`, {
          method: "POST",
          headers: getAuthHeaders(),
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let assistantText = "";
        let buffer = "";
        let currentEvent = null;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop(); // keep incomplete line in buffer

          for (const line of lines) {
            if (line.startsWith("event:")) {
              currentEvent = line.replace("event:", "").trim();
              continue;
            }
            if (line.startsWith("data:")) {
              const data = line.replace("data:", "").trim();
              if (data === "[DONE]") continue;

              if (currentEvent === "token") {
                assistantText += data;
                setMessages((prev) => {
                  const copy = [...prev];
                  const last = copy[copy.length - 1];
                  if (last && last.role === "assistant") {
                    last.content = assistantText;
                    return copy;
                  }
                  return [...copy, { role: "assistant", content: assistantText }];
                });
              } else if (currentEvent === "dag_update") {
                try {
                  const dag = JSON.parse(data);
                  setDagSuggestion(dag);
                  window.dispatchEvent(new CustomEvent("copilot-dag-apply", { detail: dag }));
                } catch (e) {
                  console.error("Failed to parse DAG update", e);
                }
              } else if (currentEvent === "error") {
                setMessages((prev) => [
                  ...prev,
                  { role: "assistant", content: `Error: ${data}` },
                ]);
              } else if (currentEvent === "done") {
                if (data && !currentSessionId) {
                  setCurrentSessionId(data);
                }
              }
              currentEvent = null;
            }
            if (line === "") {
              currentEvent = null;
            }
          }
        }
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `Error: ${err.message}` },
        ]);
      } finally {
        setIsStreaming(false);
      }
    },
    [apiBaseUrl, currentSessionId]
  );

  const loadSession = useCallback(
    async (sessionId) => {
      const res = await fetch(`${apiBaseUrl}/sessions/${sessionId}`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) return;
      const data = await res.json();
      setMessages(data.map((m) => ({ role: m.role, content: m.content })));
      setCurrentSessionId(sessionId);
    },
    [apiBaseUrl]
  );

  const startNewSession = useCallback(() => {
    setCurrentSessionId(null);
    setMessages([]);
    setDagSuggestion(null);
  }, []);

  const deleteSession = useCallback(
    async (sessionId) => {
      await fetch(`${apiBaseUrl}/sessions/${sessionId}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      if (currentSessionId === sessionId) startNewSession();
    },
    [apiBaseUrl, currentSessionId, startNewSession]
  );

  const generateDAG = useCallback(
    async (description, constraints) => {
      const res = await fetch(`${apiBaseUrl}/dag/generate`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ description, constraints }),
      });
      if (!res.ok) throw new Error("DAG generation failed");
      const data = await res.json();
      setDagSuggestion(data.dag);
      return data.dag;
    },
    [apiBaseUrl]
  );

  const value = {
    messages,
    isStreaming,
    currentSessionId,
    dagSuggestion,
    sendMessage,
    loadSession,
    startNewSession,
    deleteSession,
    generateDAG,
  };

  return <CopilotContext.Provider value={value}>{children}</CopilotContext.Provider>;
};
