import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Check, Shield, Zap, Rocket, ChevronRight, Layers, ArrowRight, ShieldCheck, Mail, Bell, CheckCircle, Key, Lock, Wifi, Database, Activity, Loader2 } from "lucide-react";
import { C, Btn, Inp, Card } from "../components/ui-legacy/primitives";
import endpoints from "../utils/endpoints";
import { supabase } from "../supabase";

export default function Wizard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [isCheckoutLoading, setIsCheckoutLoading] = useState("");
  const [backtestStatus, setBacktestStatus] = useState("idle");
  const [securityStatus, setSecurityStatus] = useState({
    emailVerified: false,
    mfaEnabled: false,
    emailAddress: "",
    loading: true
  });
  
  useEffect(() => {
    async function fetchSecurityStatus() {
      try {
        const { data: { user } } = await supabase.auth.getUser();
        if (user) {
          const emailVerified = !!user.email_confirmed_at;
          const { data, error } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
          const mfaEnabled = data?.currentLevel === 'aal2' || data?.nextLevel === 'aal2';

          setSecurityStatus({ 
            emailVerified, 
            mfaEnabled, 
            emailAddress: user.email || "",
            loading: false 
          });
        } else {
           setSecurityStatus(s => ({ ...s, loading: false }));
        }
      } catch (err) {
        console.error("Failed to fetch security status:", err);
        setSecurityStatus(s => ({ ...s, loading: false }));
      }
    }
    fetchSecurityStatus();
  }, []);

  const steps = ["Secure Account", "Demo Backtest", "Connect Exchange", "Choose Plan"];
  const plans = [
    { id: "free", n: "Free", tier: "free", inr: 0, usd: 0, f: ["1 Deployed Bot", "Algorithm Builder", "3 Backtests/mo", "No ML Training"] },
    { id: "pro", n: "Pro Tier", tier: "pro_999", inr: 999, usd: 12, f: ["5 Deployed Algos", "Unlimited Backtesting", "Telegram+Email Alerts", "Algorithm Indicators"], best: true },
    { id: "elite", n: "Enterprise Tier", tier: "elite_1999", inr: 1999, usd: 24, f: ["8 Deployed Algos", "2 ML/DL Models Training", "Priority Support", "Full API Access"] },
  ];

  const handleSelectPlan = async (p) => {
    if (p.tier === "free") {
      navigate("/app/dashboard");
      return;
    }
    setIsCheckoutLoading(p.tier);
    try {
      const data = await endpoints.billing.createCheckout({ tier: p.tier, currency: "INR" });
      if (data && data.checkoutUrl) {
        window.location.href = data.checkoutUrl;
      } else {
        navigate("/app/dashboard");
      }
    } catch (err) {
      console.error("Wizard checkout error:", err);
      navigate("/app/dashboard");
    } finally {
      setIsCheckoutLoading("");
    }
  };

  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 32 }}>
      {/* Progress */}
      <div style={{ display: "flex", alignItems: "center", width: "100%", maxWidth: 560, marginBottom: 36 }}>
        {steps.map((s, i) => (
          <div key={s} style={{ display: "flex", alignItems: "center", flex: 1 }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div style={{
                width: 30, height: 30, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                background: i < step ? C.cyan : i === step ? "rgba(0,212,255,0.12)" : C.bg3,
                border: `2px solid ${i <= step ? C.cyan : C.border}`,
                color: i < step ? "#000" : i === step ? C.cyan : C.t3, fontWeight: 900, fontSize: 11, fontFamily: "monospace"
              }}>
                {i < step ? "✓" : i + 1}
              </div>
              <span style={{ color: i === step ? C.t1 : C.t3, fontSize: 9, fontFamily: "monospace", marginTop: 4, whiteSpace: "nowrap" }}>{s}</span>
            </div>
            {i < 3 && <div style={{ height: 2, flex: 1, background: i < step ? C.cyan : C.border, margin: "0 4px", marginBottom: 16 }} />}
          </div>
        ))}
      </div>

      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 32, width: "100%", maxWidth: step === 3 ? 720 : 480 }}>
        {step === 0 && (
          <div>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 18, marginBottom: 4 }}>Secure Your Account</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 20 }}>These settings protect your funds. Please complete all steps.</p>
            {securityStatus.loading ? (
              <div style={{ display: "flex", justifyContent: "center", alignItems: "center", padding: 40, color: C.cyan }}>
                <Loader2 size={32} className="animate-spin" style={{ animation: "spin 1s linear infinite" }} />
                <style>{`@keyframes spin { 100% { transform: rotate(360deg); } }`}</style>
              </div>
            ) : (
              <>
                {[
                  { I: ShieldCheck, t: "2FA Enabled", d: "TOTP via Google Authenticator", ok: securityStatus.mfaEnabled, action: "Enable" }, 
                  { I: Mail, t: "Email Verified", d: securityStatus.emailAddress ? `Confirmation sent to ${securityStatus.emailAddress}` : "Confirm your email address", ok: securityStatus.emailVerified, action: "Verify" }, 
                  { I: Bell, t: "Security Alerts", d: "Notify on new device logins", ok: false, action: "Enable" }
                ].map(r => (
                  <div key={r.t} style={{ background: C.bg3, border: `1px solid ${r.ok ? "rgba(0,255,136,0.15)" : C.border}`, borderRadius: 10, padding: 14, display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                    <r.I size={15} style={{ color: r.ok ? C.green : C.t3 }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ color: C.t1, fontSize: 12, fontWeight: 700 }}>{r.t}</div>
                      <div style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{r.d}</div>
                    </div>
                    {r.ok ? <CheckCircle size={15} style={{ color: C.green }} /> : <Btn v="outline" sz="xs" onClick={() => {}}>{r.action}</Btn>}
                  </div>
                ))}
                <Btn v="primary" cls="w-full justify-center mt-4" onClick={() => setStep(1)}>Continue →</Btn>
              </>
            )}
          </div>
        )}
        {step === 1 && (
          <div>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 18, marginBottom: 4 }}>Run Your First Backtest</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 20 }}>See how a simple MACD crossover strategy would have performed on BTC/USDT over the last 30 days.</p>
            
            <Card cls="p-6 flex flex-col items-center justify-center mb-6" style={{ minHeight: 180 }}>
              {backtestStatus === "idle" && (
                <>
                  <Database size={40} style={{ color: C.cyan, marginBottom: 16, opacity: 0.8 }} />
                  <Btn v="primary" onClick={() => {
                    setBacktestStatus("running");
                    setTimeout(() => setBacktestStatus("complete"), 2000);
                  }}>Run Simulated Backtest</Btn>
                </>
              )}
              {backtestStatus === "running" && (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
                  <Activity size={32} className="animate-spin" style={{ color: C.cyan, animation: "spin 1s linear infinite" }} />
                  <style>{`@keyframes spin { 100% { transform: rotate(360deg); } }`}</style>
                  <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>Processing historical ticks...</span>
                </div>
              )}
              {backtestStatus === "complete" && (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: "100%" }}>
                  <CheckCircle size={32} style={{ color: C.green, marginBottom: 12 }} />
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, width: "100%", marginBottom: 16 }}>
                    <div style={{ background: C.bg1, border: `1px solid ${C.border}`, padding: 12, borderRadius: 8, textAlign: "center" }}>
                      <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>Total Return</div>
                      <div style={{ color: C.green, fontSize: 16, fontWeight: 900 }}>+12.4%</div>
                    </div>
                    <div style={{ background: C.bg1, border: `1px solid ${C.border}`, padding: 12, borderRadius: 8, textAlign: "center" }}>
                      <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>Win Rate</div>
                      <div style={{ color: C.cyan, fontSize: 16, fontWeight: 900 }}>68.2%</div>
                    </div>
                  </div>
                  <span style={{ color: C.t1, fontSize: 11, fontWeight: 700 }}>Backtest Complete!</span>
                </div>
              )}
            </Card>
            
            <Btn v="primary" cls="w-full justify-center" disabled={backtestStatus !== "complete"} onClick={() => setStep(2)}>
              Next: Connect Exchange →
            </Btn>
          </div>
        )}
        {step === 2 && (
          <div>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 18, marginBottom: 4 }}>Connect Your Exchange</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 20 }}>Add API credentials to deploy bots. Keys are encrypted at rest.</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginBottom: 16 }}>
              {["Binance", "Bybit", "OKX", "Kraken", "Coinbase", "KuCoin"].map(ex => (
                <button key={ex} style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 0", fontSize: 10, fontFamily: "monospace", fontWeight: 700, color: C.t2, cursor: "pointer", transition: "all 0.15s" }}
                  className="hover:border-cyan-500/40 hover:text-cyan-400">{ex}</button>
              ))}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 16 }}>
              <Inp lbl="API Key" ph="Paste your API key..." icon={Key} type="password" />
              <Inp lbl="Secret Key" ph="Paste your secret key..." icon={Lock} type="password" />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Btn v="outline" sz="sm" Icon={Wifi}>Test Connection</Btn>
              <Btn v="primary" cls="flex-1 justify-center" onClick={() => setStep(3)}>Next: Choose Plan →</Btn>
            </div>
          </div>
        )}
        {step === 3 && (
          <div>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 18, marginBottom: 4, textAlign: "center" }}>Choose Your Plan</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 24, textAlign: "center" }}>Canonical pricing matching backend SubscriptionTier limits.</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 14 }}>
              {plans.map(p => (
                <div key={p.n} style={{ background: p.best ? "rgba(0,212,255,0.05)" : C.bg3, border: `1px solid ${p.best ? C.cyan : C.border}`, borderRadius: 12, padding: 20, position: "relative", display: "flex", flexDirection: "column" }}>
                  {p.best && <div style={{ position: "absolute", top: -10, left: "50%", transform: "translateX(-50%)", background: C.cyan, color: "#000", fontSize: 8, fontWeight: 900, letterSpacing: 2, padding: "2px 10px", borderRadius: 20 }}>BEST VALUE</div>}
                  <div style={{ color: C.t1, fontWeight: 900, fontSize: 14 }}>{p.n}</div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 3, margin: "10px 0" }}>
                    <span style={{ color: p.best ? C.cyan : C.t1, fontSize: 24, fontWeight: 900 }}>{p.inr === 0 ? "Free" : `₹${p.inr}`}</span>
                    {p.inr > 0 && <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>/mo ({p.usd})</span>}
                  </div>
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 5, marginBottom: 14 }}>
                    {p.f.map(f => <div key={f} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, fontFamily: "monospace", color: C.t2 }}><CheckCircle size={9} style={{ color: p.best ? C.cyan : C.green, flexShrink: 0 }} />{f}</div>)}
                  </div>
                  <Btn v={p.best ? "primary" : "outline"} sz="sm" cls="w-full justify-center" disabled={isCheckoutLoading === p.tier} onClick={() => handleSelectPlan(p)}>
                    {isCheckoutLoading === p.tier ? "Loading..." : "Select →"}
                  </Btn>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
