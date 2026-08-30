/**
 * DEPRECATED / UNMOUNTED — LEGACY LANDING PAGE
 * 
 * The active, production landing page component is located at:
 * src/components/landing/LandingPage.jsx (routed at '/' in App.jsx)
 * 
 * This file is retained for historical reference only.
 */

import React, { useState, useEffect } from 'react';
import {
  Zap, ChevronRight, Shield, Cpu, Activity, ArrowUpRight, Check, BarChart2,
  Lock, Terminal, Globe, Award, HelpCircle, User, Star, Layers, Play, Loader2
} from 'lucide-react';
import { C } from '../components/ui-legacy/primitives';
import { Button } from '../components/ui/Button';
import { api } from '../api';

function LandingFeatureCard({ f, i }) {
  const [isHovered, setIsHovered] = useState(false);
  const colors = [C.accent, C.profit, C.gold];
  const cardColor = colors[i % colors.length];

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: C.bg2,
        border: `1px solid ${isHovered ? cardColor : C.border}`,
        borderRadius: 16,
        padding: 26,
        backdropFilter: "blur(8px)",
        boxShadow: isHovered
          ? `inset 0 1px 0 rgba(255,255,255,0.03), 0 24px 48px rgba(0,0,0,0.35), ${C.glow[cardColor === C.profit ? "profit" : cardColor === C.gold ? "gold" : "accent"]}`
          : `inset 0 1px 0 rgba(255,255,255,0.03), 0 24px 48px rgba(0,0,0,0.35)`,
        transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        transform: isHovered ? "translateY(-4px)" : "translateY(0)",
        cursor: "default"
      }}
    >
      <div
        style={{
          background: isHovered ? `${cardColor}20` : `${C.accent}14`,
          border: `1px solid ${isHovered ? cardColor : C.accent}40`,
          borderRadius: 10,
          padding: 9,
          display: "inline-flex",
          marginBottom: 16,
          transition: "all 0.3s ease",
          boxShadow: isHovered ? `0 0 15px ${cardColor}30` : "none"
        }}
      >
        <f.I size={19} style={{ color: isHovered ? cardColor : C.accent, transition: "color 0.3s ease" }} />
      </div>
      <div style={{ color: C.t1, fontSize: 18, fontWeight: 900, marginBottom: 8 }}>{f.t}</div>
      <div style={{ color: C.t2, fontSize: 12, lineHeight: 1.75 }}>{f.d}</div>
    </div>
  );
}

function LandingPricingCard({ plan }) {
  const [isHovered, setIsHovered] = useState(false);
  const isElite = plan.name === "Elite";
  const isPro = plan.name === "Pro";

  const borderColor = isElite ? C.gold : (isPro ? C.accent : C.border);
  const glowShadow = isElite ? C.glow.gold : (isPro ? C.glow.accent : "none");
  const badgeColor = isElite ? C.gold : (isPro ? C.accent : C.t3);

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: C.bg2,
        border: `1px solid ${isHovered ? borderColor : C.border}`,
        borderRadius: 20,
        padding: 32,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        position: "relative",
        boxShadow: isHovered
          ? `0 30px 60px rgba(0,0,0,0.4), ${glowShadow}`
          : "0 20px 40px rgba(0,0,0,0.2)",
        transform: isHovered ? "translateY(-6px)" : "translateY(0)",
        transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)"
      }}
    >
      {(isPro || isElite) && (
        <span
          style={{
            position: "absolute",
            top: -12,
            right: 24,
            background: badgeColor,
            color: "#000",
            fontSize: 9,
            fontWeight: 900,
            padding: "4px 12px",
            borderRadius: 12,
            fontFamily: "monospace",
            letterSpacing: "0.1em",
            textTransform: "uppercase"
          }}
        >
          {isElite ? "Machine Learning" : "Popular"}
        </span>
      )}
      <div>
        <div style={{ color: C.t1, fontSize: 22, fontWeight: 900, marginBottom: 4 }}>{plan.name}</div>
        <div style={{ color: C.t3, fontSize: 11, fontFamily: "monospace", marginBottom: 20 }}>{plan.desc}</div>
        <div style={{ display: "flex", alignItems: "baseline", marginBottom: 24 }}>
          <span style={{ color: C.t1, fontSize: 36, fontWeight: 900 }}>{plan.price}</span>
          <span style={{ color: C.t3, fontSize: 12, fontFamily: "monospace", marginLeft: 4 }}>/ month</span>
        </div>
        <ul style={{ display: "flex", flexDirection: "column", gap: 12, padding: 0, margin: "0 0 32px 0", listStyle: "none" }}>
          {plan.features.map(f => (
            <li key={f} style={{ display: "flex", alignItems: "center", gap: 8, color: C.t2, fontSize: 12 }}>
              <Check size={12} style={{ color: C.profit }} />
              <span style={{ fontFamily: "monospace" }}>{f}</span>
            </li>
          ))}
        </ul>
      </div>
      <button
        onClick={() => navigate("/signup")}
        style={{
          background: isPro ? C.accent : (isElite ? C.gold : "transparent"),
          border: `1px solid ${isPro ? C.accent : (isElite ? C.gold : C.border)}`,
          color: (isPro || isElite) ? "#000" : C.t1,
          width: "100%",
          padding: "12px 0",
          borderRadius: 10,
          fontWeight: 900,
          fontSize: 12,
          fontFamily: "monospace",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          cursor: "pointer",
          transition: "all 0.2s"
        }}
      >
        {plan.cta || "Get Started"}
      </button>
    </div>
  );
}

