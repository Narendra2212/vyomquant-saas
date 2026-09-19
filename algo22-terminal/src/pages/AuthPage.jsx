import React, { useState, useEffect, useMemo, useRef } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Eye, EyeOff, Lock, Mail, Zap, Globe, AlertCircle, CheckCircle, Check, X, ArrowLeft, RefreshCw, ShieldCheck } from "lucide-react";
import { Inp } from "../components/ui-legacy/primitives";
import { token } from "../design/tokens";
import { Button } from "../components/ui/Button";
import { supabase } from "../supabase";

const RESEND_COOLDOWN_SECONDS = 60;

export const AUTH_STATES = {
  PASSWORD_REQUIRED: "PASSWORD_REQUIRED",
  PASSWORD_AUTHENTICATING: "PASSWORD_AUTHENTICATING",
  OTP_REQUIRED: "OTP_REQUIRED",
  OTP_VERIFYING: "OTP_VERIFYING",
  AUTHENTICATED: "AUTHENTICATED",
};

/**
 * Safely masks an email address to avoid unnecessary exposure on verification screen.
 * Example: trader.alpha@vyomquant.io -> t••••a@vyomquant.io
 */
export function maskEmail(email) {
  if (!email || typeof email !== "string" || !email.includes("@")) return email || "";
  const parts = email.trim().split("@");
  const localPart = parts[0];
  const domain = parts.slice(1).join("@");
  
  if (localPart.length <= 2) {
    return `${localPart[0] || ""}••••@${domain}`;
  }
  const maskedLocal = `${localPart[0]}••••${localPart[localPart.length - 1]}`;
  return `${maskedLocal}@${domain}`;
}

export const evaluatePasswordStrength = (pwd) => {
  if (!pwd) return { score: 0, label: "", color: token.content.muted, checks: { length: false, upper: false, lower: false, number: false, special: false } };
  
  const checks = {
    length: pwd.length >= 8,
    upper: /[A-Z]/.test(pwd),
    lower: /[a-z]/.test(pwd),
    number: /[0-9]/.test(pwd),
    special: /[^A-Za-z0-9]/.test(pwd),
  };

  const count = Object.values(checks).filter(Boolean).length;

  let label = "Weak";
  let color = "#EF5350"; // Loss red

  if (count <= 2) {
    label = "Weak";
    color = "#EF5350";
  } else if (count === 3 || count === 4) {
    label = "Fair";
    color = "#F59E0B"; // Gold
  } else if (count === 5) {
    label = "Strong";
    color = "#10B981"; // Profit green
  }

  return { score: count, label, color, checks };
};

function PasswordStrengthMeter({ strength }) {
  if (!strength || strength.score === 0) return null;
  const pct = (strength.score / 5) * 100;
  return (
    <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: token.content.muted, fontSize: 9, fontFamily: "monospace", textTransform: "uppercase" }}>
          Password Strength
        </span>
        <span style={{ color: strength.color, fontSize: 10, fontFamily: "monospace", fontWeight: 700 }}>
          {strength.label}
        </span>
      </div>
      <div style={{ height: 4, background: token.surface.inset, borderRadius: 2, overflow: "hidden", border: `1px solid ${token.line.default}` }}>
        <div style={{ height: "100%", width: `${pct}%`, background: strength.color, transition: "all 0.2s ease" }} />
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 4, fontSize: 9, fontFamily: "monospace" }}>
        <span style={{ color: strength.checks.length ? "#10B981" : token.content.muted, display: "inline-flex", alignItems: "center", gap: 3 }}>
          {strength.checks.length ? <Check size={10} /> : <X size={10} />} 8+ chars
        </span>
        <span style={{ color: strength.checks.upper && strength.checks.lower ? "#10B981" : token.content.muted, display: "inline-flex", alignItems: "center", gap: 3 }}>
          {strength.checks.upper && strength.checks.lower ? <Check size={10} /> : <X size={10} />} Upper & lower
        </span>
        <span style={{ color: strength.checks.number ? "#10B981" : token.content.muted, display: "inline-flex", alignItems: "center", gap: 3 }}>
          {strength.checks.number ? <Check size={10} /> : <X size={10} />} Number
        </span>
        <span style={{ color: strength.checks.special ? "#10B981" : token.content.muted, display: "inline-flex", alignItems: "center", gap: 3 }}>
          {strength.checks.special ? <Check size={10} /> : <X size={10} />} Symbol
        </span>
      </div>
    </div>
  );
}

