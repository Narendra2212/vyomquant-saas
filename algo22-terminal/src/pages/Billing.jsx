import React, { useState, useEffect } from "react";
import {
  CreditCard, CheckCircle, Star, Zap, Globe, Award, ArrowUpRight
} from "lucide-react";
import { endpoints } from "../api";
import { C, Card, SectionH, PanelTitle, Btn, Tag2, Toast, ToastContainer } from "../components/ui-legacy/primitives";
export default function Billing() {
  const [currentPlan, setCurrentPlan] = useState(null);
  const [billingHistory, setBillingHistory] = useState([]);
  const [savedMethods, setSavedMethods] = useState([]);
  const [currency, setCurrency] = useState("USD");
  const [isUpgradeModalOpen, setIsUpgradeModalOpen] = useState(false);
  const [isLoadingBilling, setIsLoadingBilling] = useState(true);
  const [isCheckoutLoading, setIsCheckoutLoading] = useState("");
  const [billingError, setBillingError] = useState("");

  const plans = [
    { id: "free", name: "Free Tier", usd: 0, inr: 0, features: ["1 Deployed Bot", "Algorithm Builder", "3 Backtests/mo", "No ML Training"], description: "Start building and testing core ideas." },
    { id: "pro", name: "Pro Tier", usd: 12, inr: 999, features: ["5 Deployed Algos", "Unlimited Backtesting", "Telegram+Email Alerts", "Algorithm Indicators"], description: "Built for active algo developers." },
    { id: "enterprise", name: "Enterprise Tier", usd: 24, inr: 1999, features: ["8 Deployed Algos", "2 ML/DL Models Training", "Priority Support", "Full API Access"], description: "Institutional-grade execution and controls." },
  ];
  const EXTRA_ML_MODEL_PRICE = { INR: 199, USD: 2.5 };

  useEffect(() => {
    const controller = new AbortController();
    const loadBilling = async () => {
      setIsLoadingBilling(true);
      setBillingError("");
      try {
        const [planRes, invoicesRes, methodsRes] = await Promise.allSettled([
          endpoints.billing.getPlan(),
          endpoints.billing.getInvoices(),
          endpoints.billing.getPaymentMethods()
        ]);

        if (planRes.status === 'fulfilled' && planRes.value.data) {
          setCurrentPlan(planRes.value.data.plan || planRes.value.data);
        }
        if (invoicesRes.status === 'fulfilled' && invoicesRes.value.data) {
          setBillingHistory(invoicesRes.value.data.invoices || []);
        }
        if (methodsRes.status === 'fulfilled' && methodsRes.value.data) {
          setSavedMethods(methodsRes.value.data.methods || []);
        }
      } catch (err) {
        console.error("Failed loading billing data:", err);
        setBillingError("Unable to sync billing profile with the server.");
      } finally {
        setIsLoadingBilling(false);
      }
    };
    loadBilling();
    return () => controller.abort();
  }, []);

  const handleCheckout = async (planId) => {
    if (isCheckoutLoading) return;
    setBillingError("");
    setIsCheckoutLoading(planId);
    try {
      let tier = "free";
      if (planId === "pro" || planId === "pro_999") tier = "pro_999";
      else if (planId === "enterprise" || planId === "elite_1999") tier = "elite_1999";

      const data = await endpoints.billing.createCheckout({ tier, currency });
      if (data && data.checkoutUrl) {
        window.location.href = data.checkoutUrl; // Redirect to Stripe/Razorpay
      } else {
        throw new Error("Payment gateway URL not provided by backend.");
      }
    } catch (err) {
      Sentry.captureException(err, {
        tags: { type: "checkout_error", planId },
        extra: { currency, planId }
      });
      setBillingError(err?.response?.data?.detail || "Checkout initialization failed.");
    } finally {
      setIsCheckoutLoading("");
    }
  };

  const money = (amountUSD, amountINR) => {
    return currency === "INR" ? `ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹${Number(amountINR || 0).toLocaleString()}` : `$${Number(amountUSD || 0).toLocaleString()}`;
  };

  const fmtDate = (d) => {
    if (!d) return "N/A";
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  };

  const defaultMethod = savedMethods.find((m) => m.isDefault) || savedMethods[0];

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, position: "relative" }}>
      <SectionH title="Subscription & Billing" sub="Manage your plan, payment methods, and invoices" />

      {!!billingError && (
        <div style={{ marginBottom: 16, background: `${C.red}12`, border: `1px solid ${C.red}44`, color: C.red, borderRadius: 8, padding: "10px 14px", fontSize: 11, fontFamily: "monospace", fontWeight: 700 }}>
          <AlertTriangle size={12} style={{ display: "inline", marginRight: 6 }} /> {billingError}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        {/* Current Plan Card */}
        <Card cls="p-6" style={{ border: `1px solid ${C.cyan}40` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <div>
              <Tag2 c="cyan">CURRENT PLAN</Tag2>
              <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 22, marginTop: 6, textTransform: "capitalize" }}>
                {isLoadingBilling ? "Loading..." : `${currentPlan?.name || "Free"} Tier`}
              </h2>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ color: C.cyan, fontSize: 28, fontWeight: 900 }}>
                {isLoadingBilling ? "..." : money(currentPlan?.priceUSD, currentPlan?.priceINR)}
              </div>
              <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>/month</div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 16 }}>
            {(isLoadingBilling ? ["Syncing features..."] : (currentPlan?.features || [])).map(f => (
              <div key={f} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <CheckCircle size={10} style={{ color: C.cyan, flexShrink: 0 }} />
                <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{f}</span>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: C.bg3, borderRadius: 8, padding: "10px 14px", marginBottom: 14 }}>
            <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>
              Next billing: <span style={{ color: C.t1, fontWeight: 700 }}>{isLoadingBilling ? "..." : fmtDate(currentPlan?.nextBillingDate)}</span>
            </span>
            <Tag2 c={currentPlan?.autoRenew ? "green" : "red"}>{currentPlan?.autoRenew ? "AUTO-RENEW ON" : "AUTO-RENEW OFF"}</Tag2>
          </div>
          <Btn v="outline" sz="sm" cls="w-full justify-center" onClick={() => setIsUpgradeModalOpen(true)}>Upgrade Plan</Btn>
        </Card>

        {/* Payment Methods & History */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Card cls="p-6">
            <PanelTitle title="Payment Method" />
            {isLoadingBilling ? (
              <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Loading vault...</div>
            ) : !defaultMethod ? (
              <div style={{ background: C.bg3, border: `1px dashed ${C.border}`, borderRadius: 10, padding: 16, color: C.t3, fontSize: 10, fontFamily: "monospace", textAlign: "center" }}>No payment methods on file.</div>
            ) : (
              <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ background: "linear-gradient(135deg,#1a1f71,#003087)", borderRadius: 6, padding: "6px 10px", flexShrink: 0 }}>
                  <CreditCard size={18} style={{ color: "#fff" }} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ color: C.t1, fontWeight: 700, fontSize: 12 }}>{defaultMethod.brand} ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {defaultMethod.last4}</div>
                  <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Expires {defaultMethod.expiry}</div>
                </div>
                {defaultMethod.isDefault && <Tag2 c="green">DEFAULT</Tag2>}
              </div>
            )}
            <Btn v="ghost" sz="sm" Icon={Plus} cls="w-full justify-center mt-3">Add Payment Method</Btn>
          </Card>

          <Card cls="p-6 flex-1">
            <PanelTitle title="Billing History" />
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                  {["Invoice", "Date", "Amount", "Status"].map(h => <th key={h} style={{ color: C.t3, fontWeight: 900, padding: "6px 8px", textAlign: "left", fontSize: 8, letterSpacing: 2, textTransform: "uppercase" }}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {isLoadingBilling ? (
                  <tr><td colSpan={4} style={{ padding: "12px 8px", color: C.t3, textAlign: "center" }}>Loading ledger...</td></tr>
                ) : billingHistory.length === 0 ? (
                  <tr><td colSpan={4} style={{ padding: "12px 8px", color: C.t3, textAlign: "center" }}>No invoices found.</td></tr>
                ) : billingHistory.map(inv => (
                  <tr key={inv.id} style={{ borderBottom: `1px solid ${C.border}20` }}>
                    <td style={{ padding: "8px", color: C.cyan, cursor: "pointer" }}>{inv.id}</td>
                    <td style={{ padding: "8px", color: C.t2 }}>{fmtDate(inv.date)}</td>
                    <td style={{ padding: "8px", color: C.t1, fontWeight: 700 }}>{money(inv.amtUSD, inv.amtINR)}</td>
                    <td style={{ padding: "8px" }}><Tag2 c={inv.status === "paid" ? "green" : inv.status === "pending" ? "orange" : "red"}>{inv.status}</Tag2></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      </div>

      {/* Upgrade Modal */}
      {isUpgradeModalOpen && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(1,6,8,0.85)", backdropFilter: "blur(8px)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
          <div style={{ width: "min(1200px, 95vw)", maxHeight: "90vh", overflowY: "auto", background: C.bg1, border: `1px solid ${C.border}`, borderRadius: 16, padding: 32, boxShadow: "0 40px 100px rgba(0,0,0,0.8)" }}>

            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24 }}>
              <div>
                <h3 style={{ color: C.t1, fontSize: 24, fontWeight: 900 }}>Upgrade Your Plan</h3>
                <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginTop: 6 }}>High-performance plans for quant-scale execution.</p>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                <div style={{ display: "flex", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 999, padding: 4 }}>
                  <button onClick={() => setCurrency("USD")} style={{ background: currency === "USD" ? `${C.cyan}20` : "transparent", color: currency === "USD" ? C.cyan : C.t2, border: `1px solid ${currency === "USD" ? `${C.cyan}55` : "transparent"}`, borderRadius: 999, padding: "6px 14px", fontSize: 10, fontFamily: "monospace", fontWeight: 700, cursor: "pointer", transition: "all 0.2s" }}>USD ($)</button>
                  <button onClick={() => setCurrency("INR")} style={{ background: currency === "INR" ? `${C.cyan}20` : "transparent", color: currency === "INR" ? C.cyan : C.t2, border: `1px solid ${currency === "INR" ? `${C.cyan}55` : "transparent"}`, borderRadius: 999, padding: "6px 14px", fontSize: 10, fontFamily: "monospace", fontWeight: 700, cursor: "pointer", transition: "all 0.2s" }}>INR (ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹)</button>
                </div>
                <button onClick={() => setIsUpgradeModalOpen(false)} style={{ color: C.t3, background: "transparent", border: "none", cursor: "pointer" }} className="hover:text-red-400"><XCircle size={24} /></button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-5">
              {plans.map((p) => {
                const isActive = currentPlan?.id === p.id;
                const isProcessingThis = isCheckoutLoading === p.id;
                const priceValue = currency === "INR" ? p.inr : p.usd;
                const extraMlValue = EXTRA_ML_MODEL_PRICE[currency];

                return (
                  <div key={p.id} style={{ background: C.bg2, border: `1px solid ${p.recommended || isActive ? `${C.cyan}60` : C.border}`, borderRadius: 14, padding: 24, boxShadow: p.recommended ? `0 0 30px ${C.cyan}15` : "none", position: "relative", display: "flex", flexDirection: "column" }}>
                    {p.recommended && <div style={{ position: "absolute", top: 12, right: 12 }}><Tag2 c="cyan">RECOMMENDED</Tag2></div>}
                    {isActive && <div style={{ position: "absolute", top: 12, right: 12 }}><Tag2 c="green">CURRENT</Tag2></div>}

                    <div style={{ color: C.t1, fontWeight: 900, fontSize: 20, marginBottom: 6 }}>{p.name}</div>
                    <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", marginBottom: 16, minHeight: 30 }}>{p.description}</div>

                    <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 20 }}>
                      <span style={{ color: C.cyan, fontWeight: 900, fontSize: 36, lineHeight: 1 }}>{currency === "INR" ? "ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹" : "$"}{priceValue.toLocaleString()}</span>
                      <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>/month</span>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 24, flex: 1 }}>
                      {p.features.map(f => (
                        <div key={f} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                          <CheckCircle size={12} style={{ color: C.cyan, flexShrink: 0, marginTop: 2 }} />
                          <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", lineHeight: 1.4 }}>{f}</span>
                        </div>
                      ))}
                      {p.id === "enterprise" && (
                        <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginTop: 4, paddingLeft: 20 }}>
                          +{currency === "INR" ? "ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹" : "$"}{extraMlValue} per extra ML model
                        </div>
                      )}
                    </div>

                    <button
                      disabled={!!isCheckoutLoading || isActive}
                      onClick={() => handleCheckout(p.id)}
                      style={{
                        width: "100%", background: isActive ? `${C.green}20` : C.cyan, color: isActive ? C.green : "#000",
                        border: `1px solid ${isActive ? `${C.green}40` : "transparent"}`, borderRadius: 10, padding: "12px",
                        fontSize: 12, fontFamily: "monospace", fontWeight: 900, textTransform: "uppercase", letterSpacing: 1,
                        cursor: !!isCheckoutLoading || isActive ? "not-allowed" : "pointer",
                        opacity: !!isCheckoutLoading && !isProcessingThis ? 0.4 : 1,
                        transition: "all 0.2s"
                      }}
                    >
                      {isActive ? "Current Plan" : isProcessingThis ? "Redirecting..." : "Subscribe"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
//  PAGE: SECURITY LOGS
// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
