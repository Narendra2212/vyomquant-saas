/**
 * Razorpay Standard Web Checkout — script loader and modal runner.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The backend has created Razorpay orders for a long time and returns `order_id` plus the
 * PUBLISHABLE `key_id` on both checkout paths, but nothing in the browser ever opened the
 * checkout with them. `StrategyMarketplace.jsx` told every Razorpay subscriber "this browser
 * cannot present its checkout"; `Billing.jsx` depended on a hosted payment link that the
 * backend could fail to create. This module is the missing half.
 *
 * WHAT NEVER APPEARS HERE
 * -----------------------
 * `RAZORPAY_KEY_SECRET`, in any form. `options.key` is the publishable key id, which the
 * server sends per-checkout — there is deliberately no `VITE_RAZORPAY_KEY_ID`, because
 * `tests/test_no_secrets_in_bundle.py` forbids that literal in frontend output and because a
 * server-supplied key cannot drift from the key that signed the order. Signature checking is
 * a server call (`api.billing.verifyPayment`); this file never verifies anything itself, since
 * a verification the browser performs proves nothing to the server.
 *
 * The `checkout.razorpay.com` origin is already in the backend's CSP `script-src`
 * (`backend_app/main.py`) and in `src-tauri/tauri.conf.json`, so no policy change is needed.
 */

const CHECKOUT_SCRIPT_SRC = 'https://checkout.razorpay.com/v1/checkout.js';

/** Memoised loader promise, so N clicks inject at most one <script>. */
let checkoutScriptPromise = null;

/**
 * Outcome discriminants returned by {@link runRazorpayCheckout}.
 *
 * Callers branch on these instead of catching exceptions, because "the trader closed the
 * modal" is an ordinary outcome and not an error — reporting it as one is what produces the
 * spurious "payment failed" banners after a deliberate cancel.
 */
export const CheckoutOutcome = {
  /** Server recomputed a matching signature. The plan activates on the webhook, not now. */
  VERIFIED: 'verified',
  /** Closed without completing payment. Nothing was charged. */
  DISMISSED: 'dismissed',
  /** Razorpay reported `payment.failed`. Nothing was charged. */
  FAILED: 'failed',
  /** Payment completed but the signature did NOT verify. Never treat as paid. */
  UNVERIFIED: 'unverified',
  /** The SDK could not be loaded or opened at all. */
  UNAVAILABLE: 'unavailable',
};

/**
 * Load `checkout.js` once and resolve with the global `Razorpay` constructor.
 *
 * Rejects when the script cannot load — offline, CSP refusal, or a content blocker, all of
 * which are indistinguishable from the page's point of view and all of which mean the same
 * thing to the trader: the modal cannot open and nothing has been charged.
 *
 * @returns {Promise<Function>} the `window.Razorpay` constructor
 */
export function loadRazorpayCheckout() {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.reject(new Error('Razorpay Checkout requires a browser environment.'));
  }
  if (window.Razorpay) return Promise.resolve(window.Razorpay);
  if (checkoutScriptPromise) return checkoutScriptPromise;

  checkoutScriptPromise = new Promise((resolve, reject) => {
    const settle = () => {
      if (window.Razorpay) {
        resolve(window.Razorpay);
      } else {
        // The request completed but the global is absent: the response was not the SDK.
        checkoutScriptPromise = null;
        reject(new Error('Razorpay Checkout loaded but did not register correctly.'));
      }
    };

    // Reuse a tag an earlier attempt already inserted rather than stacking duplicates.
    const existing = document.querySelector(`script[src="${CHECKOUT_SCRIPT_SRC}"]`);
    if (existing) {
      existing.addEventListener('load', settle, { once: true });
      existing.addEventListener('error', () => {
        checkoutScriptPromise = null;
        reject(new Error('Razorpay Checkout could not be loaded.'));
      }, { once: true });
      // Already finished loading before this listener attached.
      if (window.Razorpay) settle();
      return;
    }

    const script = document.createElement('script');
    script.src = CHECKOUT_SCRIPT_SRC;
    script.async = true;
    script.addEventListener('load', settle, { once: true });
    script.addEventListener('error', () => {
      // Cleared so a later attempt can retry; a rejected memoised promise would make one
      // transient network failure permanent for the rest of the session.
      checkoutScriptPromise = null;
      script.remove();
      reject(new Error('Razorpay Checkout could not be loaded.'));
    }, { once: true });
    document.body.appendChild(script);
  });

  return checkoutScriptPromise;
}

