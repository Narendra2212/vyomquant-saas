/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Accordion — advanced settings, collapsed by default, as declared data
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.15. design.md §11.2. Requirement 15.6.
 *
 * Requirement 15.6 says advanced or infrequently-used settings render collapsed. The
 * easy reading of that is "wrap the bottom half of the form in a disclosure", and the
 * easy reading is what makes it unverifiable: whether the *right* fields are down
 * there is then a matter of where somebody happened to put a closing tag.
 *
 * So the advanced set is declared as data — `fields` is the list of field ids this
 * accordion is responsible for — and the collapsed set is observable from the DOM.
 * P31 ("advanced fields, and only advanced fields, start collapsed", task 6.17) is
 * then a set equality between two things that both exist: `partitionAdvanced(form)`
 * on the declaration side, and the fields absent from the document on the render
 * side. Two independent checks hold the two halves together:
 *
 *   * A `Field` inside the accordion whose id is not in `fields` throws in
 *     development. It is collapsing without having been declared, which is exactly
 *     the case that would slip past a set-equality check reading only the
 *     declaration. It fires on the first open rather than on mount, because a closed
 *     accordion has not rendered its children yet — which is the point.
 *   * `fields` is published as `data-advanced-fields`, so a test never has to be
 *     handed the descriptor separately to know what this accordion claimed.
 *
 * COLLAPSED MEANS NOT RENDERED
 * ---------------------------
 * The body is unmounted while closed, not hidden with CSS. A `max-height: 0` panel
 * still holds focusable controls: Tab lands the trader in a field they cannot see,
 * and a "collapsed" field is still found by every DOM query, which would leave P31
 * with nothing to measure. Unmounting is safe here because `ds/Field` is controlled —
 * the values live in the page's state and survive the toggle.
 *
 * WHY NOT WRAP `components/ui/Accordion.jsx`
 * -----------------------------------------
 * That component exists and stays where it is (task 6.24 retokens it in place; this
 * task does not touch it). It is a different component wearing the same noun:
 *
 *   * Its API is an FAQ. `items: [{question, answer}]` with `defaultOpen` an array of
 *     *indices* — there is no children slot to put a form in, and no per-form field
 *     set to declare. Reaching this task's contract through it would mean passing a
 *     single item whose `answer` is the form, and threading `fields` past an API that
 *     has no place for it.
 *   * It keeps closed content mounted, at `max-height: 0; opacity: 0` — the exact
 *     arrangement described above, so P31 could not read a collapsed set from it and
 *     a keyboard user would tab into invisible advanced fields.
 *   * It is multi-open (a `Set` of open indices) where advanced settings are one
 *     region with one boolean.
 *   * Its palette is hardcoded (`border-accent-cyan/40`, `bg-bg-surface`) and it
 *     animates `max-height`/`opacity` over 300ms on every open.
 *
 * Wrapping it would mean adopting all four and then working around them. It has ~one
 * consumer shape (the landing FAQ) and this one has another; they are better as two
 * small components than one component with two modes.
 *
 * @module components/ds/Accordion
 */

import { createContext, useContext, useId, useState } from 'react';

import { ChevronDown } from 'lucide-react';

import { assertContract, hasText } from './devAssert';

/**
 * Where the declared advanced set is published. Read by P31's DOM sweep so the
 * attribute name lives in one place.
 */
export const ADVANCED_FIELDS_ATTR = 'data-advanced-fields';

/**
 * `null` outside an accordion; the declared field-id list inside one. `ds/Field`
 * reads it to check that it was declared before it collapses.
 */
const AdvancedFieldsContext = createContext(null);

/**
 * The declared advanced set for the field currently rendering, or `null` when the
 * field is not inside an advanced-settings accordion.
 *
 * @returns {ReadonlyArray<string>|null}
 */
export function useDeclaredAdvancedFields() {
  return useContext(AdvancedFieldsContext);
}

/**
 * Split a form descriptor into the fields that render open and the fields that
 * render collapsed.
 *
 * This is the "per-form declared advanced-field set" as data. A form declares its
 * fields once —
 *
 *     const PAPER_SESSION_FORM = [
 *       { id: 'initial-capital', label: 'Initial simulated capital' },
 *       { id: 'slippage-bps',    label: 'Slippage', advanced: true },
 *       { id: 'fee-bps',         label: 'Taker fee', advanced: true },
 *     ];
 *
 * — and both the render and P31 read the same list, so "which fields are advanced"
 * cannot be answered differently in the form and in the test. Ids are deduplicated
 * and order is preserved: the partition is a partition, and a field cannot appear on
 * both sides.
 *
 * @param {Array<{id: string, advanced?: boolean}>} fields
 * @returns {{standard: ReadonlyArray<string>, advanced: ReadonlyArray<string>}}
 */
