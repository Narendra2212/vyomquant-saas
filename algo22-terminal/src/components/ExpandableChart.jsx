/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ExpandableChart.jsx — click a graph, read it large (CHART-MODAL-01)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 06. The Backtester already draws useful charts; what it could not do is let a trader
 * inspect one. A 240px-tall equity curve over a multi-year run compresses hundreds of points
 * into a band a few pixels per week, and the only way to read it was to squint.
 *
 * This renders a chart inline exactly as before, adds one control that opens the SAME chart
 * in a modal at readable height, and leaves the page underneath untouched.
 *
 * ---------------------------------------------------------------------------
 * WHY THIS TAKES CHART PROPS AND NOT A CHART ELEMENT
 * ---------------------------------------------------------------------------
 * The obvious API would be `<ExpandableChart><Chart … /></ExpandableChart>`, cloning the
 * child with a bigger `height` for the modal. That is how the duplicate-instance problem
 * Task 06 warns about gets created: the element is constructed by the caller on every render
 * of the page, whether the modal is open or not, and `cloneElement` gives you a second one
 * that shares the first's props object.
 *
 * Taking the props as data means exactly one `<Chart>` exists at a time per surface — the
 * inline one — and the modal's is CONSTRUCTED ON OPEN and UNMOUNTED ON CLOSE. There is no
 * second instance to leak, no cloned props to drift, and one description of the chart rather
 * than two that could disagree about its series or its axes.
 *
 * `ds/Chart` sizes itself from its container through `useMeasuredWidth`, so the modal copy
 * needs no width plumbing: it measures the dialog and redraws. Only `height` is passed
 * explicitly, because that is the one dimension a chart cannot infer from a flexible parent.
 *
 * ---------------------------------------------------------------------------
 * WHAT IS DELIBERATELY NOT REBUILT HERE
 * ---------------------------------------------------------------------------
 * Escape-to-close, the focus trap, focus return to the opener and hiding the background from
 * assistive technology all come from `hooks/useFocusTrap`, which `ds/ConfirmDialog` already
 * uses. Mutual exclusion with other overlays comes from `ds/overlayRegistry`, which THROWS in
 * development if two open at once — so this cannot quietly stack on top of a confirm dialog.
 *
 * Nothing is added to `ds/` from here. A shared chart-modal primitive may well be worth
 * having, but adding one from a feature commit is how a design system acquires a control
 * nobody reviewed — the same call `NotificationSwitch` records for not adding a switch.
 */

import { Suspense, lazy, useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Maximize2, X } from 'lucide-react';

import { CommandButton } from './ds/CommandButton';
import { LoadingState } from './ds/LoadingState';
import { OVERLAY_KIND, releaseOverlay, requestOverlay } from './ds/overlayRegistry';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { token, cssVar } from '../design/tokens';

/**
 * `ds/Chart`, LAZILY — and this is not a style choice.
 *
 * `Chart.jsx` is the only module in `src/` permitted to import recharts (~350 KB before
 * gzip), and `pages/Backtester.jsx` imports it through `lazy()` for exactly that reason.
 * An eager `import { Chart }` here would undo that: this component is imported eagerly by
 * the page, so recharts would be pulled into the page's chunk whether a chart is drawn or
 * not, and the split `vite.config.js` maintains for it would stop doing anything.
 *
 * Both render sites below are wrapped in `Suspense` with the same `skeleton-chart` fallback
 * the page already used, so the loading behaviour is unchanged as well as the bundle.
 */
const Chart = lazy(() => import('./ds/Chart'));

/**
 * The modal chart's height.
 *
 * A fixed figure rather than a viewport fraction. `ds/Chart` takes a pixel `height`, and a
 * `vh`-derived number would have to be recomputed on resize by this component — re-rendering
 * the chart on every pixel of a window drag. 60% of a 900px window is about this, and the
 * dialog scrolls if the viewport is shorter.
 */
const MODAL_CHART_HEIGHT = 520;

/**
 * A chart with an inline presentation and an enlarged one.
 *
 * @param {object} props
 * @param {string} props.title The chart's name, used as the dialog's accessible name and in
 *   the expand control's label. Required: a dialog with no name, and a second identical
 *   "Enlarge" button on a page with three charts, are the same accessibility defect.
 * @param {object} props.chart Props forwarded to `ds/Chart`, minus `height`. The single
 *   description of this chart — the inline and modal renders both read it.
 * @param {number} [props.height] Inline height. `ds/Chart`'s own default when omitted.
 * @param {React.ReactNode} [props.children] Rendered inside the dialog beneath the chart, for
 *   the sentence a panel uses to say where its figures come from.
 */
