/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Tabs — progressive disclosure, with a keyboard model that is not a guess
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.23. design.md §5.2, §7.4.
 * Requirements 1.2, 6.4, 18.1, 18.2, 18.4.
 *
 * ═══ WHAT IT IS FOR ═══
 *
 * Requirement 6.4 says the Backtester's detailed trade list and extended statistics
 * render "behind progressive disclosure (e.g., a tab or expandable section) rather than
 * at equal visual weight" to the tier-1 figures. §7.4's layout sketch spells that out as
 * three tabs — Trades, Monthly returns, Extended statistics — under the two curves. This
 * is that control, and task 23.2 is its first consumer.
 *
 * `ds/Accordion` is the other half of the same requirement family and they are not
 * interchangeable: an accordion is ONE region that is either shown or not (Requirement
 * 15.6's advanced form settings), and tabs are N regions of which exactly one is shown.
 * A page wanting "hide this until asked" wants the accordion; a page wanting "these
 * three are alternatives" wants this.
 *
 * ═══ THE KEYBOARD MODEL, WRITTEN DOWN ONCE ═══
 *
 * A tab strip built out of buttons is *reachable* by keyboard by accident — every button
 * is a tab stop, so Tab walks through all of them. That is the failure mode this
 * component exists to avoid, not the success case: three tabs then cost three tab stops
 * to get past, and a trader tabbing from the equity curve to the trade table walks
 * through two panels they did not ask for. The APG tabs pattern replaces that with a
 * ROVING TABINDEX, and it is the whole reason this is a component rather than a row of
 * `<button>`s at each call site:
 *
 *   * **Tab** enters the strip once, landing on the SELECTED tab, and leaves it. Only
 *     one tab is in the tab order (`tabIndex={0}`); the rest are `tabIndex={-1}` and are
 *     unreachable by Tab. The next Tab press moves to the panel.
 *   * **ArrowRight / ArrowLeft** move between tabs, wrapping at both ends.
 *   * **Home / End** jump to the first and last tab.
 *   * **Enter / Space** need no handler — these are real `<button>`s, so the browser
 *     already fires `click` for both, which selects. Writing a key handler for them
 *     would be a second, divergent path to the same behaviour.
 *
 * The arithmetic is {@link nextTabIndex}, exported and pure, so the model can be tested
 * without a DOM and so there is exactly one place that decides what ArrowLeft means. It
 * is total: an unhandled key returns `null` and the event is left alone, which is what
 * keeps Tab, Shift+Tab and every browser shortcut working.
 *
 * ═══ SELECTION FOLLOWS FOCUS ═══
 *
 * Arrowing to a tab selects it. The APG allows either that or manual activation
 * (arrow to focus, Enter to select) and gives one criterion for choosing: manual
 * activation is for when showing a panel is expensive, because automatic activation
 * would fire a request per arrow press. Nothing here is expensive — the Backtester's
 * three panels are three views of one backtest result that is already in memory — so
 * automatic activation is used, and it is the form with fewer keystrokes and no hidden
 * mode.
 *
 * ═══ EVERY PANEL IS MOUNTED; THE INACTIVE ONES ARE `hidden` ═══
 *
 * This is the opposite of `ds/Accordion`, which unmounts its closed body, and the two
 * decisions are consistent because the reasons differ:
 *
 *   * Accordion's rejected alternative was `max-height: 0`, which leaves the fields
 *     VISIBLE to Tab and to every DOM query. `hidden` is not that. It computes to
 *     `display: none`, so an inactive panel is out of the tab order, out of the
 *     accessibility tree, and absent from `getByRole` — the properties that made
 *     unmounting necessary there are already true here.
 *   * Unmounting would throw away the panel's own state. `ds/DataTable` holds its sort
 *     direction, its page and its density internally, so a trader who sorts the trade
 *     table by P&L, checks Monthly returns and comes back would find the sort gone.
 *     Losing a trader's sort on a tab switch is a defect; keeping three `display: none`
 *     subtrees mounted is not.
 *   * It is what makes `aria-controls` honest. The APG asks every `role="tab"` to point
 *     at its panel, and a tab pointing at an id that is not in the document is a broken
 *     reference — worse than no reference, because assistive technology follows it.
 *     With every panel mounted, all N pairings resolve, in both directions.
 *
 * ═══ THE PANEL IS FOCUSABLE, THE TABLIST IS NAMED ═══
 *
 * `tabIndex={0}` on the panel, per the APG: a panel whose content has no focusable
 * element of its own would otherwise be unreachable, so a keyboard user could select
 * "Trades" and never get to the table. `label` is required and becomes the tablist's
 * `aria-label`, because "tablist" on its own says nothing about what the tabs switch
 * between (Requirement 18.4).
 *
 * ═══ NO COLOUR PROP ═══
 *
 * The selected tab is marked on three axes — `aria-selected`, a 2px underline in
 * `--color-brand`, and the label moving from `content-secondary` to `--color-brand` — so
 * the selection is not carried by hue alone. Those are the only colours this component
 * spends, and they come from tokens; there is no prop that can change them.
 *
 * @module components/ds/Tabs
 */

import { useCallback, useId, useRef, useState } from 'react';

import { cssVar } from '../../design/tokens';

import { assertContract, hasText } from './devAssert';

/**
 * The keys this component consumes. Everything else is left to the browser, which is
 * what keeps Tab, Shift+Tab and the platform shortcuts working inside the strip.
 */
export const TAB_NAVIGATION_KEYS = Object.freeze(['ArrowLeft', 'ArrowRight', 'Home', 'End']);

/**
 * The whole keyboard model as arithmetic: which tab a key press moves to.
 *
 * Returns `null` for a key this component does not handle, which the caller reads as
 * "do not touch this event". Total over garbage: a non-integer `count`, a `count` of
 * zero and a `currentIndex` outside the strip all resolve without throwing, because a
 * key handler that can throw is a key handler that can strand focus.
 *
 * Wrapping at both ends is the APG's optional behaviour and is chosen deliberately: a
 * three-tab strip is short enough that ArrowRight off the end reaching the first tab is
 * predictable, and it means neither end is a dead key.
 *
 * @param {string} key A `KeyboardEvent.key`.
 * @param {number} currentIndex The index of the tab that has focus.
 * @param {number} count How many tabs there are.
 * @returns {number|null} The index to move focus and selection to, or `null`.
 */
export function nextTabIndex(key, currentIndex, count) {
  if (!Number.isInteger(count) || count <= 0) return null;
  const from = Number.isInteger(currentIndex) && currentIndex >= 0 && currentIndex < count
    ? currentIndex
    : 0;

  switch (key) {
    case 'ArrowRight':
      return (from + 1) % count;
    case 'ArrowLeft':
      return (from - 1 + count) % count;
    case 'Home':
      return 0;
    case 'End':
      return count - 1;
    default:
      return null;
  }
}

/**
 * The declared tabs, cleaned. An entry with no id or no label is dropped rather than
 * rendered: a tab with no label is a blank button, and a tab with no id cannot be paired
 * with a panel.
 *
 * Duplicate ids are dropped too, keeping the first. Two tabs sharing an id would produce
 * two elements with the same DOM `id`, at which point `aria-controls` and
 * `aria-labelledby` both resolve to whichever came first and the second tab's panel is
 * unreachable.
 */
function readItems(items) {
  const list = Array.isArray(items) ? items : [];
  const seen = new Set();
  const clean = [];
  list.forEach((item) => {
    if (!item || typeof item !== 'object') return;
    if (!hasText(item.id) || !hasText(item.label)) return;
    const id = item.id.trim();
    // Whitespace would split the id across `aria-controls`, which is a space-separated
    // token list — the reference would silently point at two elements that do not exist.
    if (/\s/.test(id)) return;
    if (seen.has(id)) return;
    seen.add(id);
    clean.push({ ...item, id });
  });
  return clean;
}

/**
 * A tab strip and its panels.
 *
 * Controlled and uncontrolled are both supported, because the two consumers want
 * different things: the Backtester's results region owns which tab is open (it resets to
 * Trades on a new run), while a panel that merely offers three views of static data has
 * no reason to hold that in page state.
 *
 *   uncontrolled  <Tabs label="Backtest details" items={…} defaultValue="trades" />
 *   controlled    <Tabs label="Backtest details" items={…} value={tab} onChange={setTab} />
 *
 * @param {Object} props
 * @param {string} props.label REQUIRED. The tablist's accessible name (Requirement
 *   18.4). "Tablist" alone does not say what the tabs switch between.
 * @param {Array<{id: string, label: string, content?: React.ReactNode}>} props.items
 *   REQUIRED, non-empty. `id` must be non-empty and whitespace-free — it is half of
 *   every `aria-controls` / `aria-labelledby` pairing.
 * @param {string} [props.value] The selected tab's id. Supplying it makes the component
 *   controlled; `onChange` is then how selection changes.
 * @param {Function} [props.onChange] `(id, index) => void`. Called for a click and for
 *   every arrow-key move, since selection follows focus.
 * @param {string} [props.defaultValue] The initially selected id when uncontrolled.
 *   Defaults to the first item.
 * @param {string} [props.className] Appended to the root.
 */
export function Tabs({
  label,
  items,
  value,
  onChange,
  defaultValue,
  className = '',
  ...rest
}) {
  const generatedId = useId();
  const tabRefs = useRef([]);
  // Seeded once. `defaultValue` is the caller's opening tab when it names a real one;
  // otherwise the first tab, because a strip with no selection has no tab in the tab
  // order and is a keyboard dead end.
  const [internalValue, setInternalValue] = useState(() => {
    const seeded = readItems(items);
    if (hasText(defaultValue) && seeded.some((item) => item.id === defaultValue.trim())) {
      return defaultValue.trim();
    }
    return seeded.length > 0 ? seeded[0].id : '';
  });

  const list = readItems(items);

  assertContract(
    hasText(label),
    'Tabs: `label` is required — it is the tablist\'s accessible name (Requirement 18.4). '
      + 'Without it a screen reader announces "tab list" and nothing about what the tabs '
      + 'switch between. Pass what the strip divides, e.g. `label="Backtest details"`.',
  );

  assertContract(
    list.length > 0,
    'Tabs: `items` must be a non-empty array of `{ id, label, content }`, where `id` is '
      + 'non-empty and contains no whitespace. It is half of every `aria-controls` / '
      + `\`aria-labelledby\` pairing. Received ${
        Array.isArray(items) ? `${items.length} entr${items.length === 1 ? 'y' : 'ies'}, none usable` : typeof items
      }.`,
  );

  // Controlled iff `value` was supplied as a non-empty string. `null` and `''` are read
  // as "not chosen yet" rather than as a bad id, because a page whose tab state starts
  // empty is a normal shape and should not be a contract violation.
  const controlled = typeof value === 'string' && value.trim() !== '';
  const requested = controlled ? value.trim() : internalValue;
  const requestedIndex = list.findIndex((item) => item.id === requested);

  // A controlled `value` naming a tab that does not exist is a call-site defect — a
  // stale id kept across an `items` change, or a typo. It renders the first tab rather
  // than nothing, because a strip with no selection has no tab in the tab order at all
  // and is a keyboard dead end.
  assertContract(
    !(controlled && list.length > 0 && requestedIndex === -1),
    `Tabs${hasText(label) ? ` "${label}"` : ''}: \`value\` is "${requested}", which is not `
      + `one of the declared ids (${list.map((item) => item.id).join(', ')}). Selecting the first `
      + 'tab instead. A controlled `value` must always name a live tab — reset it in the same '
      + 'update that replaces `items`.',
  );

  const activeIndex = requestedIndex === -1 ? 0 : requestedIndex;
  const activeItem = list[activeIndex];

  const select = useCallback(
    (index) => {
      const next = list[index];
      if (!next) return;
      if (!controlled) setInternalValue(next.id);
      if (typeof onChange === 'function') onChange(next.id, index);
    },
    // `list` is rebuilt every render, so it is depended on by identity rather than by
    // value; the callback is cheap and this is correct rather than merely stable.
    [controlled, list, onChange],
  );

  const handleKeyDown = useCallback(
    (event, index) => {
      const target = nextTabIndex(event.key, index, list.length);
      // `null` means a key this component does not own. Returning without touching the
      // event is what leaves Tab, Shift+Tab and the platform shortcuts intact.
      if (target === null) return;
      event.preventDefault();
      select(target);
      // Focus moves with selection: the roving tabindex is only half the pattern, and
      // without this the newly selected tab would be the one tab stop while focus sat on
      // an element that is now `tabIndex={-1}`.
      tabRefs.current[target]?.focus();
    },
    [list.length, select],
  );

  // In production the asserts above log and return, so a `Tabs` with nothing to render
  // still has to render something. Nothing is the honest answer: an empty strip with an
  // empty panel would be a labelled region containing a contradiction.
  if (!activeItem) return null;

  const tabDomId = (id) => `${generatedId}tab-${id}`;
  const panelDomId = (id) => `${generatedId}panel-${id}`;

  return (
    <div
      data-ds="tabs"
      data-tabs-active={activeItem.id}
      className={`flex min-w-0 flex-col ${className}`.trim()}
      {...rest}
    >
      <div
        role="tablist"
        aria-label={hasText(label) ? label : undefined}
        data-ds="tablist"
        className="flex min-w-0 items-center gap-1 overflow-x-auto border-b border-line-default"
      >
        {list.map((item, index) => {
          const selected = index === activeIndex;
          return (
            <button
              key={item.id}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              id={tabDomId(item.id)}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={panelDomId(item.id)}
              // The roving tabindex. Exactly one tab is reachable by Tab; the strip is
              // traversed with the arrow keys once entered. See the module docblock.
              tabIndex={selected ? 0 : -1}
              data-tab-id={item.id}
              data-tab-selected={selected ? 'true' : 'false'}
              onClick={() => select(index)}
              onKeyDown={(event) => handleKeyDown(event, index)}
              className="shrink-0 whitespace-nowrap px-3 py-2 text-title font-medium transition-colors"
              // Inline rather than `border-b-2` + `border-transparent`: the underline's
              // colour is a token, and composing a class name from the selected state
              // would put the hue behind string concatenation. `marginBottom: -1px`
              // lifts the underline over the tablist's own bottom border so the two
              // occupy one line rather than stacking to 3px.
              style={{
                borderBottomWidth: '2px',
                borderBottomStyle: 'solid',
                borderBottomColor: selected ? cssVar('color.brand') : 'transparent',
                marginBottom: '-1px',
                color: selected ? cssVar('color.brand') : cssVar('color.content.secondary'),
              }}
            >
              {item.label}
            </button>
          );
        })}
      </div>

      {/* Every panel is mounted and the inactive ones are `hidden` — see the module
          docblock for why this differs from `ds/Accordion`. `hidden` computes to
          `display: none`, so an inactive panel holds no tab stop and is absent from the
          accessibility tree, while its content keeps whatever state it owns. */}
      {list.map((item, index) => (
        <div
          key={item.id}
          id={panelDomId(item.id)}
          role="tabpanel"
          aria-labelledby={tabDomId(item.id)}
          hidden={index !== activeIndex}
          // The APG's requirement, not decoration: a panel whose content happens to hold
          // nothing focusable would otherwise be unreachable, so a keyboard user could
          // select "Trades" and never arrive at the table.
          tabIndex={0}
          data-ds="tabpanel"
          data-tab-id={item.id}
          className="mt-3 min-w-0"
        >
          {item.content}
        </div>
      ))}
    </div>
  );
}

export default Tabs;
