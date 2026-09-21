/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/OverflowMenu — the row's trailing `⋮`, and the divider inside it
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 17.2. design.md §7.2, §5.2, §11.7.
 * Requirements 4.3, 15.3, 18.1, 18.4.
 *
 * §7.2 draws a table row whose non-destructive actions sit inline and whose destructive
 * and live-transition actions sit below a divider in a trailing overflow menu:
 *
 *   [ Backtest ] [ Edit ] [ Duplicate ] [ Signal Trace ]    ⋮
 *                                                           ├─ ─────────────
 *                                                           ├─ Deploy live      (live)
 *                                                           └─ Archive strategy (destructive)
 *
 * No primitive rendered that. `ds/ActionControl` is not it — it renders a single
 * `{label, to}` spec for `EmptyState` / `ErrorState` and its own docblock says the barrel
 * must not export it — and `ds/Drawer` is a modal side panel, which is a different
 * interaction and the wrong weight for two entries. This is that primitive, once.
 *
 * ═══ WHY THE SEPARATION IS A GROUP AND NOT ONLY A RULE ═══
 *
 * Requirement 4.3 asks for destructive and live-transition actions to be *visually
 * separated*. A horizontal rule satisfies that for a sighted trader and says nothing at
 * all to a screen reader, which would hear a run of menu entries with no hint that two of
 * them place real orders or take a strategy out of the library. So the separated entries
 * are wrapped in a `role="group"` carrying {@link SEPARATED_GROUP_LABEL} as its
 * accessible name, *and* preceded by a `role="separator"`. The separation is then a fact
 * in both modalities rather than a fact about a border colour.
 *
 * The divider is rendered whenever a separated entry exists, including when it is the
 * menu's first child — which is exactly what §7.2 draws. There it is the boundary against
 * the inline controls in the row, not a boundary between two groups inside the menu, and
 * dropping it would erase the only mark the sketch has.
 *
 * ═══ WHY IT POSITIONS ITSELF WITH `position: fixed` ═══
 *
 * The intended consumer is a `ds/DataTable` cell, and `DataTable`'s scroll wrapper is
 * `overflow-x-auto`. CSS computes `overflow-y: visible` to `auto` when the other axis is
 * not visible, so an absolutely-positioned panel inside that wrapper is clipped on BOTH
 * axes: the menu would open invisible, or open a scrollbar inside the table. A fixed
 * panel measured from the trigger's own bounding box escapes the clip without the
 * consumer having to know it exists.
 *
 * The rect is re-measured on scroll (captured, so a scroll in ANY ancestor is seen) and
 * on resize, so the panel tracks its trigger rather than detaching from it. Both
 * listeners are attached only while the menu is open. The offsets and the `z-index` are
 * inline for the reason `ds/Tooltip` gives for the same choice: the utilities that would
 * express them have no other call site in `src/`, and a class that exists in one file is a
 * class the `dead-tailwind` guard has to reason about for no benefit. The `z-index` is
 * still the `z.dropdown` token.
 *
 * ═══ WHY IT IS NOT AN OVERLAY ═══
 *
 * `ds/overlayRegistry` enforces Requirement 17.3 — one modal at a time — for
 * `ConfirmDialog` and `Drawer`. This menu is **not** modal: it traps nothing, scrims
 * nothing, and one Escape or one click elsewhere closes it. Registering it would mean an
 * open row menu could refuse to let a confirmation open, which is backwards — every
 * separated entry's job is to open one.
 *
 * ═══ KEYBOARD (Requirement 18.1, §11.7) ═══
 *
 *   * **Enter / Space / ArrowDown** on the trigger opens the menu and focuses the first
 *     entry; **ArrowUp** opens it and focuses the last.
 *   * **ArrowDown / ArrowUp** move between entries, wrapping at both ends;
 *     **Home / End** jump to the first and last.
 *   * **Escape** closes and returns focus to the trigger. So does activating an entry, so
 *     a trader who opens a confirmation and cancels it lands back on the control they
 *     started from rather than at the top of the page.
 *   * **Tab** closes the menu and is then left to the browser, which moves focus to the
 *     next control after the trigger. A menu that swallowed Tab would be a focus trap,
 *     and this is not a modal.
 *
 * The arithmetic is {@link nextMenuIndex}, which is pure, exported, and delegates to
 * `ds/Tabs`'s `nextTabIndex` after mapping the vertical keys onto the horizontal ones.
 * One implementation of "wrap at both ends, tolerate a garbage index" serves both
 * components; a second copy would be a second chance to get the modulo wrong.
 *
 * @module components/ds/OverflowMenu
 */

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { MoreVertical } from 'lucide-react';