export default function AuthPage({ mode = "signin" }) {
  const navigate = useNavigate();

  // Mode: "signin" | "signup"
  const [authMode, setAuthMode] = useState(mode === "signup" ? "signup" : "signin");

  // Explicit Authentication State Machine
  const [authState, setAuthState] = useState(AUTH_STATES.PASSWORD_REQUIRED);
  const [passwordVerified, setPasswordVerified] = useState(false);

  // Form states
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);

  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);

  const [confirmPassword, setConfirmPassword] = useState("");
  const [confirmTouched, setConfirmTouched] = useState(false);
  const [showConfirmPw, setShowConfirmPw] = useState(false);

  const [agreedToPolicies, setAgreedToPolicies] = useState(false);

  // OTP form state: 6 separate digits
  const [otpDigits, setOtpDigits] = useState(["", "", "", "", "", ""]);
  const otpInputRefs = useRef([]);

  // UI / Status state
  const [loadingAction, setLoadingAction] = useState(""); // "" | "password_auth" | "verify_otp" | "google" | "resend_otp"
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Resend cooldown timer
  const [cooldown, setCooldown] = useState(0);

  const isUp = authMode === "signup";
  const isLoading = !!loadingAction;

  const clearStatus = () => {
    setError("");
    setSuccess("");
  };

  useEffect(() => {
    setAuthMode(mode === "signup" ? "signup" : "signin");
    setAuthState(AUTH_STATES.PASSWORD_REQUIRED);
    setPasswordVerified(false);
    clearStatus();
    setEmailTouched(false);
    setConfirmTouched(false);
  }, [mode]);

  // Handle countdown interval
  useEffect(() => {
    if (cooldown <= 0) return;
    const interval = setInterval(() => {
      setCooldown((prev) => {
        if (prev <= 1) {
          clearInterval(interval);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [cooldown]);

  // Focus first OTP digit upon entering OTP stage
  useEffect(() => {
    if (authState === AUTH_STATES.OTP_REQUIRED) {
      const timer = setTimeout(() => {
        otpInputRefs.current[0]?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [authState]);

  // Inline email format validation
  const emailInlineError = useMemo(() => {
    if (!emailTouched || !email) return "";
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email.trim())) {
      return "Please enter a valid email address (e.g. user@domain.com)";
    }
    return "";
  }, [email, emailTouched]);

  // Password strength score
  const passwordStrength = useMemo(() => {
    return evaluatePasswordStrength(password);
  }, [password]);

  // Confirm password match check
  const confirmPasswordError = useMemo(() => {
    if (!isUp || !confirmTouched || !confirmPassword) return "";
    if (password !== confirmPassword) {
      return "Passwords do not match";
    }
    return "";
  }, [isUp, password, confirmPassword, confirmTouched]);

  // STEP 1: PASSWORD AUTHENTICATION
  const handlePasswordAuth = async (e) => {
    if (e) e.preventDefault();
    setEmailTouched(true);

    const targetEmail = email.trim();
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!targetEmail || !emailRegex.test(targetEmail)) {
      setError("Please enter a valid email address.");
      return;
    }

    if (!password) {
      setError("Please enter your password.");
      return;
    }

    if (isUp) {
      setConfirmTouched(true);
      if (passwordStrength.score < 2) {
        setError("Please choose a stronger password before continuing.");
        return;
      }
      if (confirmPasswordError || password !== confirmPassword) {
        setError("Passwords do not match.");
        return;
      }
      if (!agreedToPolicies) {
        setError("You must agree to the Terms and Risk Disclosure.");
        return;
      }
    }

    if (loadingAction) return;

    clearStatus();
    setLoadingAction("password_auth");
    setAuthState(AUTH_STATES.PASSWORD_AUTHENTICATING);

    try {
      if (isUp) {
        // Sign Up Flow: Create account with password
        const { data, error: signUpError } = await supabase.auth.signUp({
          email: targetEmail,
          password: password,
        });

        if (signUpError) throw signUpError;

        // Account registered, dispatch email verification OTP
        setPasswordVerified(true);
        setAuthState(AUTH_STATES.OTP_REQUIRED);
        setCooldown(RESEND_COOLDOWN_SECONDS);
        setOtpDigits(["", "", "", "", "", ""]);
        setSuccess("Account credentials registered. Enter the 6-digit verification code sent to your email.");
      } else {
        // Sign In Flow: Verify password first
        const { data, error: signInError } = await supabase.auth.signInWithPassword({
          email: targetEmail,
          password: password,
        });

        if (signInError) {
          throw signInError;
        }

        // Password verified successfully!
        // DO NOT grant application access or persist session to sessionStorage yet.
        // Immediately dispatch the 2nd factor Email OTP challenge.
        try {
          await supabase.auth.signInWithOtp({
            email: targetEmail,
            options: {
              shouldCreateUser: false,
            },
          });
        } catch {
          // If signInWithOtp rate-limited or fails, user can use resend button
        }

        setPasswordVerified(true);
        setAuthState(AUTH_STATES.OTP_REQUIRED);
        setCooldown(RESEND_COOLDOWN_SECONDS);
        setOtpDigits(["", "", "", "", "", ""]);
        setSuccess("Password verified. Enter the 6-digit verification code sent to your email.");
      }
    } catch (err) {
      setPasswordVerified(false);
      setAuthState(AUTH_STATES.PASSWORD_REQUIRED);
      const msg = err.message || "";
      if (msg.toLowerCase().includes("invalid login credentials") || msg.toLowerCase().includes("invalid credentials")) {
        setError("Invalid email or password. Please check your credentials and try again.");
      } else {
        setError(msg || "Authentication failed. Please check your inputs.");
      }
    } finally {
      setLoadingAction("");
    }
  };

  // STEP 2: 6-DIGIT EMAIL OTP VERIFICATION
  const handleVerifyOtp = async (e) => {
    if (e) e.preventDefault();
    const token = otpDigits.join("").trim();

    if (token.length !== 6 || !/^\d{6}$/.test(token)) {
      setError("Please enter all 6 numeric digits of your verification code.");
      return;
    }

    // Security Invariant: Must have completed password authentication
    if (!passwordVerified) {
      setError("Password authentication must be completed before verifying OTP.");
      setAuthState(AUTH_STATES.PASSWORD_REQUIRED);
      return;
    }

    if (loadingAction) return;

    clearStatus();
    setLoadingAction("verify_otp");
    setAuthState(AUTH_STATES.OTP_VERIFYING);

    try {
      const { data, error: verifyError } = await supabase.auth.verifyOtp({
        email: email.trim(),
        token: token,
        type: "email",
      });

      if (verifyError) {
        throw verifyError;
      }

      if (data?.session?.access_token) {
        setAuthState(AUTH_STATES.AUTHENTICATED);
        sessionStorage.setItem("token", data.session.access_token);
        setSuccess("Authentication successful. Redirecting to dashboard...");
        navigate("/app/dashboard");
      } else {
        setAuthState(AUTH_STATES.OTP_REQUIRED);
        setError("Verification accepted, but no active session was returned. Please try signing in again.");
      }
    } catch (err) {
      setAuthState(AUTH_STATES.OTP_REQUIRED);
      const msg = err.message || "";
      if (msg.toLowerCase().includes("expired")) {
        setError("The verification code has expired. Please request a new code.");
      } else if (msg.toLowerCase().includes("invalid") || msg.toLowerCase().includes("token")) {
        setError("Invalid verification code. Please check your email or request a new code.");
      } else {
        setError(msg || "Verification failed. Please check the code and try again.");
      }
      setOtpDigits(["", "", "", "", "", ""]);
      otpInputRefs.current[0]?.focus();
    } finally {
      setLoadingAction("");
    }
  };

  // Resend OTP
  const handleResendOtp = async () => {
    if (cooldown > 0 || loadingAction || !passwordVerified) return;
    clearStatus();
    setLoadingAction("resend_otp");

    try {
      const { error: otpError } = await supabase.auth.signInWithOtp({
        email: email.trim(),
        options: {
          shouldCreateUser: false,
        },
      });

      if (otpError) throw otpError;

      setCooldown(RESEND_COOLDOWN_SECONDS);
      setSuccess("A new 6-digit verification code has been dispatched.");
    } catch (err) {
      setError(err.message || "Failed to resend verification code. Please try again later.");
    } finally {
      setLoadingAction("");
    }
  };

  // OTP Digit Change Handler
  const handleDigitChange = (index, value) => {
    const numericChar = value.replace(/\D/g, "").slice(-1);
    const newDigits = [...otpDigits];
    newDigits[index] = numericChar;
    setOtpDigits(newDigits);

    // Auto-advance focus
    if (numericChar && index < 5) {
      otpInputRefs.current[index + 1]?.focus();
    }
  };

  // OTP Keydown Handler (Backspace and arrow navigation)
  const handleDigitKeyDown = (index, e) => {
    if (e.key === "Backspace") {
      if (!otpDigits[index] && index > 0) {
        otpInputRefs.current[index - 1]?.focus();
      }
    } else if (e.key === "ArrowLeft" && index > 0) {
      otpInputRefs.current[index - 1]?.focus();
    } else if (e.key === "ArrowRight" && index < 5) {
      otpInputRefs.current[index + 1]?.focus();
    }
  };

  // OTP Paste Handler
  const handleOtpPaste = (e) => {
    e.preventDefault();
    const pastedData = e.clipboardData.getData("text").trim();
    const digitsOnly = pastedData.replace(/\D/g, "").slice(0, 6);

    if (digitsOnly.length > 0) {
      const newDigits = [...otpDigits];
      for (let i = 0; i < 6; i++) {
        newDigits[i] = digitsOnly[i] || "";
      }
      setOtpDigits(newDigits);
      const nextFocusIndex = Math.min(digitsOnly.length, 5);
      otpInputRefs.current[nextFocusIndex]?.focus();
    }
  };

  // Return to Password step
  const handleBackToPassword = () => {
    clearStatus();
    setPasswordVerified(false);
    setAuthState(AUTH_STATES.PASSWORD_REQUIRED);
    setOtpDigits(["", "", "", "", "", ""]);
  };

  // Google OAuth Fallback
  const handleGoogleAuth = async () => {
    if (loadingAction) return;
    clearStatus();
    setLoadingAction("google");
    try {
      const { error: oAuthError } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: `${window.location.origin}/app/dashboard`,
          queryParams: {
            access_type: "offline",
            prompt: "consent",
          },
        },
      });
      if (oAuthError) throw oAuthError;
    } catch (err) {
      setError(err.message || "Google authentication failed.");
      setLoadingAction("");
    }
  };

  const isOtpStage = authState === AUTH_STATES.OTP_REQUIRED || authState === AUTH_STATES.OTP_VERIFYING;
  const formattedCooldown = `00:${cooldown.toString().padStart(2, "0")}`;

  return (
    <div
      style={{
        background: token.surface.canvas,
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
        overflow: "hidden",
        padding: "16px",
      }}
    >
      {/* Background Radial Glow */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage:
            "radial-gradient(ellipse 70% 70% at 20% 50%, rgba(0,212,255,0.05) 0%, transparent 60%), radial-gradient(ellipse 50% 50% at 80% 30%, rgba(139,92,246,0.04) 0%, transparent 55%)",
          pointerEvents: "none",
        }}
      />

      {/* Main Terminal Card */}
      <div
        style={{
          background: token.surface.raised,
          border: `1px solid ${token.line.default}`,
          borderRadius: 18,
          padding: "36px 32px",
          width: 520,
          maxWidth: "100%",
          position: "relative",
          zIndex: 1,
          boxShadow: "0 40px 80px rgba(0,0,0,0.7)",
        }}
      >
        {/* Header Branding */}
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <div
            style={{
              background: "linear-gradient(135deg, #00d4ff, #0055ff)",
              borderRadius: 10,
              padding: "9px 10px",
              display: "inline-flex",
              marginBottom: 12,
              boxShadow: "0 0 20px rgba(0,212,255,0.25)",
            }}
          >
            {!isOtpStage ? (
              <Zap size={22} color="#000" strokeWidth={2.5} aria-hidden="true" />
            ) : (
              <ShieldCheck size={22} color="#000" strokeWidth={2.5} aria-hidden="true" />
            )}
          </div>

          <h1 style={{ color: token.content.primary, fontWeight: 900, fontSize: 22, letterSpacing: -0.5, margin: 0 }}>
            {!isOtpStage
              ? isUp
                ? "Create Your Account"
                : "Sign In to Terminal"
              : "Verify Your Identity"}
          </h1>

          <p
            style={{
              color: token.content.secondary,
              fontSize: 11,
              fontFamily: "monospace",
              marginTop: 6,
              lineHeight: 1.5,
            }}
          >
            {!isOtpStage ? (
              "Institutional Password + Email OTP Authentication"
            ) : (
              <span>
                A 6-digit verification code has been sent to{" "}
                <span style={{ color: token.brand.base, fontWeight: 600 }}>{maskEmail(email)}</span>
              </span>
            )}
          </p>
        </div>

        {/* STEP 1: PASSWORD AUTHENTICATION */}
        {!isOtpStage && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8, marginBottom: 20 }}>
              <button
                type="button"
                onClick={handleGoogleAuth}
                disabled={isLoading}
                aria-label="Sign in with Google"
                style={{
                  background: token.surface.inset,
                  border: `1px solid ${token.line.default}`,
                  borderRadius: 8,
                  padding: "9px 0",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 6,
                  fontSize: 11,
                  fontFamily: "monospace",
                  color: token.content.secondary,
                  cursor: isLoading ? "not-allowed" : "pointer",
                  transition: "all 0.15s",
                  opacity: isLoading ? 0.6 : 1,
                }}
                className="hover:border-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-400"
              >
                <Globe size={13} aria-hidden="true" />
                {loadingAction === "google" ? "Redirecting..." : "Continue with Google"}
              </button>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
              <div style={{ height: 1, flex: 1, background: token.line.default }} />
              <span style={{ color: token.content.muted, fontSize: 9, fontFamily: "monospace", letterSpacing: 1 }}>
                OR ENTER CREDENTIALS
              </span>
              <div style={{ height: 1, flex: 1, background: token.line.default }} />
            </div>

            <form noValidate onSubmit={handlePasswordAuth} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {/* Email Input */}
              <div>
                <Inp
                  id="auth-email"
                  lbl="Email Address"
                  ph="trader@vyomquant.io"
                  type="email"
                  icon={Mail}
                  val={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onBlur={() => setEmailTouched(true)}
                  disabled={isLoading}
                  autoComplete="email"
                  required
                />
                {emailInlineError && (
                  <p style={{ color: "#EF5350", fontSize: 10, fontFamily: "monospace", marginTop: 4 }}>
                    {emailInlineError}
                  </p>
                )}
              </div>

              {/* Password Input */}
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label
                  htmlFor="auth-password"
                  style={{
                    color: token.content.secondary,
                    fontSize: 9,
                    fontFamily: "monospace",
                    fontWeight: 900,
                    letterSpacing: 2,
                    textTransform: "uppercase",
                  }}
                >
                  Password
                </label>
                <div style={{ position: "relative" }}>
                  <Lock
                    size={12}
                    aria-hidden="true"
                    style={{ color: token.content.muted, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }}
                  />
                  <input
                    id="auth-password"
                    type={showPw ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••••••"
                    disabled={isLoading}
                    aria-label="Password"
                    autoComplete={isUp ? "new-password" : "current-password"}
                    required
                    style={{
                      background: token.surface.inset,
                      border: `1px solid ${token.line.default}`,
                      color: token.content.primary,
                      width: "100%",
                      borderRadius: 8,
                      padding: "8px 40px 8px 32px",
                      fontSize: 12,
                      fontFamily: "monospace",
                      outline: "none",
                      opacity: isLoading ? 0.6 : 1,
                      cursor: isLoading ? "not-allowed" : "text",
                    }}
                    className="focus:border-cyan-500/50 focus:ring-2 focus:ring-cyan-400 transition-all"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPw(!showPw)}
                    aria-label={showPw ? "Hide password" : "Show password"}
                    style={{
                      position: "absolute",
                      right: 10,
                      top: "50%",
                      transform: "translateY(-50%)",
                      color: token.content.muted,
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                    }}
                    className="hover:text-slate-400 focus:outline-none focus:ring-1 focus:ring-cyan-400 rounded"
                  >
                    {showPw ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                  </button>
                </div>
                {isUp && <PasswordStrengthMeter strength={passwordStrength} />}
              </div>

              {/* Confirm Password (Sign-Up only) */}
              {isUp && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <label
                    htmlFor="auth-confirm-password"
                    style={{
                      color: token.content.secondary,
                      fontSize: 9,
                      fontFamily: "monospace",
                      fontWeight: 900,
                      letterSpacing: 2,
                      textTransform: "uppercase",
                    }}
                  >
                    Confirm Password
                  </label>
                  <div style={{ position: "relative" }}>
                    <Lock
                      size={12}
                      aria-hidden="true"
                      style={{ color: token.content.muted, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }}
                    />
                    <input
                      id="auth-confirm-password"
                      type={showConfirmPw ? "text" : "password"}
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      onBlur={() => setConfirmTouched(true)}
                      placeholder="••••••••••••"
                      disabled={isLoading}
                      aria-label="Confirm Password"
                      autoComplete="new-password"
                      required
                      style={{
                        background: token.surface.inset,
                        border: `1px solid ${token.line.default}`,
                        color: token.content.primary,
                        width: "100%",
                        borderRadius: 8,
                        padding: "8px 40px 8px 32px",
                        fontSize: 12,
                        fontFamily: "monospace",
                        outline: "none",
                        opacity: isLoading ? 0.6 : 1,
                        cursor: isLoading ? "not-allowed" : "text",
                      }}
                      className="focus:border-cyan-500/50 focus:ring-2 focus:ring-cyan-400 transition-all"
                    />
                    <button
                      type="button"
                      onClick={() => setShowConfirmPw(!showConfirmPw)}
                      aria-label={showConfirmPw ? "Hide confirm password" : "Show confirm password"}
                      style={{
                        position: "absolute",
                        right: 10,
                        top: "50%",
                        transform: "translateY(-50%)",
                        color: token.content.muted,
                        background: "transparent",
                        border: "none",
                        cursor: "pointer",
                      }}
                      className="hover:text-slate-400 focus:outline-none focus:ring-1 focus:ring-cyan-400 rounded"
                    >
                      {showConfirmPw ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                    </button>
                  </div>
                  {confirmPasswordError && (
                    <p style={{ color: "#EF5350", fontSize: 10, fontFamily: "monospace", marginTop: 2 }}>
                      {confirmPasswordError}
                    </p>
                  )}
                  {confirmTouched && confirmPassword && !confirmPasswordError && (
                    <p
                      style={{
                        color: "#10B981",
                        fontSize: 10,
                        fontFamily: "monospace",
                        marginTop: 2,
                        display: "flex",
                        alignItems: "center",
                        gap: 4,
                      }}
                    >
                      <Check size={10} /> Passwords match
                    </p>
                  )}
                </div>
              )}

              {/* Terms Checkbox for Sign-Up */}
              {isUp && (
                <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                  <label
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 8,
                      fontSize: 11,
                      fontFamily: "monospace",
                      color: token.content.secondary,
                      cursor: "pointer",
                      lineHeight: "1.4",
                    }}
                  >
                    <input
                      type="checkbox"
                      required
                      checked={agreedToPolicies}
                      onChange={(e) => setAgreedToPolicies(e.target.checked)}
                      style={{ accentColor: token.brand.base, marginTop: 2 }}
                    />
                    <span>
                      I agree to the{" "}
                      <Link
                        to="/legal/terms"
                        style={{ color: token.brand.base, textDecoration: "underline" }}
                        className="hover:text-cyan-300 focus:ring-1 focus:ring-cyan-400 rounded"
                      >
                        Terms
                      </Link>{" "}
                      and{" "}
                      <Link
                        to="/legal/risk"
                        style={{ color: token.brand.base, textDecoration: "underline" }}
                        className="hover:text-cyan-300 focus:ring-1 focus:ring-cyan-400 rounded"
                      >
                        Risk Disclosure
                      </Link>
                      .
                    </span>
                  </label>
                </div>
              )}

              <Button
                variant="primary"
                size="md"
                className="w-full justify-center mt-1"
                disabled={isLoading || !email.trim() || !password}
                type="submit"
              >
                {loadingAction === "password_auth" ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <RefreshCw size={13} className="animate-spin" /> Authenticating...
                  </span>
                ) : isUp ? (
                  "CREATE ACCOUNT & SEND OTP →"
                ) : (
                  "SIGN IN →"
                )}
              </Button>

              {/* Divider & Google OAuth */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  margin: "18px 0 14px",
                  gap: 12,
                }}
              >
                <div style={{ flex: 1, height: 1, background: token.line.default }} />
                <span style={{ color: token.content.muted, fontSize: 10, fontFamily: "monospace", letterSpacing: 1, textTransform: "uppercase" }}>
                  OR
                </span>
                <div style={{ flex: 1, height: 1, background: token.line.default }} />
              </div>

              <button
                type="button"
                onClick={handleGoogleAuth}
                disabled={isLoading}
                data-testid="google-auth-button"
                style={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 10,
                  background: "rgba(255, 255, 255, 0.05)",
                  border: `1px solid ${token.line.default}`,
                  borderRadius: 8,
                  padding: "10px 16px",
                  color: token.content.primary,
                  fontSize: 12,
                  fontFamily: "monospace",
                  fontWeight: 600,
                  cursor: isLoading ? "not-allowed" : "pointer",
                  transition: "all 0.15s ease",
                }}
                className="hover:bg-white/10 hover:border-white/25 focus:ring-1 focus:ring-cyan-400"
              >
                {loadingAction === "google" ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <RefreshCw size={13} className="animate-spin" /> Redirecting to Google...
                  </span>
                ) : (
                  <>
                    <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true">
                      <path
                        fill="#4285F4"
                        d="M23.745 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.82-2.4 3.68v3.05h3.88c2.27-2.09 3.66-5.17 3.66-9.17z"
                      />
                      <path
                        fill="#34A853"
                        d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.1-6.72-4.93H1.25v3.15C3.26 21.36 7.36 24 12 24z"
                      />
                      <path
                        fill="#FBBC05"
                        d="M5.28 14.27c-.25-.72-.38-1.49-.38-2.27s.13-1.55.38-2.27V6.58H1.25C.45 8.18 0 10.03 0 12s.45 3.82 1.25 5.42l4.03-3.15z"
                      />
                      <path
                        fill="#EA4335"
                        d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.36 0 3.26 2.64 1.25 6.58l4.03 3.15c.95-2.83 3.6-4.98 6.72-4.98z"
                      />
                    </svg>
                    Continue with Google
                  </>
                )}
              </button>
            </form>
          </>
        )}

        {/* STEP 2: 6-DIGIT EMAIL OTP VERIFICATION */}
        {isOtpStage && (
          <form noValidate onSubmit={handleVerifyOtp} style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "center" }}>
              <label
                htmlFor="otp-digit-0"
                style={{
                  color: token.content.secondary,
                  fontSize: 10,
                  fontFamily: "monospace",
                  fontWeight: 700,
                  letterSpacing: 2,
                  textTransform: "uppercase",
                }}
              >
                Enter 6-Digit Email Code
              </label>

              {/* 6 Digit Input Group */}
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  justifyContent: "center",
                  width: "100%",
                }}
                onPaste={handleOtpPaste}
              >
                {otpDigits.map((digit, i) => (
                  <input
                    key={i}
                    id={`otp-digit-${i}`}
                    ref={(el) => (otpInputRefs.current[i] = el)}
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={1}
                    value={digit}
                    onChange={(e) => handleDigitChange(i, e.target.value)}
                    onKeyDown={(e) => handleDigitKeyDown(i, e)}
                    disabled={isLoading}
                    aria-label={`Digit ${i + 1} of 6`}
                    style={{
                      background: token.surface.inset,
                      border: `1px solid ${digit ? token.brand.base : token.line.default}`,
                      color: token.content.primary,
                      width: 46,
                      height: 54,
                      textAlign: "center",
                      fontSize: 20,
                      fontWeight: 900,
                      borderRadius: 8,
                      outline: "none",
                      fontFamily: "monospace",
                      transition: "all 0.15s",
                      boxShadow: digit ? `0 0 10px ${token.brand.base}20` : "none",
                      opacity: isLoading ? 0.6 : 1,
                    }}
                    className="focus:border-cyan-400 focus:ring-2 focus:ring-cyan-400/30"
                  />
                ))}
              </div>
            </div>

            {/* Resend Cooldown Counter & Back to Password */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                fontSize: 11,
                fontFamily: "monospace",
                color: token.content.muted,
                padding: "0 4px",
              }}
            >
              {cooldown > 0 ? (
                <span>Resend code in {formattedCooldown}</span>
              ) : (
                <button
                  type="button"
                  onClick={handleResendOtp}
                  disabled={isLoading}
                  style={{
                    color: token.brand.base,
                    background: "transparent",
                    border: "none",
                    cursor: isLoading ? "not-allowed" : "pointer",
                    fontSize: 11,
                    fontFamily: "monospace",
                    padding: 0,
                  }}
                  className="hover:underline focus:ring-1 focus:ring-cyan-400 rounded"
                >
                  {loadingAction === "resend_otp" ? "Resending..." : "Resend code"}
                </button>
              )}

              <button
                type="button"
                onClick={handleBackToPassword}
                disabled={isLoading}
                style={{
                  color: token.content.muted,
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: 11,
                  fontFamily: "monospace",
                }}
                className="hover:text-cyan-400 focus:ring-1 focus:ring-cyan-400 rounded"
              >
                <ArrowLeft size={12} /> Back
              </button>
            </div>

            <Button
              variant="primary"
              size="md"
              className="w-full justify-center"
              disabled={isLoading || otpDigits.join("").length !== 6}
              type="submit"
            >
              {loadingAction === "verify_otp" ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <RefreshCw size={13} className="animate-spin" /> Verifying OTP...
                </span>
              ) : (
                "VERIFY & CONTINUE →"
              )}
            </Button>
          </form>
        )}

        {/* Feedback Alerts */}
        {!!error && (
          <div
            role="alert"
            style={{
              marginTop: 16,
              background: "rgba(255, 61, 0, 0.12)",
              border: "1px solid rgba(255, 61, 0, 0.35)",
              color: "#FF3D00",
              borderRadius: 8,
              padding: "10px 12px",
              fontSize: 11,
              fontFamily: "monospace",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <AlertCircle size={14} style={{ flexShrink: 0 }} aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {!!success && (
          <div
            role="status"
            style={{
              marginTop: 16,
              background: "rgba(0, 200, 83, 0.12)",
              border: "1px solid rgba(0, 200, 83, 0.35)",
              color: "#00C853",
              borderRadius: 8,
              padding: "10px 12px",
              fontSize: 11,
              fontFamily: "monospace",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <CheckCircle size={14} style={{ flexShrink: 0 }} aria-hidden="true" />
            <span>{success}</span>
          </div>
        )}

        {/* Auth Mode Toggle Footer */}
        {!isOtpStage && (
          <p
            style={{
              color: token.content.muted,
              fontSize: 10,
              fontFamily: "monospace",
              textAlign: "center",
              marginTop: 20,
            }}
          >
            {isUp ? "Already registered on VyomQuant? " : "No account yet? "}
            <button
              type="button"
              style={{
                color: token.brand.base,
                cursor: "pointer",
                background: "transparent",
                border: "none",
                fontFamily: "monospace",
              }}
              className="hover:underline focus:ring-1 focus:ring-cyan-400 rounded"
              onClick={() => {
                setAuthMode(isUp ? "signin" : "signup");
                clearStatus();
              }}
            >
              {isUp ? "Sign in" : "Create one free"}
            </button>
          </p>
        )}

        {/* Legal Links Footer */}
        <div
          style={{
            marginTop: 24,
            borderTop: `1px solid ${token.line.default}`,
            paddingTop: 16,
            display: "flex",
            justifyContent: "center",
            gap: 16,
          }}
        >
          <Link
            to="/legal/terms"
            style={{ color: token.content.muted, fontSize: 9, fontFamily: "monospace" }}
            className="hover:underline hover:text-white focus:ring-1 focus:ring-cyan-400 rounded"
          >
            Terms
          </Link>
          <Link
            to="/legal/privacy"
            style={{ color: token.content.muted, fontSize: 9, fontFamily: "monospace" }}
            className="hover:underline hover:text-white focus:ring-1 focus:ring-cyan-400 rounded"
          >
            Privacy
          </Link>
          <Link
            to="/legal/risk"
            style={{ color: token.content.muted, fontSize: 9, fontFamily: "monospace" }}
            className="hover:underline hover:text-white focus:ring-1 focus:ring-cyan-400 rounded"
          >
            Risk Disclosure
          </Link>
          <Link
            to="/legal/refund"
            style={{ color: token.content.muted, fontSize: 9, fontFamily: "monospace" }}
            className="hover:underline hover:text-white focus:ring-1 focus:ring-cyan-400 rounded"
          >
            Refunds
          </Link>
        </div>
      </div>
    </div>
  );
}