/** The publishable key id, under either spelling the two backend endpoints use. */
function readKeyId(session) {
  return session?.key_id || session?.razorpay_key || '';
}

/** The order id, under either spelling. */
function readOrderId(session) {
  return session?.order_id || session?.provider_reference || '';
}

/**
 * Whether a checkout session carries what Standard Checkout needs to open.
 *
 * Both values come from the server. Without either, the modal cannot be opened and the
 * caller should fall back to a hosted URL or report unavailability — never fabricate one.
 *
 * @param {object} session response body from createCheckout / library.checkout
 * @returns {boolean}
 */
export function canOpenRazorpayCheckout(session) {
  return Boolean(readKeyId(session) && readOrderId(session));
}

/**
 * Pull a trader-safe sentence out of a Razorpay `payment.failed` payload.
 *
 * Razorpay nests the useful text at `error.description` and the machine code at
 * `error.reason` / `error.code`. Everything is optional, so this narrows to a string or the
 * supplied fallback and never renders `undefined` or `[object Object]` into the UI.
 */
function describeFailure(response, fallback) {
  const error = response?.error;
  const description = typeof error?.description === 'string' ? error.description.trim() : '';
  if (description) return description;
  const reason = typeof error?.reason === 'string' ? error.reason.trim() : '';
  if (reason && reason !== 'NONE') return reason;
  return fallback;
}

/**
 * Open Standard Checkout for an existing server-created order and resolve with the outcome.
 *
 * Never rejects for an ordinary user action. A dismiss, a declined card and a failed
 * signature all resolve with a discriminated {@link CheckoutOutcome}, so the caller renders
 * one message per case instead of treating a cancel as a crash. It rejects only when the SDK
 * itself cannot be loaded or constructed, which is reported as `UNAVAILABLE`.
 *
 * FAILURE-THEN-DISMISS ORDERING
 * Razorpay fires `payment.failed` and, in most flows, leaves the modal open so the trader can
 * retry with another instrument. So a failure is recorded, not resolved: if a later attempt
 * succeeds, `handler` wins; if the trader gives up, `ondismiss` reports the recorded failure
 * rather than a bare "cancelled", which would hide the declined card that caused it.
 *
 * @param {object}   params
 * @param {object}   params.session    checkout response carrying key_id/razorpay_key + order_id
 * @param {Function} params.verify     async ({razorpay_order_id, razorpay_payment_id, razorpay_signature}) => VerificationResult
 * @param {string}   [params.name]     merchant name shown in the modal
 * @param {string}   [params.description]
 * @param {object}   [params.prefill]  { name, email, contact }
 * @param {object}   [params.notes]
 * @param {string}   [params.themeColor]
 * @returns {Promise<{status: string, message?: string, result?: object, paymentId?: string, orderId?: string}>}
 */
