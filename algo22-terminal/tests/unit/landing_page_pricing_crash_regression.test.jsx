/**
 * tests/unit/landing_page_pricing_crash_regression.test.jsx — the INR tier contract, and
 * retail-ui-simplification tasks 10.1 and 10.3.
 *
 * Requirements 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 15.5, 18.1.
 *
 * WHY THIS FILE CARRIES A REPRODUCTION RECORD
 * ------------------------------------------
 * It is the only test that mounts the live landing tree end-to-end, so Requirement 13's
 * reproduction ran through it first. The record is kept here the way every guard under
 * `tests/unit/guards/` keeps its measurement history in its own header. The full three-outcome
 * recording, the outstanding question and the one filed content finding are in
 * `tests/unit/landing/landingSections.test.jsx`'s header; this is the half that was measured
 * here.
 *
 * **Nothing below changed when this header was written.** Eleven `it` blocks, the same eleven
 * commit 62c1e0e left green. A recording that altered an assertion would not be a recording.
 *
 * WHAT WAS MEASURED HERE (task 10.1, commit 62c1e0e — outcome 1 of three)
 * ---------------------------------------------------------------------
 * `renders entire LandingPage end-to-end without crashing` held three assertions that could not
 * fail: `expect(container.querySelector('#pricing')).toBeDefined()` and the same for
 * `#architecture` and `#waitlist`. **`null` is defined**, so all three passed whether or not the
 * anchor existed, and a silently absent in-page anchor is one of Requirement 13.3's five
 * candidate symptoms. All three are `not.toBeNull()` now — one line each, and the repaired form
 * was proved to have teeth with a throwaway probe against an anchor that does not exist, which
 * the old form passed and the new form failed.
 *
 * The measurement, after the repair: **all three anchors are present**, the tree mounts without
 * throwing, and `Infrastructure Tiers` resolves. Task 10.2 then checked the five `Navbar` scroll
 * targets — `#platform`, `#architecture`, `#security`, `#pricing`, `#faq` — and every one
 * resolves to a section the page renders, which matters because `scrollToSection` is
 * `if (el) el.scrollIntoView(…)` and a missing target is a click that does nothing, in silence.
 * **A render failure and a navigation failure are both ruled out in jsdom**, and Requirement
 * 13.3's five candidates drop to three: a layout fault at a specific viewport, a missing or 403
 * asset, and incorrect content. None of the three is observable in jsdom.
 *
 * THE HALF OF THE STANDING HYPOTHESIS THIS FILE SETTLES (§8.3 — outcome 3 of three)
 * -------------------------------------------------------------------------------
 * §1.11 records the requester's recollection that the INR-only pricing conversion was made
 * against `src/pages/Landing.jsx`, which is routed nowhere. It was not.
 * `git log --oneline -- algo22-terminal/src/components/landing/` puts that work at `55382cb`
 * (2026-09-21) on the **live** directory, and that commit does not appear in the log for
 * `src/pages/Landing.jsx` at all. This file is the independent confirmation: what it asserts
 * below is the live `components/landing/Pricing.jsx`, INR-only, four tiers, no toggle. **That
 * half of the hypothesis is refuted — the pricing work reached the visitor.**
 *
 * The other half is confirmed: `0ecf86a` (2026-09-20) added the six missing imports to
 * `src/pages/Landing.jsx` and touched nothing under `src/components/landing/`, so that fix
 * landed in the dead file and changed nothing a visitor sees. The full log output and the diff
 * evidence are in `landingSections.test.jsx`'s header. It explains why a fix had no effect; it
 * says nothing about what the original symptom is.
 *
 * WHAT IS OUTSTANDING, AND WHAT IS NOT A DEFECT
 * --------------------------------------------
 * The question put to the requester is recorded in `landingSections.test.jsx`'s header, together
 * with §8.2's steps 4 and 5 — the deployed bundle's asset paths and a real viewport sweep — which
 * are blocked on the answer and invisible to every test here. **No symptom reproduced**
 * (Requirement 13.5), and that is the result rather than a blocker: no defect list was invented,
 * and the one concrete content finding (`SecuritySection`'s `AES-256` claim against a Fernet
 * vault) is filed under Requirement 13.6 in the other file and has since been **fixed**, by the
 * `fix(landing): stop advertising AES-256 on a Fernet vault` commit, which withdrew the claim from
 * `SecuritySection.jsx`, `FAQ.jsx` and `components/legal/LegalPage.jsx` and left the backend
 * docblocks out of scope. Nothing in this file asserted that copy, so no assertion here moved.
 *
 * WHAT MUST NOT BE LOST FROM THIS FILE
 * -----------------------------------
 * The INR contract below, which exists because this was a real crash once: the section formatted
 * whatever `GET /api/billing/plans` returned and a null price threw on `toLocaleString`. Four
 * tiers at ₹0/₹499/₹999/₹2,499, four `.card-surface` cards under `#pricing`, `Recommended` on Pro
 * Quant, no currency selector, `api.billing.getPlans` never called, and annual arithmetic that
 * cannot throw. Requirement 15.5 records the INR-only state so a token migration cannot
 * reintroduce a toggle by reverting a file.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LandingPage from '../../src/components/landing/LandingPage';
import Pricing from '../../src/components/landing/Pricing';
import { api } from '../../src/api';

/**
 * Landing pricing is INR-only and states four published tiers.
 *
 * This file began as a `toLocaleString` crash regression suite, when the section
 * rendered whatever `GET /api/billing/plans` returned and a null price threw on
 * format. The prices are now declared in the component (see the header comment
 * in `Pricing.jsx`: the endpoint FX-converts the USD base and therefore does not
 * serve the catalogue's INR column), so the crash surface is gone along with the
 * fetch. What is asserted instead is the contract that replaced it — four tiers,
 * rupees only, and arithmetic that still cannot throw.
 */
