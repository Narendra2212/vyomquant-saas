import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Plus, Trash2, RefreshCw, CheckCircle, XCircle,
  Globe, Key, Eye, EyeOff, Wifi, Database, AlertTriangle
} from "lucide-react";
import { endpoints } from "../api";
import { C, Card, SectionH, PanelTitle, Btn, Inp, Toast, ToastContainer } from "../components/ui-legacy/primitives";
export default function ExchangeManager() {
  // Built-in fallback list of top CCXT exchanges to ensure UI is never empty
  const POPULAR_EXCHANGES = [
    "binance", "binanceus", "bybit", "okx", "kraken", "coinbasepro",
    "kucoin", "htx", "bitget", "gateio", "mexc", "bitfinex", "bitstamp",
    "gemini", "upbit", "deribit", "phemex", "woo", "bingx", "bitmart",
    "huobi", "crypto.com", "ascendex", "poloniex", "whitebit"
  ].sort();

  const [connectedExchanges, setConnectedExchanges] = useState([]);
  const [supportedExchanges, setSupportedExchanges] = useState(POPULAR_EXCHANGES);
  const [selectedExchange, setSelectedExchange] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [label, setLabel] = useState("");
  const [searchExchange, setSearchExchange] = useState("");
  const [loadingExchanges, setLoadingExchanges] = useState(true);
  const [isTesting, setIsTesting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [processingById, setProcessingById] = useState({});
  const [toast, setToast] = useState(null);
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  const normalizeConnected = (rows = []) =>
    (Array.isArray(rows) ? rows : []).map((row, i) => ({
      id: row.id ?? row.exchange_id ?? i + 1,
      name: row.name ?? row.exchange ?? "Unknown",
      key: row.key_masked ?? row.masked_key ?? "????????????????????????????",
      status: row.status ?? "connected",
      perms: row.perms ?? row.permissions ?? ["Spot Trading", "Read"],
      vol: row.vol ?? row.volume ?? "$0",
      ts: row.ts ?? row.created_at ?? "Just now",
    }));

  const loadConnectedExchanges = async (signal) => {
    try {
      const data = await endpoints.exchange.list();
      const rows = Array.isArray(data) ? data : data?.data || data?.exchanges || [];
      setConnectedExchanges(normalizeConnected(rows));
    } catch (err) {
      if (err?.name !== "CanceledError") console.error("Failed loading connected exchanges:", err);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    const loadAll = async () => {
      setLoadingExchanges(true);
      try {
        // 1. Fetch connected exchanges
        await loadConnectedExchanges(controller.signal);

        // 2. Fetch supported CCXT exchanges
        const supportedData = await endpoints.exchange.getSupported();
        const rawSupported = Array.isArray(supportedData) ? supportedData : supportedData?.supported || [];

        // Only overwrite the fallback if the backend successfully returns a valid array
        if (rawSupported.length > 0) {
          setSupportedExchanges(rawSupported);
        }
      } catch (err) {
        if (err && err.name !== "CanceledError") {
          console.warn("Backend CCXT list failed to load, utilizing built-in fallback list.");
        }
      } finally {
        setLoadingExchanges(false);
      }
    };
    loadAll();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(timer);
  }, [toast]);

  const handleTestConnection = async () => {
    if (!selectedExchange || !apiKey || !secretKey || isTesting) return;
    setIsTesting(true);
    try {
      await endpoints.exchange.testConnection({
        exchange_id: selectedExchange,
        api_key: apiKey,
        secret_key: secretKey,
        label
      });
      setToast({ type: "success", msg: "Connection verified via CCXT successfully." });
    } catch (err) {
      setToast({ type: "error", msg: err?.response?.data?.detail || "Connection test failed. Check API keys." });
    } finally {
      setIsTesting(false);
    }
  };

  const handleSaveKey = async () => {
    if (!selectedExchange || !apiKey || !secretKey || isSaving) return;
    setIsSaving(true);
    try {
      await endpoints.exchange.saveKeys({
        exchange_id: selectedExchange,
        api_key: apiKey,
        secret_key: secretKey,
        label
      });
      setToast({ type: "success", msg: "Exchange keys securely encrypted and stored in Vault." });

      // SECURE CLEANUP: Immediately wipe raw secrets from client state
      setApiKey("");
      setSecretKey("");
      setLabel("");
      setSelectedExchange("");

      // Refresh the list from the backend
      await loadConnectedExchanges();
    } catch (err) {
      setToast({ type: "error", msg: err?.response?.data?.detail || "Failed to save exchange keys." });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteSaved = async (id) => {
    if (processingById[id]) return;
    setProcessingById((p) => ({ ...p, [id]: true }));
    try {
      await endpoints.exchange.delete(id);
      setConnectedExchanges((rows) => rows.filter((row) => row.id !== id));
      setToast({ type: "success", msg: "Exchange connection removed." });
    } catch (err) {
      setToast({ type: "error", msg: "Failed to delete exchange connection." });
    } finally {
      setProcessingById((p) => ({ ...p, [id]: false }));
    }
  };

  const filteredSupported = supportedExchanges.filter((ex) =>
    ex.toLowerCase().includes(searchExchange.toLowerCase())
  );

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, position: "relative" }}>
      <SectionH title="Exchange Vault" sub="Manage institutional API connections via CCXT engine" />

      {/* Connected Exchanges */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
        {loadingExchanges ? (
          <Card cls="p-4"><div style={{ color: C.t3, fontFamily: "monospace", fontSize: 10 }}>Syncing with backend...</div></Card>
        ) : connectedExchanges.length === 0 ? (
          <Card cls="p-4 text-center"><div style={{ color: C.t3, fontFamily: "monospace", fontSize: 11, padding: "10px 0" }}>No active exchange connections found.</div></Card>
        ) : connectedExchanges.map(ex => (
          <Card key={ex.id} cls="p-5">
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <div style={{ background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.15)", borderRadius: 10, padding: 10, flexShrink: 0 }}>
                <Globe size={20} style={{ color: C.cyan }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span style={{ color: C.t1, fontWeight: 900, fontSize: 15, textTransform: "capitalize" }}>{ex.name}</span>
                  <Tag2 c="green">{ex.status.toUpperCase()}</Tag2>
                </div>
                <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                  <Key size={9} style={{ display: "inline", marginRight: 4 }} />{ex.key}
                  <span style={{ margin: "0 8px" }}>?</span>
                  <Clock size={9} style={{ display: "inline", marginRight: 4 }} />Added: {ex.ts}
                </div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <Btn v="danger" sz="sm" Icon={Trash2} onClick={() => handleDeleteSaved(ex.id)} disabled={!!processingById[ex.id]} />
              </div>
            </div>
          </Card>
        ))}
      </div>

      {/* Add New Connection via CCXT */}
      <Card cls="p-6">
        <PanelTitle title="Connect New Exchange" sub="Select from over 100+ supported CCXT integrations. Keys are encrypted with AES-256." />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>

          {/* Left Column: CCXT Search & Scroll List */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 3, textTransform: "uppercase" }}>Supported Exchanges</label>
            <div style={{ position: "relative" }}>
              <Search size={12} style={{ color: C.t3, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", zIndex: 1 }} />
              <input
                placeholder="Search CCXT network..."
                value={searchExchange}
                onChange={e => setSearchExchange(e.target.value)}
                style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, padding: "8px 12px 8px 30px", borderRadius: 8, fontSize: 11, fontFamily: "monospace", outline: "none" }}
              />
            </div>
            <div style={{
              background: C.bg1, border: `1px solid ${C.border}`, borderRadius: 8,
              maxHeight: 220, overflowY: "auto", display: "flex", flexDirection: "column",
              scrollbarWidth: "thin", scrollbarColor: `${C.cyan}40 ${C.bg1}`
            }}>
              {filteredSupported.length === 0 ? (
                <div style={{ padding: 20, textAlign: "center", color: C.t3, fontSize: 10, fontFamily: "monospace" }}>No exchanges match your search.</div>
              ) : filteredSupported.map(ex => (
                <div
                  key={ex}
                  onClick={() => setSelectedExchange(ex)}
                  style={{
                    padding: "10px 14px", borderBottom: `1px solid ${C.border}55`, cursor: "pointer",
                    background: selectedExchange === ex ? `${C.cyan}20` : "transparent",
                    color: selectedExchange === ex ? C.cyan : C.t2,
                    fontSize: 11, fontFamily: "monospace", fontWeight: selectedExchange === ex ? 900 : 400,
                    textTransform: "capitalize", transition: "all 0.1s"
                  }}
                  className="hover:bg-cyan-500/10 hover:text-cyan-400"
                >
                  {ex}
                </div>
              ))}
            </div>
            {selectedExchange && (
              <div style={{ marginTop: 8, fontSize: 10, color: C.green, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 6 }}>
                <CheckCircle size={10} /> Selected: <span style={{ fontWeight: 900, textTransform: "uppercase" }}>{selectedExchange}</span>
              </div>
            )}
          </div>

          {/* Right Column: Credentials Input */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Inp lbl="API Key" ph="Enter exchange API key..." icon={Key} type="password" val={apiKey} onChange={e => setApiKey(e.target.value)} disabled={!selectedExchange} />
            <Inp lbl="Secret Key" ph="Enter exchange secret key..." icon={Lock} type="password" val={secretKey} onChange={e => setSecretKey(e.target.value)} disabled={!selectedExchange} />
            <Inp lbl="Label (optional)" ph="e.g., Main Binance Account" val={label} onChange={e => setLabel(e.target.value)} disabled={!selectedExchange} />

            <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
              <Btn v="outline" sz="sm" Icon={Wifi} cls="flex-1 justify-center" onClick={handleTestConnection} disabled={isTesting || isSaving || !selectedExchange}>
                {isTesting ? "Testing Engine..." : "Test Connection"}
              </Btn>
              <Btn v="primary" sz="sm" cls="flex-1 justify-center" onClick={handleSaveKey} disabled={isSaving || isTesting || !selectedExchange}>
                {isSaving ? "Encrypting..." : "Save Keys"}
              </Btn>
            </div>
          </div>
        </div>
      </Card>

      {/* Toast Notifications */}
      {toast && (
        <div style={{ position: "fixed", right: 24, bottom: 24, background: toast.type === "success" ? `${C.green}20` : `${C.red}20`, border: `1px solid ${toast.type === "success" ? `${C.green}55` : `${C.red}55`}`, color: toast.type === "success" ? C.green : C.red, borderRadius: 8, padding: "10px 16px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, zIndex: 120, boxShadow: "0 10px 30px rgba(0,0,0,0.5)" }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
//  PAGE: RISK SETTINGS
// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
