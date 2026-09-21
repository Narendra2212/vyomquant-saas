/**
 * ═══════════════════════════════════════════════════════════════════════════
 * overlayRegistry — exactly ONE overlay may be open (design §11.6)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.20. Requirement 17.3. Property P34.
 *
 * WHAT THIS MODULE OWNS
 * ---------------------
 * One question: *is an overlay already open, and may this one open?* Nothing else. It
 * holds no React state, renders nothing, touches no DOM and knows nothing about focus —
 * `hooks/useFocusTrap.js` owns focus and `ds/ConfirmDialog` / `ds/Drawer` own rendering.
 * Keeping it to a plain module means the rule is enforced in one place that both overlay
 * components must pass through, rather than in a context provider some future page could
 * forget to mount.
 *
 * WHY A REGISTRY AND NOT A CONVENTION
 * -----------------------------------
 * Requirement 17.3: modals and drawers render "fully within the viewport **without
 * overlapping other open modals or drawers**". Two stacked overlays are the one case a
 * viewport clamp cannot fix — each one fits, and the pair is still unreadable. The only
 * way to make the requirement structurally true is to make the second one impossible, so
 * that is what this does.
 *
 * It also matters for safety rather than just layout. Both overlay components trap focus
 * (Requirement 18.3). Two live traps fight over `document.activeElement`, and the loser is
 * a dialog a keyboard user can see and cannot reach — on a surface whose whole job is
 * standing between a trader and a live order.
 *
 * DEV THROWS, PRODUCTION NO-OPS
 * -----------------------------
 * §11.6: "a second `open` while one is open is a development-time error and a no-op in
 * production". Both halves are deliberate:
 *
 *   * **Development throws.** A second overlay is a composition bug in the calling page,
 *     not a runtime condition to degrade around. It is the same discipline `CommandButton`
 *     applies to a missing `disabledReason` and `Panel` to a missing `environment` — the
 *     mistake is made impossible to ship rather than merely discouraged.
 *   * **Production denies.** In production the second overlay simply does not open and the
 *     conflict is logged. A trader mid-confirmation must not lose the dialog they are
 *     reading to a crash, and the first overlay — the one already on screen, possibly
 *     holding a live-deploy acknowledgement — is the one that keeps its claim. Denying the
 *     newcomer is the conservative direction.
 *
 * `import.meta.env.DEV` is read at call time rather than captured at module load, matching
 * `design/errorCopy.js`: it keeps the production bundle's constant-folded `false` and lets
 * a test drive both halves with `vi.stubEnv('DEV', …)`.
 *
 * CLAIMS ARE ID-SCOPED
 * --------------------
 * {@link releaseOverlay} only clears the registry when the id it is given is the id that
 * holds the claim. That is what makes a denied overlay's unmount harmless: `ConfirmDialog`
 * and `Drawer` both release in an effect cleanup, and a denied component releasing
 * unconditionally would hand the registry away while the granted overlay was still on
 * screen — turning one bug into two.
 *
 * @module components/ds/overlayRegistry
 */

/** The two kinds of overlay this registry arbitrates between. */
export const OVERLAY_KIND = Object.freeze({
  DIALOG: 'dialog',
  DRAWER: 'drawer',
});

/** The kind values, as a list, so callers do not re-spell them. */
export const OVERLAY_KINDS = Object.freeze(Object.values(OVERLAY_KIND));

/**
 * Thrown in development when a second overlay asks to open, or when a claim is malformed.
 *
 * A named class rather than a bare `Error` so a test can assert on the type and a future
 * error boundary can recognise it, and it carries the two descriptors so the message is
 * not the only place the facts live.
 */
export class OverlayConflictError extends Error {
  constructor(message, { requested = null, current = null } = {}) {
    super(message);
    this.name = 'OverlayConflictError';
    this.requested = requested;
    this.current = current;
  }
}

/**
 * The one claim, or `null`. Module-level on purpose — see the header.
 *
 * @type {{id: string, kind: string, label: string|null}|null}
 */
let claim = null;

/**
 * Read at call time, not captured at module load. See the header.
 *
 * @returns {boolean}
 */
function isDevelopment() {
  return import.meta.env.DEV === true;
}