describe('Landing Page & Pricing — INR-only tier contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const renderPricing = () =>
    render(
      <MemoryRouter>
        <Pricing />
      </MemoryRouter>
    );

  describe('The four published tiers', () => {
    it('quotes ₹0, ₹499, ₹999 and ₹2,499 per month', () => {
      renderPricing();

      expect(screen.getByText('Infrastructure Tiers')).toBeDefined();
      expect(screen.getByText('₹0')).toBeDefined();
      expect(screen.getByText('₹499')).toBeDefined();
      expect(screen.getByText('₹999')).toBeDefined();
      expect(screen.getByText('₹2,499')).toBeDefined();
    });

    it('renders exactly four tier cards, one per published plan', () => {
      const { container } = renderPricing();

      const names = ['Free / Sandbox', 'Trader', 'Pro Quant', 'Institutional'];
      names.forEach((name) => expect(screen.getByText(name)).toBeDefined());

      // One `/month` label per card is the card count, independent of copy.
      expect(container.querySelectorAll('#pricing .card-surface')).toHaveLength(4);
      expect(screen.getAllByText('/month')).toHaveLength(4);
    });

    it('marks Pro Quant as the recommended tier', () => {
      renderPricing();
      expect(screen.getByText('Recommended')).toBeDefined();
    });
  });

  describe('Rupee is the only currency on the surface', () => {
    it('prints no dollar sign anywhere in the section', () => {
      const { container } = renderPricing();

      expect(container.querySelector('#pricing').textContent).not.toContain('$');
    });

    it('offers no currency selector — there is nothing to switch to', () => {
      renderPricing();

      expect(screen.queryByRole('button', { name: /USD/i })).toBeNull();
      expect(screen.queryByRole('button', { name: /INR/i })).toBeNull();
    });

    it('does not call the FX-localised billing endpoint for its figures', async () => {
      const getPlans = vi.spyOn(api.billing, 'getPlans').mockResolvedValue({ plans: [] });

      renderPricing();
      // A fetch would land in a microtask after mount, so let the queue drain.
      await Promise.resolve();

      expect(getPlans).not.toHaveBeenCalled();
    });
  });

  describe('Annual billing arithmetic', () => {
    it('applies the 20% reduction to every paid tier without throwing', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

      await waitFor(() => {
        // Monthly-equivalent: 499 / 999 / 2499 less 20%, rounded.
        expect(screen.getByText('₹399')).toBeDefined();
        expect(screen.getByText('₹799')).toBeDefined();
        expect(screen.getByText('₹1,999')).toBeDefined();
      });

      // Billed-at lines carry the full year at the same 20% reduction.
      expect(screen.getByText(/Billed at ₹4,790\/yr — save 20%/)).toBeDefined();
      expect(screen.getByText(/Billed at ₹9,590\/yr — save 20%/)).toBeDefined();
      expect(screen.getByText(/Billed at ₹23,990\/yr — save 20%/)).toBeDefined();
    });

    it('leaves the free tier at ₹0 with no annual billing line', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

      await waitFor(() => {
        expect(screen.getByText('₹0')).toBeDefined();
      });
      // Three paid tiers carry the line; the free tier does not.
      expect(screen.getAllByText(/Billed at ₹/)).toHaveLength(3);
    });

    it('returns to the monthly figures when Monthly is reselected', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));
      await waitFor(() => expect(screen.getByText('₹399')).toBeDefined());

      fireEvent.click(screen.getByRole('button', { name: /Monthly/i }));
      await waitFor(() => {
        expect(screen.getByText('₹499')).toBeDefined();
        expect(screen.queryByText(/Billed at ₹/)).toBeNull();
      });
    });
  });

  describe('Full Landing Page Integration', () => {
    it('renders entire LandingPage end-to-end without crashing', async () => {
      const { container } = render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        // `null` is defined, so `toBeDefined()` here passed whether or not the
        // anchor existed. `not.toBeNull()` is what asks the question.
        expect(container.querySelector('#pricing')).not.toBeNull();
        expect(container.querySelector('#architecture')).not.toBeNull();
        expect(container.querySelector('#waitlist')).not.toBeNull();
        expect(screen.getByText('Infrastructure Tiers')).toBeDefined();
      });
    });

    it('shows no dollar sign anywhere on the landing page', async () => {
      const { container } = render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => expect(screen.getByText('Infrastructure Tiers')).toBeDefined());

      expect(container.textContent).not.toContain('$');
    });
  });
});