export async function runRazorpayCheckout({
  session,
  verify,
  name = 'Aerora Dynamics',
  description = '',
  prefill = {},
  notes = {},
  themeColor,
}) {
  if (!canOpenRazorpayCheckout(session)) {
    return {
      status: CheckoutOutcome.UNAVAILABLE,
      message: 'The payment session is missing its order reference. Nothing has been charged.',
    };
  }
  if (typeof verify !== 'function') {
    // A programming error, surfaced rather than silently skipping verification — a checkout
    // whose signature is never checked must not be presented as a completed payment.
    throw new Error('runRazorpayCheckout requires a `verify` function.');
  }

  let Razorpay;
  try {
    Razorpay = await loadRazorpayCheckout();
  } catch (err) {
    return {
      status: CheckoutOutcome.UNAVAILABLE,
      message: `${err?.message || 'Razorpay Checkout could not be loaded.'} Nothing has been charged.`,
    };
  }

  return new Promise((resolve) => {
    // One outcome per invocation. `handler` and `ondismiss` can both fire, and a second
    // resolve would be ignored by the Promise but would still run the verification call
    // twice, so the guard is here rather than relying on Promise semantics.
    let settled = false;
    const settle = (outcome) => {
      if (settled) return;
      settled = true;
      resolve(outcome);
    };

    /** Last `payment.failed` seen, reported if the trader then closes the modal. */
    let recordedFailure = null;

    const options = {
      // PUBLISHABLE key id, from the server, per checkout.
      key: readKeyId(session),
      order_id: readOrderId(session),
      name,
      // Razorpay takes the authoritative amount from the order; these are for display only.
      ...(Number.isFinite(session?.amount) ? { amount: session.amount } : {}),
      ...(session?.currency ? { currency: session.currency } : {}),
      ...(description ? { description } : {}),
      ...(Object.keys(prefill).length ? { prefill } : {}),
      ...(Object.keys(notes).length ? { notes } : {}),
      ...(themeColor ? { theme: { color: themeColor } } : {}),
      retry: { enabled: false },
      handler: async (response) => {
        // Payment authorised. Authenticity is still unproven until the server says so, so
        // nothing is reported as successful on the strength of this callback alone.
        try {
          const result = await verify({
            razorpay_order_id: response?.razorpay_order_id || readOrderId(session),
            razorpay_payment_id: response?.razorpay_payment_id || '',
            razorpay_signature: response?.razorpay_signature || '',
          });
          settle({
            status: CheckoutOutcome.VERIFIED,
            result,
            orderId: response?.razorpay_order_id,
            paymentId: response?.razorpay_payment_id,
            message: result?.detail
              || 'Payment verified. Your plan will activate once the gateway confirms it.',
          });
        } catch (err) {
          // The signature did not verify, or the verify call itself could not complete.
          // Both are reported as unverified: this client has no way to tell them apart, and
          // presenting either as a completed purchase is the one thing it must not do.
          settle({
            status: CheckoutOutcome.UNVERIFIED,
            orderId: response?.razorpay_order_id,
            paymentId: response?.razorpay_payment_id,
            message:
              err?.response?.data?.detail
              || err?.message
              || 'We could not verify this payment. Do not retry — contact support if you were charged.',
          });
        }
      },
      modal: {
        ondismiss: () => {
          if (recordedFailure) {
            settle({ status: CheckoutOutcome.FAILED, message: recordedFailure });
          } else {
            settle({
              status: CheckoutOutcome.DISMISSED,
              message: 'Checkout closed. Nothing has been charged.',
            });
          }
        },
      },
    };

    let instance;
    try {
      instance = new Razorpay(options);
    } catch (err) {
      settle({
        status: CheckoutOutcome.UNAVAILABLE,
        message: `${err?.message || 'Razorpay Checkout could not be opened.'} Nothing has been charged.`,
      });
      return;
    }

    if (typeof instance.on === 'function') {
      instance.on('payment.failed', (response) => {
        recordedFailure = describeFailure(
          response,
          'The payment did not go through. Nothing has been charged.',
        );
      });
    }

    try {
      instance.open();
    } catch (err) {
      settle({
        status: CheckoutOutcome.UNAVAILABLE,
        message: `${err?.message || 'Razorpay Checkout could not be opened.'} Nothing has been charged.`,
      });
    }
  });
}
