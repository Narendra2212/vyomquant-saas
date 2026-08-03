import React, { useState, useEffect, useMemo, useCallback } from "react";
import {
  CreditCard, CheckCircle, Star, Zap, Globe, Award, ArrowUpRight, Plus, AlertTriangle, XCircle, Loader2, TrendingUp, Bot, Cpu, Database, BarChart3, Shield, Crown, ChevronRight
} from "lucide-react";
import { api } from "../api";
import { C, Card, SectionH, PanelTitle, Btn, Tag2 } from "../components/ui-legacy/primitives";

export default function Billing() {
  const [currentPlan, setCurrentPlan] = useState(null);
  const [billingHistory, setBillingHistory] = useState([]);
  const [savedMethods, setSavedMethods] = useState([]);
  const [currency, setCurrency] = useState("USD");
  const [isLoadingBilling, setIsLoadingBilling] = useState(true);
  const [isCheckoutLoading, setIsCheckoutLoading] = useState("");
  const [billingError, setBillingError] = useState("");
  const [plans, setPlans] = useState([]);
  const [usage, setUsage] = useState({});
  const [quotas, setQuotas] = useState({});
  const [subscriptionStatus, setSubscriptionStatus] = useState("active");
  const [renewalDate, setRenewalDate] = useState(null);
  const [cancelAtPeriodEnd, setCancelAtPeriodEnd] = useState(false);

  useEffect(() => {
    const loadPlans = async () => {
      try {
        const response = await api.billing.getPlans();
        setPlans(response?.plans || []);
      } catch (err) {
        console.error("Failed to load plans:", err);
      }
    };
    loadPlans();
  }, []);

  useEffect(() => {
    const loadCurrency = async () => {
      try {
        const data = await api.billing.getCurrency();
        if (data && data.currency) {
          setCurrency(data.currency);
        }
      } catch (err) {
        console.error("Failed to load currency preference:", err);
      }
    };
    loadCurrency();
  }, []);

  useEffect(() => {
    const loadBilling = async () => {
      setIsLoadingBilling(true);
      setBillingError("");
      try {
        const [entitlementsRes, invoicesRes, methodsRes] = await Promise.allSettled([
          api.billing.getEntitlements(),
          api.billing.getInvoices(),
          api.billing.getPaymentMethods()
        ]);

        if (entitlementsRes.status === 'fulfilled' && entitlementsRes.value.data) {
          const data = entitlementsRes.value.data;
          setCurrentPlan({
            id: data.plan,
            name: data.plan.charAt(0).toUpperCase() + data.plan.slice(1),
            features: data.features || [],
          });
          setUsage(data.usage || {});
          setQuotas(data.quotas || {});
          setSubscriptionStatus(data.subscription_status || "active");
          setRenewalDate(data.renewal_date);
          setCancelAtPeriodEnd(data.cancel_at_period_end || false);
        }
        if (invoicesRes.status === 'fulfilled' && invoicesRes.value.data) {
          setBillingHistory(invoicesRes.value.data || []);
        }
        if (methodsRes.status === 'fulfilled' && methodsRes.value.data) {
          setSavedMethods(methodsRes.value.data || []);
        }
      } catch (err) {
        console.error("Failed loading billing data:", err);
        setBillingError("Unable to sync billing profile with the server.");
      } finally {
        setIsLoadingBilling(false);
      }
    };
    loadBilling();
  }, []);

  // WebSocket listener for real-time subscription updates
  useEffect(() => {
    let ws = null;
    
    const connectWebSocket = () => {
      const token = localStorage.getItem('token');
      if (!token) return;
      
      const userId = localStorage.getItem('userId');
      if (!userId) return;
      
      const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/user/${userId}?token=${token}`;
      
      try {
        ws = new WebSocket(wsUrl);
        
        ws.onopen = () => {
          console.log('Billing WebSocket connected');
        };
        
        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data);
            
            if (message.type === 'subscription_update' || message.type === 'plan_changed') {
              // Refresh billing data when subscription changes
              loadBilling();
            }
          } catch (err) {
            console.error('Failed to parse WebSocket message:', err);
          }
        };
        
        ws.onerror = (error) => {
          console.error('WebSocket error:', error);
        };
        
        ws.onclose = () => {
          console.log('WebSocket closed, reconnecting in 5s...');
          setTimeout(connectWebSocket, 5000);
        };
      } catch (err) {
        console.error('Failed to connect WebSocket:', err);
      }
    };
    
    const loadBilling = async () => {
      setIsLoadingBilling(true);
      setBillingError("");
      try {
        const [entitlementsRes, invoicesRes, methodsRes] = await Promise.allSettled([
          api.billing.getEntitlements(),
          api.billing.getInvoices(),
          api.billing.getPaymentMethods()
        ]);

        if (entitlementsRes.status === 'fulfilled' && entitlementsRes.value.data) {
          const data = entitlementsRes.value.data;
          setCurrentPlan({
            id: data.plan,
            name: data.plan.charAt(0).toUpperCase() + data.plan.slice(1),
            features: data.features || [],
          });
          setUsage(data.usage || {});
          setQuotas(data.quotas || {});
        }
        if (invoicesRes.status === 'fulfilled' && invoicesRes.value.data) {
          setBillingHistory(invoicesRes.value.data || []);
        }
        if (methodsRes.status === 'fulfilled' && methodsRes.value.data) {
          setSavedMethods(methodsRes.value.data || []);
        }
      } catch (err) {
        console.error("Failed loading billing data:", err);
        setBillingError("Unable to sync billing profile with the server.");
      } finally {
        setIsLoadingBilling(false);
      }
    };
    
    connectWebSocket();
    
    return () => {
      if (ws) {
        ws.close();
      }
    };
  }, []);

  const handleCheckout = useCallback(async (planId) => {
    if (isCheckoutLoading) return;
    setBillingError("");
    setIsCheckoutLoading(planId);
    try {
      const data = await api.billing.createCheckout({ tier: planId, currency });
      if (data && data.checkoutUrl) {
        window.location.href = data.checkoutUrl;
      } else {
        throw new Error("Payment gateway URL not provided by backend.");
      }
    } catch (err) {
      console.error("Checkout error:", err);
      const detail = err?.response?.data?.detail;
      const message = typeof detail === 'string' ? detail : "Checkout initialization failed.";
      setBillingError(message);
    } finally {
      setIsCheckoutLoading("");
    }
  }, [isCheckoutLoading, currency]);

  const handleCurrencyChange = useCallback(async (newCurrency) => {
    setCurrency(newCurrency);
    try {
      await api.billing.setCurrency(newCurrency);
    } catch (err) {
      console.error("Failed to save currency preference:", err);
    }
  }, []);

  const money = useCallback((amountUSD, amountINR) => {
    return currency === "INR" ? `₹${Number(amountINR || 0).toLocaleString()}` : `$${Number(amountUSD || 0).toLocaleString()}`;
  }, [currency]);

  const fmtDate = useCallback((d) => {
    if (!d) return "N/A";
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }, []);

  const defaultMethod = useMemo(() => savedMethods.find((m) => m.is_default) || savedMethods[0], [savedMethods]);

  const getUsagePercent = useCallback((resource) => {
    const used = usage[resource] || 0;
    const limit = quotas[resource] || 0;
    if (limit === 0 || limit === Infinity) return 0;
    return Math.min((used / limit) * 100, 100);
  }, [usage, quotas]);

  const getUsageColor = useCallback((percent) => {
    if (percent >= 90) return C.red;
    if (percent >= 70) return "#f59e0b";
    return C.green;
  }, []);

  return (
    <div style={{ padding: 24, overflowY: "auto", flex: 1, background: C.bg0 }}>
      <SectionH title="Subscription & Billing" sub="Manage your plan, usage, and payment methods" />

      {!!billingError && (
        <div style={{ marginBottom: 16, background: `${C.red}12`, border: `1px solid ${C.red}44`, color: C.red, borderRadius: 8, padding: "12px 16px", fontSize: 12, fontFamily: "monospace", fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span><AlertTriangle size={14} style={{ display: "inline", marginRight: 8, verticalAlign: "middle" }} /> {billingError}</span>
          <button onClick={() => window.location.reload()} style={{ background: `${C.red}20`, border: `1px solid ${C.red}40`, color: C.red, borderRadius: 6, padding: "6px 12px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, cursor: "pointer", transition: "all 0.2s" }}>Retry</button>
        </div>
      )}

      {isLoadingBilling ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Skeleton for Current Plan */}
          <div style={{ background: C.bg1, border: `1px solid ${C.border}`, borderRadius: 12, padding: 24, height: 120 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
              <div style={{ width: 200, height: 24, background: C.bg3, borderRadius: 4 }} />
              <div style={{ width: 100, height: 32, background: C.bg3, borderRadius: 16 }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
              {[1, 2, 3].map(i => (
                <div key={i} style={{ height: 60, background: C.bg2, borderRadius: 8 }} />
              ))}
            </div>
          </div>
          {/* Skeleton for Pricing Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
            {[1, 2, 3, 4].map(i => (
              <div key={i} style={{ background: C.bg1, border: `1px solid ${C.border}`, borderRadius: 12, padding: 24, height: 320 }} />
            ))}
          </div>
        </div>
      ) : (
        <>
          {/* Current Plan Overview */}
          <Card cls="p-6 mb-6" style={{ border: `1px solid ${C.cyan}40`, background: `linear-gradient(135deg, ${C.bg2} 0%, ${C.bg1} 100%)` }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ background: `${C.cyan}20`, borderRadius: 12, padding: 12 }}>
                  {currentPlan?.id === "enterprise" ? <Crown size={24} style={{ color: C.cyan }} /> :
                   currentPlan?.id === "pro" ? <Star size={24} style={{ color: C.cyan }} /> :
                   currentPlan?.id === "starter" ? <Zap size={24} style={{ color: C.cyan }} /> :
                   <Shield size={24} style={{ color: C.cyan }} />}
                </div>
                <div>
                  <Tag2 c="cyan" style={{ marginBottom: 4 }}>CURRENT PLAN</Tag2>
                  <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 24, textTransform: "capitalize" }}>
                    {currentPlan?.name || "Free"}
                  </h2>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                    <Tag2 c={subscriptionStatus === "active" ? "green" : subscriptionStatus === "cancelled" ? "red" : "orange"}>
                      {subscriptionStatus?.toUpperCase() || "ACTIVE"}
                    </Tag2>
                    {renewalDate && (
                      <span style={{ color: C.t3, fontSize: 11, fontFamily: "monospace" }}>
                        Renews {fmtDate(renewalDate)}
                      </span>
                    )}
                    {cancelAtPeriodEnd && (
                      <span style={{ color: C.red, fontSize: 11, fontFamily: "monospace", fontWeight: 700 }}>
                        Cancels at renewal
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ display: "flex", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 999, padding: 4 }}>
                  <button onClick={() => handleCurrencyChange("USD")} style={{ background: currency === "USD" ? `${C.cyan}20` : "transparent", color: currency === "USD" ? C.cyan : C.t2, border: `1px solid ${currency === "USD" ? `${C.cyan}55` : "transparent"}`, borderRadius: 999, padding: "6px 14px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, cursor: "pointer", transition: "all 0.2s" }}>USD ($)</button>
                  <button onClick={() => handleCurrencyChange("INR")} style={{ background: currency === "INR" ? `${C.cyan}20` : "transparent", color: currency === "INR" ? C.cyan : C.t2, border: `1px solid ${currency === "INR" ? `${C.cyan}55` : "transparent"}`, borderRadius: 999, padding: "6px 14px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, cursor: "pointer", transition: "all 0.2s" }}>INR (₹)</button>
                </div>
              </div>
            </div>

            {/* Usage Summary */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, marginBottom: 20 }}>
              {[
                { label: "Strategies", used: usage.strategies || 0, limit: quotas.strategies || 0, icon: Database },
                { label: "Live Bots", used: usage.bots || 0, limit: quotas.bots || 0, icon: Bot },
                { label: "ML Models", used: usage.ml_trainings || 0, limit: quotas.ml_trainings || 0, icon: Cpu },
                { label: "Marketplace Listings", used: usage.marketplace_published || 0, limit: quotas.marketplace_published || 0, icon: TrendingUp },
              ].map((item) => {
                const percent = item.limit === Infinity ? 0 : getUsagePercent(item.label.toLowerCase().replace(" ", "_"));
                const color = getUsageColor(percent);
                const displayLimit = item.limit === Infinity ? "∞" : item.limit;
                return (
                  <div key={item.label} style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <item.icon size={16} style={{ color: C.t3 }} />
                      <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", fontWeight: 600 }}>{item.label}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 8 }}>
                      <span style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>{item.used}</span>
                      <span style={{ color: C.t3, fontSize: 12, fontFamily: "monospace" }}>/ {displayLimit}</span>
                    </div>
                    <div style={{ height: 6, background: C.bg1, borderRadius: 3, overflow: "hidden" }}>
                      <div style={{ height: "100%", background: color, width: `${percent}%`, transition: "width 0.3s ease" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Pricing Cards */}
          <div style={{ marginBottom: 24 }}>
            <h3 style={{ color: C.t1, fontSize: 18, fontWeight: 900, marginBottom: 16 }}>Available Plans</h3>
            {plans.length === 0 ? (
              <div style={{ color: C.t3, fontSize: 12, fontFamily: "monospace", textAlign: "center", padding: 24 }}>Loading plans...</div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
                {plans.map((p) => {
                const isActive = currentPlan?.id === p.id;
                const isProcessingThis = isCheckoutLoading === p.id;
                const priceValue = currency === "INR" ? p.inr : p.usd;

                return (
                  <div key={p.id} style={{ 
                    background: C.bg2, 
                    border: `2px solid ${p.recommended || isActive ? C.cyan : C.border}`, 
                    borderRadius: 16, 
                    padding: 24, 
                    position: "relative", 
                    display: "flex", 
                    flexDirection: "column", 
                    boxShadow: p.recommended ? `0 0 40px ${C.cyan}15` : "none",
                    transition: "all 0.3s ease",
                    cursor: isActive ? "default" : "pointer",
                    transform: isActive ? "none" : "translateY(0)",
                  }}
                  onMouseEnter={(e) => {
                    if (!isActive) {
                      e.currentTarget.style.transform = "translateY(-8px)";
                      e.currentTarget.style.boxShadow = `0 12px 40px ${C.cyan}20`;
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isActive) {
                      e.currentTarget.style.transform = "translateY(0)";
                      e.currentTarget.style.boxShadow = p.recommended ? `0 0 40px ${C.cyan}15` : "none";
                    }
                  }}
                  >
                    {p.recommended && !isActive && (
                      <div style={{ position: "absolute", top: -12, left: "50%", transform: "translateX(-50%)", background: C.cyan, color: "#000", fontSize: 10, fontWeight: 900, letterSpacing: 1, padding: "4px 12px", borderRadius: 999 }}>RECOMMENDED</div>
                    )}
                    {isActive && (
                      <div style={{ position: "absolute", top: -12, left: "50%", transform: "translateX(-50%)", background: C.green, color: "#000", fontSize: 10, fontWeight: 900, letterSpacing: 1, padding: "4px 12px", borderRadius: 999 }}>CURRENT</div>
                    )}

                    <div style={{ color: C.t1, fontWeight: 900, fontSize: 22, marginBottom: 4 }}>{p.name}</div>
                    <div style={{ color: C.t3, fontSize: 12, fontFamily: "monospace", marginBottom: 16 }}>{p.description}</div>

                    <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 20 }}>
                      <span style={{ color: C.cyan, fontWeight: 900, fontSize: 40, lineHeight: 1 }}>{currency === "INR" ? "₹" : "$"}{priceValue.toLocaleString()}</span>
                      <span style={{ color: C.t3, fontSize: 12, fontFamily: "monospace" }}>/month</span>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 24, flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <CheckCircle size={14} style={{ color: C.cyan, flexShrink: 0 }} />
                        <span style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>Strategies: {p.quotas?.strategies === Infinity ? "Unlimited" : p.quotas?.strategies || 0}</span>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <CheckCircle size={14} style={{ color: C.cyan, flexShrink: 0 }} />
                        <span style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>Live Bots: {p.quotas?.bots === Infinity ? "Unlimited" : p.quotas?.bots || 0}</span>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <CheckCircle size={14} style={{ color: C.cyan, flexShrink: 0 }} />
                        <span style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>ML Models: {p.quotas?.ml_trainings === Infinity ? "Unlimited" : p.quotas?.ml_trainings || 0}</span>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <CheckCircle size={14} style={{ color: C.cyan, flexShrink: 0 }} />
                        <span style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>Marketplace: {p.quotas?.marketplace_published === Infinity ? "Unlimited" : p.quotas?.marketplace_published || 0}</span>
                      </div>
                      {p.features.includes("marketplace_access") && (
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <CheckCircle size={14} style={{ color: C.cyan, flexShrink: 0 }} />
                          <span style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>Marketplace Access</span>
                        </div>
                      )}
                      {p.features.includes("api_access") && (
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <CheckCircle size={14} style={{ color: C.cyan, flexShrink: 0 }} />
                          <span style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>API Access</span>
                        </div>
                      )}
                      {p.features.includes("priority_support") && (
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <CheckCircle size={14} style={{ color: C.cyan, flexShrink: 0 }} />
                          <span style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>Priority Support</span>
                        </div>
                      )}
                    </div>

                    <button
                      disabled={!!isCheckoutLoading || isActive}
                      onClick={() => handleCheckout(p.id)}
                      style={{
                        width: "100%", background: isActive ? `${C.green}20` : C.cyan, color: isActive ? C.green : "#000",
                        border: `1px solid ${isActive ? `${C.green}40` : "transparent"}`, borderRadius: 10, padding: "14px",
                        fontSize: 12, fontFamily: "monospace", fontWeight: 900, textTransform: "uppercase", letterSpacing: 1,
                        cursor: !!isCheckoutLoading || isActive ? "not-allowed" : "pointer",
                        opacity: !!isCheckoutLoading && !isProcessingThis ? 0.5 : 1,
                        transition: "all 0.3s ease",
                      }}
                      onMouseEnter={(e) => {
                        if (!isActive && !isCheckoutLoading) {
                          e.currentTarget.style.transform = "scale(1.02)";
                          e.currentTarget.style.boxShadow = `0 4px 20px ${C.cyan}30`;
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!isActive && !isCheckoutLoading) {
                          e.currentTarget.style.transform = "scale(1)";
                          e.currentTarget.style.boxShadow = "none";
                        }
                      }}
                    >
                      {isActive ? "Current Plan" : isProcessingThis ? <Loader2 size={14} className="animate-spin" /> : "Subscribe"}
                    </button>
                  </div>
                );
              })}
            </div>
            )}
          </div>

          {/* Feature Comparison Table */}
          <Card cls="p-6 mb-6">
            <h3 style={{ color: C.t1, fontSize: 18, fontWeight: 900, marginBottom: 16 }}>Feature Comparison</h3>
            {plans.length === 0 ? (
              <div style={{ color: C.t3, fontSize: 12, fontFamily: "monospace", textAlign: "center", padding: 24 }}>Loading plans...</div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "monospace" }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                      <th style={{ color: C.t3, fontWeight: 900, padding: "12px 16px", textAlign: "left", fontSize: 10, letterSpacing: 1, textTransform: "uppercase" }}>Feature</th>
                      {plans.map(p => (
                        <th key={p.id} style={{ color: currentPlan?.id === p.id ? C.cyan : C.t3, fontWeight: 900, padding: "12px 16px", textAlign: "center", fontSize: 10, letterSpacing: 1, textTransform: "uppercase", background: currentPlan?.id === p.id ? `${C.cyan}10` : "transparent" }}>{p.name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { feature: "Strategy Builder", getVal: (p) => p.features.includes("unlimited_builder") ? "✓" : "✗" },
                      { feature: "Backtesting", getVal: (p) => p.features.includes("unlimited_backtesting") ? "✓" : "✗" },
                      { feature: "Live Trading", getVal: (p) => p.features.includes("live_trading") ? "✓" : "✗" },
                      { feature: "Saved Strategies", getVal: (p) => p.quotas?.strategies === Infinity ? "∞" : p.quotas?.strategies || 0 },
                      { feature: "Live Bots", getVal: (p) => p.quotas?.bots === Infinity ? "∞" : p.quotas?.bots || 0 },
                      { feature: "ML Training", getVal: (p) => p.features.includes("ml_training") ? (p.quotas?.ml_trainings === Infinity ? "∞" : p.quotas?.ml_trainings || 0) : "✗" },
                      { feature: "Marketplace Access", getVal: (p) => p.features.includes("marketplace_access") ? "✓" : "✗" },
                      { feature: "Marketplace Publishing", getVal: (p) => p.features.includes("marketplace_publish") ? (p.quotas?.marketplace_published === Infinity ? "∞" : p.quotas?.marketplace_published || 0) : "✗" },
                      { feature: "API Access", getVal: (p) => p.features.includes("api_access") ? "✓" : "✗" },
                      { feature: "Priority Support", getVal: (p) => p.features.includes("priority_support") ? "✓" : "✗" },
                    ].map((row, ri) => (
                      <tr key={ri} style={{ borderBottom: `1px solid ${C.border}20` }}>
                        <td style={{ padding: "12px 16px", color: C.t2, fontWeight: 600 }}>{row.feature}</td>
                        {plans.map(p => {
                          const val = row.getVal(p);
                          const isCurrent = currentPlan?.id === p.id;
                          const isIncluded = val === "✓" || val === "∞" || (typeof val === "number" && val > 0);
                          return (
                            <td key={p.id} style={{ padding: "12px 16px", textAlign: "center", color: isCurrent ? C.cyan : isIncluded ? C.t1 : C.t3, background: isCurrent ? `${C.cyan}10` : "transparent", fontWeight: isCurrent ? 700 : 400 }}>
                              {val}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {/* Payment Methods & Billing History */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(400px, 1fr))", gap: 16 }}>
            <Card cls="p-6">
              <PanelTitle title="Payment Methods" />
              {!defaultMethod ? (
                <div style={{ background: C.bg3, border: `1px dashed ${C.border}`, borderRadius: 10, padding: 24, color: C.t3, fontSize: 12, fontFamily: "monospace", textAlign: "center", marginTop: 16 }}>No payment methods on file.</div>
              ) : (
                <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, display: "flex", alignItems: "center", gap: 12, marginTop: 16 }}>
                  <div style={{ background: "linear-gradient(135deg,#1a1f71,#003087)", borderRadius: 8, padding: 10, flexShrink: 0 }}>
                    <CreditCard size={20} style={{ color: "#fff" }} />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ color: C.t1, fontWeight: 700, fontSize: 13 }}>{defaultMethod.brand} •••• {defaultMethod.last4}</div>
                    <div style={{ color: C.t3, fontSize: 11, fontFamily: "monospace" }}>Expires {defaultMethod.expiry_month}/{defaultMethod.expiry_year}</div>
                  </div>
                  {defaultMethod.is_default && <Tag2 c="green">DEFAULT</Tag2>}
                </div>
              )}
              <Btn v="ghost" sz="sm" Icon={Plus} cls="w-full justify-center mt-4">Add Payment Method</Btn>
            </Card>

            <Card cls="p-6">
              <PanelTitle title="Billing History" />
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace", marginTop: 16 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                    {["Invoice", "Date", "Amount", "Status"].map(h => <th key={h} style={{ color: C.t3, fontWeight: 900, padding: "8px 12px", textAlign: "left", fontSize: 9, letterSpacing: 1, textTransform: "uppercase" }}>{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {billingHistory.length === 0 ? (
                    <tr><td colSpan={4} style={{ padding: "16px 12px", color: C.t3, textAlign: "center" }}>No invoices found.</td></tr>
                  ) : billingHistory.slice(0, 5).map(inv => (
                    <tr key={inv.id} style={{ borderBottom: `1px solid ${C.border}20` }}>
                      <td style={{ padding: "10px 12px", color: C.cyan, cursor: "pointer", fontFamily: "monospace" }}>{inv.id.slice(0, 8)}...</td>
                      <td style={{ padding: "10px 12px", color: C.t2 }}>{fmtDate(inv.date)}</td>
                      <td style={{ padding: "10px 12px", color: C.t1, fontWeight: 700 }}>{money(inv.amtUSD, inv.amtINR)}</td>
                      <td style={{ padding: "10px 12px" }}><Tag2 c={inv.status === "paid" ? "green" : inv.status === "pending" ? "orange" : "red"}>{inv.status}</Tag2></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
//  PAGE: SECURITY LOGS
// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
