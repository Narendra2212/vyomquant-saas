import React, { useState, useEffect, useMemo } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Eye, EyeOff, Lock, Mail, Zap, Globe, AlertCircle, CheckCircle, Check, X } from "lucide-react";
import { C, Btn, Inp } from "../components/ui-legacy/primitives";
import { supabase } from "../supabase";

const evaluatePasswordStrength = (pwd) => {
  if (!pwd) return { score: 0, label: "", color: C.t3, checks: { length: false, upper: false, lower: false, number: false, special: false } };
  
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
        <span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", textTransform: "uppercase" }}>
          Password Strength
        </span>
        <span style={{ color: strength.color, fontSize: 10, fontFamily: "monospace", fontWeight: 700 }}>
          {strength.label}
        </span>
      </div>
      <div style={{ height: 4, background: C.bg3, borderRadius: 2, overflow: "hidden", border: `1px solid ${C.border}` }}>
        <div style={{ height: "100%", width: `${pct}%`, background: strength.color, transition: "all 0.2s ease" }} />
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 4, fontSize: 9, fontFamily: "monospace" }}>
        <span style={{ color: strength.checks.length ? "#10B981" : C.t3, display: "inline-flex", alignItems: "center", gap: 3 }}>
          {strength.checks.length ? <Check size={10} /> : <X size={10} />} 8+ chars
        </span>
        <span style={{ color: strength.checks.upper && strength.checks.lower ? "#10B981" : C.t3, display: "inline-flex", alignItems: "center", gap: 3 }}>
          {strength.checks.upper && strength.checks.lower ? <Check size={10} /> : <X size={10} />} Upper & lower
        </span>
        <span style={{ color: strength.checks.number ? "#10B981" : C.t3, display: "inline-flex", alignItems: "center", gap: 3 }}>
          {strength.checks.number ? <Check size={10} /> : <X size={10} />} Number
        </span>
        <span style={{ color: strength.checks.special ? "#10B981" : C.t3, display: "inline-flex", alignItems: "center", gap: 3 }}>
          {strength.checks.special ? <Check size={10} /> : <X size={10} />} Symbol
        </span>
      </div>
    </div>
  );
}

