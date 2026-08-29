/**
 * AssetSelector.jsx — the DATA node's `symbol` control. Task 7.3, Requirements 11.7, 11.2,
 * 11.3, 5.2.
 *
 * Every market this offers came from `GET /api/strategy-operations/assets` on this page load.
 * There is no bundled list, no "popular pairs", no last-known-good copy in `localStorage`, and
 * no `|| "BTC/USDT"` anywhere below this comment. That is the entire point of the component:
 * SB-06 was a silently defaulted symbol, and the ten-pair fallback lists in
 * `routers/market.py` (removed by task 7.2) and `contexts/DataPipelineContext.jsx` (removed by
 * this task) were the same defect in two other places.
 *
 * The four states, kept distinguishable
 * -------------------------------------
 * A selector has four honest things to say, and collapsing any two of them misleads the author:
 *
 *   `loading`   the universe is being fetched; nothing is known yet
 *   `ready`, N>0  N markets matched, out of a stated total
 *   `ready`, N=0  **the filters matched nothing** — a real answer; the fix is the filters
 *   `error`       the platform cannot say what it trades — the fix is to retry
 *
 * The last two look identical if an error renders as an empty list, which is why the error
 * state renders an `alert` carrying the backend's own sentence and a retry, and never an empty
 * listbox. An empty listbox with no explanation reads as "this platform trades nothing".
 *
 * Typing does not choose a market
 * -------------------------------
 * `symbol` is published `required` with no default because it decides which market a saved
 * strategy trades (Requirement 5.4, SB-06). So the parameter is set **only** when the author
 * activates an option that came off the wire. A half-typed string leaves the parameter unset,
 * the form's blocking state visible and the save refused — which is the correct outcome for
 * "BTC/USD" typed at a venue that lists "BTC/USDT".
 *
 * Accessibility
 * -------------
 * An ARIA 1.2 combobox: a labelled text input with `role="combobox"`,
 * `aria-expanded`, `aria-controls` and `aria-activedescendant` over a `role="listbox"` of
 * `role="option"` children. Full keyboard operation (Up, Down, Home, End, Enter, Escape) and
 * no pointer-only affordance. Every state is announced as text through a polite live region or,
 * for a failure, an `alert` — the loading, empty and error states are told apart by their
 * words, never by their colour, and the chosen market is restated in text beside the control
 * rather than only implied by the input's contents.
 */

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';

import { useAssetSearch } from '../../hooks/useAssetSearch';
import { describeAsset, marketTypeOptionsFrom } from '../../lib/assetUniverse';

const INPUT_CLASS =
  'w-full rounded border bg-bg-elevated px-2 py-1 font-mono text-body text-text-primary ' +
  'placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent-cyan ' +
  'disabled:opacity-50';

const FILTER_CLASS =
  'w-full rounded border border-border-default bg-bg-elevated px-1.5 py-0.5 font-mono ' +
  'text-caption text-text-primary focus:outline-none focus:ring-2 focus:ring-accent-cyan ' +
  'disabled:opacity-50';

const LINK_CLASS =
  'font-mono text-caption text-text-muted underline focus:outline-none focus:ring-2 ' +
  'focus:ring-accent-cyan';

/** `aria-describedby` composes rather than replaces: the form's wiring stays intact. */
const describedBy = (...ids) => {
  const joined = ids.filter(Boolean).join(' ');
  return joined === '' ? undefined : joined;
};

/**
 * @param {object}   props
 * @param {object}   props.controlProps The id / name / disabled / required / ARIA bundle
 *   `ParameterForm` builds for this field.
 * @param {object}   props.spec         The `symbol` `ParamSpec`.
 * @param {string|null} props.value     The parameter's current value. Never prefilled.
 * @param {Function} props.onChange     `(value) => void`; `null` clears the parameter.
 * @param {boolean}  props.disabled     Read-only, e.g. a DEPLOYED version (Requirement 9.9).
 * @param {Array}    props.params       Sibling `ParamSpec`s — the source of the `market_type`
 *   filter's option set, so this file holds no market-type vocabulary of its own.
 * @param {object}   props.values       Sibling values, used to seed that filter.
 */
