/**
 * ═══════════════════════════════════════════════════════════════════════════
 * `components/strategies/StrategyCard` — the owner's strategy as one dense card
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 14. Requirements 4.1, 4.2, 4.3, 4.5, 14.5, 17.1, 18.2, 18.4, 19.3.
 *
 * WHAT REQUIREMENT 4.5 ACTUALLY FORBIDS, AND WHY A CARD IS ALLOWED
 * ---------------------------------------------------------------------------
 * `.kiro/specs/vyomquant-ui-redesign/requirements.md:81`, verbatim:
 *
 *   > THE Strategies_Page SHALL NOT render strategies as oversized cards exceeding the
 *   > information density needed to display the fields listed in 4.1.
 *
 * The prohibition is on **oversized** cards whose size exceeds what the fields need — not
 * on cards. Requirement 4.1 itself asks for "a list **or** table view", and `design.md`
 * classifies 4.5 as a manual "density judgement" (`design.md:2092`, `§15.2` item 4;
 * `design.md:2492` records the verdict method as "manual review"). `DataTable` was the
 * design's chosen way of satisfying it "by construction" (`design.md:1057`,
 * `tasks.md:1107`) — the cheap way to be dense, not the only permitted one.
 *
 * So this card is built to the requirement's measure rather than around it:
 *
 *   * every one of Requirement 4.1's ten fields is on it, plus the five Task 14 adds;
 *   * one line per fact in `text-micro`, in a two-column `<dl>` whose label column is
 *     sized to its content — no metric tiles, no progress bars, no decorative artwork,
 *     no spacer rows;
 *   * the card's height is the sum of its facts. Nothing pads it.
 *
 * The card grid this page USED to have is the one 4.5 was written against, and it is worth
 * naming so this is not read as a revert: it was a ~220px-tall tile carrying eight tags,
 * three metric tiles, a progress bar and seven always-visible buttons — for ten fields,
 * three of which were fabricated (`row.pnl`, `row.win_rate`, `row.health ?? "healthy"`).
 * The density complaint was true of THAT card. It is not a property of cards.
 *
 * WHY THIS IS NOT `ds/Panel`
 * ---------------------------------------------------------------------------
 * The ownership section's entries are `Panel level={3}`, and this deliberately is not:
 *   * a card's name has to be a LINK to the strategy (the table had `rowHref`), and
 *     `Panel`'s `title` is plain text with no slot for one;
 *   * `Panel` is a region with a heading and its own state machine. A card is a row of a
 *     collection whose state is the collection's, so per-card state dispatch would be a
 *     second opinion about one read;
 *   * `Panel`'s padding is sized for a page region. Requirement 4.5 is a density rule, and
 *     this is the one component on the page where that rule is the binding constraint.
 * The chrome is tokens only — no colour literal, no `c=` prop, no raw hex. Every hue on the
 * card comes from `design/semantic.js` inside `ds/StatusBadge`, `ds/StrategyStatus` or
 * `ds/Metric`'s marker.
 *
 * NOTHING IS DERIVED HERE
 * ---------------------------------------------------------------------------
 * This component renders what it is handed. It computes no status, defaults no value and
 * substitutes nothing for an absent one: a fact the reads do not carry arrives as `null`
 * and renders `ds/Metric`'s not-available marker carrying the declaring entry's own reason
 * (Requirement 19.3). There is no `?? 0`, no `?? "healthy"` and no `"N/A"` in this file.
 */

import { Link } from 'react-router-dom';

import { NotAvailableMarker } from '../ds/Metric';
import { StatusBadge, humaniseState } from '../ds/StatusBadge';