export default function AuthPage({ mode, go }) {
  const navigate = useNavigate();
  const [showPw, setShowPw] = useState(false);
  const [showConfirmPw, setShowConfirmPw] = useState(false);
  const [authView, setAuthView] = useState(mode === "signup" ? "signup" : "signin");
  
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [confirmTouched, setConfirmTouched] = useState(false);

  const [loadingAction, setLoadingAction] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [agreedToPolicies, setAgreedToPolicies] = useState(false);

  const isUp = authView === "signup";

  const clearStatus = () => {
    setError("");
    setSuccess("");
  };

  useEffect(() => {
    setAuthView(mode === "signup" ? "signup" : "signin");
    clearStatus();
    setEmailTouched(false);
    setConfirmTouched(false);
  }, [mode]);

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

  const handleSignUp = async (e) => {
    e.preventDefault();
    setEmailTouched(true);
    setConfirmTouched(true);

    if (emailInlineError) return;
    if (passwordStrength.score < 2) {
      setError("Please choose a stronger password before continuing.");
      return;
    }
    if (confirmPasswordError) return;

    if (!agreedToPolicies) {
      clearStatus();
      setError("You must agree to the Terms and Risk Disclosure.");
      return;
    }
    if (loadingAction) return;
    clearStatus();
    setLoadingAction("signup");

    try {
      const { data, error } = await supabase.auth.signUp({
        email: email.trim(),
        password: password,
      });

      if (error) throw error;

      if (data?.session?.access_token) {
        sessionStorage.setItem("token", data.session.access_token);
        navigate("/app/dashboard");
      } else {
        setSuccess("Registration successful. Please check your email to verify your account.");
      }
    } catch (err) {
      setError(err.message || "Registration failed. Please check your inputs.");
    } finally {
      setLoadingAction("");
    }
  };

  const handleSignIn = async (e) => {
    e.preventDefault();
    setEmailTouched(true);

    if (emailInlineError) return;
    if (loadingAction) return;

    clearStatus();
    setLoadingAction("signin");

    try {
      const { data, error } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password: password,
      });

      if (error) throw error;

      if (data?.session?.access_token) {
        sessionStorage.setItem("token", data.session.access_token);
        navigate("/app/dashboard");
      } else {
        setError("Sign in failed. Please check your credentials.");
      }
    } catch (err) {
      setError(err.message || "Sign in failed. Check your credentials.");
    } finally {
      setLoadingAction("");
    }
  };

  const handleGoogleAuth = async () => {
    if (loadingAction) return;
    clearStatus();
    setLoadingAction("google");
    try {
      setError("Google authentication requires Supabase OAuth configuration. Please use email/password authentication.");
    } catch (err) {
      setError(err.message || "Google authentication failed.");
    } finally {
      setLoadingAction("");
    }
  };

  const handleForgotPassword = async () => {
    setEmailTouched(true);
    if (!email || emailInlineError) {
      clearStatus();
      setError("Please enter a valid email address first.");
      return;
    }
    clearStatus();
    setLoadingAction("forgot-password");

    try {
      const { data, error } = await supabase.auth.resetPasswordForEmail(email.trim(), {
        redirectTo: `${window.location.origin}/reset-password`
      });

      if (error) throw error;

      setSuccess("Password reset email sent successfully. Please check your inbox.");
    } catch (err) {
      setError(err.message || "Failed to send password reset email.");
    } finally {
      setLoadingAction("");
    }
  };

  const isLoading = !!loadingAction;

  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", inset: 0, backgroundImage: `radial-gradient(ellipse 70% 70% at 20% 50%, rgba(0,212,255,0.05) 0%, transparent 60%),radial-gradient(ellipse 50% 50% at 80% 30%, rgba(139,92,246,0.04) 0%, transparent 55%)` }} />
      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 36, width: 550, maxWidth: "calc(100vw - 32px)", position: "relative", zIndex: 1, boxShadow: "0 40px 80px rgba(0,0,0,0.7)" }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ background: "linear-gradient(135deg,#00d4ff,#0055ff)", borderRadius: 10, padding: "9px 10px", display: "inline-flex", marginBottom: 12 }}>
            <Zap size={22} color="#000" strokeWidth={2.5} aria-hidden="true" />
          </div>
          <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 22, letterSpacing: -0.5 }}>
            {isUp ? "Create Your Account" : "Welcome Back"}
          </h1>
        </div>

        <>
          <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8, marginBottom: 20 }}>
            <button
              type="button"
              onClick={handleGoogleAuth}
              disabled={isLoading}
              aria-label="Sign in with Google"
              style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "9px 0", display: "flex", alignItems: "center", justifyContent: "center", gap: 6, fontSize: 11, fontFamily: "monospace", color: C.t2, cursor: isLoading ? "not-allowed" : "pointer", transition: "all 0.15s", opacity: isLoading ? 0.6 : 1 }}
              className="hover:border-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-400"
            >
              <Globe size={13} aria-hidden="true" /> {loadingAction === "google" ? "Redirecting..." : "Google"}
            </button>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
            <div style={{ height: 1, flex: 1, background: C.border }} /><span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>OR CONTINUE</span><div style={{ height: 1, flex: 1, background: C.border }} />
          </div>
        </>

        <form onSubmit={isUp ? handleSignUp : handleSignIn} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <Inp
              id="auth-email"
              lbl="Email"
              ph="admin@algo22.io"
              type="email"
              icon={Mail}
              val={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => setEmailTouched(true)}
              disabled={isLoading}
              required
            />
            {emailInlineError && (
              <p style={{ color: "#EF5350", fontSize: 10, fontFamily: "monospace", marginTop: 4 }}>
                {emailInlineError}
              </p>
            )}
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label htmlFor="auth-password" style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 3, textTransform: "uppercase" }}>
              Password
            </label>
            <div style={{ position: "relative" }}>
              <Lock size={12} aria-hidden="true" style={{ color: C.t3, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
              <input
                id="auth-password"
                type={showPw ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                disabled={isLoading}
                aria-label="Password"
                required
                style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, width: "100%", borderRadius: 8, padding: "8px 40px 8px 32px", fontSize: 12, fontFamily: "monospace", outline: "none", opacity: isLoading ? 0.6 : 1, cursor: isLoading ? "not-allowed" : "text" }}
                className="focus:border-cyan-500/50 focus:ring-2 focus:ring-cyan-400 transition-all"
              />
              <button
                type="button"
                onClick={() => setShowPw(!showPw)}
                aria-label={showPw ? "Hide password" : "Show password"}
                style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", color: C.t3, background: "transparent", border: "none", cursor: "pointer" }}
                className="hover:text-slate-400 focus:outline-none focus:ring-1 focus:ring-cyan-400 rounded"
              >
                {showPw ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
              </button>
            </div>
            {isUp && <PasswordStrengthMeter strength={passwordStrength} />}
          </div>

          {isUp && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label htmlFor="auth-confirm-password" style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 3, textTransform: "uppercase" }}>
                Confirm Password
              </label>
              <div style={{ position: "relative" }}>
                <Lock size={12} aria-hidden="true" style={{ color: C.t3, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
                <input
                  id="auth-confirm-password"
                  type={showConfirmPw ? "text" : "password"}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  onBlur={() => setConfirmTouched(true)}
                  placeholder="••••••••••••"
                  disabled={isLoading}
                  aria-label="Confirm Password"
                  required
                  style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, width: "100%", borderRadius: 8, padding: "8px 40px 8px 32px", fontSize: 12, fontFamily: "monospace", outline: "none", opacity: isLoading ? 0.6 : 1, cursor: isLoading ? "not-allowed" : "text" }}
                  className="focus:border-cyan-500/50 focus:ring-2 focus:ring-cyan-400 transition-all"
                />
                <button
                  type="button"
                  onClick={() => setShowConfirmPw(!showConfirmPw)}
                  aria-label={showConfirmPw ? "Hide confirm password" : "Show confirm password"}
                  style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", color: C.t3, background: "transparent", border: "none", cursor: "pointer" }}
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
                <p style={{ color: "#10B981", fontSize: 10, fontFamily: "monospace", marginTop: 2, display: "flex", alignItems: "center", gap: 4 }}>
                  <Check size={10} /> Passwords match
                </p>
              )}
            </div>
          )}

          {isUp ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
              <label style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 11, fontFamily: "monospace", color: C.t2, cursor: "pointer", lineHeight: "1.4" }}>
                <input
                  type="checkbox"
                  required
                  checked={agreedToPolicies}
                  onChange={(e) => setAgreedToPolicies(e.target.checked)}
                  style={{ accentColor: C.cyan, marginTop: 2 }}
                />
                <span>
                  I agree to the{" "}
                  <Link to="/legal" style={{ color: C.cyan, textDecoration: "underline" }} className="hover:text-cyan-300 focus:ring-1 focus:ring-cyan-400 rounded">
                    Terms
                  </Link>{" "}
                  and{" "}
                  <Link to="/legal" style={{ color: C.cyan, textDecoration: "underline" }} className="hover:text-cyan-300 focus:ring-1 focus:ring-cyan-400 rounded">
                    Risk Disclosure
                  </Link>.
                </span>
              </label>
            </div>
          ) : (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, fontFamily: "monospace", color: C.t2, cursor: "pointer" }}>
                <input type="checkbox" style={{ accentColor: C.cyan }} /> Remember me
              </label>
              <button
                type="button"
                style={{ color: C.cyan, fontSize: 10, fontFamily: "monospace", cursor: "pointer", background: "transparent", border: "none" }}
                className="hover:underline focus:ring-1 focus:ring-cyan-400 rounded"
                onClick={handleForgotPassword}
              >
                Forgot password?
              </button>
            </div>
          )}
          <Btn v="primary" sz="md" cls="w-full justify-center mt-1" disabled={isLoading} type="submit">
            {isUp ? (loadingAction === "signup" ? "Creating Account..." : "Create Account") : (loadingAction === "signin" ? "Signing In..." : "Sign In")}
          </Btn>
        </form>

        {!!error && (
          <div role="alert" style={{ marginTop: 14, background: "rgba(255, 61, 0, 0.12)", border: "1px solid rgba(255, 61, 0, 0.35)", color: "#FF3D00", borderRadius: 8, padding: "10px 12px", fontSize: 11, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={14} style={{ flexShrink: 0 }} aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {!!success && (
          <div role="status" style={{ marginTop: 14, background: "rgba(0, 200, 83, 0.12)", border: "1px solid rgba(0, 200, 83, 0.35)", color: "#00C853", borderRadius: 8, padding: "10px 12px", fontSize: 11, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 8 }}>
            <CheckCircle size={14} style={{ flexShrink: 0 }} aria-hidden="true" />
            <span>{success}</span>
          </div>
        )}

        <p style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", textAlign: "center", marginTop: 20 }}>
          {isUp ? "Already have an account? " : "No account yet? "}
          <button
            type="button"
            style={{ color: C.cyan, cursor: "pointer", background: "transparent", border: "none" }}
            className="hover:underline focus:ring-1 focus:ring-cyan-400 rounded"
            onClick={() => {
              setAuthView(isUp ? "signin" : "signup");
              clearStatus();
            }}
          >
            {isUp ? "Sign in" : "Create one free"}
          </button>
        </p>

        <div style={{ marginTop: 24, borderTop: `1px solid ${C.border}`, paddingTop: 16, display: "flex", justifyContent: "center", gap: 16 }}>
          <Link to="/legal" style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }} className="hover:underline hover:text-white focus:ring-1 focus:ring-cyan-400 rounded">Terms</Link>
          <Link to="/legal" style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }} className="hover:underline hover:text-white focus:ring-1 focus:ring-cyan-400 rounded">Privacy</Link>
          <Link to="/legal" style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }} className="hover:underline hover:text-white focus:ring-1 focus:ring-cyan-400 rounded">Risk Disclosure</Link>
          <Link to="/legal" style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }} className="hover:underline hover:text-white focus:ring-1 focus:ring-cyan-400 rounded">Refunds</Link>
        </div>
      </div>
    </div>
  );
}