export function AssetSelector({
  controlProps = {},
  spec = {},
  value = null,
  onChange,
  disabled = false,
  params = [],
  values = {},
}) {
  const reactId = useId();
  const controlId = controlProps.id || `asset-${reactId}`;
  const listboxId = `${controlId}-listbox`;
  const statusId = `${controlId}-status`;
  const metaId = `${controlId}-meta`;
  const errorId = `${controlId}-error`;
  const filtersId = `${controlId}-filters`;

  const marketTypeOptions = useMemo(() => marketTypeOptionsFrom(params), [params]);

  /**
   * The `market_type` filter is seeded from the DATA block's own `market_type` parameter, so
   * an author who set the block to `swap` is not first shown spot markets. It is a filter over
   * what is *listed*, not a second copy of that parameter: changing it writes nothing back,
   * and every row states its own `market_type`, so a mismatch is visible rather than implied.
   */
  const seededMarketType = useMemo(() => {
    const declared = typeof values?.market_type === 'string' ? values.market_type : null;
    return declared && marketTypeOptions.includes(declared) ? declared : null;
  }, [values, marketTypeOptions]);

  const search = useAssetSearch({
    filters: { marketType: seededMarketType, activeOnly: true },
    enabled: !disabled,
  });

  const { filters, setFilter } = search;

  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const inputRef = useRef(null);

  // Keep the seed in step if the author changes the block's `market_type` parameter.
  useEffect(() => {
    if (seededMarketType !== null && filters.marketType !== seededMarketType) {
      setFilter('marketType', seededMarketType);
    }
    // Only when the seed itself changes; the author is free to widen the filter afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seededMarketType]);

  const assets = search.assets;

  // An active option that no longer exists after a refetch must not stay addressed.
  useEffect(() => {
    setActiveIndex((index) => (index >= assets.length ? -1 : index));
  }, [assets.length]);

  const commit = useCallback(
    (asset) => {
      if (onChange) onChange(asset.symbol);
      setQuery('');
      setFilter('search', null);
      setOpen(false);
      setActiveIndex(-1);
    },
    [onChange, setFilter],
  );

  const clear = useCallback(() => {
    if (onChange) onChange(null);
    setQuery('');
    setFilter('search', null);
    setOpen(false);
    setActiveIndex(-1);
  }, [onChange, setFilter]);

  const onQueryChange = useCallback(
    (event) => {
      const text = event.target.value;
      setQuery(text);
      // Debounced inside the hook: one request per pause, not per keystroke.
      setFilter('search', text);
      setOpen(true);
      setActiveIndex(-1);
    },
    [setFilter],
  );

  const move = useCallback(
    (delta) => {
      if (assets.length === 0) return;
      setOpen(true);
      setActiveIndex((index) => {
        const next = index + delta;
        if (next < 0) return assets.length - 1;
        if (next >= assets.length) return 0;
        return next;
      });
    },
    [assets.length],
  );

  const onKeyDown = useCallback(
    (event) => {
      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          move(1);
          break;
        case 'ArrowUp':
          event.preventDefault();
          move(-1);
          break;
        case 'Home':
          if (open && assets.length > 0) {
            event.preventDefault();
            setActiveIndex(0);
          }
          break;
        case 'End':
          if (open && assets.length > 0) {
            event.preventDefault();
            setActiveIndex(assets.length - 1);
          }
          break;
        case 'Enter':
          if (open && activeIndex >= 0 && activeIndex < assets.length) {
            event.preventDefault();
            commit(assets[activeIndex]);
          }
          break;
        case 'Escape':
          if (open) {
            event.preventDefault();
            setOpen(false);
            setActiveIndex(-1);
          }
          break;
        default:
          break;
      }
    },
    [activeIndex, assets, commit, move, open],
  );

  const optionId = (index) => `${listboxId}-option-${index}`;

  /**
   * The status sentence. One line, always present, always a full statement — the count with
   * its total, or the reason there is no count. Never a bare colour or a bare spinner.
   */
  const statusText = () => {
    if (disabled) return 'Read-only: this version cannot be edited.';
    if (search.pending) return 'Waiting for you to finish typing…';
    if (search.isLoading) {
      return search.pagesLoaded > 0
        ? 'Loading more markets…'
        : 'Loading the markets this platform can trade…';
    }
    if (search.isError) {
      // The reason is in the alert below, verbatim from the backend. This line says only what
      // is on screen, so "nothing" is never left to be inferred from an empty list.
      return 'No markets are listed: the asset list could not be loaded.';
    }
    if (search.isEmptyResult) {
      return 'No market matches these filters. The market list loaded correctly — change the search or the filters.';
    }
    if (search.isReady) {
      const shown = assets.length;
      const total = search.total === null ? shown : search.total;
      return `${shown} of ${total} matching market${total === 1 ? '' : 's'} shown.`;
    }
    return 'Type to search the markets this platform can trade.';
  };

  const selected = typeof value === 'string' && value.trim() !== '' ? value.trim() : null;

  return (
    <div
      data-testid="asset-selector"
      data-state={search.state}
      data-selected={selected || undefined}
      data-asset-count={assets.length}
      data-total={search.total === null ? undefined : search.total}
    >
      <input
        {...controlProps}
        ref={inputRef}
        type="text"
        className={INPUT_CLASS + (controlProps['aria-invalid'] ? ' border-accent-loss' : ' border-border-default')}
        role="combobox"
        autoComplete="off"
        // The HTML `required` attribute is dropped and `aria-required` kept, the same
        // distinction `ParameterForm` draws for a required BOOLEAN: this input holds a *search
        // string*, not the parameter's value, so `required` would mark the control invalid
        // while a market was legitimately chosen and valid while one was not. The requirement
        // is still announced, and the blocking state is still the form's.
        required={undefined}
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={open && activeIndex >= 0 ? optionId(activeIndex) : undefined}
        aria-describedby={describedBy(
          controlProps['aria-describedby'],
          statusId,
          search.isError ? errorId : null,
          search.sourceMeta ? metaId : null,
        )}
        // The example is a placeholder, never a value: a prefilled symbol is a market nobody
        // chose (Requirement 5.4).
        placeholder={spec.example ? `Search markets, e.g. ${spec.example}` : 'Search markets'}
        value={query}
        disabled={disabled}
        onChange={onQueryChange}
        onKeyDown={onKeyDown}
        onFocus={() => setOpen(true)}
        data-testid="asset-search-input"
      />

      {/* The chosen market restated in text. A combobox whose input holds a search string
          must say what is actually set, or the author cannot tell a search from a choice. */}
      <p className="mt-1 font-mono text-caption text-text-secondary" data-testid="asset-selected">
        {selected ? (
          <>
            <span className="text-text-primary">{selected}</span>
            {!disabled ? (
              <>
                {' · '}
                <button type="button" className={LINK_CLASS} onClick={clear} data-testid="asset-clear">
                  Clear
                </button>
              </>
            ) : null}
          </>
        ) : (
          'No market chosen yet. Choose one from the list — typing alone does not set it.'
        )}
      </p>

      {/* Filters (Requirement 11.2). Collapsed by default: search covers symbol, base and
          quote, and these narrow it further. */}
      <button
        type="button"
        className="mt-1 flex items-center gap-1 font-mono text-caption uppercase tracking-wider text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent-cyan"
        aria-expanded={filtersOpen}
        aria-controls={filtersId}
        onClick={() => setFiltersOpen((value_) => !value_)}
        data-testid="asset-filters-toggle"
      >
        <span aria-hidden="true">{filtersOpen ? '▾' : '▸'}</span>
        Filters
      </button>

      <div id={filtersId} hidden={!filtersOpen} data-testid="asset-filters">
        {filtersOpen ? (
          <div className="mt-1 grid grid-cols-2 gap-1.5">
            <label className="font-mono text-caption text-text-muted" htmlFor={`${controlId}-base`}>
              Base
              <input
                id={`${controlId}-base`}
                className={FILTER_CLASS}
                type="text"
                autoComplete="off"
                disabled={disabled}
                // Free text, because the platform publishes no currency vocabulary. Offering a
                // picked list here would mean inventing one, which is the defect this task
                // removes. An unmatched code returns an honest empty result, not an error.
                placeholder="exact, e.g. BTC"
                value={filters.base || ''}
                onChange={(event) => setFilter('base', event.target.value)}
                data-testid="asset-filter-base"
              />
            </label>

            <label className="font-mono text-caption text-text-muted" htmlFor={`${controlId}-quote`}>
              Quote
              <input
                id={`${controlId}-quote`}
                className={FILTER_CLASS}
                type="text"
                autoComplete="off"
                disabled={disabled}
                placeholder="exact, e.g. USDT"
                value={filters.quote || ''}
                onChange={(event) => setFilter('quote', event.target.value)}
                data-testid="asset-filter-quote"
              />
            </label>

            <label className="font-mono text-caption text-text-muted" htmlFor={`${controlId}-market-type`}>
              Market type
              <select
                id={`${controlId}-market-type`}
                className={FILTER_CLASS}
                disabled={disabled || marketTypeOptions.length === 0}
                value={filters.marketType || ''}
                onChange={(event) => setFilter('marketType', event.target.value || null)}
                data-testid="asset-filter-market-type"
              >
                <option value="">Any</option>
                {/* The DATA descriptor's own `market_type` options. No copy is kept here. */}
                {marketTypeOptions.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex items-end gap-1 font-mono text-caption text-text-muted" htmlFor={`${controlId}-active-only`}>
              <input
                id={`${controlId}-active-only`}
                type="checkbox"
                className="h-3.5 w-3.5 rounded border-border-default bg-bg-elevated accent-accent-cyan focus:outline-none focus:ring-2 focus:ring-accent-cyan"
                disabled={disabled}
                checked={filters.activeOnly !== false}
                onChange={(event) => setFilter('activeOnly', event.target.checked)}
                data-testid="asset-filter-active-only"
              />
              Tradeable only
            </label>
          </div>
        ) : null}
      </div>

      {/* One live region for every non-failure state, so a screen reader hears the count
          change without the control being re-focused. */}
      <p
        id={statusId}
        role="status"
        aria-live="polite"
        className="mt-1 font-mono text-caption text-text-muted"
        data-testid="asset-selector-status"
      >
        {statusText()}
      </p>

      {/* The universe moving under a paged list, stated rather than hidden. */}
      {search.universeChanged ? (
        <p
          role="status"
          className="mt-1 font-mono text-caption text-accent-gold"
          data-testid="asset-universe-changed"
        >
          The market list was refreshed while these pages were being loaded, so this list was
          assembled across two versions of it.
          {search.duplicatesDropped > 0
            ? ` ${search.duplicatesDropped} repeated market${search.duplicatesDropped === 1 ? '' : 's'} shown once.`
            : ''}
        </p>
      ) : null}

      {search.cursorRestarted ? (
        <p role="status" className="mt-1 font-mono text-caption text-accent-gold" data-testid="asset-cursor-restarted">
          The page position expired, so the list restarted from the first page.
        </p>
      ) : null}

      {search.stale ? (
        <p className="mt-1 font-mono text-caption text-text-muted" data-testid="asset-universe-stale">
          {/* Real markets with their age stated, per task 7.1's reading of Requirement 11.6. */}
          These markets are from a cached list past its refresh interval
          {search.sourceMeta && Number.isFinite(search.sourceMeta.age_seconds)
            ? ` (${Math.round(search.sourceMeta.age_seconds)}s old)`
            : ''}
          . A refresh has been scheduled.
        </p>
      ) : null}

      {/* A failure is an alert carrying the backend's own sentence, verbatim — never an empty
          list, and never a substitute list. */}
      {search.isError && search.error ? (
        <div
          id={errorId}
          role="alert"
          data-testid="asset-selector-error"
          data-code={search.error.code}
          data-status={search.error.status ?? undefined}
          className="mt-1 rounded border border-accent-loss bg-bg-elevated p-1.5"
        >
          <p className="font-mono text-caption text-accent-loss">{search.error.message}</p>
          {search.error.retryAfterSeconds !== null ? (
            <p className="font-mono text-caption text-text-muted" data-testid="asset-retry-after">
              {`The server asked for ${search.error.retryAfterSeconds}s before the next attempt.`}
            </p>
          ) : null}
          {search.error.authExpired ? (
            <p className="font-mono text-caption text-text-muted">
              Sign in again — retrying will not help.
            </p>
          ) : (
            <button
              type="button"
              className={LINK_CLASS}
              onClick={search.retry}
              data-testid="asset-retry"
              disabled={!search.error.retryable}
            >
              Retry
            </button>
          )}
        </div>
      ) : null}

      <ul
        id={listboxId}
        role="listbox"
        aria-label={`Markets matching ${spec.label || 'symbol'}`}
        hidden={!open || assets.length === 0}
        className="mt-1 max-h-56 overflow-y-auto rounded border border-border-default bg-bg-elevated"
        data-testid="asset-listbox"
      >
        {assets.map((asset, index) => (
          <li
            key={asset.symbol}
            id={optionId(index)}
            role="option"
            aria-selected={asset.symbol === selected}
            data-testid={`asset-option-${asset.symbol}`}
            data-market-type={asset.marketType || undefined}
            data-active={asset.active === null ? undefined : String(asset.active)}
            data-index={index}
            className={`cursor-pointer border-b border-border-subtle px-2 py-1 last:border-b-0 ${
              index === activeIndex ? 'bg-bg-base' : ''
            }`}
            // A pointer path in addition to the keyboard one, not instead of it.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => commit(asset)}
            onMouseEnter={() => setActiveIndex(index)}
          >
            <span className="font-mono text-body text-text-primary">{asset.symbol}</span>
            {asset.symbol === selected ? (
              <span className="ml-1 font-mono text-caption text-accent-cyan">chosen</span>
            ) : null}
            {/* The endpoint's own figures. An unpublished minimum reads "not published",
                never 0 (Requirement 11.4). */}
            <span className="block font-mono text-caption text-text-muted">
              {describeAsset(asset)}
            </span>
          </li>
        ))}
      </ul>

      {search.hasMore && !disabled ? (
        <button
          type="button"
          className={`${LINK_CLASS} mt-1`}
          onClick={search.loadMore}
          disabled={search.isLoading}
          data-testid="asset-load-more"
        >
          {search.isLoading
            ? 'Loading…'
            : `Load more (${assets.length} of ${search.total === null ? assets.length : search.total})`}
        </button>
      ) : null}

      {/* Provenance: which venues the list was assembled from, and any that failed. A market
          list quietly missing an exchange is how a universe silently narrows. */}
      {search.sourceMeta ? (
        <p id={metaId} className="mt-1 font-mono text-caption text-text-muted" data-testid="asset-source-meta">
          {`Assembled from ${(search.sourceMeta.exchanges || []).join(', ') || 'no exchange'}`}
          {(search.sourceMeta.exchanges_failed || []).length > 0
            ? ` · unavailable: ${search.sourceMeta.exchanges_failed.join(', ')}`
            : ''}
          {Number.isFinite(search.sourceMeta.universe_total)
            ? ` · ${search.sourceMeta.universe_total} markets in the universe`
            : ''}
        </p>
      ) : null}
    </div>
  );
}

export default AssetSelector;
