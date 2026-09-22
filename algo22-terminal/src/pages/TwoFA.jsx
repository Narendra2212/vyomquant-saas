/**
 * pages/TwoFA.jsx — TOTP enrolment and the two-factor challenge.
 *
 * retail-ui-simplification task 7.3 (commit 9). Requirements 3.1, 3.2, 4.1, 4.2, 4.3, 4.4,
 * 5.1, 5.2, 6.2, 16.1. design.md §2.4, §5.3.
 *
 * 12 absolute pixel sizes to 0 and 12 colour literals to 0, both budgets lowered in this
 * commit. 10 inline `monospace` declarations to 2.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE MFA LIFECYCLE IS NOT TOUCHED. NOT ONE LINE OF IT.
 * ═══════════════════════════════════════════════════════════════════════════
 * `listFactors`, `enroll`, `challenge`, `verify` and `unenroll` are carried across verbatim,
 * in the same order, with the same arguments, the same cleanup of unverified factors, the same
 * `challengeId` invalidation on a failed code and the same 1.5s redirect. Requirement 16.1
 * puts authentication logic out of scope entirely; **this commit is presentation only.**
 *
 * WHICH IS WHY `usePanelState` IS NOT ADOPTED HERE, AND THAT IS A RECORDED FINDING
 * ------------------------------------------------------------------------------
 * Requirement 4.1 asks every read to resolve through `usePanelState` and the page's own
 * `loading`/`error` pair to go. It cannot be honoured on this page without breaking the
 * clause above, and the reason is structural rather than a preference:
 *
 *   1. `usePanelState` drives ONE reader and may call it again — on a `deps` change, and on
 *      `refetch`. What this page's `isLoading` covers is not a read: it is an ENROLMENT, and
 *      re-running it unenrolls the trader's unverified factors and enrols a new one. A hook
 *      that can re-issue its reader must never be pointed at that.
 *   2. The hook classifies a THROWN `ApiError` through `classifyReadFailure`, which reads an
 *      `ApiError`'s `.data` envelope and `.status`. Supabase returns `{data, error}` and
 *      throws nothing, so every failure here would land on the hook's generic `error` arm and
 *      the specific MFA conditions — a wrong code, a missing factor, a stale challenge —
 *      would be flattened into one.
 *   3. `errorMessage` here is not a failed-read state at all. It carries *Please enter all 6
 *      digits*, *Factor not initialized*, *Invalid authentication code* — form validation and
 *      flow conditions, which `usePanelState` has no state for and Requirement 16 forbids
 *      losing.
 *
 * So the lifecycle keeps its own `isLoading` / `errorMessage` / `successMessage`, and what
 * changes is what RENDERS them: `ds/LoadingState` and `ds/Alert` instead of three hand-built
 * blocks. Recorded here as Requirement 4.4 asks — a page that cannot be built from the
 * existing set states the need rather than inventing a primitive or bending a hook.
 *
 * NO `pageFields.js` ENTRY EITHER, AND FOR A GOOD REASON (Requirement 4.2)
 * ----------------------------------------------------------------------
 * **This page renders no figure.** There is no number on it — no count, no balance, no
 * percentage, no timestamp. Requirement 4.2 declares every trader-visible FIGURE, and padding
 * the declaration with a page that has none would make `PAGE_FIELDS_BY_PAGE` look complete
 * where it is empty. The one value the screen delivers is the TOTP secret, and a secret is an
 * identifier rather than a reading: there is no "not available" arm for it, because a secret
 * that could not be generated is an enrolment failure and the page already says so.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 12 PIXEL SIZES, BY §2.4'S NINE QUESTIONS
 * ═══════════════════════════════════════════════════════════════════════════
 * Each resolution is a comment on its call site. Five of the twelve resolve by moving onto a
 * primitive that already reads a declared step, so their declaration is removed rather than
 * mapped; seven resolve in place.
 *
 *   20 ×1  the `<h1>`                        Q8 heading, page level   → `--text-page`
 *   11 ×1  the step instruction              Q1 sentence              → `--text-body`
 *   11 ×3  the error, success and loading    → `ds/Alert` ×2, `ds/LoadingState`; removed
 *   10 ×2  the QR fallback and the scan line Q1 sentences             → `--text-body`
 *   10 ×1  *Manual Setup Key:*               Q4 label                 → `--text-micro`
 *   10 ×2  the two footer commands           → `ds/CommandButton`; removed
 *   12 ×1  the TOTP secret                   Q7 value, panel level    → `--text-title`
 *   22 ×1  the six code inputs               Q3 control text          → `--text-page`
 *
 * TWO OF THOSE ARE JUDGEMENTS AND BOTH ARE FINDINGS ABOUT THE ROLE SET (§2.4's Q9)
 * ------------------------------------------------------------------------------
 * **The TOTP secret is an IDENTIFIER and §2.3 has no identifier role.** Its `value` test is
 * "has a unit, a sign or a magnitude", and a base32 secret has none of the three; `chip`,
 * `label`, `table cell`, `sentence` and `heading` all miss it outright. Q9 says to record the
 * call site rather than guess, so: it is taken as `value` at panel level — it is literally
 * what this panel exists to deliver — and `--text-title` is one step up from the 12px it had.
 * The gap in the role set is the finding. The same gap will be reached again by an order id,
 * a hash and an API key prefix, so it is worth naming once.
 *
 * **`control text`'s step is a FLOOR, not a cap.** Q3 catches the six code inputs and sends
 * `control text` to `--text-body`, which would take a 6-digit one-time code from 22px to 13px
 * inside a 56px-tall box — a 41% shrink on the one control this screen exists for, which is
 * the opposite of the argument §2.3 added the role for ("form-control text at `small` is below
 * the page's own default, which is backwards — this is the one place where a misread costs a
 * wrong number typed"). Read as a floor, the role admits any step at or above `body`, and the
 * declared step nearest this call site's treatment is `--text-page` (24px, +2). §2.5's tell
 * for an illegitimate resolution is one chosen to avoid a layout change; this one grows.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 10 INLINE `monospace` DECLARATIONS → 2 (Requirements 3.1, 3.2)
 * ═══════════════════════════════════════════════════════════════════════════
 * Requirement 3.1's rule, applied exactly as task 7.3 states it: a TOTP code and a recovery
 * code are identifiers and keep monospace; the instructions around them are prose and do not.
 *
 *   KEPT (2): the secret `<code>`, and the six digit inputs. Both hold the code itself, where
 *   a fixed advance width is what lets a reader check one character against another and stops
 *   `I`/`l` and `0`/`O` from being a transcription risk on a string that must be typed exactly.
 *
 *   DROPPED (8): the step instruction, the error line, the success line, the loading line, the
 *   *Scan with…* line, the *Manual Setup Key:* label and both footer commands. Every one is
 *   prose or a label. A monospace sentence is the strongest signal on a page that it belongs
 *   to the previous generation, and on a security screen it also reads as machine output at
 *   the moment the trader most needs to be reading English.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 12 COLOUR LITERALS (Requirements 5.1, 5.2, 5.3)
 * ═══════════════════════════════════════════════════════════════════════════
 * Eight of the twelve go with the two blocks that became `ds/Alert`, which derives its hue,
 * its icon and its live-region role from `severity` and takes no colour at all: the error
 * arm's `rgba(239,68,68,0.1)` wash, `rgba(239,68,68,0.3)` border, `#fca5a5` text and
 * `#ef4444` glyph, and the success arm's `rgba(16,185,129,0.1)`, `rgba(16,185,129,0.3)`,
 * `#6ee7b7` and `#10b981`. None of those eight is in the palette — they are Tailwind's
 * red/emerald ramps, not this app's `#EF5350`/`#26A69A` — so migrating them one for one would
 * have been re-deciding two states' hues by hand at the same time.
 *
 * The other four each needed a decision:
 *
 *   * `rgba(0,212,255,0.08)` and `rgba(0,212,255,0.2)`, the header medallion's wash and ring.
 *     The wash takes `token.brand.wash`, which is the declared 10% brand and the closest thing
 *     to it that exists. **There is no 20% brand border token**, and the semi-transparent
 *     brand border is exactly the case task 5.2 already decided on Marketplace's featured
 *     card: `--color-line-strong` in place of it. Same resolution here, so the two pages do
 *     not diverge on one question.
 *   * `rgba(0,0,0,0.4)` on the QR panel → `token.shadow.raised`, which is
 *     `0 4px 12px rgba(0,0,0,0.35)`. A hand-mixed elevation two units away from a declared one.
 *   * **`#ffffff` behind the QR code is FUNCTIONAL, not decorative, and it is the one literal
 *     here that took thought.** A QR reader needs a light quiet zone around the modules or it
 *     cannot find the finder patterns, so this is not a surface colour that can go to
 *     `surface-panel`. It takes `token.content.primary` (#F0F2F5) — the lightest value the
 *     palette declares — which holds roughly 18:1 against the black modules, well past the
 *     ~3:1 a camera needs. Pure white is not in the palette and is not reintroduced for it.
 *
 * `token.status.profit.fg` on the copy button's confirmed state is left exactly where it was:
 * it already read the token layer, and "the secret is on your clipboard" is a completed action
 * — `statusToken('ok')`'s group — so the name is right as well as the value.
 */

