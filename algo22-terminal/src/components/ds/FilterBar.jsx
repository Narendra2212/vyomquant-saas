/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/FilterBar — the filters, the search, and the count that explains the table
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.14. design.md §5.1, §7.7. Requirements 11.2, 11.5.
 *
 * WHAT IT REPLACES
 * ---------------
 * Three pages filter a table today and none of them does it the same way:
 *
 *   * `TradeHistory` — four chips (`ALL BUY SELL PROFIT`) as bare `<button>`s with
 *     inline `rgba(0,212,255,0.15)` backgrounds and no search control at all, which
 *     is the half of Requirement 11.2 that is simply missing.
 *   * `Strategies` — a "Filter" button opening a pop-over with two lists (status and
 *     environment). The current filter is not visible until you open the pop-over, so
 *     a trader who filtered five minutes ago sees a short table and no reason for it.
 *   * `SignalTrace` — eight controls in a grid: three free-text inputs, three selects
 *     and two dates. The three text inputs are labelled by `placeholder` only
 *     (`"Strategy ID"`, `"Exchange ID"`, `"BTC/USDT"`) — Requirement 15.1's exact
 *     prohibition, on a filter row.
 *
 * So `filters` covers the shapes those three actually use — `segmented`, `select`,
 * `text`, `date` — and every one of them renders through `ds/Field`, which means
 * Requirement 15.1 holds on a filter row for the same reason it holds on a form: the
 * label is required, visible, and cannot be a placeholder. The pages themselves are
 * not touched here; tasks 15.1, 17.1 and 21.4 own that migration.
 *
 * THE COUNT IS LOAD-BEARING (Requirement 11.5)
 * -------------------------------------------
 * `role="status"` reading `{resultCount} of {totalCount}` is not a nicety. Requirement
 * 11.5 asks the empty state to distinguish "no trades at all" from "no trades match
 * the current filter", and those two are the same empty table — the only thing that
 * tells them apart is whether rows exist outside the filter. That is `totalCount`.
 * `emptyVariantFor()` below is that decision, written once, so `TradeHistory`,
 * `Strategies` and `SignalTrace` cannot each guess at it and cannot drift; a page
 * feeds its result straight to `ds/EmptyState`'s `variant`.
 *
 * The region is announced because the numbers change in response to something the
 * trader just did, away from where their focus is. `ds/DataTable` has its own
 * `Showing 1–50 of 312` status for pagination; this one is about the filter, and a
 * page rendering both is announcing two different facts, not the same one twice.
 *
 * SEGMENTED FILTERS ARE REAL RADIO BUTTONS
 * ---------------------------------------
 * A `<fieldset>` with a `<legend>` and native `<input type="radio">`, styled through
 * `peer-checked:`. The ARIA alternative (`role="radiogroup"` plus `role="radio"`,
 * `aria-checked` and a roving `tabIndex`) is a reimplementation of behaviour the
 * browser already has: arrow-key movement, single-selection, the group as one tab
 * stop, and a label association that needs no `aria-label` to be correct. The inputs
 * are `sr-only` rather than `hidden` so they stay focusable, and the focus ring is
 * drawn on the visible chip with `peer-focus-visible:`.
 *
 * A "clear filters" control is deliberately not a prop. Requirement 11.5's no-match
 * case needs one, but the page already owns the reset — it is the page's filter state
 * — and `actions` is where it goes, next to Refresh and Export.
 *
 * @module components/ds/FilterBar
 */

import { useId } from 'react';

import { Field } from './Field';
import { assertContract, hasText } from './devAssert';

/** The control shapes the three in-scope pages actually need. */
export const FILTER_KINDS = Object.freeze(['segmented', 'select', 'text', 'date']);

/** Marks the one live region, so a page or a test can find it without a text match. */
export const COUNT_ATTR = 'data-filter-count';

/**
 * Which of Requirement 11.5's two empty states applies, or `null` when the table has
 * rows and no empty state is due.
 *
 * The whole distinction is one comparison, and putting it here is what stops three
 * pages from each inventing it:
 *
 *   * `totalCount === 0` — there is nothing to find. `no-data`: explain what trades
 *     are and how to make one happen.
 *   * `resultCount === 0 && totalCount > 0` — the rows exist and the filter is hiding
 *     them. `no-match`: name the filter and offer to clear it. Telling this trader to
 *     go and create a trade would be telling them to duplicate data they already have.
 *
 * @param {number} resultCount Rows after filtering.
 * @param {number} totalCount Rows before filtering.
 * @returns {'no-data'|'no-match'|null}
 */