/** A descriptor rendered for a log line or an error message. */
function describe(descriptor) {
  if (!descriptor) return 'none';
  const { kind, id, label } = descriptor;
  return label ? `${kind} "${label}" (${id})` : `${kind} ${id}`;
}

/**
 * A development-time error; a logged no-op in production.
 *
 * @param {string} message
 * @param {{requested?: Object|null, current?: Object|null}} context
 * @returns {false} always, so callers can `return refuse(...)`.
 */
function refuse(message, context) {
  if (isDevelopment()) {
    throw new OverlayConflictError(message, context);
  }
  console.error(`[overlayRegistry] ${message}`);
  return false;
}

/**
 * Normalise and validate a claim descriptor.
 *
 * @param {*} input
 * @returns {{id: string, kind: string, label: string|null}|null} `null` when malformed.
 */
function readDescriptor(input) {
  if (!input || typeof input !== 'object') return null;
  const id = typeof input.id === 'string' ? input.id.trim() : '';
  if (id === '') return null;
  const kind = typeof input.kind === 'string' ? input.kind.trim().toLowerCase() : '';
  if (!OVERLAY_KINDS.includes(kind)) return null;
  const label = typeof input.label === 'string' && input.label.trim() !== '' ? input.label.trim() : null;
  return { id, kind, label };
}

/**
 * Ask to open an overlay.
 *
 * @param {{id: string, kind: string, label?: string}} descriptor `id` must be stable for
 *   the lifetime of the component instance — both overlay components use `useId()`, which
 *   is stable across re-renders and unique per instance. `kind` is one of
 *   {@link OVERLAY_KIND}. `label` is optional and appears only in the dev error and the
 *   production log line, so a conflict names the two surfaces involved rather than two
 *   opaque ids.
 * @returns {boolean} `true` when the overlay may open. `false` only in production, and
 *   only when something is already open or the descriptor is malformed — in development
 *   both of those throw instead.
 */
export function requestOverlay(descriptor) {
  const requested = readDescriptor(descriptor);
  if (requested === null) {
    return refuse(
      'An overlay asked to open without a usable {id, kind} descriptor. `id` must be a '
        + `non-empty string and \`kind\` one of ${OVERLAY_KINDS.join(', ')}.`,
      { requested: descriptor ?? null, current: claim },
    );
  }

  // Re-asking with the id that already holds the claim is idempotent, not a conflict. This
  // is not a courtesy: React 18 StrictMode mounts effects, tears them down and mounts them
  // again, and a remount that re-requests before its own cleanup has run must not be read
  // as a second overlay.
  if (claim !== null && claim.id === requested.id) {
    claim = requested;
    return true;
  }

  if (claim !== null) {
    return refuse(
      `Two overlays cannot be open at once (Requirement 17.3). ${describe(claim)} is already `
        + `open, so ${describe(requested)} was refused. Close the first one before opening `
        + 'the second — a confirmation and a drawer on screen together overlap, and their two '
        + 'focus traps fight over the keyboard.',
      { requested, current: claim },
    );
  }

  claim = requested;
  return true;
}

/**
 * Release the claim, if this id holds it.
 *
 * Safe to call unconditionally from an effect cleanup: an id that does not hold the claim
 * changes nothing. See the header on why that matters.
 *
 * @param {string} id
 * @returns {boolean} `true` when this call actually cleared the claim.
 */
export function releaseOverlay(id) {
  const key = typeof id === 'string' ? id.trim() : '';
  if (key === '' || claim === null || claim.id !== key) return false;
  claim = null;
  return true;
}

/**
 * The open overlay's descriptor, or `null`.
 *
 * A copy, so a caller cannot mutate the registry's own record through it.
 *
 * @returns {{id: string, kind: string, label: string|null}|null}
 */
export function currentOverlay() {
  return claim === null ? null : { ...claim };
}

/** @returns {boolean} whether any overlay is open. */
export function isOverlayOpen() {
  return claim !== null;
}

/**
 * Drop the claim unconditionally.
 *
 * For tests, which need a clean registry between cases because the claim is module state.
 * Application code should call {@link releaseOverlay} with its own id instead — an
 * unconditional reset from a component would let a page tear down an overlay it does not
 * own.
 *
 * @returns {void}
 */
export function resetOverlayRegistry() {
  claim = null;
}
