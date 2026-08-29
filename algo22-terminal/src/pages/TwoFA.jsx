import React, { useState, useEffect, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { ShieldCheck, Lock, Copy, Check, AlertCircle, RefreshCw, ArrowLeft, Key } from "lucide-react";
import { C } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { supabase } from "../supabase";

export default function TwoFA() {
  const navigate = useNavigate();
  const location = useLocation();
  const [step, setStep] = useState(1); // 1: Setup / QR, 2: Verification
  const [factorId, setFactorId] = useState(null);
  const [challengeId, setChallengeId] = useState(null);
  const [qrCodeData, setQrCodeData] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [copied, setCopied] = useState(false);
  const [code, setCode] = useState(["", "", "", "", "", ""]);
  const [isLoading, setIsLoading] = useState(true);
  const [isVerifying, setIsVerifying] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [hasExistingFactor, setHasExistingFactor] = useState(false);
  const refs = useRef([]);

  // 1. Initialize MFA lifecycle on mount
  useEffect(() => {
    let isMounted = true;

    async function initMFA() {
      setIsLoading(true);
      setErrorMessage("");

      try {
        const { data: { user }, error: userError } = await supabase.auth.getUser();
        if (userError || !user) {
          navigate("/signin", { replace: true });
          return;
        }

        // Check existing MFA factors
        const { data: factorsData, error: factorsError } = await supabase.auth.mfa.listFactors();
        if (factorsError) {
          console.warn("MFA listFactors warning:", factorsError.message);
        }

        const totpFactors = factorsData?.totp || factorsData?.all?.filter(f => f.factor_type === "totp") || [];
        const verifiedFactor = totpFactors.find(f => f.status === "verified");

        if (verifiedFactor) {
          if (isMounted) {
            setHasExistingFactor(true);
            setFactorId(verifiedFactor.id);
            setStep(2); // Jump to challenge verification
            // Create challenge immediately for existing factor
            const { data: challenge, error: chalError } = await supabase.auth.mfa.challenge({ factorId: verifiedFactor.id });
            if (!chalError && challenge?.id) {
              setChallengeId(challenge.id);
            }
          }
        } else {
          // Unenrolled or unverified: Enroll a new TOTP factor
          // First clean up unverified factors if any exist
          for (const uf of totpFactors.filter(f => f.status !== "verified")) {
            try {
              await supabase.auth.mfa.unenroll({ factorId: uf.id });
            } catch {
              // Ignore cleanup error
            }
          }

          const { data: enrollData, error: enrollError } = await supabase.auth.mfa.enroll({
            factorType: "totp",
            issuer: "VyomQuant",
            friendlyName: user.email || "VyomQuant Authenticator",
          });

          if (enrollError) {
            throw enrollError;
          }

          if (isMounted && enrollData) {
            setFactorId(enrollData.id);
            setSecretKey(enrollData.totp?.secret || "");
            setQrCodeData(enrollData.totp?.qr_code || "");
            setStep(1);
          }
        }
      } catch (err) {
        console.error("MFA Initialization failed:", err);
        if (isMounted) {
          setErrorMessage(err.message || "Failed to initialize MFA setup. Please refresh.");
        }
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }

    initMFA();

    return () => {
      isMounted = false;
    };
  }, [navigate]);

  const onDigit = (i, v) => {
    if (!/^\d?$/.test(v)) return;
    const n = [...code];
    n[i] = v;
    setCode(n);
    if (v && i < 5) {
      refs.current[i + 1]?.focus();
    }
  };

  const handleCopySecret = () => {
    if (!secretKey) return;
    navigator.clipboard.writeText(secretKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleProceedToVerify = async () => {
    if (!factorId) {
      setErrorMessage("Enrollment factor missing. Please refresh.");
      return;
    }
    setErrorMessage("");
    setIsVerifying(true);
    try {
      const { data: challenge, error: chalError } = await supabase.auth.mfa.challenge({ factorId });
      if (chalError) throw chalError;
      setChallengeId(challenge.id);
      setStep(2);
    } catch (err) {
      console.error("Failed to create challenge:", err);
      setErrorMessage(err.message || "Failed to create MFA challenge.");
    } finally {
      setIsVerifying(false);
    }
  };

  const handleVerifyCode = async (e) => {
    if (e) e.preventDefault();
    const enteredCode = code.join("").trim();

    if (enteredCode.length !== 6) {
      setErrorMessage("Please enter all 6 digits of the authentication code.");
      return;
    }

    if (!factorId) {
      setErrorMessage("Factor not initialized. Please restart setup.");
      return;
    }

    setIsVerifying(true);
    setErrorMessage("");
    setSuccessMessage("");

    try {
      // If challengeId is missing, create one first
      let activeChallengeId = challengeId;
      if (!activeChallengeId) {
        const { data: challenge, error: chalErr } = await supabase.auth.mfa.challenge({ factorId });
        if (chalErr) throw chalErr;
        activeChallengeId = challenge.id;
        setChallengeId(challenge.id);
      }

      // Verify code with Supabase Auth MFA
      const { data: verifyData, error: verifyErr } = await supabase.auth.mfa.verify({
        factorId,
        challengeId: activeChallengeId,
        code: enteredCode,
      });

      if (verifyErr) {
        throw verifyErr;
      }

      setSuccessMessage("Two-factor authentication verified successfully!");

      // Transition session to next step / dashboard
      setTimeout(() => {
        const redirectPath = location.state?.from || "/app/dashboard";
        navigate(redirectPath, { replace: true });
      }, 1500);

    } catch (err) {
      console.error("MFA Verification error:", err);
      setErrorMessage(err.message || "Invalid authentication code. Please check your app and try again.");
      setCode(["", "", "", "", "", ""]);
      refs.current[0]?.focus();
      // Invalidate challenge so a fresh one is created on retry
      setChallengeId(null);
    } finally {
      setIsVerifying(false);
    }
  };

  const handleResetFactor = async () => {
    if (!window.confirm("Are you sure you want to reset your MFA configuration?")) return;
    setIsLoading(true);
    setErrorMessage("");
    try {
      if (factorId) {
        await supabase.auth.mfa.unenroll({ factorId });
      }
      // Re-enroll fresh
      const { data: enrollData, error: enrollError } = await supabase.auth.mfa.enroll({
        factorType: "totp",
        issuer: "VyomQuant",
        friendlyName: "VyomQuant Authenticator",
      });
      if (enrollError) throw enrollError;

      setFactorId(enrollData.id);
      setSecretKey(enrollData.totp?.secret || "");
      setQrCodeData(enrollData.totp?.qr_code || "");
      setHasExistingFactor(false);
      setStep(1);
      setCode(["", "", "", "", "", ""]);
    } catch (err) {
      setErrorMessage(err.message || "Failed to reset MFA.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 36, width: "100%", maxWidth: 440 }}>
        
        {/* Header */}
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.2)", borderRadius: "50%", width: 60, height: 60, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 12 }}>
            <ShieldCheck size={26} style={{ color: C.cyan }} />
          </div>
          <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 20, letterSpacing: "-0.02em" }}>
            {hasExistingFactor ? "Two-Factor Verification" : "Setup Two-Factor Authentication"}
          </h1>
          <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginTop: 6, lineHeight: 1.5 }}>
            {step === 1
              ? "Scan the QR code below with Google Authenticator, Authy, or 1Password"
              : "Enter the 6-digit verification code generated by your authenticator"}
          </p>
        </div>

        {/* Feedback Alerts */}
        {errorMessage && (
          <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 10, padding: "10px 14px", display: "flex", alignItems: "center", gap: 10, marginBottom: 18, color: "#fca5a5", fontSize: 11, fontFamily: "monospace" }}>
            <AlertCircle size={15} style={{ color: "#ef4444", flexShrink: 0 }} />
            <span>{errorMessage}</span>
          </div>
        )}

        {successMessage && (
          <div style={{ background: "rgba(16,185,129,0.1)", border: "1px solid rgba(16,185,129,0.3)", borderRadius: 10, padding: "10px 14px", display: "flex", alignItems: "center", gap: 10, marginBottom: 18, color: "#6ee7b7", fontSize: 11, fontFamily: "monospace" }}>
            <Check size={15} style={{ color: "#10b981", flexShrink: 0 }} />
            <span>{successMessage}</span>
          </div>
        )}

        {/* Loading Spinner */}
        {isLoading ? (
          <div style={{ padding: "40px 0", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, color: C.t3, fontFamily: "monospace", fontSize: 11 }}>
            <RefreshCw size={24} className="animate-spin" style={{ color: C.cyan }} />
            <span>Connecting to Supabase MFA...</span>
          </div>
        ) : step === 1 ? (
          /* STEP 1: Enrollment QR Code and Secret Key */
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 14, padding: 20, display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
              {qrCodeData ? (
                <div style={{ background: "#ffffff", padding: 12, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 4px 14px rgba(0,0,0,0.4)" }}>
                  {qrCodeData.startsWith("data:image") ? (
                    <img src={qrCodeData} alt="TOTP QR Code" style={{ width: 180, height: 180, display: "block" }} />
                  ) : (
                    <div dangerouslySetInnerHTML={{ __html: qrCodeData }} style={{ width: 180, height: 180 }} />
                  )}
                </div>
              ) : (
                <div style={{ height: 180, width: 180, background: C.bg4, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontSize: 10, textAlign: "center", padding: 10 }}>
                  QR code unavailable. Please use the secret key below.
                </div>
              )}
              <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", textAlign: "center" }}>
                Scan with Google Authenticator, Microsoft Authenticator, or Authy
              </span>
            </div>

            {/* Secret key manual entry */}
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px" }}>
              <div style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
                <Key size={12} style={{ color: C.cyan }} />
                <span>Manual Setup Key:</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <code style={{ color: C.cyan, background: C.bg1, flex: 1, padding: "8px 12px", borderRadius: 6, fontSize: 12, fontFamily: "monospace", letterSpacing: "0.1em", userSelect: "all", overflowX: "auto" }}>
                  {secretKey || "Generating secret..."}
                </code>
                <button
                  type="button"
                  onClick={handleCopySecret}
                  style={{ background: C.bg4, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: copied ? C.green : C.t2, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}
                  title="Copy secret key"
                >
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                </button>
              </div>
            </div>

            <Button
              variant="primary"
              className="w-full justify-center mt-1"
              onClick={handleProceedToVerify}
              disabled={isVerifying || !factorId}
            >
              {isVerifying ? "Preparing Challenge..." : "I've Scanned It — Enter 6-Digit Code →"}
            </Button>
          </div>
        ) : (
          /* STEP 2: Verification with 6-digit TOTP code */
          <form onSubmit={handleVerifyCode} style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              {code.map((d, i) => (
                <input
                  key={i}
                  ref={el => (refs.current[i] = el)}
                  value={d}
                  maxLength={1}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  onChange={e => onDigit(i, e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Backspace" && !code[i] && i > 0) {
                      refs.current[i - 1]?.focus();
                    }
                  }}
                  style={{
                    background: C.bg3,
                    border: `1px solid ${d ? C.cyan : C.border}`,
                    color: C.t1,
                    width: 48,
                    height: 56,
                    textAlign: "center",
                    fontSize: 22,
                    fontWeight: 900,
                    borderRadius: 8,
                    outline: "none",
                    fontFamily: "monospace",
                    transition: "all 0.15s",
                    boxShadow: d ? `0 0 10px ${C.cyan}20` : "none",
                  }}
                />
              ))}
            </div>

            <Button
              variant="primary"
              type="submit"
              className="w-full justify-center"
              disabled={isVerifying || code.join("").length !== 6}
            >
              {isVerifying ? "Verifying with Supabase..." : "Verify & Complete →"}
            </Button>

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
              {!hasExistingFactor ? (
                <button
                  type="button"
                  style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", background: "transparent", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
                  onClick={() => setStep(1)}
                  className="hover:text-cyan-400"
                >
                  <ArrowLeft size={11} /> Back to QR code
                </button>
              ) : (
                <div />
              )}
              
              <button
                type="button"
                style={{ color: C.t4, fontSize: 10, fontFamily: "monospace", background: "transparent", border: "none", cursor: "pointer" }}
                onClick={handleResetFactor}
                className="hover:text-red-400"
              >
                Reset Authenticator
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