function LandingFAQItem({ q, a }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div style={{ borderBottom: `1px solid ${C.border}`, padding: "16px 0" }}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          width: "100%",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "transparent",
          border: "none",
          color: C.t1,
          fontSize: 15,
          fontWeight: 700,
          cursor: "pointer",
          padding: "8px 0",
          textAlign: "left",
          outline: "none"
        }}
      >
        <span>{q}</span>
        {isOpen ? <ChevronUp size={16} style={{ color: C.accent }} /> : <ChevronDown size={16} style={{ color: C.t3 }} />}
      </button>
      {isOpen && (
        <div style={{ color: C.t2, fontSize: 13, lineHeight: 1.6, padding: "8px 0 12px", fontFamily: "monospace" }}>
          {a}
        </div>
      )}
    </div>
  );
}

function LandingTestimonialCard({ t }) {
  return (
    <div
      style={{
        background: `${C.bg2}aa`,
        border: `1px solid ${C.border}`,
        borderRadius: 16,
        padding: 24,
        boxShadow: "0 10px 30px rgba(0,0,0,0.1)",
        backdropFilter: "blur(4px)"
      }}
    >
      <div style={{ display: "flex", gap: 2, marginBottom: 14 }}>
        {Array.from({ length: 5 }).map((_, i) => (
          <Star key={i} size={11} fill={C.gold} color={C.gold} />
        ))}
      </div>
      <p style={{ color: C.t2, fontSize: 13, lineHeight: 1.6, fontStyle: "italic", marginBottom: 16 }}>
        "{t.quote}"
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div
          style={{
            background: `${C.accent}22`,
            border: `1px solid ${C.accent}40`,
            borderRadius: "50%",
            width: 30,
            height: 30,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: C.accent,
            fontSize: 10,
            fontWeight: 900,
            fontFamily: "monospace"
          }}
        >
          {t.author.charAt(0)}
        </div>
        <div>
          <div style={{ color: C.t1, fontSize: 12, fontWeight: 900 }}>{t.author}</div>
          <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>{t.role}</div>
        </div>
      </div>
    </div>
  );
}