import { cssVar } from '../../design/tokens';
import { ENVIRONMENT, statusToken } from '../../design/semantic';
import { assertContract, hasText, isComponentType } from './devAssert';
import { nextTabIndex } from './Tabs';

/**
 * The three treatments an entry can carry. Deliberately `ds/ConfirmDialog`'s
 * `CONFIRM_INTENTS` vocabulary, spelled the same way, because a `live` entry opens a
 * `live` dialog and a `destructive` entry opens a `destructive` one — two vocabularies for
 * one escalation would let the menu and the dialog disagree about how serious an action is.
 */
export const MENU_INTENTS = Object.freeze(['neutral', 'live', 'destructive']);

/**
 * The accessible name of the separated group (Requirements 4.3, 18.4).
 *
 * Names both reasons an entry lands there — it is destructive, or it starts live trading —
 * because the group holds both, and a trader hearing only one would be told the wrong
 * thing about the other.
 */
export const SEPARATED_GROUP_LABEL = 'Destructive and live-trading actions';

/** Vertical menu keys → the horizontal keys `nextTabIndex` already implements. */
const KEY_ALIAS = Object.freeze({
  ArrowDown: 'ArrowRight',
  ArrowUp: 'ArrowLeft',
  Home: 'Home',
  End: 'End',
});

/** The keys this component consumes for navigation. Everything else is the browser's. */
export const MENU_NAVIGATION_KEYS = Object.freeze(Object.keys(KEY_ALIAS));

/**
 * Which entry a key press moves focus to.
 *
 * Returns `null` for a key this component does not own, which the caller reads as "do not
 * touch this event". Total over garbage — a non-integer `count`, a `count` of zero and an
 * out-of-range `currentIndex` all resolve without throwing, because a key handler that can
 * throw is a key handler that can strand focus inside an open menu.
 *
 * @param {string} key A `KeyboardEvent.key`.
 * @param {number} currentIndex The index of the entry that has focus.
 * @param {number} count How many entries there are.
 * @returns {number|null}
 */
export function nextMenuIndex(key, currentIndex, count) {
  const alias = Object.prototype.hasOwnProperty.call(KEY_ALIAS, key) ? KEY_ALIAS[key] : null;
  return alias === null ? null : nextTabIndex(alias, currentIndex, count);
}

/**
 * The declared entries, cleaned.
 *
 * An entry with no id, no label or no `onSelect` is DROPPED rather than rendered: an entry
 * with no label is a blank row a keyboard user can focus and learn nothing from, and one
 * with no handler is a dead control. Duplicate ids are dropped too, keeping the first —
 * two entries sharing an id would give two elements the same DOM `id`, at which point the
 * `aria-describedby` reference on a disabled entry resolves to whichever came first.
 *
 * The separated entries are moved to the end, so the render order matches §7.2's sketch
 * whatever order the call site declares them in. `Array.prototype.sort` is stable, so the
 * relative order inside each group survives.
 */
function readItems(items) {
  const source = Array.isArray(items) ? items : [];
  const seen = new Set();
  const clean = [];

  source.forEach((item) => {
    if (!item || typeof item !== 'object') return;
    if (!hasText(item.id) || !hasText(item.label)) return;
    if (typeof item.onSelect !== 'function') return;
    const id = item.id.trim();
    if (seen.has(id)) return;
    seen.add(id);
    clean.push({
      ...item,
      id,
      separated: item.separated === true,
      disabled: item.disabled === true,
      intent: MENU_INTENTS.includes(item.intent) ? item.intent : 'neutral',
    });
  });

  return clean.sort((a, b) => Number(a.separated) - Number(b.separated));
}

/**
 * An entry's colour, from `design/semantic.js` and from nowhere else.
 *
 * `live` takes `env.live` and `destructive` takes `status.error`, which is §7.2's own
 * instruction. `neutral` resolves to the primary content token, because a neutral action
 * is not a state and must not read as one.
 */
function intentColour(intent) {
  if (intent === 'live') return ENVIRONMENT.LIVE.fg;
  if (intent === 'destructive') return statusToken('error').fg;
  return cssVar('color.content.primary');
}

const PANEL_CLASS =
  'flex min-w-0 flex-col rounded-md border border-line-default bg-surface-raised py-1 shadow-raised';

const TRIGGER_CLASS =
  'inline-flex items-center justify-center rounded-sm border border-line-strong p-1 '
  + 'text-content-secondary transition-colors hover:text-content-primary';

const ITEM_CLASS =
  'flex w-full min-w-0 items-center gap-2 whitespace-nowrap px-3 py-1.5 text-left text-body '
  + 'transition-colors hover:bg-surface-inset';