/**
 * The card grid's track definition.
 *
 * ---------------------------------------------------------------------------
 * `auto-fill`, NOT `auto-fit` — the same keyword STRAT-CARDS-01 turned on.
 * ---------------------------------------------------------------------------
 * `auto-fit` collapses tracks that hold no item and hands the freed space to the survivors,
 * so a one-strategy account gets ONE track whose `1fr` maximum stretches it across the
 * container: a full-width band, which is the "long horizontal strip" Task 14 reported.
 * `auto-fill` keeps the empty tracks, so a single card stays a card.
 *
 * The floor is 340px rather than the ownership grid's 300px because this card carries five
 * more facts than an ownership entry; below about 340px the `<dl>`'s value column starts
 * wrapping every timestamp onto a second line, which makes the card TALLER as it narrows —
 * the opposite of what Requirement 4.5 asks for. No Tailwind utility and no token expresses
 * a grid track, and this carries no colour, so it stays an inline style — the same class of
 * value as `ds/LoadingState`'s `SKELETON_GEOMETRY`.
 *
 * Regression: tests/unit/pages/strategiesCardLayout.test.jsx
 */
export const STRATEGY_CARD_GRID_COLUMNS = Object.freeze({
  gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
});

/** The `<dl>`: label column sized to its content, value column taking the rest. */
const CARD_DL_COLUMNS = Object.freeze({ gridTemplateColumns: 'auto 1fr' });

/**
 * The card shell.
 *
 * `h-full` so every card in a row is the height of the tallest rather than floating at its
 * own height, which is what makes a grid of them read as a grid. `min-w-0` at both levels
 * is what stops a long strategy name forcing horizontal overflow (Requirement 17.1).
 */
const CARD_CLASS =
  'flex h-full min-w-0 flex-col gap-2 rounded-lg border border-line-default '
  + 'bg-surface-panel p-3';

/**
 * The ownership vocabulary, humanised from the server's own spelling.
 *
 * `library_entries.py` writes `OWNED` / `SUBSCRIBED`, and `humaniseState` turns either into
 * exactly the label Task 14 names — "Owned", "Subscribed" — with no mapping table here to
 * drift from the wire. A code this build has never seen humanises to whatever the server
 * called it rather than being dropped or renamed, so the offer can never be misreported.
 */
export const ownershipLabel = (code) => humaniseState(code);

/**
 * Task 14's listing label. Reached when the read carries a marketplace listing for the
 * entry; an owned strategy's listing state is on neither read (see `marketplaceListing` in
 * `design/pageFields.js`) and renders the marker instead.
 */
export const LISTED_LABEL = 'Listed';

/** One `<dt>`/`<dd>` pair. `data-card-field` is the card's analogue of a column key. */
function Fact({ field, label, children }) {
  return (
    <>
      <dt className="text-content-secondary">{label}</dt>
      <dd data-card-field={field} className="m-0 min-w-0 break-words text-content-primary">
        {children}
      </dd>
    </>
  );
}

/**
 * A recorded value, or the marker carrying the field's declared reason.
 *
 * The one place the card decides between a value and an absence, so there is a single
 * answer to "what does this page do with a `null`" (Requirement 14.5).
 */
function Reported({ value, label, reason }) {
  return value === null || value === undefined || value === ''
    ? <NotAvailableMarker label={label} reason={reason} />
    : value;
}

/**
 * One strategy, as a card.
 *
 * @param {Object} props
 * @param {Object} props.row A normalised row from `pages/Strategies`.
 * @param {boolean} props.focused Whether `?strategy_id=` deep-linked to this strategy.
 * @param {Array<{field: string, label: string, reason?: string, value?: *,
 *   node?: React.ReactNode}>} props.facts The `<dl>` rows, already resolved by the page.
 *   Each is either a `value` plus its declared `reason`, or a prebuilt `node` for the facts
 *   that need a component. The card does not know which fields exist — the page reads that
 *   from `design/pageFields.js` and hands the answer over, so the declaration stays the
 *   single source of truth.
 * @param {React.ReactNode} props.badges Status chips for the header (status, archived,
 *   ownership, listing). Built by the page from the same declarations.
 * @param {React.ReactNode} props.actions The row's action set, unchanged.
 * @param {string} props.nameLabel The declared label for `name`, for the marker.
 */