export function emptyVariantFor(resultCount, totalCount) {
  const result = Number.isFinite(resultCount) ? resultCount : 0;
  const total = Number.isFinite(totalCount) ? totalCount : 0;
  if (result > 0) return null;
  return total > 0 ? 'no-match' : 'no-data';
}

/** True for a count that can be announced: a non-negative whole number. */
function isCount(value) {
  return Number.isInteger(value) && value >= 0;
}

/**
 * One segmented group: a legend and a row of chips, backed by native radios.
 */
function SegmentedFilter({ filter, value, onChange, groupName }) {
  const options = Array.isArray(filter.options) ? filter.options : [];

  return (
    <fieldset className="flex min-w-0 flex-col gap-1 border-0 p-0">
      {/* The visible group label. Requirement 15.1 applies to a filter as much as to
          a form field: "ALL BUY SELL PROFIT" on its own does not say what it filters. */}
      <legend className="mb-1 p-0 text-small font-medium text-content-secondary">
        {filter.label}
      </legend>
      <div className="flex flex-wrap items-center gap-1">
        {options.map((option) => {
          const optionId = `${groupName}-${String(option.value)}`;
          return (
            <div key={String(option.value)} className="relative">
              <input
                type="radio"
                id={optionId}
                name={groupName}
                value={String(option.value)}
                checked={String(value ?? '') === String(option.value)}
                onChange={() => onChange(filter.id, option.value)}
                className="peer sr-only"
              />
              <label
                htmlFor={optionId}
                className={
                  'inline-flex cursor-pointer items-center rounded-sm border border-line-default '
                  + 'px-3 py-1 text-small text-content-secondary transition-colors '
                  + 'hover:border-line-strong hover:text-content-primary '
                  + 'peer-checked:border-brand peer-checked:bg-brand-wash peer-checked:text-brand '
                  // The radio is `sr-only`, so the global `:focus-visible` outline
                  // would be drawn on a 1px box nobody can see. It is moved to the
                  // chip the trader is actually looking at.
                  + 'peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 '
                  + 'peer-focus-visible:outline-brand'
                }
              >
                {option.label}
              </label>
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}

/**
 * A filter row: the filters, a labelled search input, the live count, and the page's
 * own actions.
 *
 * @param {Object} props
 * @param {Array<{id: string, label: string, kind?: string, options?: Array, placeholder?: string, hint?: string}>} props.filters
 *   Declared as data. `kind` defaults to `segmented`, which needs `options`.
 * @param {Object} props.values Current value per filter id.
 * @param {Function} props.onChange `(filterId, value) => void`.
 * @param {string} [props.search] The search text, exactly as typed.
 * @param {Function} [props.onSearchChange] `(text) => void`. Omit to render no search.
 * @param {string} [props.searchPlaceholder] An example, never the label.
 * @param {string} [props.searchLabel] The visible label. Defaults to `Search`.
 * @param {number} props.resultCount REQUIRED — rows after filtering.
 * @param {number} props.totalCount REQUIRED — rows before filtering.
 * @param {string} [props.countNoun] What is being counted, for the announcement.
 * @param {React.ReactNode} [props.actions] Refresh, Export, Clear filters.
 * @param {string} [props.className]
 */
export function FilterBar({
  filters,
  values,
  onChange,
  search,
  onSearchChange,
  searchPlaceholder,
  searchLabel = 'Search',
  resultCount,
  totalCount,
  countNoun,
  actions,
  className = '',
  ...rest
}) {
  const instanceId = useId();
  const list = Array.isArray(filters) ? filters : [];
  const current = values && typeof values === 'object' ? values : {};

  // Requirement 11.5 depends on both numbers being real. A filter bar that cannot say
  // how many rows exist outside the filter cannot tell an empty table from a filtered
  // one, and the page downstream would have to guess — which is the failure this
  // component was added to remove.
  assertContract(
    isCount(resultCount) && isCount(totalCount),
    'FilterBar: `resultCount` and `totalCount` are both required whole counts '
      + `(received ${JSON.stringify(resultCount)} and ${JSON.stringify(totalCount)}). They are what `
      + "lets `EmptyState` tell Requirement 11.5's \"no rows at all\" from \"no rows match this "
      + 'filter", and the live region has nothing to announce without them.',
  );
  assertContract(
    !isCount(resultCount) || !isCount(totalCount) || resultCount <= totalCount,
    `FilterBar: \`resultCount\` (${resultCount}) cannot exceed \`totalCount\` (${totalCount}). A `
      + 'filter narrows a set; if this fires, the two counts are being read from different sources — '
      + 'typically a client-side filtered length against a server-side page total.',
  );

  list.forEach((filter, index) => {
    const kind = hasText(filter && filter.kind) ? filter.kind : 'segmented';
    assertContract(
      Boolean(filter) && typeof filter === 'object' && hasText(filter.id),
      `FilterBar: the filter at index ${index} needs a non-empty \`id\` — it is the key `
        + '`onChange` reports against and the key `values` is read by.',
    );
    assertContract(
      Boolean(filter) && hasText(filter.label),
      `FilterBar: the filter at index ${index}${filter && filter.id ? ` (${filter.id})` : ''} needs a `
        + 'non-empty `label` (Requirement 15.1). A row of chips with no group label does not say what '
        + 'it filters, and a placeholder is not a label.',
    );
    assertContract(
      FILTER_KINDS.includes(kind),
      `FilterBar (${(filter && filter.id) || index}): \`kind\` must be one of `
        + `${FILTER_KINDS.join(' | ')}, received ${JSON.stringify(filter && filter.kind)}.`,
    );
    assertContract(
      (kind !== 'segmented' && kind !== 'select')
        || (Array.isArray(filter.options) && filter.options.length > 0),
      `FilterBar (${(filter && filter.id) || index}): \`kind="${kind}"\` requires a non-empty `
        + '`options` array. A choice control with no choices is a dead end.',
    );
  });

  const handleFilterChange = (filterId, value) => {
    if (typeof onChange === 'function') onChange(filterId, value);
  };

  const noun = hasText(countNoun) ? countNoun : null;
  const showSearch = typeof onSearchChange === 'function';

  return (
    <div
      data-filter-bar="true"
      className={`flex flex-col gap-3 ${className}`.trim()}
      {...rest}
    >
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        {list.map((filter) => {
          const kind = hasText(filter && filter.kind) ? filter.kind : 'segmented';
          const filterId = (filter && filter.id) || '';
          const value = current[filterId];

          if (kind === 'segmented') {
            return (
              <SegmentedFilter
                key={filterId}
                filter={filter}
                value={value}
                onChange={handleFilterChange}
                groupName={`${instanceId}-${filterId}`}
              />
            );
          }

          // `select`, `text` and `date` are all `ds/Field`, which is the point: there
          // is one labelling rule in this application and a filter does not get its
          // own version of it.
          return (
            <Field
              key={filterId}
              id={`${instanceId}-${filterId}`}
              label={filter.label}
              type={kind === 'select' ? 'text' : kind}
              options={kind === 'select' ? filter.options : undefined}
              placeholder={filter.placeholder}
              hint={filter.hint}
              value={value === undefined || value === null ? '' : value}
              onChange={(event) => handleFilterChange(filterId, event.target.value)}
              className="w-44"
            />
          );
        })}

        {showSearch ? (
          <Field
            id={`${instanceId}-search`}
            label={searchLabel}
            type="search"
            placeholder={searchPlaceholder}
            value={search === undefined || search === null ? '' : search}
            onChange={(event) => onSearchChange(event.target.value)}
            className="w-56"
          />
        ) : null}

        {actions ? <div className="ml-auto flex items-center gap-2">{actions}</div> : null}
      </div>

      {/* Requirement 11.5's discriminator, and the announcement that a filter did
          something. `data-result-count` / `data-total-count` are there so a page can
          reach the same two numbers `emptyVariantFor` uses without re-parsing the
          sentence, and so P18 reads numbers rather than prose. */}
      <p
        role="status"
        {...{ [COUNT_ATTR]: 'true' }}
        data-result-count={String(resultCount)}
        data-total-count={String(totalCount)}
        data-empty-variant={emptyVariantFor(resultCount, totalCount) || ''}
        className="text-micro text-content-secondary"
      >
        {noun === null ? `${resultCount} of ${totalCount}` : `${resultCount} of ${totalCount} ${noun}`}
      </p>
    </div>
  );
}

export default FilterBar;
