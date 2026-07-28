import React, { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { ShieldCheck, Lock } from "lucide-react";
import { C, Btn, Inp } from "../components/ui-legacy/primitives";

export default function TwoFA() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [code, setCode] = useState(["", "", "", "", "", ""]);
  const refs = useRef([]);
  const onDigit = (i, v) => {
    if (!/^\d?$/.test(v)) return;
    const n = [...code]; n[i] = v; setCode(n);
    if (v && i < 5) refs.current[i + 1]?.focus();
  };
  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 36, width: 420 }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.2)", borderRadius: "50%", width: 60, height: 60, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 12 }}>
            <ShieldCheck size={26} style={{ color: C.cyan }} />
          </div>
          <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 20 }}>Two-Factor Authentication</h1>
          <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginTop: 4 }}>{step === 1 ? "Set up TOTP on your authenticator app" : "Enter the 6-digit verification code"}</p>
        </div>
        {step === 1 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
              {/* QR placeholder */}
              <div style={{ background: "#fff", padding: 12, borderRadius: 8, display: "grid", gridTemplateColumns: "repeat(10,8px)", gridTemplateRows: "repeat(10,8px)", gap: 1 }}>
                {Array.from({ length: 100 }, (_, i) => (
                  <div key={i} style={{ background: (i % 7 === 0 || i % 11 === 3 || i < 10 || i > 90 || i % 10 === 0 || i % 10 === 9) ? "#000" : "#fff", borderRadius: 1 }} />
                ))}
              </div>
              <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>Scan with Google Authenticator or Authy</span>
            </div>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 }}>
              <div style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginBottom: 6 }}>Or enter this setup key:</div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <code style={{ color: C.cyan, background: C.bg1, flex: 1, padding: "6px 10px", borderRadius: 6, fontSize: 12, fontFamily: "monospace" }}>JBSWY3DPEHPK3PXP</code>
                <Copy size={13} style={{ color: C.t3, cursor: "pointer" }} className="hover:text-cyan-400" />
              </div>
            </div>
            <Btn v="primary" cls="w-full justify-center" onClick={() => setStep(2)}>I've Added It â€” Verify Code â†’</Btn>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              {code.map((d, i) => (
                <input key={i} ref={el => refs.current[i] = el} value={d} maxLength={1}
                  onChange={e => onDigit(i, e.target.value)}
                  style={{ background: C.bg3, border: `1px solid ${d ? C.cyan : C.border}`, color: C.t1, width: 46, height: 54, textAlign: "center", fontSize: 22, fontWeight: 900, borderRadius: 8, outline: "none", fontFamily: "monospace", transition: "all 0.15s" }} />
              ))}
            </div>
            <Btn v="primary" cls="w-full justify-center" onClick={() => navigate("/wizard")}>Verify & Continue â†’</Btn>
            <button style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", textAlign: "center", cursor: "pointer" }} onClick={() => setStep(1)} className="hover:text-cyan-400">
              â† Back to QR code
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
//  PAGE: SETUP WIZARD
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