export function StrategyCard({ row, focused, facts, badges, actions, nameLabel }) {
  const title = row.name === null || row.name === undefined || row.name === ''
    ? <NotAvailableMarker label={nameLabel} />
    : row.name;

  return (
    <article
      data-testid={`strategy-card-${row.id}`}
      data-strategy-card={row.id}
      className={CARD_CLASS}
    >
      <header className="flex min-w-0 flex-col gap-1.5">
        <div className="flex min-w-0 items-start justify-between gap-2">
          {/* `h3`: the page's `h1` is `ds/PageHeader`'s, the section's `h2` is `ds/Panel`'s.
              The link is the card's primary affordance and the row's old `rowHref`, so it
              carries the accessible name rather than the card needing a label of its own
              (Requirement 18.4). `focus-visible` is the shared ring (Requirement 18.2). */}
          <h3 className="m-0 min-w-0 text-small font-semibold">
            <Link
              to={`/app/strategies/${row.id}`}
              data-card-field="name"
              className="min-w-0 break-words text-content-primary underline-offset-2 hover:underline focus-visible:underline"
            >
              {title}
            </Link>
          </h3>
          {/* The old card grid's "★ FOCUSED TARGET STRATEGY" banner, at card scale.
              `focused` is outside `statusToken`'s vocabulary, so it resolves to the neutral
              group: a deep-link marker is not a status and must not read as one. */}
          {focused ? (
            <StatusBadge state="focused" label="Focused target" size="sm" />
          ) : null}
        </div>

        <div data-testid={`strategy-card-badges-${row.id}`} className="flex flex-wrap items-center gap-1">
          {badges}
        </div>

        {/* NO METADATA CHIP ROW HERE, DELIBERATELY.
            An earlier draft repeated the pair, the venue and the timeframe as chips above
            the `<dl>` that already carries all three. That is the padding Requirement 4.5
            forbids — the same three values rendered twice, making the card taller for no
            added fact — and a chip has no label, so an absent one is invisible where the
            `<dl>`'s marker says which fact is missing and why (Requirement 19.3). One
            rendering per fact, in the `<dl>`. */}
      </header>

      <dl
        data-testid={`strategy-card-facts-${row.id}`}
        className="m-0 grid gap-x-3 gap-y-1 font-mono text-micro"
        style={CARD_DL_COLUMNS}
      >
        {facts.map((fact) => (
          <Fact key={fact.field} field={fact.field} label={fact.label}>
            {/* `node` is an already-built element for the facts that need a component —
                the status badges, the risk verdict, a formatted instant. Everything else
                is a plain value plus its declared reason, and goes through the one
                `Reported` arm so there is a single answer to "what does a `null` render".
                `node` is checked for `undefined` rather than for truthiness: a fact is
                free to resolve to `null`, and that must reach `Reported` rather than
                being mistaken for "no node supplied". */}
            {fact.node !== undefined
              ? fact.node
              : <Reported value={fact.value} label={fact.label} reason={fact.reason} />}
          </Fact>
        ))}
      </dl>

      {/* Requirement 4.2's actions, and Requirement 4.3's separation, exactly as the table
          rendered them: the page passes its own `ActionsCell`, which partitions through
          `lib/rowActions.js`. `mt-auto` pins the row to the bottom of a `h-full` card so
          the controls line up across the grid instead of floating under each card's last
          fact. */}
      <footer
        data-testid={`strategy-card-actions-${row.id}`}
        className="mt-auto border-t border-line-subtle pt-2"
      >
        {actions}
      </footer>
    </article>
  );
}

/**
 * The grid.
 *
 * A plain `<ul>`/`<li>`: a collection of cards IS a list, and `role="list"` on a `<ul>` is
 * the redundant role `jsx-a11y/no-redundant-roles` flags — `pages/Strategies.jsx` is held
 * at zero findings by `tests/unit/guards/a11y-ratchet.test.js`, with no waiver line.
 */
export function StrategyCardGrid({ children, label }) {
  return (
    <ul
      data-testid="strategy-card-grid"
      aria-label={label}
      className="m-0 grid list-none gap-3 p-0"
      style={STRATEGY_CARD_GRID_COLUMNS}
    >
      {children}
    </ul>
  );
}

/** One grid cell. `min-w-0` is what lets the card inside it shrink rather than overflow. */
export function StrategyCardCell({ children }) {
  return <li className="min-w-0 list-none">{children}</li>;
}

export default StrategyCard;