export function partitionAdvanced(fields) {
  const list = Array.isArray(fields) ? fields : [];
  const standard = [];
  const advanced = [];
  const seen = new Set();
  list.forEach((field) => {
    if (!field || typeof field !== 'object' || !hasText(field.id)) return;
    const id = field.id;
    if (seen.has(id)) return;
    seen.add(id);
    (field.advanced === true ? advanced : standard).push(id);
  });
  return { standard: Object.freeze(standard), advanced: Object.freeze(advanced) };
}

/**
 * A collapsed-by-default disclosure for a form's advanced settings.
 *
 * @param {Object} props
 * @param {string} props.title REQUIRED — the visible name of the region and the
 *   accessible name of the toggle.
 * @param {ReadonlyArray<string>} props.fields REQUIRED — the ids of the fields this
 *   accordion collapses. Typically `partitionAdvanced(FORM).advanced`.
 * @param {string} [props.summary] What is inside, so the trader can decide whether to
 *   open it without opening it.
 * @param {boolean} [props.defaultOpen] Requirement 15.6's answer is `false`, which is
 *   why that is the default. A page passing `true` is opting out and should say why.
 * @param {2|3|4} [props.level] Heading level for the toggle, matching `ds/Panel`'s
 *   `level` prop. An accordion inside a `Panel` at the default `level={2}` is a `3`.
 * @param {string} [props.id]
 * @param {string} [props.className]
 * @param {React.ReactNode} props.children The advanced fields. Unmounted while closed.
 */
export function Accordion({
  title,
  fields,
  summary,
  defaultOpen = false,
  level = 3,
  id,
  className = '',
  children,
  ...rest
}) {
  const generatedId = useId();
  const baseId = hasText(id) ? id : `accordion-${generatedId}`;
  const [open, setOpen] = useState(defaultOpen === true);

  assertContract(
    hasText(title),
    'Accordion: `title` is required — it is the accessible name of the toggle and the only thing '
      + 'the trader can read before deciding to open it. An unnamed disclosure is a chevron.',
  );

  // `fields` is the whole mechanism, not decoration: without it the collapsed set is
  // whatever happens to be between the tags, and Requirement 15.6 becomes unverifiable.
  assertContract(
    Array.isArray(fields) && fields.length > 0 && fields.every((entry) => hasText(entry)),
    `Accordion (${hasText(title) ? title : baseId}): \`fields\` is required and must be a non-empty `
      + 'array of field ids (Requirement 15.6). It is the declared advanced set — pass '
      + '`partitionAdvanced(FORM).advanced`. Without it, which fields are collapsed is decided by '
      + 'where a closing tag landed rather than by the form.',
  );

  const declared = Array.isArray(fields) ? fields.filter(hasText) : [];
  const panelId = `${baseId}-panel`;
  const toggleId = `${baseId}-toggle`;
  // The APG accordion pattern: the toggle lives in a heading, so the region is
  // reachable from a screen reader's heading list rather than only by tabbing.
  const Heading = level === 2 ? 'h2' : level === 4 ? 'h4' : 'h3';

  return (
    <div
      {...{ [ADVANCED_FIELDS_ATTR]: declared.join(' ') }}
      data-accordion-open={open ? 'true' : 'false'}
      className={`rounded-md border border-line-default bg-surface-panel ${className}`.trim()}
      {...rest}
    >
      <Heading className="m-0">
        <button
          id={toggleId}
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((previous) => !previous)}
          className="flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left"
        >
          <span className="flex min-w-0 flex-col">
            <span className="text-title font-medium text-content-primary">{title}</span>
            {hasText(summary) ? (
              <span className="text-micro text-content-secondary">{summary}</span>
            ) : null}
          </span>
          <span className="flex shrink-0 items-center gap-2">
            {/* The count is the honest summary of a closed region: "3 settings" tells
                the trader whether opening it is worth the click. */}
            <span className="text-micro text-content-secondary">
              {`${declared.length} setting${declared.length === 1 ? '' : 's'}`}
            </span>
            <ChevronDown
              size={14}
              aria-hidden="true"
              className={`text-content-muted transition-transform ${open ? 'rotate-180' : ''}`.trim()}
            />
          </span>
        </button>
      </Heading>

      {/* Unmounted, not hidden. See the module note — a CSS-collapsed panel is still
          tabbable and still answers every DOM query, which is both a keyboard trap
          and the reason P31 would have nothing to measure. */}
      {open ? (
        <div
          id={panelId}
          role="region"
          aria-labelledby={toggleId}
          className="flex flex-col gap-3 border-t border-line-subtle px-3 py-3"
        >
          <AdvancedFieldsContext.Provider value={declared}>
            {children}
          </AdvancedFieldsContext.Provider>
        </div>
      ) : null}
    </div>
  );
}

export default Accordion;
