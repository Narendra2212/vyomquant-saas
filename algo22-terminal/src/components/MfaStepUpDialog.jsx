/**
 * ═══════════════════════════════════════════════════════════════════════════
 * MfaStepUpDialog.jsx — in-flow AAL2 step-up (MFA-STEPUP-01)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE DEFECT THIS EXISTS TO CLOSE
 * ---------------------------------------------------------------------------
 * Connecting an exchange is guarded server-side by `Depends(require_aal2)`
 * (`routers/exchange.py:375` for the write, `:848` for the delete).
 * `core/dependencies.py` implements that by reading the `aal` claim off the Supabase JWT
 * and answering 403 unless it is `"aal2"`. A session only reaches `aal2` by completing a
 * Supabase MFA challenge, and the app performed that exchange in exactly one place: the
 * standalone `/app/2fa` screen.
 *
 * So a trader who filled in their API key, pressed Save and was refused got a sentence
 * telling them to leave the page, go to Security → Two-Factor Authentication, verify, come
 * back, and type everything again. The guard is correct. The dead end was ours.
 *
 * This dialog runs the same Supabase challenge INSIDE the workflow that needs it, and hands
 * control back to the caller so the original request can be retried. Nothing about the
 * server-side guard changes, and `/app/2fa` keeps enrollment, reset and recovery.
 *
 * ---------------------------------------------------------------------------
 * SECURITY PROPERTIES — the reasons this is safe, each load-bearing
 * ---------------------------------------------------------------------------
 *
 * 1. THE CODE GOES ONLY TO SUPABASE AUTH. `supabase.auth.mfa.verify()` is the sole
 *    consumer. It is never sent to an exchange (a venue's API has its own auth and would
 *    have no idea what this is), never sent to VyomQuant's backend, and never attached to
 *    the request being retried. The platform's TOTP and an exchange's credentials are
 *    different secrets protecting different things, and conflating them is the mistake this
 *    component is written to make impossible.
 *
 * 2. VERIFICATION IS SERVER-SIDE. Supabase checks the code and reissues the JWT with
 *    `aal2`. This component cannot certify itself: if it called `onVerified` without a
 *    successful `verify`, the retried request would still carry an `aal1` token and the
 *    backend would still answer 403. The client is not trusted here, by construction.
 *
 * 3. NOTHING IS PERSISTED OR LOGGED. The six digits live in component state for the
 *    duration of one attempt and are cleared on failure, on success and on close. There is
 *    no `console.log` of a code, no code in an error message, and no backup code handled
 *    here at all — recovery stays on `/app/2fa`, which already owns it.
 *
 * 4. EXPIRY AND REPLAY ARE SUPABASE'S, AND WE DO NOT WORK AROUND THEM. A challenge is
 *    single-use and time-boxed server-side. On any failure this component DISCARDS the
 *    challenge id and takes a fresh one for the next attempt, so a stale challenge is never
 *    re-presented — the behaviour `TwoFA.jsx` already established.
 *
 * 5. ATTEMPTS ARE BOUNDED CLIENT-SIDE TOO. `MAX_ATTEMPTS` stops the dialog after five
 *    wrong codes. This is not the security control — Supabase rate-limits the endpoint and
 *    that is what actually protects the factor — it is there so a mistyped code cannot be
 *    hammered into a lockout the trader does not understand, and so the UI says something
 *    true when the server starts refusing.
 *
 * ---------------------------------------------------------------------------
 * THE RACE THIS COMPONENT EXISTS TO ORDER CORRECTLY
 * ---------------------------------------------------------------------------
 * `apiClient.js` does not read the Supabase session per request. It mirrors
 * `session.access_token` into `sessionStorage.token` from a `supabase.auth.onAuthStateChange`
 * listener, gated on `carriesLiveSession(event)` (`auth/sessionEvents.js`).
 *
 * `MFA_CHALLENGE_VERIFIED` is already in that list, and its comment there anticipates
 * exactly this feature: "Reissues the access token with `aal2` ... a mirror still holding
 * the pre-challenge token would have the user pass MFA and then be refused anyway."
 *
 * But that listener fires ASYNCHRONOUSLY with respect to the `verify()` promise. Retrying
 * the instant `verify()` resolves can therefore send the OLD `aal1` token and collect a
 * second 403 — which would look to the trader like their correct code was rejected.
 *
 * So `onVerified` is not called until the mirror has actually changed, polled with a
 * bounded budget. If the budget expires the retry happens anyway: the server is the
 * authority, a stale token simply earns another refusal, and silently doing nothing would
 * be worse than a reported failure.
 */

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ShieldCheck } from 'lucide-react';