import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { ShieldCheck, Copy, Check, RefreshCw, ArrowLeft, Key } from 'lucide-react';

import { Alert } from '../components/ds/Alert';
import { CommandButton } from '../components/ds/CommandButton';
import { LoadingState } from '../components/ds/LoadingState';
import { token } from '../design/tokens';
import { Button } from '../components/ui/Button';
import { supabase } from '../supabase';

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
    // The confirmation stays a native one. Requirement 16.5 keeps every confirmation's
    // explicit action, and `no-native-dialogs.budget.js` holds this call site — moving it to
    // `ds/ConfirmDialog` lowers that budget and belongs to the commit that does so.
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
    <div style={{ background: token.surface.canvas, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 18, padding: 36, width: "100%", maxWidth: 440 }}>

        {/* Header */}
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div
            style={{
              // The declared 10% brand wash, and `line.strong` where a 20% brand border used
              // to be — task 5.2's resolution for the same construct on Marketplace.
              background: token.brand.wash,
              border: `1px solid ${token.line.strong}`,
              borderRadius: "50%",
              width: 60,
              height: 60,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              marginBottom: 12,
            }}
          >
            <ShieldCheck size={26} aria-hidden="true" style={{ color: token.brand.base }} />
          </div>
          {/* fontSize: 20 → --text-page (heading — Q8, the page's own <h1>). */}
          <h1 className="text-page font-semibold text-content-primary">
            {hasExistingFactor ? "Two-Factor Verification" : "Setup Two-Factor Authentication"}
          </h1>
          {/* fontSize: 11 → --text-body (sentence). The monospace goes with it: this is the
              instruction a trader reads before touching anything, and it is prose. */}
          <p className="mt-1.5 text-body leading-relaxed text-content-secondary">
            {step === 1
              ? "Scan the QR code below with Google Authenticator, Authy, or 1Password"
              : "Enter the 6-digit verification code generated by your authenticator"}
          </p>
        </div>

        {/* Feedback. `severity` carries the hue, the icon and whether the announcement is
            assertive; both sentences are unchanged. `ds/Alert` has no `success` severity and
            one is not added to a shared primitive from inside a page commit, so a completed
            verification reports at `info` and its sentence says what happened. */}
        {errorMessage && (
          <Alert severity="error" title={errorMessage} className="mb-4" />
        )}

        {successMessage && (
          <Alert severity="info" title={successMessage} className="mb-4" />
        )}

        {isLoading ? (
          /* fontSize: 11 → the primitive's step. `inline` renders the label as visible text
             inside one `role="status"` region with `aria-busy`, so the sentence is not lost
             and it is announced once rather than not at all. */
          <div className="flex flex-col items-center justify-center py-10">
            <LoadingState kind="inline" label="Connecting to Supabase MFA..." />
          </div>
        ) : step === 1 ? (
          /* STEP 1: Enrollment QR Code and Secret Key */
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, borderRadius: 14, padding: 20, display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
              {qrCodeData ? (
                <div
                  style={{
                    // FUNCTIONAL, not decoration: a QR reader needs a light quiet zone to
                    // find the finder patterns. `content.primary` is the lightest value the
                    // palette declares and holds ~18:1 against the modules.
                    background: token.content.primary,
                    padding: 12,
                    borderRadius: 10,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    boxShadow: token.shadow.raised,
                  }}
                >
                  {qrCodeData.startsWith("data:image") ? (
                    <img src={qrCodeData} alt="TOTP QR Code" style={{ width: 180, height: 180, display: "block" }} />
                  ) : (
                    <div dangerouslySetInnerHTML={{ __html: qrCodeData }} style={{ width: 180, height: 180 }} />
                  )}
                </div>
              ) : (
                /* fontSize: 10 → --text-body (sentence). It is the instruction that keeps
                   enrolment possible when the QR never arrived, so it was the smallest text
                   on the screen at the moment it mattered most. */
                <div
                  className="flex items-center justify-center rounded-lg p-2.5 text-center text-body text-content-secondary"
                  style={{ height: 180, width: 180, background: token.surface.inset }}
                >
                  QR code unavailable. Please use the secret key below.
                </div>
              )}
              {/* fontSize: 10 → --text-body (sentence — a finite verb and a list). */}
              <span className="text-center text-body text-content-secondary">
                Scan with Google Authenticator, Microsoft Authenticator, or Authy
              </span>
            </div>

            {/* Secret key manual entry */}
            <div style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, borderRadius: 10, padding: "12px 14px" }}>
              {/* fontSize: 10 → --text-micro (label — Q4: it names the secret beneath it). */}
              <label
                htmlFor="totp-secret"
                className="mb-1.5 flex items-center gap-1.5 text-micro font-medium uppercase tracking-wide text-content-secondary"
              >
                <Key size={12} aria-hidden="true" style={{ color: token.brand.base }} />
                <span>Manual Setup Key:</span>
              </label>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {/* fontSize: 12 → --text-title (value — Q7 at panel level; see the header on
                    why an identifier lands here and what the role set is missing). Monospace
                    KEPT: this string has to be typed character for character. */}
                <code
                  id="totp-secret"
                  className="flex-1 overflow-x-auto rounded-md px-3 py-2 font-mono text-title tracking-widest text-brand"
                  style={{ background: token.surface.panel, userSelect: "all" }}
                >
                  {secretKey || "Generating secret..."}
                </code>
                <button
                  type="button"
                  onClick={handleCopySecret}
                  style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, borderRadius: 6, padding: "8px 10px", color: copied ? token.status.profit.fg : token.content.secondary, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}
                  title="Copy secret key"
                  aria-label="Copy secret key"
                >
                  {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
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
                /* fontSize: 22 → --text-page (control text — Q3. The role's step is a floor
                   and not a cap; see the header. Monospace KEPT: these six boxes hold the
                   code itself. */
                <input
                  key={i}
                  ref={el => (refs.current[i] = el)}
                  value={d}
                  maxLength={1}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  // Six identical boxes announced as six identical "edit text" fields is a
                  // control with no accessible name in practice. Requirement 20.1.
                  aria-label={`Verification code digit ${i + 1} of 6`}
                  onChange={e => onDigit(i, e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Backspace" && !code[i] && i > 0) {
                      refs.current[i - 1]?.focus();
                    }
                  }}
                  className="font-mono text-page font-semibold"
                  style={{
                    background: token.surface.inset,
                    border: `1px solid ${d ? token.brand.base : token.line.default}`,
                    color: token.content.primary,
                    width: 48,
                    height: 56,
                    textAlign: "center",
                    borderRadius: 8,
                    outline: "none",
                    transition: `all ${token.transition.fast}`,
                    boxShadow: d ? token.shadow.panel : "none",
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

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginTop: 4 }}>
              {/* fontSize: 10 ×2 → the primitive's step. Both were hand-built `<button>`s
                  carrying their own colour, size and family; `ds/CommandButton` reads a
                  declared step and derives its hue from the intent. The reset is
                  `destructive` because it unenrols the authenticator the trader is holding. */}
              {!hasExistingFactor ? (
                <CommandButton intent="ghost" icon={ArrowLeft} onClick={() => setStep(1)}>
                  Back to QR code
                </CommandButton>
              ) : (
                <div />
              )}

              <CommandButton intent="destructive" icon={RefreshCw} onClick={handleResetFactor}>
                Reset Authenticator
              </CommandButton>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