export function ExpandableChart({ title, chart, height, children }) {
  const instanceId = useId();
  const titleId = `${instanceId}-title`;
  const [open, setOpen] = useState(false);
  const dialogRef = useRef(null);
  const closeRef = useRef(null);

  // ── Overlay registration ──────────────────────────────────────────────────
  useEffect(() => {
    if (!open) return undefined;
    requestOverlay({ id: instanceId, kind: OVERLAY_KIND.DIALOG, label: title });
    return () => releaseOverlay(instanceId);
  }, [open, instanceId, title]);

  const close = useCallback(() => setOpen(false), []);

  /*
    `returnFocusTo` is NOT passed. `useFocusTrap` records what held focus when the trap
    engaged and hands it back on release, which is the opener by construction — and that is
    more reliable than a ref of our own, because `ds/CommandButton` is a plain function
    component with no `forwardRef`, so a `ref` on it would be null and focus would return
    nowhere. The close control below is a real `<button>` for the same reason: the trap needs
    a node it can actually focus.
  */
  useFocusTrap({
    active: open,
    containerRef: dialogRef,
    // The close control, not the chart: `ds/Chart` is keyboard-navigable and taking focus
    // straight into its data cursor would skip past the way out.
    initialFocusRef: closeRef,
    onEscape: close,
  });

  return (
    <>
      <div className="relative">
        <Suspense fallback={<LoadingState kind="skeleton-chart" label={`Loading ${title}`} />}>
          <Chart {...chart} height={height} />
        </Suspense>
        {/*
          The control is a real button beside the chart, not a click handler on the chart
          itself. `ds/Chart` owns its own pointer and keyboard interaction for the data
          cursor, so swallowing clicks there would mean a trader exploring the series could
          not stop a modal opening under them — and a `<div onClick>` over a chart is
          unreachable by keyboard, which is the half Task 06's accessibility requirement is
          about.
        */}
        <div className="absolute right-0 top-0">
          <CommandButton
            intent="ghost"
            icon={Maximize2}
            aria-label={`Enlarge ${title}`}
            data-testid="chart-expand"
            onClick={() => setOpen(true)}
          >
            Enlarge
          </CommandButton>
        </div>
      </div>

      {open
        ? createPortal(
          <div
            data-ds="chart-modal-backdrop"
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: cssVar('z.modal'),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 'var(--spacing-6)',
              background: token.surface.overlay,
            }}
          >
            <div
              ref={dialogRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby={titleId}
              data-testid="chart-modal"
              data-ds-overlay={OVERLAY_KIND.DIALOG}
              style={{
                width: '100%',
                // Wide, but not edge-to-edge: a chart that runs to the viewport's margins has
                // no quiet space and reads worse than one that stops short of it.
                maxWidth: 1100,
                // The dialog, not the page, scrolls when the viewport is shorter than the
                // chart — which is what keeps the backdrop covering the page behind it.
                maxHeight: 'calc(100dvh - 2 * var(--spacing-6))',
                overflowY: 'auto',
                background: token.surface.panel,
                border: `1px solid ${token.line.default}`,
                borderRadius: 'var(--radius-lg)',
                padding: 'var(--spacing-5)',
                display: 'flex',
                flexDirection: 'column',
                gap: 'var(--spacing-4)',
              }}
            >
              <div className="flex items-start justify-between gap-4">
                <h2
                  id={titleId}
                  className="text-section"
                  style={{ margin: 0, color: token.content.primary }}
                >
                  {title}
                </h2>
                {/*
                  A real `<button>`, not `ds/CommandButton`, for one reason: the focus trap
                  needs a node it can focus and `CommandButton` has no `forwardRef`. Every
                  colour is a token, so the colour guard is satisfied; `aria-label` carries
                  the chart's name so three modals on one page do not all announce "Close".
                */}
                <button
                  ref={closeRef}
                  type="button"
                  aria-label={`Close ${title}`}
                  data-testid="chart-modal-close"
                  onClick={close}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-line-default px-2.5 py-1.5 text-body text-content-secondary hover:bg-surface-raised hover:text-content-primary"
                >
                  <X size={14} aria-hidden="true" />
                  Close
                </button>
              </div>

              {/*
                One `<Chart>`, built here, from the same props the inline one reads. It exists
                only while this branch is mounted, so closing the dialog disposes of it.
              */}
              <Suspense
                fallback={<LoadingState kind="skeleton-chart" label={`Loading ${title}`} />}
              >
                <Chart {...chart} height={MODAL_CHART_HEIGHT} />
              </Suspense>

              {children}
            </div>
          </div>,
          document.body,
        )
        : null}
    </>
  );
}

export default ExpandableChart;