import { token, cssVar } from '../design/tokens';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { Alert } from './ds/Alert';
import { CommandButton } from './ds/CommandButton';
import { OVERLAY_KIND, releaseOverlay, requestOverlay } from './ds/overlayRegistry';
import { supabase } from '../supabase';

/** Digits in a TOTP code. Supabase rejects anything else before we could. */
const CODE_LENGTH = 6;

/**
 * Wrong codes this dialog will accept before it stops offering the control.
 *
 * Deliberately NOT the security boundary — see property 5 in the header. Five is chosen to
 * absorb a genuine mistype and a clock-skew miss without walking a user into Supabase's own
 * rate limit, which produces a less legible failure.
 */
const MAX_ATTEMPTS = 5;

/** How long to wait for `apiClient`'s token mirror to pick up the reissued session. */
const TOKEN_SETTLE_TIMEOUT_MS = 3000;
const TOKEN_SETTLE_INTERVAL_MS = 50;

/**
 * The mirror `apiClient.js` writes and every request reads.
 *
 * Read directly rather than through `App.jsx`'s `getEffectiveToken()`: `App.jsx` imports the
 * page tree, and this component is rendered from inside it, so importing that helper here
 * would close an import cycle. The key is the same one, and
 * `tests/unit/components/mfaStepUpDialog.test.jsx` pins that this and `apiClient` agree
 * about it.
 */
const readMirroredToken = () => {
  try {
    return window.sessionStorage.getItem('token');
  } catch {
    // A privacy-mode browser can throw on sessionStorage access. Treat it as "cannot
    // observe", which makes the settle below a no-op rather than a crash.
    return null;
  }
};

/**
 * Resolve once the mirrored token differs from `previous`, or the budget expires.
 *
 * @returns {Promise<boolean>} whether the mirror was observed to change.
 */
async function waitForReissuedToken(previous) {
  const deadline = Date.now() + TOKEN_SETTLE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (readMirroredToken() !== previous) return true;
    // Awaiting in a loop is the point here: this is a poll with a budget, not a batch of
    // independent requests that could run concurrently.
    await new Promise((resolve) => setTimeout(resolve, TOKEN_SETTLE_INTERVAL_MS));
  }
  return readMirroredToken() !== previous;
}

/**
 * The factor this dialog can challenge, or a reason it cannot.
 *
 * Only a `verified` TOTP factor is usable: an `unverified` one is a half-finished
 * enrollment, and challenging it would fail in a way that reads as a broken dialog rather
 * than as unfinished setup.
 */
async function resolveTotpFactor() {
  const { data, error } = await supabase.auth.mfa.listFactors();
  if (error) return { factorId: null, reason: 'unreadable', error };

  const factors = data?.totp ?? [];
  const verified = factors.find((factor) => factor?.status === 'verified');
  if (!verified) return { factorId: null, reason: 'not-enrolled', error: null };

  return { factorId: verified.id, reason: null, error: null };
}

/**
 * In-flow MFA step-up.
 *
 * @param {object} props
 * @param {boolean} props.open
 * @param {Function} props.onCancel Called by Cancel, Escape and the close control. The
 *   caller must treat this as "the operation did not happen" — this dialog never performs
 *   the guarded operation itself.
 * @param {Function} props.onVerified Called ONCE, after a successful verify AND after the
 *   reissued token is observable. The caller retries its own request here.
 * @param {string} [props.operationLabel] What the step-up is for, e.g. "connect this
 *   exchange". Rendered so the dialog explains itself rather than appearing unprompted.
 */