/**
 * A trailing overflow menu for a row's actions.
 *
 * @param {Object} props
 * @param {string} props.label The trigger's accessible name and the menu's
 *   (Requirement 18.4). REQUIRED, and it must name the *row*: forty rows whose menus are
 *   all called "More actions" give a screen-reader user forty indistinguishable controls.
 *   Pass ``label={`More actions for ${row.name}`}``.
 * @param {Array<{id: string, label: string, onSelect: Function,
 *   intent?: 'neutral'|'live'|'destructive', separated?: boolean, disabled?: boolean,
 *   disabledReason?: string, icon?: React.ComponentType}>} props.items The entries. One
 *   missing `id`, `label` or `onSelect` is dropped — see {@link readItems}. `separated`
 *   puts the entry below the divider, and the CALLER decides it, from
 *   `lib/rowActions.js`'s classifier: which actions are destructive is a domain fact, not
 *   a rendering one.
 * @param {string} [props.className] Applied to the trigger's wrapper.
 */
export function OverflowMenu({ label, items, className = '', ...rest }) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [anchor, setAnchor] = useState(null);

  const triggerRef = useRef(null);
  const panelRef = useRef(null);
  const itemRefs = useRef([]);

  const generatedId = useId();
  const menuId = `${generatedId}menu`;
  const triggerId = `${generatedId}trigger`;

  const list = readItems(items);

  // Requirement 18.4. Without a name the trigger is an icon-only button that announces
  // "button" and nothing else, and every row's is identical.
  assertContract(
    hasText(label),
    'OverflowMenu requires a `label` — it is the trigger\'s accessible name and the menu\'s '
      + '(Requirement 18.4). An icon-only trigger announces nothing, and a table whose rows '
      + 'share one menu label gives a screen-reader user no way to tell them apart. Pass '
      + '`label={`More actions for ${row.name}`}`.',
  );

  // Requirement 15.3, the rule `ds/CommandButton` already enforces: a disabled control must
  // say why. A disabled MENU entry is the worst case of it — it cannot be hovered for a
  // title and it disappears the moment the menu closes.
  const unexplained = list.filter((item) => item.disabled && !hasText(item.disabledReason));
  assertContract(
    unexplained.length === 0,
    `OverflowMenu "${label}" has disabled entries carrying no \`disabledReason\`: `
      + `${unexplained.map((item) => item.id).join(', ')}. Requirement 15.3: a disabled `
      + 'control must display why. Pass `disabledReason`, or leave the entry enabled and fail '
      + 'the action with a reason instead.',
  );

  const close = useCallback((returnFocus) => {
    setOpen(false);
    setAnchor(null);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  /** The trigger's box in viewport coordinates. Re-read, never cached — see the header. */
  const measure = useCallback(() => {
    const node = triggerRef.current;
    if (!node || typeof node.getBoundingClientRect !== 'function') return;
    const rect = node.getBoundingClientRect();
    setAnchor({ top: rect.bottom, right: rect.right });
  }, []);

  const openAt = useCallback(
    (edge) => {
      if (list.length === 0) return;
      measure();
      setActiveIndex(edge === 'last' ? list.length - 1 : 0);
      setOpen(true);
    },
    [list.length, measure],
  );

  // Focus follows `activeIndex` while the menu is open. `useLayoutEffect` so the move
  // happens in the frame the panel appears: a menu that focuses one paint later is a menu
  // whose first keypress can land on the trigger instead.
  useLayoutEffect(() => {
    if (!open) return;
    itemRefs.current[activeIndex]?.focus();
  }, [open, activeIndex]);

  // Track the trigger, and close on an outside pointer. Both only while open.
  useEffect(() => {
    if (!open) return undefined;

    const reposition = () => measure();
    const onPointerDown = (event) => {
      const inTrigger = triggerRef.current?.contains(event.target);
      const inPanel = panelRef.current?.contains(event.target);
      // Focus is NOT returned to the trigger here: the trader has just pointed somewhere
      // else, and yanking focus back would fight them for it.
      if (!inTrigger && !inPanel) close(false);
    };

    // Captured, because `scroll` does not bubble and the scroll that matters happens in
    // `DataTable`'s own wrapper rather than on `window`.
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    document.addEventListener('pointerdown', onPointerDown, true);

    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
      document.removeEventListener('pointerdown', onPointerDown, true);
    };
  }, [open, measure, close]);

  const activate = useCallback(
    (item) => {
      if (item.disabled) return;
      // Closed and focus restored BEFORE the handler runs, so a handler that opens a
      // `ConfirmDialog` claims focus from the trigger — which is where the dialog returns
      // it when the trader cancels.
      close(true);
      item.onSelect();
    },
    [close],
  );

  const handleTriggerKeyDown = (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      openAt('first');
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      openAt('last');
    }
  };

  const handleMenuKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close(true);
      return;
    }
    if (event.key === 'Tab') {
      // Not swallowed — see the header. Closing without moving focus lets the browser take
      // it from the trigger to the next control.
      close(false);
      return;
    }
    const target = nextMenuIndex(event.key, activeIndex, list.length);
    if (target === null) return;
    event.preventDefault();
    setActiveIndex(target);
  };

  /** One entry. `index` is its position in the whole list, which is what focus roves over. */
  const renderItem = (item, index) => {
    const Icon = isComponentType(item.icon) ? item.icon : null;
    const reasonId = item.disabled ? `${generatedId}reason-${item.id}` : undefined;

    return (
      <button
        key={item.id}
        ref={(node) => {
          itemRefs.current[index] = node;
        }}
        type="button"
        role="menuitem"
        // Roving tabindex: exactly one entry is a tab stop and the arrow keys traverse the
        // rest. `aria-disabled` rather than `disabled`, so a disabled entry stays focusable
        // and its reason stays reachable — `disabled` would hide the entry from the keyboard
        // and make its explanation unreadable, which is what Requirement 15.3 is against.
        tabIndex={index === activeIndex ? 0 : -1}
        aria-disabled={item.disabled ? true : undefined}
        aria-describedby={reasonId}
        data-action={item.id}
        data-menu-intent={item.intent}
        data-menu-separated={item.separated ? 'true' : 'false'}
        className={ITEM_CLASS}
        style={{
          color: intentColour(item.intent),
          opacity: item.disabled ? 0.45 : undefined,
          cursor: item.disabled ? 'not-allowed' : undefined,
        }}
        onClick={() => activate(item)}
        onFocus={() => setActiveIndex(index)}
      >
        {Icon === null ? null : <Icon size={14} aria-hidden="true" />}
        <span className="min-w-0 flex-1">{item.label}</span>
        {item.disabled ? (
          <span id={reasonId} className="sr-only">{item.disabledReason}</span>
        ) : null}
      </button>
    );
  };

  // In production `assertContract` logs and returns, so a menu with a broken contract still
  // has to render something. Nothing is the honest answer for an empty list: a trigger that
  // opens an empty panel is a control that does nothing.
  if (list.length === 0) return null;

  const ordinary = list.filter((item) => !item.separated);
  const separated = list.filter((item) => item.separated);

  return (
    <span
      data-ds="overflow-menu"
      className={`relative inline-flex ${className}`.trim()}
      {...rest}
    >
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        aria-label={hasText(label) ? label : undefined}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        data-ds="overflow-menu-trigger"
        className={TRIGGER_CLASS}
        onClick={() => (open ? close(true) : openAt('first'))}
        onKeyDown={handleTriggerKeyDown}
      >
        <MoreVertical size={14} aria-hidden="true" />
      </button>

      {open ? (
        <div
          ref={panelRef}
          id={menuId}
          role="menu"
          aria-label={hasText(label) ? label : undefined}
          // Programmatically focusable, never a tab stop. The `menu` role is an interactive
          // one, so it has to be focusable to be valid — but focus lives on the ENTRIES here
          // (roving tabindex, not `aria-activedescendant`), and a `tabIndex={0}` container
          // would add a second stop that announces the menu and does nothing. `ds/Drawer`
          // takes `tabIndex={-1}` on its dialog for the same reason.
          tabIndex={-1}
          data-ds="overflow-menu-panel"
          className={PANEL_CLASS}
          onKeyDown={handleMenuKeyDown}
          style={{
            position: 'fixed',
            // `anchor` is `null` until the first measurement. jsdom reports a zero rect,
            // which is a legitimate measurement and is used as one.
            top: anchor === null ? 0 : `${anchor.top + 4}px`,
            // Right-aligned to the trigger: the menu is the row's last cell, so growing
            // leftwards is what keeps it on screen.
            left: anchor === null ? 0 : `${anchor.right}px`,
            transform: 'translateX(-100%)',
            zIndex: cssVar('z.dropdown'),
            minWidth: '12rem',
          }}
        >
          {ordinary.map((item, index) => renderItem(item, index))}

          {separated.length === 0 ? null : (
            <>
              {/* §7.2's divider. Rendered even when it is the menu's first child — see
                  the header. */}
              <div
                role="separator"
                aria-orientation="horizontal"
                data-ds="overflow-menu-separator"
                className="my-1 border-t border-line-default"
              />
              <div
                role="group"
                aria-label={SEPARATED_GROUP_LABEL}
                data-ds="overflow-menu-separated"
              >
                {separated.map((item, offset) => renderItem(item, ordinary.length + offset))}
              </div>
            </>
          )}
        </div>
      ) : null}
    </span>
  );
}

export default OverflowMenu;
