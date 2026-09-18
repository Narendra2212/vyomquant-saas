import React, { useState } from "react";
import { Shield } from "lucide-react";
import { token } from "../design/tokens";

export const LegalPage = ({ onBack, go }) => {
  const [activeTab, setActiveTab] = useState("tos"); // tos, privacy, risk, refund

  // Tab content with gorgeous dark glassmorphic panels
  const tabs = {
    tos: {
      title: "Terms of Service",
      content: (
        <div style={{ lineHeight: "1.6", color: token.content.primary }}>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>1. Beta Service & Paper Trading Only</h2>
          <p style={{ marginBottom: "12px" }}>The Beta Service is provided solely for testing, evaluation, and paper trading purposes.</p>
          <ul style={{ paddingLeft: "20px", marginBottom: "16px", listStyleType: "square", color: token.content.secondary }}>
            <li><strong>NO REAL MONETARY TRANSACTIONS OR DEPLOYMENTS</strong>: You acknowledge that the Beta Service uses simulated balances and does not perform live trades on real exchange accounts.</li>
            <li><strong>AS-IS BASIS</strong>: Provided on an "AS IS" and "AS AVAILABLE" basis.</li>
          </ul>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>2. Eligibility & Accounts</h2>
          <p style={{ marginBottom: "16px" }}>You are responsible for keeping your credentials (including API keys, passwords, and 2FA settings) secure.</p>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>3. Subscription Tiers & Billing</h2>
          <p style={{ marginBottom: "16px" }}>Subscriptions are billed in advance. All transactions are processed via third-party providers (Stripe, Razorpay).</p>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>4. Prohibited Uses</h2>
          <p style={{ marginBottom: "16px" }}>You agree not to reverse engineer, decompile, or attempt to extract source code.</p>
        </div>
      )
    },
    privacy: {
      title: "Privacy Policy",
      content: (
        <div style={{ lineHeight: "1.6", color: token.content.primary }}>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>1. Information We Collect</h2>
          <p style={{ marginBottom: "12px" }}>We collect account credentials via Supabase Auth, encrypted exchange API keys, usage telemetry, and billing tokens.</p>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>2. How We Use Information</h2>
          <p style={{ marginBottom: "12px" }}>To authenticate users, execute backtests/model training, verify webhooks, and optimize platform performance.</p>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>3. Data Storage & Security</h2>
          <p style={{ marginBottom: "12px" }}>Authentication data is stored securely via Supabase Auth. API keys and secrets are encrypted in transit and at rest.</p>
        </div>
      )
    },
    risk: {
      title: "Risk Disclosure",
      content: (
        <div style={{ lineHeight: "1.6", color: token.content.primary }}>
          <h2 style={{ color: token.status.loss.fg, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>⚠️ High-Risk Investment Warning</h2>
          <p style={{ marginBottom: "12px" }}>Trading financial instruments involves substantial risk of loss and is not suitable for every investor.</p>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>1. Algorithmic & Quantitative Trading Risk</h2>
          <p style={{ marginBottom: "12px" }}>Automated strategies execute based on predefined rules. Bugs, logic errors, or connection drops can lead to unexpected and costly executions.</p>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>2. Simulated / Paper Trading Limitations</h2>
          <p style={{ marginBottom: "12px" }}>Paper trading uses simulated balances and idealized execution. It does not account for real-world liquidity, market impact, slippage, or latency.</p>
        </div>
      )
    },
    refund: {
      title: "Refund Policy",
      content: (
        <div style={{ lineHeight: "1.6", color: token.content.primary }}>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>1. Digital Subscription Tiers</h2>
          <p style={{ marginBottom: "12px" }}>All subscription payments (Pro, Enterprise, and ML Add-ons) are digital software entitlements activated immediately. All purchases are final and non-refundable.</p>
          <h2 style={{ color: token.brand.base, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>2. Cancellation and Renewal</h2>
          <p style={{ marginBottom: "12px" }}>You may cancel your subscription at any time. Upon cancellation, you retain access until the end of your billing period.</p>
        </div>
      )
    }
  };

  return (
    <div style={{
      background: "rgba(14, 19, 25, 0.75)",
      backdropFilter: "blur(20px)",
      border: `1px solid ${token.line.default}`,
      borderRadius: token.radius.xl,
      padding: "24px",
      maxWidth: "800px",
      width: "100%",
      margin: "40px auto",
      boxShadow: "0 20px 40px rgba(0,0,0,0.5)",
      fontFamily: "monospace"
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "20px" }}>
        <h1 style={{ color: token.content.primary, fontSize: "16px", fontWeight: "800", display: "flex", alignItems: "center", gap: 10 }}>
          <Shield size={16} color={token.brand.base} /> LEGAL & RISK CENTER
        </h1>
        {onBack && (
          <button onClick={onBack} style={{
            background: token.surface.inset,
            border: `1px solid ${token.line.default}`,
            color: token.content.secondary,
            padding: "6px 12px",
            borderRadius: token.radius.md,
            cursor: "pointer",
            fontSize: "10px",
            transition: "all 0.2s"
          }}
            onMouseEnter={(e) => { e.target.style.background = token.surface.inset; e.target.style.color = token.content.primary; }}
            onMouseLeave={(e) => { e.target.style.background = token.surface.inset; e.target.style.color = token.content.secondary; }}
          >
            ← BACK
          </button>
        )}
      </div>

      {/* Tabs list */}
      <div style={{ display: "flex", borderBottom: `1px solid ${token.line.default}`, gap: 8, marginBottom: "20px" }}>
        {Object.entries(tabs).map(([key, tab]) => {
          const isActive = activeTab === key;
          return (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              style={{
                background: isActive ? `${token.brand.base}15` : "transparent",
                border: "none",
                borderBottom: isActive ? `2px solid ${token.brand.base}` : "2px solid transparent",
                color: isActive ? token.content.primary : token.content.muted,
                padding: "10px 16px",
                fontSize: "10px",
                fontWeight: "700",
                cursor: "pointer",
                transition: "all 0.2s",
                fontFamily: "monospace"
              }}
            >
              {tab.title.toUpperCase()}
            </button>
          );
        })}
      </div>

      {/* Tab content panel */}
      <div style={{
        background: "rgba(0, 0, 0, 0.2)",
        border: `1px solid ${token.line.default}80`,
        borderRadius: token.radius.lg,
        padding: "20px",
        minHeight: "280px"
      }}>
        {tabs[activeTab].content}
      </div>
    </div>
  );
};

export default LegalPage;