export function MfaStepUpDialog({ open, onCancel, onVerified, operationLabel }) {
  const instanceId = useId();
  const titleId = `${instanceId}-title`;
  const dialogRef = useRef(null);
  const inputRef = useRef(null);

  const [code, setCode] = useState('');
  const [factorId, setFactorId] = useState(null);
  const [challengeId, setChallengeId] = useState(null);
  const [status, setStatus] = useState('preparing');
  const [errorMessage, setErrorMessage] = useState('');
  const [attempts, setAttempts] = useState(0);

  const active = open === true;

  // ── Overlay registration (mutual exclusion with other modals) ──────────────
  useEffect(() => {
    if (!active) return undefined;
    requestOverlay({ id: instanceId, kind: OVERLAY_KIND.DIALOG, label: 'Two-factor verification' });
    return () => releaseOverlay(instanceId);
  }, [active, instanceId]);

  // ── Reset on close, so a reopen never shows a previous attempt's state ─────
  useEffect(() => {
    if (active) return;
    setCode('');
    setFactorId(null);
    setChallengeId(null);
    setStatus('preparing');
    setErrorMessage('');
    setAttempts(0);
  }, [active]);

  // ── Find the factor and open a challenge ───────────────────────────────────
  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;

    (async () => {
      const { factorId: id, reason, error } = await resolveTotpFactor();
      if (cancelled) return;

      if (reason === 'not-enrolled') {
        setStatus('not-enrolled');
        return;
      }
      if (reason === 'unreadable') {
        setStatus('blocked');
        setErrorMessage(
          error?.message
            || 'Your authenticator settings could not be read, so verification cannot start.',
        );
        return;
      }

      setFactorId(id);
      const { data: challenge, error: challengeError } = await supabase.auth.mfa.challenge({
        factorId: id,
      });
      if (cancelled) return;

      if (challengeError) {
        setStatus('blocked');
        setErrorMessage(challengeError.message || 'A verification challenge could not be started.');
        return;
      }

      setChallengeId(challenge.id);
      setStatus('ready');
    })();

    return () => {
      cancelled = true;
    };
  }, [active]);

  useEffect(() => {
    if (status === 'ready') inputRef.current?.focus();
  }, [status]);

  const handleCancel = useCallback(() => {
    if (status === 'verifying') return; // a verify is in flight; let it finish or fail
    if (typeof onCancel === 'function') onCancel();
  }, [onCancel, status]);

  const handleSubmit = useCallback(async () => {
    if (status !== 'ready') return;
    if (code.length !== CODE_LENGTH) return;
    if (attempts >= MAX_ATTEMPTS) return;

    setStatus('verifying');
    setErrorMessage('');

    const tokenBeforeVerify = readMirroredToken();

    try {
      // A failed attempt burns the challenge, so take a fresh one when needed.
      let activeChallengeId = challengeId;
      if (!activeChallengeId) {
        const { data: challenge, error: challengeError } = await supabase.auth.mfa.challenge({
          factorId,
        });
        if (challengeError) throw challengeError;
        activeChallengeId = challenge.id;
      }

      const { error: verifyError } = await supabase.auth.mfa.verify({
        factorId,
        challengeId: activeChallengeId,
        code,
      });
      if (verifyError) throw verifyError;

      /*
        Verified. Do NOT hand control back yet — see "THE RACE" in the header. The retry
        must not go out on the pre-challenge token.
      */
      setStatus('settling');
      setCode('');
      await waitForReissuedToken(tokenBeforeVerify);

      setStatus('verified');
      if (typeof onVerified === 'function') onVerified();
    } catch (err) {
      /*
        No `console.error(err)` here, unlike the rest of this page's handlers. A failed
        `mfa.verify` rejection is the one error object in this flow whose context includes
        the submitted code, and a browser console is a place users paste from.
      */
      const nextAttempts = attempts + 1;
      setAttempts(nextAttempts);
      setCode('');
      setChallengeId(null); // never re-present a spent challenge
      setStatus(nextAttempts >= MAX_ATTEMPTS ? 'exhausted' : 'ready');
      setErrorMessage(
        nextAttempts >= MAX_ATTEMPTS
          ? 'That code was not accepted, and this is the last attempt this dialog will take. '
            + 'Close it, check your authenticator app, and start again.'
          : err?.message || 'That code was not accepted. Check your authenticator and try again.',
      );
      inputRef.current?.focus();
    }
  }, [attempts, challengeId, code, factorId, onVerified, status]);

  useFocusTrap({
    active,
    containerRef: dialogRef,
    initialFocusRef: inputRef,
    onEscape: status === 'verifying' ? undefined : handleCancel,
  });

  if (!active) return null;

  const busy = status === 'verifying' || status === 'settling' || status === 'preparing';

  return createPortal(
    <div
      data-ds="mfa-stepup-backdrop"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: cssVar('z.modal'),
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--spacing-4)',
        background: token.surface.overlay,
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="mfa-stepup-dialog"
        data-ds-overlay={OVERLAY_KIND.DIALOG}
        data-status={status}
        style={{
          width: '100%',
          maxWidth: 420,
          background: token.surface.panel,
          border: `1px solid ${token.line.default}`,
          borderRadius: 'var(--radius-lg)',
          padding: 'var(--spacing-6)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--spacing-4)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--spacing-3)' }}>
          <ShieldCheck size={20} color={token.brand.base} aria-hidden="true" />
          <h2 id={titleId} className="text-section" style={{ margin: 0, color: token.content.primary }}>
            Two-factor verification
          </h2>
        </div>

        <p className="text-body" style={{ margin: 0, color: token.content.secondary }}>
          {operationLabel
            ? `Confirm it is you to ${operationLabel}. `
            : 'Confirm it is you to continue. '}
          Enter the {CODE_LENGTH}-digit code from your authenticator app.
        </p>

        {/*
          Stated explicitly, because this dialog appears in the middle of a workflow that is
          ALSO about credentials for a third party. A trader who has just typed an exchange
          API key has every reason to ask where this code is going.
        */}
        <p className="text-small" style={{ margin: 0, color: token.content.secondary }}>
          This code verifies you with VyomQuant only. It is never sent to the exchange.
        </p>

        {status === 'not-enrolled' ? (
          <Alert severity="guidance" title="No authenticator is set up yet">
            This action needs two-factor authentication, and this account has no authenticator
            enrolled. Set one up in Security → Two-Factor Authentication, then come back and
            try again. Nothing was saved.
          </Alert>
        ) : null}

        {errorMessage ? (
          <Alert severity="error" title={errorMessage} />
        ) : null}

        {status === 'preparing' ? (
          <p className="text-small" style={{ margin: 0, color: token.content.secondary }}>
            Starting verification…
          </p>
        ) : null}

        {(status === 'ready' || status === 'verifying' || status === 'settling') ? (
          <label className="text-micro" style={{ color: token.content.secondary }}>
            Verification code
            <input
              ref={inputRef}
              data-testid="mfa-stepup-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              // No `name`, and `autoComplete="one-time-code"` rather than `off`: this is the
              // one field a password manager SHOULD help with, and a stray `name` is what
              // gets a value remembered by the browser.
              maxLength={CODE_LENGTH}
              value={code}
              disabled={busy}
              onChange={(event) => {
                setCode(event.target.value.replace(/\D/g, '').slice(0, CODE_LENGTH));
                if (errorMessage) setErrorMessage('');
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleSubmit();
              }}
              style={{
                display: 'block',
                width: '100%',
                marginTop: 'var(--spacing-2)',
                padding: 'var(--spacing-3)',
                background: token.surface.inset,
                border: `1px solid ${token.line.strong}`,
                borderRadius: 'var(--radius-md)',
                color: token.content.primary,
                fontFamily: 'var(--font-mono)',
                fontSize: 'var(--text-section)',
                letterSpacing: '0.35em',
                textAlign: 'center',
              }}
            />
          </label>
        ) : null}

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 'var(--spacing-2)',
            marginTop: 'var(--spacing-2)',
          }}
        >
          {/*
            `disabledReason` is not optional on either control: `ds/CommandButton` THROWS on
            `disabled` without it (Requirement 15.3 — a disabled control must say why, because
            a disabled button is the one control a trader cannot interrogate). The first
            version of this dialog omitted both and took the whole page down with it under the
            test runner, which is the assertion working as designed.
          */}
          <CommandButton
            intent="secondary"
            onClick={handleCancel}
            disabled={status === 'verifying'}
            disabledReason={
              status === 'verifying'
                ? 'Your code is being checked. This finishes on its own in a moment.'
                : undefined
            }
          >
            Cancel
          </CommandButton>
          {status === 'not-enrolled' || status === 'exhausted' || status === 'blocked' ? null : (
            <CommandButton
              intent="primary"
              onClick={handleSubmit}
              disabled={code.length !== CODE_LENGTH}
              disabledReason={
                code.length !== CODE_LENGTH
                  ? `Enter all ${CODE_LENGTH} digits from your authenticator app.`
                  : undefined
              }
              loading={status === 'verifying' || status === 'settling'}
              loadingLabel="Verifying"
            >
              Verify and continue
            </CommandButton>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default MfaStepUpDialog;
