import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import {
  Plus, Trash2, RefreshCw, CheckCircle, XCircle,
  Globe, Key, Eye, EyeOff, Wifi, Database, AlertTriangle, Shield, Activity,
  Settings, Clock, Zap, Lock
} from "lucide-react";
import { api } from "../api";

function sanitizeErrorMessage(err, fallback = "An error occurred. Please try again.") {
  if (!err) return fallback;
  if (err.name === "AbortError" || err.name === "CanceledError") return null;

  const status = err?.response?.status;
  if (status === 401) return "Session expired. Please log in again.";
  if (status === 403) return "Access denied. Insufficient permissions.";
  if (status === 429) return "Exchange rate limit reached. Please try again shortly.";

  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") {
    // Check for internal stack traces, DB errors, or raw exceptions
    if (detail.includes("Traceback") || detail.includes("SELECT") || detail.includes("TypeError") || detail.includes("500 Internal")) {
      return "Exchange service temporarily unavailable. Please try again.";
    }
    if (detail.toLowerCase().includes("cannot delete exchange")) {
      return detail;
    }
    if (detail.toLowerCase().includes("verification failed") || detail.toLowerCase().includes("authentication failed")) {
      return "Exchange credentials could not be verified. Please check API Key and Secret.";
    }
    return detail;
  }
  return fallback;
}