export default function Landing() {
  const navigate = useNavigate();
  const [showDemoModal, setShowDemoModal] = useState(false);
  const [pricingPlans, setPricingPlans] = useState([]);
  const [isLoadingPlans, setIsLoadingPlans] = useState(true);

  useEffect(() => {
    const loadPlans = async () => {
      try {
        const response = await api.billing.getPlans();
        const frontendPlans = response.plans.map(plan => ({
          name: plan.name,
          desc: plan.description,
          price: plan.recommended ? `$${plan.usd}` : `$${plan.usd}`,
          features: plan.features.map(f => {
            // Convert feature keys to human-readable text
            const featureMap = {
              'unlimited_builder': 'Unlimited Strategy Builder',
              'unlimited_backtesting': 'Unlimited Backtesting',
              'live_trading': 'Live Trading',
              'ml_training': 'ML Model Training',
              'marketplace_access': 'Marketplace Access',
              'marketplace_publish': 'Marketplace Publishing',
              'api_access': 'API Access',
              'priority_support': 'Priority Support'
            };
            return featureMap[f] || f;
          })
        }));
        setPricingPlans(frontendPlans);
      } catch (err) {
        console.error('Failed to load plans:', err);
        // Fallback to minimal plans if API fails
        setPricingPlans([
          {
            name: "Free",
            desc: "For exploring the builder and basic backtesting",
            price: "$0",
            features: ["Unlimited Strategy Builder", "Unlimited Backtesting"]
          }
        ]);
      } finally {
        setIsLoadingPlans(false);
      }
    };
    loadPlans();
  }, []);

  const features = [
    {
      I: GitBranch,
      t: "Visual Strategy Builder",
      d: "Map indicators and entry/exit conditions visually. Custom logic executes seamlessly without coding.",
    },
    {
      I: Activity,
      t: "Backtesting Engine",
      d: "Simulate strategy performance across years of high-fidelity historical market data in seconds.",
    },
    {
      I: BarChart2,
      t: "Monte Carlo Simulation",
      d: "Stress-test strategy risk parameters under hundreds of randomized market scenarios to verify durability.",
    },
    {
      I: TrendingUp,
      t: "Walk Forward Analysis",
      d: "Prevent strategy overfitting with walking out-of-sample testing windows that confirm real-world utility.",
    },
    {
      I: Bot,
      t: "Paper Trading Mode",
      d: "Forward-test your strategies in live markets with simulated balances using exact exchange latency.",
    },
    {
      I: Shield,
      t: "Institutional Risk Controls",
      d: "Enforce strict margin safety limits and automatic global liquidation thresholds at the account level.",
    },
  ];

  const testimonials = [
    {
      quote: "Aerora made it possible for me to automate my trading strategies. The visual builder is incredibly intuitive.",
      author: "Sarah K.",
      role: "Quantitative Trader"
    },
    {
      quote: "The Monte Carlo simulation features are institutional grade. Essential for verifying strategy durability before going live.",
      author: "David L.",
      role: "Asset Manager"
    },
    {
      quote: "Sub-millisecond execution and reliable paper trading. My forward testing matches live results perfectly.",
      author: "Michael R.",
      role: "Crypto Scalper"
    }
  ];

  const faqs = [
    {
      q: "Do I need coding experience to use VyomQuant?",
      a: "No coding required. Our visual builder allows you to map logic and indicators using drag-and-drop conditions."
    },
    {
      q: "What is the difference between Paper and Live trading?",
      a: "Paper trading forward-tests strategies with real-time live data and simulated cash. Live trading connects to your exchange API keys to trade real assets (Live mode requires Pro or Elite plan)."
    },
    {
      q: "Can I cancel or change my subscription tier at any time?",
      a: "Yes, you can upgrade, downgrade, or cancel your subscription at any time directly from your billing dashboard."
    },
    {
      q: "Is my API key credentials information secure?",
      a: "Yes. Exchange API keys are encrypted at-rest using AES-256 GCM in our Security Vault. Secrets never leave our secure backend execution path."
    }
  ];

  return (
    <div style={{ background: C.bg, minHeight: "100vh", overflowY: "auto" }}>
      {/* Hero Section */}
      <section
        className="py-28 px-6 md:px-10"
        style={{
          textAlign: "center",
          position: "relative",
          overflow: "hidden",
          background: `radial-gradient(ellipse 70% 55% at 50% 8%, ${C.cyan}1f 0%, transparent 72%)`,
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage: `linear-gradient(to bottom, transparent, ${C.bg} 94%), repeating-linear-gradient(0deg, ${C.border}14 0, ${C.border}14 1px, transparent 1px, transparent 56px), repeating-linear-gradient(90deg, ${C.border}12 0, ${C.border}12 1px, transparent 1px, transparent 56px)`,
          }}
        />

        <div className="relative z-10 max-w-5xl mx-auto py-10 md:py-16">
          <h1
            style={{
              color: C.t1,
              fontWeight: 900,
              letterSpacing: "-0.04em",
              lineHeight: 1.05,
              fontSize: "clamp(2.4rem, 6vw, 4.8rem)",
              marginBottom: 18,
            }}
          >
            Build, Test, and Deploy Crypto Trading Strategies Without Writing Code
          </h1>

          <p
            style={{
              color: C.t2,
              fontSize: 15,
              margin: "0 auto 40px",
              maxWidth: 720,
              lineHeight: 1.6,
              letterSpacing: "0.01em",
            }}
          >
            VyomQuant is the ultimate no-code platform for algorithmic traders. Create complex logic blocks, backtest with historical tick data, validate with Monte Carlo and Walk Forward analysis, and deploy live or paper bots in one click.
          </p>

          <div style={{ display: "flex", justifyContent: "center", gap: 16, flexWrap: "wrap" }}>
            <button
              onClick={() => navigate("/signup")}
              style={{
                background: C.accent,
                color: "#000",
                borderRadius: 12,
                padding: "14px 28px",
                fontSize: 13,
                fontWeight: 900,
                letterSpacing: "0.05em",
                textTransform: "uppercase",
                border: `1px solid ${C.accent}`,
                cursor: "pointer",
                boxShadow: `${C.glow.accent}, 0 4px 20px rgba(0,0,0,0.3)`,
                transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
                animation: "ctaPulse 3s ease-in-out infinite"
              }}
              onMouseEnter={(e) => {
                e.target.style.transform = "translateY(-2px) scale(1.02)";
                e.target.style.boxShadow = `${C.glow.accent}, 0 8px 30px rgba(0,0,0,0.4)`;
              }}
              onMouseLeave={(e) => {
                e.target.style.transform = "translateY(0) scale(1)";
                e.target.style.boxShadow = `${C.glow.accent}, 0 4px 20px rgba(0,0,0,0.3)`;
              }}
            >
              <style>{`
                @keyframes ctaPulse {
                  0%, 100% { box-shadow: 0 0 20px ${C.accent}60, 0 0 40px ${C.accent}30, 0 4px 20px rgba(0,0,0,0.3); }
                  50% { box-shadow: 0 0 30px ${C.accent}80, 0 0 60px ${C.accent}50, 0 4px 20px rgba(0,0,0,0.3); }
                }
              `}</style>
              Start Free
            </button>

            <button
              onClick={() => setShowDemoModal(true)}
              style={{
                background: "transparent",
                color: C.t1,
                borderRadius: 12,
                padding: "14px 28px",
                fontSize: 13,
                fontWeight: 900,
                letterSpacing: "0.05em",
                textTransform: "uppercase",
                border: `1px solid ${C.border}`,
                cursor: "pointer",
                transition: "all 0.2s"
              }}
              onMouseEnter={(e) => {
                e.target.style.background = `${C.border}44`;
                e.target.style.transform = "translateY(-2px)";
              }}
              onMouseLeave={(e) => {
                e.target.style.background = "transparent";
                e.target.style.transform = "translateY(0)";
              }}
            >
              Book Demo
            </button>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-20 px-6 md:px-10" style={{ borderTop: `1px solid ${C.border}` }}>
        <div className="max-w-6xl mx-auto">
          <div style={{ textAlign: "center", marginBottom: 50 }}>
            <h2 style={{ color: C.t1, fontSize: 32, fontWeight: 900, marginBottom: 12 }}>Platform Features</h2>
            <p style={{ color: C.t2, fontSize: 14, fontFamily: "monospace" }}>Everything you need to run institutional-grade strategies</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {features.map((f, i) => (
              <LandingFeatureCard key={f.t} f={f} i={i} />
            ))}
          </div>
        </div>
      </section>

      {/* Pricing Section */}
      <section className="py-20 px-6 md:px-10" style={{ borderTop: `1px solid ${C.border}`, background: C.bg0 }}>
        <div className="max-w-6xl mx-auto">
          <div style={{ textAlign: "center", marginBottom: 50 }}>
            <h2 style={{ color: C.t1, fontSize: 32, fontWeight: 900, marginBottom: 12 }}>Simple Pricing Tiers</h2>
            <p style={{ color: C.t2, fontSize: 14, fontFamily: "monospace" }}>Pay as you scale your quantitative pipeline</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {isLoadingPlans ? (
              <div className="col-span-3 flex justify-center items-center py-20">
                <Loader2 className="animate-spin" style={{ color: C.accent }} size={32} />
              </div>
            ) : (
              pricingPlans.map((plan) => (
                <LandingPricingCard key={plan.name} plan={plan} />
              ))
            )}
          </div>
        </div>
      </section>

      {/* Testimonials Section */}
      <section className="py-20 px-6 md:px-10" style={{ borderTop: `1px solid ${C.border}` }}>
        <div className="max-w-6xl mx-auto">
          <div style={{ textAlign: "center", marginBottom: 50 }}>
            <h2 style={{ color: C.t1, fontSize: 32, fontWeight: 900, marginBottom: 12 }}>Algorithmic Trader Reviews</h2>
            <p style={{ color: C.t2, fontSize: 14, fontFamily: "monospace" }}>See how quants deploy strategy systems with VyomQuant</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {testimonials.map((t, i) => (
              <LandingTestimonialCard key={i} t={t} />
            ))}
          </div>
        </div>
      </section>

      {/* FAQ Section */}
      <section className="py-20 px-6 md:px-10" style={{ borderTop: `1px solid ${C.border}`, background: C.bg0 }}>
        <div className="max-w-4xl mx-auto">
          <div style={{ textAlign: "center", marginBottom: 50 }}>
            <h2 style={{ color: C.t1, fontSize: 32, fontWeight: 900, marginBottom: 12 }}>Frequently Asked Questions</h2>
            <p style={{ color: C.t2, fontSize: 14, fontFamily: "monospace" }}>Got questions? We've got answers.</p>
          </div>
          <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: "24px 32px" }}>
            {faqs.map((faq, i) => (
              <LandingFAQItem key={i} q={faq.q} a={faq.a} />
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer style={{
        borderTop: `1px solid ${C.border}`,
        padding: "40px 24px",
        background: C.bg0,
        textAlign: "center"
      }}>
        <div style={{
          maxWidth: "600px",
          margin: "0 auto",
          display: "flex",
          justifyContent: "center",
          gap: "24px",
          marginBottom: "16px"
        }}>
          <button onClick={() => navigate("/legal")} style={{ background: "transparent", border: "none", color: C.t3, fontSize: "11px", fontFamily: "monospace", cursor: "pointer", transition: "color 0.2s" }} onMouseEnter={e => e.target.style.color = C.accent} onMouseLeave={e => e.target.style.color = C.t3}>Terms of Service</button>
          <button onClick={() => navigate("/legal")} style={{ background: "transparent", border: "none", color: C.t3, fontSize: "11px", fontFamily: "monospace", cursor: "pointer", transition: "color 0.2s" }} onMouseEnter={e => e.target.style.color = C.accent} onMouseLeave={e => e.target.style.color = C.t3}>Privacy Policy</button>
          <button onClick={() => navigate("/legal")} style={{ background: "transparent", border: "none", color: C.t3, fontSize: "11px", fontFamily: "monospace", cursor: "pointer", transition: "color 0.2s" }} onMouseEnter={e => e.target.style.color = C.accent} onMouseLeave={e => e.target.style.color = C.t3}>Risk Disclosure</button>
          <button onClick={() => navigate("/legal")} style={{ background: "transparent", border: "none", color: C.t3, fontSize: "11px", fontFamily: "monospace", cursor: "pointer", transition: "color 0.2s" }} onMouseEnter={e => e.target.style.color = C.accent} onMouseLeave={e => e.target.style.color = C.t3}>Refund Policy</button>
        </div>
        <p style={{ color: C.t4, fontSize: "10px", fontFamily: "monospace" }}>&copy; {new Date().getFullYear()} VyomQuant. Simulated paper trading beta platform.</p>
      </footer>

      {/* Book Demo Modal */}
      {showDemoModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            backdropFilter: "blur(4px)",
            zIndex: 1000,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16
          }}
        >
          <div
            style={{
              background: C.bg2,
              border: `1px solid ${C.border}`,
              borderRadius: 18,
              padding: 32,
              width: 480,
              maxWidth: "100%",
              boxShadow: "0 40px 80px rgba(0,0,0,0.8)",
              position: "relative"
            }}
          >
            <button
              onClick={() => setShowDemoModal(false)}
              style={{
                position: "absolute",
                top: 16,
                right: 16,
                background: "transparent",
                border: "none",
                color: C.t3,
                fontSize: 20,
                cursor: "pointer",
                lineHeight: 1
              }}
              className="hover:text-white"
            >
              ×
            </button>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 20, marginBottom: 6 }}>Book a Private Demo</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 24 }}>
              See how VyomQuant can automate your visual trading strategies at scale.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                alert("Demo request submitted! Our team will contact you shortly.");
                setShowDemoModal(false);
              }}
              style={{ display: "flex", flexDirection: "column", gap: 14 }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>Name</label>
                <input required type="text" placeholder="John Doe" style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 12px", fontSize: 12, fontFamily: "monospace", outline: "none" }} />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>Email</label>
                <input required type="email" placeholder="john@company.com" style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 12px", fontSize: 12, fontFamily: "monospace", outline: "none" }} />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>Strategy Scale</label>
                <select style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 12px", fontSize: 12, fontFamily: "monospace", outline: "none" }}>
                  <option>Personal (1-5 bots)</option>
                  <option>Professional (5-20 bots)</option>
                  <option>Institutional (20+ bots)</option>
                </select>
              </div>
              <button type="submit" style={{ background: C.accent, color: "#000", border: `1px solid ${C.accent}`, borderRadius: 10, padding: "12px 0", fontWeight: 900, fontSize: 12, fontFamily: "monospace", textTransform: "uppercase", cursor: "pointer", marginTop: 8 }}>
                Request Access
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: AUTH
// ═══════════════════════════════════════════════════════════════════

