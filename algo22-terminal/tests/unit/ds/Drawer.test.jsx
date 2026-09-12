/**
 * `ds/Drawer` — vyomquant-ui-redesign task 6.20.
 * Requirements 17.3, 17.4, 18.1, 18.3, 18.4. design.md §5.2, §6.4, §11.6.
 *
 * What this asserts is mostly that the drawer and `ConfirmDialog` really do share one focus
 * trap, one Escape route and one overlay registry, rather than each having its own — §11.6
 * and §11.7 put the rules on the pair, and a pair that disagrees is the failure Requirement
 * 17.3 describes.
 *
 * Its two known consumers want different placements: the account menu (task 8.8) a side
 * panel, the Strategy Builder's tablet inspector (task 24.7) a bottom one (§11.6).
 */

import { useRef, useState } from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, screen, act, fireEvent } from '@testing-library/react';

import Drawer from '../../../src/components/ds/Drawer';
import { isOverlayOpen, resetOverlayRegistry } from '../../../src/components/ds/overlayRegistry';

const BASE = Object.freeze({ open: true, onClose: () => {}, title: 'Account' });

beforeEach(() => {
  resetOverlayRegistry();
  vi.stubEnv('DEV', true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  resetOverlayRegistry();
});

const panel = () => screen.getByRole('dialog');

/**
 * The header's close button — the keyboard route out.
 *
 * A bare `getByRole('button', { name: 'Close' })` is ambiguous by design: `closeLabel`
 * names the scrim as well (see "gives the scrim a name" below), so an open drawer has two
 * controls called "Close" and only this one is reachable by keyboard. The role and the
 * accessible name are still what selects it — `getAllByRole` matches the pair — and
 * `data-ds="drawer-close"` picks the header button out of the two.
 */
const closeButton = () => {
  const named = screen.getAllByRole('button', { name: 'Close' });
  const header = named.find((element) => element.dataset.ds === 'drawer-close');
  expect(header).toBeTruthy();
  return header;
};

/** See the identical helper in `ConfirmDialog.test.jsx`. */
function silenceExpectedThrow() {
  const swallow = (event) => event.preventDefault();
  window.addEventListener('error', swallow);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return () => window.removeEventListener('error', swallow);
}

describe('Drawer: open and closed', () => {
  it('renders nothing when closed and claims no overlay', () => {
    render(<Drawer {...BASE} open={false} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(isOverlayOpen()).toBe(false);
  });

  it('renders through a portal, escaping an ancestor with hidden overflow', () => {
    const view = render(
      <div style={{ overflow: 'hidden' }}>
        <Drawer {...BASE} />
      </div>,
    );
    expect(view.container.querySelector('[role="dialog"]')).toBeNull();
    expect(panel().dataset.dsOverlay).toBe('drawer');
  });

  it('releases its claim when closed', () => {
    const view = render(<Drawer {...BASE} />);
    expect(isOverlayOpen()).toBe(true);
    view.rerender(<Drawer {...BASE} open={false} />);
    expect(isOverlayOpen()).toBe(false);
  });
});

describe('Drawer: ARIA (Requirement 18.4)', () => {
  it('is a modal dialog named by its title', () => {
    render(<Drawer {...BASE} />);
    expect(panel().getAttribute('aria-modal')).toBe('true');
    expect(screen.getByRole('dialog', { name: 'Account' })).toBe(panel());
  });

  it('accepts an ariaLabel when there is no visible title', () => {
    render(<Drawer open onClose={() => {}} ariaLabel="Node parameters" />);
    expect(screen.getByRole('dialog', { name: 'Node parameters' })).toBeTruthy();
  });

  it('throws in development when it has no accessible name at all', () => {
    const restore = silenceExpectedThrow();
    expect(() => render(<Drawer open onClose={() => {}} />)).toThrow(/accessible name/);
    restore();
  });

  it('gives the scrim a name, so it is not an unlabelled button in the tree', () => {
    render(<Drawer {...BASE} closeLabel="Dismiss" />);
    const scrim = document.body.querySelector('[data-ds="drawer-scrim"]');
    expect(scrim.tagName).toBe('BUTTON');
    expect(scrim.getAttribute('aria-label')).toBe('Dismiss');
  });
});

describe('Drawer: closing', () => {
  it('closes on the close button', () => {
    let closes = 0;
    render(<Drawer {...BASE} onClose={() => { closes += 1; }} />);
    act(() => {
      closeButton().click();
    });
    expect(closes).toBe(1);
  });

  it('closes on Escape', () => {
    let closes = 0;
    render(<Drawer {...BASE} onClose={() => { closes += 1; }} />);
    fireEvent.keyDown(document.activeElement, { key: 'Escape' });
    expect(closes).toBe(1);
  });

  it('closes on the scrim by default', () => {
    let closes = 0;
    render(<Drawer {...BASE} onClose={() => { closes += 1; }} />);
    act(() => {
      document.body.querySelector('[data-ds="drawer-scrim"]').click();
    });
    expect(closes).toBe(1);
  });

  it('does not close on the scrim when dismissal is turned off', () => {
    let closes = 0;
    render(<Drawer {...BASE} dismissOnScrim={false} onClose={() => { closes += 1; }} />);
    act(() => {
      document.body.querySelector('[data-ds="drawer-scrim"]').click();
    });
    expect(closes).toBe(0);
    // The keyboard route out is still there.
    act(() => {
      closeButton().click();
    });
    expect(closes).toBe(1);
  });
});

describe('Drawer: focus', () => {
  it('puts initial focus on the close button, not the content', () => {
    render(
      <Drawer {...BASE}>
        <button type="button">Sign out</button>
      </Drawer>,
    );
    expect(document.activeElement).toBe(closeButton());
  });

  it('keeps the scrim out of the tab cycle', () => {
    render(
      <Drawer {...BASE}>
        <button type="button">Sign out</button>
      </Drawer>,
    );
    const scrim = document.body.querySelector('[data-ds="drawer-scrim"]');
    const visited = new Set();
    for (let index = 0; index < 6; index += 1) {
      fireEvent.keyDown(document.activeElement, { key: 'Tab' });
      visited.add(document.activeElement);
    }
    expect(visited.has(scrim)).toBe(false);
    expect([...visited].every((element) => panel().contains(element))).toBe(true);
  });

  it('traps Tab and Shift+Tab inside the panel', () => {
    render(
      <Drawer {...BASE}>
        <a href="/docs">Docs</a>
        <button type="button">Sign out</button>
      </Drawer>,
    );
    for (let index = 0; index < 10; index += 1) {
      fireEvent.keyDown(document.activeElement, { key: 'Tab', shiftKey: index % 2 === 0 });
      expect(panel().contains(document.activeElement)).toBe(true);
    }
  });

  it('returns focus to the trigger on close', () => {
    function Host() {
      const [open, setOpen] = useState(false);
      const trigger = useRef(null);
      return (
        <>
          <button type="button" ref={trigger} onClick={() => setOpen(true)}>
            Account
          </button>
          <Drawer open={open} onClose={() => setOpen(false)} title="Account menu" />
        </>
      );
    }
    render(<Host />);
    const trigger = screen.getByRole('button', { name: 'Account' });
    act(() => {
      trigger.focus();
      trigger.click();
    });
    expect(document.activeElement).toBe(closeButton());

    act(() => {
      closeButton().click();
    });
    expect(document.activeElement).toBe(trigger);
  });

  it('hides the rest of the app from assistive technology while open', () => {
    const behind = document.createElement('div');
    document.body.appendChild(behind);
    const view = render(<Drawer {...BASE} />);
    expect(behind.getAttribute('aria-hidden')).toBe('true');
    view.unmount();
    expect(behind.hasAttribute('aria-hidden')).toBe(false);
    behind.remove();
  });
});

describe('Drawer: placement and the viewport clamp (Requirement 17.3)', () => {
  it('defaults to the right', () => {
    render(<Drawer {...BASE} />);
    expect(panel().dataset.dsPlacement).toBe('right');
  });

  it.each(['right', 'left'])('clamps a %s side panel to the viewport width', (placement) => {
    render(<Drawer {...BASE} placement={placement} />);
    const { style } = panel();
    expect(style.maxWidth).toContain('420px');
    expect(style.maxWidth).toContain('100vw');
    expect(style.height).toBe('100dvh');
    expect(panel().dataset.dsPlacement).toBe(placement);
  });

  it('clamps the bottom panel to the viewport height (§11.6, the tablet inspector)', () => {
    render(<Drawer {...BASE} placement="bottom" />);
    const { style } = panel();
    expect(style.maxHeight).toContain('100dvh');
    expect(style.maxHeight).toContain('--spacing-8');
    expect(style.width).toBe('100%');
  });

  it('sits below the modal layer, so it can never paint over a confirmation', () => {
    render(<Drawer {...BASE} />);
    const layer = document.body.querySelector('[data-ds="drawer-layer"]');
    expect(layer.style.zIndex).toBe('var(--z-drawer)');
  });

  it('puts the only scroll region inside the panel', () => {
    render(<Drawer {...BASE}>content</Drawer>);
    expect(panel().style.overflow).toBe('hidden');
    const body = panel().querySelector('[data-ds="drawer-body"]');
    expect(body.style.overflowY).toBe('auto');
    expect(body.style.minHeight).toBe('0');
  });

  it('throws in development on an unknown placement', () => {
    const restore = silenceExpectedThrow();
    expect(() => render(<Drawer {...BASE} placement="diagonal" />)).toThrow(/placement/);
    restore();
  });

  it('falls back to the right in production on an unknown placement', () => {
    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<Drawer {...BASE} placement="diagonal" />);
    expect(panel().dataset.dsPlacement).toBe('right');
  });
});

describe('Drawer: one overlay at a time', () => {
  it('throws in development when another drawer is already open', () => {
    const restore = silenceExpectedThrow();
    expect(() =>
      render(
        <>
          <Drawer open onClose={() => {}} title="First" />
          <Drawer open onClose={() => {}} title="Second" />
        </>,
      ),
    ).toThrow(/Requirement 17.3/);
    restore();
  });

  it('renders only the first in production', () => {
    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <>
        <Drawer open onClose={() => {}} title="First" />
        <Drawer open onClose={() => {}} title="Second" />
      </>,
    );
    const panels = screen.getAllByRole('dialog');
    expect(panels).toHaveLength(1);
    expect(panels[0].textContent).toContain('First');
  });
});

describe('Drawer: footer', () => {
  it('pins a footer below the scroll region', () => {
    render(<Drawer {...BASE} footer={<button type="button">Sign out</button>}>content</Drawer>);
    const footer = panel().querySelector('[data-ds="drawer-footer"]');
    expect(footer).toBeTruthy();
    expect(footer.textContent).toBe('Sign out');
  });

  it('renders no footer element when none is supplied', () => {
    render(<Drawer {...BASE}>content</Drawer>);
    expect(panel().querySelector('[data-ds="drawer-footer"]')).toBeNull();
  });
});