export default function ExchangeManager() {
  const [connectedExchanges, setConnectedExchanges] = useState([]);
  const [supportedExchanges, setSupportedExchanges] = useState([]);
  const [selectedExchange, setSelectedExchange] = useState(null);
  const [authSchema, setAuthSchema] = useState(null);
  const [credentialValues, setCredentialValues] = useState({});
  const [showPasswords, setShowPasswords] = useState({});
  const [loadingExchanges, setLoadingExchanges] = useState(true);
  const [loadingSchema, setLoadingSchema] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [processingById, setProcessingById] = useState({});
  const [toast, setToast] = useState(null);

  const isMountedRef = useRef(true);
  const abortControllerRef = useRef(null);

  const loadConnectedExchanges = useCallback(async () => {
    try {
      const data = await api.exchange.list();
      if (!isMountedRef.current) return;
      setConnectedExchanges(Array.isArray(data) ? data : []);
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg = sanitizeErrorMessage(err, "Failed to load exchange connections.");
      if (msg) setToast({ type: "error", msg });
    }
  }, []);

  const loadSupportedExchanges = useCallback(async () => {
    try {
      const data = await api.exchange.getSupported();
      if (!isMountedRef.current) return;
      setSupportedExchanges(data?.exchanges || []);
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg = sanitizeErrorMessage(err, "Failed to load supported exchanges.");
      if (msg) setToast({ type: "error", msg });
    }
  }, []);

  const loadAllData = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoadingExchanges(true);
    try {
      const results = await Promise.allSettled([
        api.exchange.getSupported(),
        api.exchange.list()
      ]);

      if (!isMountedRef.current) return;

      const [supportedRes, connectedRes] = results;

      if (supportedRes.status === "fulfilled") {
        setSupportedExchanges(supportedRes.value?.exchanges || []);
      } else {
        const msg = sanitizeErrorMessage(supportedRes.reason, "Failed to load supported exchanges directory.");
        if (msg) setToast({ type: "error", msg });
      }

      if (connectedRes.status === "fulfilled") {
        setConnectedExchanges(Array.isArray(connectedRes.value) ? connectedRes.value : []);
      } else {
        const msg = sanitizeErrorMessage(connectedRes.reason, "Failed to load connected exchanges.");
        if (msg) setToast({ type: "error", msg });
      }
    } finally {
      if (isMountedRef.current) {
        setLoadingExchanges(false);
      }
    }
  }, []);

  const loadAuthSchema = useCallback(async (exchangeId) => {
    setLoadingSchema(true);
    try {
      const schema = await api.exchange.getAuthSchema(exchangeId);
      if (!isMountedRef.current) return;
      setAuthSchema(schema);
      const initialValues = {};
      const fields = Array.isArray(schema?.fields) ? schema.fields : [];
      fields.forEach(field => {
        const fieldName = field?.name || field?.field_id;
        if (fieldName) {
          initialValues[fieldName] = field?.default ?? "";
        }
      });
      setCredentialValues(initialValues);
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg = sanitizeErrorMessage(err, "Failed to load exchange authentication schema.");
      if (msg) setToast({ type: "error", msg });
    } finally {
      if (isMountedRef.current) {
        setLoadingSchema(false);
      }
    }
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    loadAllData();
    return () => {
      isMountedRef.current = false;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [loadAllData]);

  useEffect(() => {
    if (selectedExchange?.id) {
      loadAuthSchema(selectedExchange.id);
    } else {
      setAuthSchema(null);
      setCredentialValues({});
    }
  }, [selectedExchange, loadAuthSchema]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(timer);
  }, [toast]);

  const handleTestConnection = async () => {
    if (!selectedExchange || !credentialValues.api_key || !credentialValues.secret_key || isTesting) return;
    setIsTesting(true);
    try {
      const result = await api.exchange.testConnection({
        exchange_id: selectedExchange.id,
        api_key: credentialValues.api_key,
        secret_key: credentialValues.secret_key,
        password: credentialValues.password,
        uid: credentialValues.uid,
      });
      if (!isMountedRef.current) return;
      setToast({ 
        type: "success", 
        msg: `Connection verified! Balance: $${result?.usdt_balance || 0} USDT. Clock: ${result?.clock_sync || 'Synchronized'}` 
      });
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg = sanitizeErrorMessage(err, "Connection test failed. Please check your API credentials.");
      if (msg) setToast({ type: "error", msg });
    } finally {
      if (isMountedRef.current) setIsTesting(false);
    }
  };

  const handleSaveKey = async () => {
    if (!selectedExchange || !credentialValues.api_key || !credentialValues.secret_key || isSaving) return;
    setIsSaving(true);
    try {
      await api.exchange.saveKeys({
        exchange_id: selectedExchange.id,
        api_key: credentialValues.api_key,
        secret_key: credentialValues.secret_key,
        password: credentialValues.password,
        uid: credentialValues.uid,
        label: credentialValues.label,
      });
      if (!isMountedRef.current) return;
      setToast({ type: "success", msg: "Exchange keys encrypted and stored securely." });

      setCredentialValues({});
      setShowPasswords({});
      setSelectedExchange(null);
      setAuthSchema(null);

      await loadConnectedExchanges();
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg = sanitizeErrorMessage(err, "Failed to save exchange keys.");
      if (msg) setToast({ type: "error", msg });
    } finally {
      if (isMountedRef.current) setIsSaving(false);
    }
  };

  const handleDeleteSaved = async (exchangeId, exchangeName) => {
    if (processingById[exchangeId]) return;
    
    const exchange = connectedExchanges.find(e => e.exchange_id === exchangeId);
    if (exchange && exchange.bot_count > 0) {
      setToast({ 
        type: "error", 
        msg: `Cannot delete: ${exchange.bot_count} active bot(s) running. Stop bots first.` 
      });
      return;
    }

    if (!confirm(`Are you sure you want to disconnect ${exchangeName.toUpperCase()}? This action cannot be undone.`)) {
      return;
    }

    setProcessingById((p) => ({ ...p, [exchangeId]: true }));
    try {
      await api.exchange.delete(exchangeId);
      if (!isMountedRef.current) return;
      setConnectedExchanges((rows) => rows.filter((row) => row.exchange_id !== exchangeId));
      setToast({ type: "success", msg: "Exchange connection removed." });
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg = sanitizeErrorMessage(err, "Failed to delete exchange connection.");
      if (msg) setToast({ type: "error", msg });
    } finally {
      if (isMountedRef.current) {
        setProcessingById((p) => ({ ...p, [exchangeId]: false }));
      }
    }
  };

  const handleTestStoredConnection = async (exchangeId) => {
    if (processingById[exchangeId]) return;
    setProcessingById((p) => ({ ...p, [exchangeId]: true }));
    try {
      const result = await api.exchange.testStoredConnection({ exchange_id: exchangeId });
      if (!isMountedRef.current) return;
      setToast({ 
        type: "success", 
        msg: `${exchangeId.toUpperCase()} verified! Balance: $${result?.usdt_balance || 0} USDT` 
      });
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg = sanitizeErrorMessage(err, "Stored connection test failed.");
      if (msg) setToast({ type: "error", msg });
    } finally {
      if (isMountedRef.current) {
        setProcessingById((p) => ({ ...p, [exchangeId]: false }));
      }
    }
  };

  const handleReconnect = async (exchangeId) => {
    if (processingById[exchangeId]) return;
    setProcessingById((p) => ({ ...p, [exchangeId]: true }));
    try {
      await api.exchange.reconnect(exchangeId);
      if (!isMountedRef.current) return;
      setToast({ 
        type: "success", 
        msg: `${exchangeId.toUpperCase()} reconnected successfully!` 
      });
      await loadConnectedExchanges();
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg = sanitizeErrorMessage(err, "Reconnect failed.");
      if (msg) setToast({ type: "error", msg });
    } finally {
      if (isMountedRef.current) {
        setProcessingById((p) => ({ ...p, [exchangeId]: false }));
      }
    }
  };


  const alphabeticalExchanges = useMemo(() => {
    const grouped = {};
    supportedExchanges.forEach(ex => {
      const firstLetter = ex.display_name[0].toUpperCase();
      if (!grouped[firstLetter]) grouped[firstLetter] = [];
      grouped[firstLetter].push(ex);
    });
    return grouped;
  }, [supportedExchanges]);

  const handleCredentialChange = (fieldName, value) => {
    setCredentialValues(prev => ({ ...prev, [fieldName]: value }));
  };

  const togglePasswordVisibility = (fieldName) => {
    setShowPasswords(prev => ({ ...prev, [fieldName]: !prev[fieldName] }));
  };

  return (
    <div style={{ padding: 24, overflowY: "auto", flex: 1, position: "relative", background: "#0f172a" }}>
      {/* Header */}
      <div style={{ marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
            <Globe size={28} style={{ color: "#3b82f6" }} />
            <h1 style={{ color: "#f1f5f9", fontSize: 24, fontWeight: 700, margin: 0 }}>Exchange Management</h1>
          </div>
          <p style={{ color: "#94a3b8", fontSize: 14, margin: 0 }}>Manage institutional API connections via CCXT engine</p>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <button
            onClick={() => loadConnectedExchanges()}
            disabled={loadingExchanges}
            style={{
              padding: "10px 16px",
              borderRadius: 8,
              border: "1px solid #475569",
              background: "#1e293b",
              color: "#94a3b8",
              fontSize: 13,
              fontWeight: 600,
              cursor: loadingExchanges ? "not-allowed" : "pointer",
              display: "flex",
              alignItems: "center",
              gap: 8,
              transition: "all 0.2s"
            }}
          >
            <RefreshCw size={16} style={loadingExchanges ? { animation: "spin 1s linear infinite" } : {}} />
            Refresh
          </button>
        </div>
      </div>

      {/* Stats Cards */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 24 }}>
        <div style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12, padding: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <div style={{ width: 36, height: 36, borderRadius: 8, background: "#10b98120", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <CheckCircle size={18} style={{ color: "#10b981" }} />
            </div>
            <span style={{ color: "#94a3b8", fontSize: 12 }}>Connected</span>
          </div>
          <div style={{ color: "#f1f5f9", fontSize: 24, fontWeight: 700 }}>{connectedExchanges.length}</div>
        </div>
        <div style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12, padding: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <div style={{ width: 36, height: 36, borderRadius: 8, background: "#3b82f620", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Activity size={18} style={{ color: "#3b82f6" }} />
            </div>
            <span style={{ color: "#94a3b8", fontSize: 12 }}>Active Bots</span>
          </div>
          <div style={{ color: "#f1f5f9", fontSize: 24, fontWeight: 700 }}>
            {connectedExchanges.reduce((sum, ex) => sum + (ex.bot_count || 0), 0)}
          </div>
        </div>
        <div style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12, padding: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <div style={{ width: 36, height: 36, borderRadius: 8, background: "#8b5cf620", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Shield size={18} style={{ color: "#8b5cf6" }} />
            </div>
            <span style={{ color: "#94a3b8", fontSize: 12 }}>Health</span>
          </div>
          <div style={{ color: "#10b981", fontSize: 24, fontWeight: 700 }}>
            {connectedExchanges.length > 0 ? "Healthy" : "-"}
          </div>
        </div>
        <div style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12, padding: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <div style={{ width: 36, height: 36, borderRadius: 8, background: "#f59e0b20", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Database size={18} style={{ color: "#f59e0b" }} />
            </div>
            <span style={{ color: "#94a3b8", fontSize: 12 }}>Supported</span>
          </div>
          <div style={{ color: "#f1f5f9", fontSize: 24, fontWeight: 700 }}>{supportedExchanges.length}+</div>
        </div>
      </div>

      {/* Connected Exchanges */}
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ color: "#f1f5f9", fontSize: 18, fontWeight: 700, marginBottom: 16 }}>Connected Exchanges</h2>
        {loadingExchanges ? (
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 200, color: "#64748b", fontSize: 14 }}>
            <RefreshCw size={24} style={{ animation: "spin 1s linear infinite", marginRight: 12 }} />
            Loading exchange connections...
          </div>
        ) : connectedExchanges.length === 0 ? (
          <div style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12, padding: 40, textAlign: "center", color: "#64748b" }}>
            <Globe size={48} style={{ margin: "0 auto 16px", opacity: 0.3 }} />
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>No exchanges connected</div>
            <div style={{ fontSize: 13 }}>Connect your first exchange below to start trading</div>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(400px, 1fr))", gap: 16 }}>
            {connectedExchanges.map(ex => (
              <div key={ex.id} style={{ 
                background: "#1e293b", 
                border: "1px solid #334155", 
                borderRadius: 12, 
                padding: 20,
                transition: "all 0.2s"
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <div style={{ width: 48, height: 48, borderRadius: 12, background: "#3b82f620", display: "flex", alignItems: "center", justifyContent: "center" }}>
                      <Globe size={24} style={{ color: "#3b82f6" }} />
                    </div>
                    <div>
                      <div style={{ color: "#f1f5f9", fontSize: 16, fontWeight: 700, marginBottom: 4 }}>{ex.name}</div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#10b981" }} />
                        <span style={{ color: "#10b981", fontSize: 12, fontWeight: 600 }}>{ex.status}</span>
                      </div>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      onClick={() => handleReconnect(ex.exchange_id)}
                      disabled={!!processingById[ex.exchange_id]}
                      style={{
                        padding: "6px 12px",
                        borderRadius: 8,
                        border: "1px solid #3b82f650",
                        background: "#3b82f610",
                        color: "#3b82f6",
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: processingById[ex.exchange_id] ? "not-allowed" : "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: 6
                      }}
                      title="Reconnect"
                    >
                      <Zap size={14} />
                      Reconnect
                    </button>
                    <button
                      onClick={() => handleTestStoredConnection(ex.exchange_id)}
                      disabled={!!processingById[ex.exchange_id]}
                      style={{
                        padding: 8,
                        borderRadius: 8,
                        border: "1px solid #475569",
                        background: "#1e293b",
                        color: "#94a3b8",
                        cursor: processingById[ex.exchange_id] ? "not-allowed" : "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center"
                      }}
                      title="Test Connection"
                    >
                      <RefreshCw size={16} style={processingById[ex.exchange_id] ? { animation: "spin 1s linear infinite" } : {}} />
                    </button>
                    <button
                      onClick={() => handleDeleteSaved(ex.exchange_id, ex.name)}
                      disabled={!!processingById[ex.exchange_id]}
                      style={{
                        padding: 8,
                        borderRadius: 8,
                        border: "1px solid #ef444450",
                        background: "#ef444410",
                        color: "#ef4444",
                        cursor: processingById[ex.exchange_id] ? "not-allowed" : "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center"
                      }}
                      title="Disconnect"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12, marginBottom: 16 }}>
                  <div>
                    <div style={{ color: "#64748b", fontSize: 11, marginBottom: 4 }}>API Key</div>
                    <div style={{ color: "#f1f5f9", fontSize: 12, fontFamily: "monospace" }}>{ex.masked_key}</div>
                  </div>
                  <div>
                    <div style={{ color: "#64748b", fontSize: 11, marginBottom: 4 }}>Account Type</div>
                    <div style={{ color: "#f1f5f9", fontSize: 12 }}>{ex.account_type || "Spot"}</div>
                  </div>
                  <div>
                    <div style={{ color: "#64748b", fontSize: 11, marginBottom: 4 }}>Active Bots</div>
                    <div style={{ color: "#f1f5f9", fontSize: 12, fontWeight: 600 }}>{ex.bot_count || 0}</div>
                  </div>
                  <div>
                    <div style={{ color: "#64748b", fontSize: 11, marginBottom: 4 }}>Health</div>
                    <div style={{ color: "#10b981", fontSize: 12, fontWeight: 600 }}>{ex.health || "healthy"}</div>
                  </div>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 12, borderTop: "1px solid #334155" }}>
                  <div style={{ display: "flex", gap: 16, fontSize: 11, color: "#64748b" }}>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <Clock size={12} />
                      Connected: {ex.connected_at ? new Date(ex.connected_at).toLocaleDateString() : "Recently"}
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: "#64748b" }}>
                    Tier: {ex.subscription_tier || "free"}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Connect New Exchange */}
      <div style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12, padding: 24 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <div style={{ width: 40, height: 40, borderRadius: 10, background: "#10b98120", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Plus size={20} style={{ color: "#10b981" }} />
          </div>
          <div>
            <h2 style={{ color: "#f1f5f9", fontSize: 16, fontWeight: 700, margin: 0 }}>Connect New Exchange</h2>
            <p style={{ color: "#64748b", fontSize: 12, margin: "4px 0 0 0" }}>
              Select from 100+ supported CCXT integrations. Keys are encrypted with AES-256.
            </p>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
          {/* Left Column: Exchange Selection */}
          {/* Heading for the list below, not a label for a single control, so it is a group
              label rather than a <label> with nothing to associate itself with. */}
          <div role="group" aria-labelledby="exchange-select-heading" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div id="exchange-select-heading" style={{ color: "#94a3b8", fontSize: 12, fontWeight: 600, letterSpacing: 1 }}>SELECT EXCHANGE</div>
            <div style={{
              background: "#0f172a", 
              border: "1px solid #334155", 
              borderRadius: 8,
              maxHeight: 500, 
              overflowY: "auto",
              display: "flex",
              flexDirection: "column"
            }}>
              {loadingExchanges ? (
                <div style={{ padding: 40, textAlign: "center", color: "#64748b", fontSize: 13 }}>
                  <RefreshCw size={24} style={{ animation: "spin 1s linear infinite", margin: "0 auto 12px" }} />
                  Loading exchanges...
                </div>
              ) : supportedExchanges.length === 0 ? (
                <div style={{ padding: 20, textAlign: "center", color: "#64748b", fontSize: 13 }}>
                  No exchanges available
                </div>
              ) : (
                <div>
                  {Object.entries(alphabeticalExchanges).map(([letter, exchanges]) => (
                    <div key={letter}>
                      <div style={{ 
                        padding: "8px 16px", 
                        background: "#1e293b", 
                        color: "#64748b", 
                        fontSize: 11, 
                        fontWeight: 600,
                        borderBottom: "1px solid #334155",
                        position: "sticky",
                        top: 0,
                        zIndex: 10
                      }}>
                        {letter} ({exchanges.length})
                      </div>
                      {exchanges.map(ex => (
                        <div
                          key={ex.id}
                          // A pointer-only row cannot be selected without a mouse. It picks
                          // the exchange, so it gets the button role, its selected state, a
                          // tab stop and Enter/Space activation.
                          role="button"
                          tabIndex={0}
                          aria-pressed={selectedExchange?.id === ex.id}
                          onClick={() => setSelectedExchange(ex)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
                              // Space scrolls the page by default, which would move the list
                              // out from under the row just selected.
                              event.preventDefault();
                              setSelectedExchange(ex);
                            }
                          }}
                          style={{
                            padding: "10px 16px 10px 24px",
                            cursor: "pointer",
                            background: selectedExchange?.id === ex.id ? "#3b82f620" : "transparent",
                            color: selectedExchange?.id === ex.id ? "#3b82f6" : "#94a3b8",
                            fontSize: 13,
                            fontWeight: selectedExchange?.id === ex.id ? 600 : 400,
                            transition: "all 0.2s",
                            borderBottom: "1px solid #1e293b",
                            display: "flex",
                            alignItems: "center",
                            gap: 10
                          }}
                        >
                          <div style={{ 
                            width: 24, 
                            height: 24, 
                            borderRadius: 6, 
                            background: "#334155", 
                            display: "flex", 
                            alignItems: "center", 
                            justifyContent: "center",
                            fontSize: 10,
                            fontWeight: 700,
                            color: "#94a3b8"
                          }}>
                            {ex.display_name[0]}
                          </div>
                          <div>
                            <div style={{ fontWeight: 500 }}>{ex.display_name}</div>
                            <div style={{ fontSize: 11, color: "#64748b" }}>
                              {ex.spot_support && "Spot"} 
                              {ex.spot_support && ex.futures_support && " • "}
                              {ex.futures_support && "Futures"}
                              {ex.sandbox_support && " • Sandbox"}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right Column: Credentials */}
          {/* Heading for the credential field group, not a label for a single control. */}
          <div role="group" aria-labelledby="exchange-credentials-heading" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div id="exchange-credentials-heading" style={{ color: "#94a3b8", fontSize: 12, fontWeight: 600, letterSpacing: 1 }}>API CREDENTIALS</div>
            
            {!selectedExchange ? (
              <div style={{ 
                padding: 40, 
                textAlign: "center", 
                color: "#64748b", 
                fontSize: 13,
                background: "#0f172a",
                borderRadius: 8,
                border: "1px dashed #334155"
              }}>
                <Globe size={32} style={{ margin: "0 auto 12px", opacity: 0.3 }} />
                Select an exchange to view required credentials
              </div>
            ) : loadingSchema ? (
              <div style={{ 
                padding: 40, 
                textAlign: "center", 
                color: "#64748b", 
                fontSize: 13,
                background: "#0f172a",
                borderRadius: 8,
                border: "1px solid #334155"
              }}>
                <RefreshCw size={24} style={{ animation: "spin 1s linear infinite", margin: "0 auto 12px" }} />
                Loading authentication schema...
              </div>
            ) : authSchema ? (
              <>
                <div style={{ 
                  padding: 12, 
                  background: "#1e293b", 
                  borderRadius: 8, 
                  marginBottom: 16,
                  border: "1px solid #334155"
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <div style={{ 
                      width: 32, 
                      height: 32, 
                      borderRadius: 8, 
                      background: "#3b82f620", 
                      display: "flex", 
                      alignItems: "center", 
                      justifyContent: "center",
                      fontSize: 12,
                      fontWeight: 700,
                      color: "#3b82f6"
                    }}>
                      {selectedExchange.display_name[0]}
                    </div>
                    <div>
                      <div style={{ color: "#f1f5f9", fontSize: 14, fontWeight: 600 }}>{selectedExchange.display_name}</div>
                      <div style={{ color: "#64748b", fontSize: 11 }}>
                        {selectedExchange.spot_support && "Spot"} 
                        {selectedExchange.spot_support && selectedExchange.futures_support && " • "}
                        {selectedExchange.futures_support && "Futures"}
                        {selectedExchange.sandbox_support && " • Sandbox"}
                      </div>
                    </div>
                  </div>
                </div>

                {(Array.isArray(authSchema?.fields) ? authSchema.fields : []).map(field => (
                  <div key={field.name}>
                    <label style={{ color: "#64748b", fontSize: 11, marginBottom: 6, display: "block" }}>
                      {field.label}
                      {field.required && <span style={{ color: "#ef4444" }}> *</span>}
                    </label>
                    {field.type === "password" ? (
                      <div style={{ position: "relative" }}>
                        <input
                          type={showPasswords[field.name] ? "text" : "password"}
                          placeholder={field.placeholder}
                          value={credentialValues[field.name] || ""}
                          onChange={e => handleCredentialChange(field.name, e.target.value)}
                          style={{ 
                            width: "100%", 
                            background: "#0f172a", 
                            border: "1px solid #334155", 
                            color: "#f1f5f9", 
                            padding: "12px 16px", 
                            paddingRight: 40,
                            borderRadius: 8, 
                            fontSize: 13,
                            outline: "none"
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => togglePasswordVisibility(field.name)}
                          style={{
                            position: "absolute",
                            right: 12,
                            top: "50%",
                            transform: "translateY(-50%)",
                            background: "none",
                            border: "none",
                            color: "#64748b",
                            cursor: "pointer",
                            padding: 4
                          }}
                        >
                          {showPasswords[field.name] ? <EyeOff size={16} /> : <Eye size={16} />}
                        </button>
                      </div>
                    ) : field.type === "select" ? (
                      <select
                        value={credentialValues[field.name] ?? field.default ?? ""}
                        onChange={e => handleCredentialChange(field.name, e.target.value)}
                        style={{ 
                          width: "100%", 
                          background: "#0f172a", 
                          border: "1px solid #334155", 
                          color: "#f1f5f9", 
                          padding: "12px 16px", 
                          borderRadius: 8, 
                          fontSize: 13,
                          outline: "none"
                        }}
                      >
                        {(field.options || []).map(opt => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    ) : field.type === "boolean" ? (
                      <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", color: "#f1f5f9", fontSize: 13 }}>
                        <input
                          type="checkbox"
                          checked={!!credentialValues[field.name]}
                          onChange={e => handleCredentialChange(field.name, e.target.checked)}
                          style={{ width: 16, height: 16, accentColor: "#3b82f6" }}
                        />
                        <span>{field.placeholder || "Enable"}</span>
                      </label>
                    ) : (
                      <input
                        type={field.type}
                        placeholder={field.placeholder}
                        value={credentialValues[field.name] || ""}
                        onChange={e => handleCredentialChange(field.name, e.target.value)}
                        style={{ 
                          width: "100%", 
                          background: "#0f172a", 
                          border: "1px solid #334155", 
                          color: "#f1f5f9", 
                          padding: "12px 16px", 
                          borderRadius: 8, 
                          fontSize: 13,
                          outline: "none"
                        }}
                      />
                    )}
                    {field.description && (
                      <div style={{ color: "#64748b", fontSize: 10, marginTop: 4 }}>{field.description}</div>
                    )}
                  </div>
                ))}

                <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
                  <button
                    onClick={handleTestConnection}
                    disabled={isTesting || isSaving || !credentialValues.api_key || !credentialValues.secret_key}
                    style={{
                      flex: 1,
                      padding: "12px 20px",
                      borderRadius: 8,
                      border: "1px solid #475569",
                      background: "#1e293b",
                      color: "#94a3b8",
                      fontSize: 13,
                      fontWeight: 600,
                      cursor: (isTesting || isSaving || !credentialValues.api_key || !credentialValues.secret_key) ? "not-allowed" : "pointer",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 8,
                      transition: "all 0.2s",
                      opacity: (isTesting || isSaving || !credentialValues.api_key || !credentialValues.secret_key) ? 0.5 : 1
                    }}
                  >
                    {isTesting ? <RefreshCw size={16} style={{ animation: "spin 1s linear infinite" }} /> : <Wifi size={16} />}
                    {isTesting ? "Testing..." : "Test Connection"}
                  </button>
                  <button
                    onClick={handleSaveKey}
                    disabled={isSaving || isTesting || !credentialValues.api_key || !credentialValues.secret_key}
                    style={{
                      flex: 1,
                      padding: "12px 20px",
                      borderRadius: 8,
                      border: "none",
                      background: (isSaving || isTesting || !credentialValues.api_key || !credentialValues.secret_key) ? "#374151" : "#10b981",
                      color: "#ffffff",
                      fontSize: 13,
                      fontWeight: 600,
                      cursor: (isSaving || isTesting || !credentialValues.api_key || !credentialValues.secret_key) ? "not-allowed" : "pointer",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 8,
                      transition: "all 0.2s",
                      opacity: (isSaving || isTesting || !credentialValues.api_key || !credentialValues.secret_key) ? 0.5 : 1
                    }}
                  >
                    {isSaving ? <RefreshCw size={16} style={{ animation: "spin 1s linear infinite" }} /> : <Lock size={16} />}
                    {isSaving ? "Encrypting..." : "Save Keys"}
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>

      {/* Toast Notification */}
      {toast && (
        <div style={{ 
          position: "fixed", 
          right: 24, 
          bottom: 24, 
          background: toast.type === "success" ? "#10b98120" : toast.type === "error" ? "#ef444420" : "#3b82f620",
          border: `1px solid ${toast.type === "success" ? "#10b981" : toast.type === "error" ? "#ef4444" : "#3b82f6"}`,
          color: toast.type === "success" ? "#10b981" : toast.type === "error" ? "#ef4444" : "#3b82f6",
          borderRadius: 12, 
          padding: "16px 20px", 
          fontSize: 13, 
          fontFamily: "system-ui", 
          fontWeight: 500, 
          zIndex: 120, 
          boxShadow: "0 10px 40px rgba(0,0,0,0.4)",
          display: "flex",
          alignItems: "center",
          gap: 12,
          backdropFilter: "blur(10px)"
        }}>
          {toast.type === "success" && <CheckCircle size={20} />}
          {toast.type === "error" && <AlertTriangle size={20} />}
          {toast.type === "info" && <Zap size={20} />}
          {toast.msg}
        </div>
      )}

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
