/**
 * tests/unit/landing/landingSections.test.jsx
 *
 * Originally retail-ui-simplification tasks 10.2 and 10.3 (Requirements 13.1–13.6, 18.1).
 * Rewritten for the landing-page redesign.
 *
 * WHAT THIS FILE IS
 * -----------------
 * One `it` per rendered child of `LandingPage.jsx`, asserting that the section carries its own
 * copy. The original purpose is unchanged and is worth restating, because it is the failure this
 * file exists to catch: a section that MOUNTS SUCCESSFULLY AND RENDERS NOTHING. An empty
 * `<section>` throws nothing, `LandingPage` has no error boundary and no lazy children, so an
 * end-to-end "it did not crash" test cannot see a section that produced only chrome.
 *
 * HOW THE ASSERTIONS AVOID BEING VACUOUS
 * --------------------------------------
 * Carried over from the version this replaces, and still the most important paragraph here.
 * "Renders something" is trivially easy to write so that it cannot fail: `render()` hands back a
 * `container` for an empty div too, so `expect(container).toBeDefined()` passes on nothing, and
 * `expect(container.querySelector('#pricing')).toBeDefined()` passes when the selector returns
 * `null` — which is the exact vacuity task 10.1 was raised to fix.
 *
 * So every assertion below is `getByText` / `getByRole` — queries that THROW when the thing is
 * absent — against a string or accessible role the section genuinely owns, read out of that
 * component's source rather than guessed.
 *
 * The teeth were re-checked after the rewrite rather than assumed. Each of the twelve sections
 * was temporarily asserted against copy it does not contain; every one failed with "Unable to
 * find an element with the text", naming the missing string. The probes were reverted.
 *
 * ═══ WHAT THE REDESIGN CHANGED ═══
 *
 * FOURTEEN CHILDREN BECAME TWELVE. Three sections were deleted and two added, so this file has
 * twelve `it` blocks rather than fourteen. None was dropped to make a count tidy.
 *
 *   gone   `ModernTradingSection` — two of its four claims were false against the backend.
 *          There is no TWAP and no Iceberg anywhere in `backend_app/`; the only VWAP is
 *          `rolling_vwap` in `backend/indicators_backend.py`, an `IndicatorSpec` block rather
 *          than an execution algorithm; and no FIX implementation exists. A third claim
 *          ("your strategy logic remains on your machine") was conditional on desktop builds
 *          that are not published. Its heading also duplicated `TrustSection`'s.
 *   gone   `Waitlist` — its own copy read "you can sign up immediately" directly above a
 *          waitlist form, which is two funnels arguing over one click.
 *   gone   `FounderSection` — nothing in it was untrue; a founder bio at position 8 of 14 is
 *          in the wrong place.
 *   new    `ProofStrip` — the credibility band under the hero. Every item on it names a file
 *          in the backend, because this repository contains no customer count, testimonial,
 *          certification or funding claim, and a fabricated one is the easiest thing on a page
 *          for a prospect to check.
 *   new    `MarketplaceSection` — `/marketplace` is a PUBLIC route in `App.jsx` and the
 *          previous page linked to it zero times.
 *
 * TWO FILES CHANGED JOBS, AND ONE CHANGED NAME. `TrustSection` and the old
 * `ScreenshotsSection` used to pitch the same four capabilities as `ModernTradingSection` and
 * `HowItWorks` — twenty feature cards for four features. `TrustSection` now states each pillar
 * once; the tour shows what each looks like in use. Their old headings ("Built for Serious
 * Systematic Traders", "Engineered for Systematic Precision") are gone, which is why neither
 * appears below.
 *
 * `ScreenshotsSection.jsx` is now `ProductTour.jsx`. The page carries no product screenshots and
 * has settled on carrying none, so a filename promising them described an intention that had been
 * dropped. The two ratchet entries naming the old path moved with the file.
 *
 * THE INVENTED FIGURES ARE ASSERTED ABSENT. `Hero` carried `Sharpe: 2.1`, `Win Rate: 67.4%` and
 * `Max DD: -8.2%`; the tour carried `+28.4% Net Return`, `Sharpe Ratio 2.14`,
 * `Sortino Ratio 3.08`, `Profit Factor 1.82` and a rising twenty-bar equity curve. None came from
 * a backtest. `no invented performance figure survives on the page` below is what stops them
 * returning — a removal that nothing asserts is a removal that gets undone.
 *
 * THE DESKTOP DEAD ENDS ARE ASSERTED ABSENT. Eleven sites across `Hero`, `Navbar`, `FinalCTA` and
 * `Footer` advertised Windows and macOS installers pointing at `/download#windows` and
 * `/download#macos`, where every artifact renders `unavailable`. `no section links into a
 * withdrawn installer` holds that closed.
 *
 * `DownloadSection` — THE WITHDRAWAL IS STILL LOAD-BEARING
 * -------------------------------------------------------
 * production-launch-hardening task 4.3 withdrew four advertised desktop installers: all four
 * `/releases/…` URLs return 403 because CI's `dist/` carries no `releases/` directory and
 * `aws s3 sync dist/ --delete` removes that prefix on every deploy. The cards render `ds/Panel`'s
 * `unavailable` state carrying the reason declared in `design/pageFields`.
 *
 * The redesign proposed deleting this section and that was DECLINED: VyomQuant is genuinely built
 * for Windows, macOS and Linux — `src-tauri/` is a real Tauri 2 project — and the desktop
 * terminals are releasing shortly. So the section's FRAMING is now forward-looking while each
 * artifact still reports its true state, and the four panels are untouched.
 * `tests/unit/pages/downloadSurface.test.jsx` owns the per-artifact guarantees in depth; the
 * block below asserts only that this section still renders all four markers and still offers the
 * one destination that works, so that a future edit to the heading cannot quietly take the panels
 * with it. The reason strings are read through `artifactFieldFor` — the same lookup the component
 * uses — rather than copied, so there is one place to keep them.
 */

import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import LandingPage from '../../../src/components/landing/LandingPage';
import Navbar from '../../../src/components/landing/Navbar';
import Hero from '../../../src/components/landing/Hero';
import ProofStrip from '../../../src/components/landing/ProofStrip';
import HowItWorks from '../../../src/components/landing/HowItWorks';
import TrustSection from '../../../src/components/landing/TrustSection';
import ProductTour from '../../../src/components/landing/ProductTour';
import WorkflowSection from '../../../src/components/landing/WorkflowSection';
import MarketplaceSection from '../../../src/components/landing/MarketplaceSection';
import CreatorSection from '../../../src/components/landing/CreatorSection';
import SecuritySection from '../../../src/components/landing/SecuritySection';
import DownloadSection from '../../../src/components/landing/DownloadSection';
import Pricing from '../../../src/components/landing/Pricing';
import FAQ from '../../../src/components/landing/FAQ';
import FinalCTA from '../../../src/components/landing/FinalCTA';
import Footer from '../../../src/components/landing/Footer';

import { artifactFieldFor } from '../../../src/components/download/PlatformArtifact';

afterEach(cleanup);

/** Every section that renders a router `Link`. */
const mountRouted = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

/**
 * For the sections that import nothing from `react-router-dom`. Deliberately unwrapped: a `Link`
 * added to one of these later throws "useHref may be used only in the context of a Router", and
 * this file is where that shows up rather than in production.
 */
const mountBare = (ui) => render(ui);

describe('Landing_Surface — each rendered section carries its own content', () => {
  describe('1 — Navbar', () => {
    it('renders the navigation landmark, the wordmark and its five scroll targets', () => {
      mountRouted(<Navbar />);

      expect(screen.getByRole('navigation', { name: 'Main Navigation' })).toBeTruthy();
      expect(screen.getAllByText('VyomQuant').length).toBeGreaterThan(0);

      // `Marketplace` is the one that matters most here: the public strategy library was
      // reachable and unadvertised, and this is the header entry that fixed it.
      ['Platform', 'How it works', 'Marketplace', 'Pricing', 'FAQ'].forEach((label) => {
        expect(screen.getByRole('button', { name: label })).toBeTruthy();
      });

      expect(screen.getByRole('link', { name: 'Sign In' })).toBeTruthy();
      expect(screen.getByRole('link', { name: /Start free/ })).toBeTruthy();
    });

    it('offers no desktop installer button', () => {
      // Four of the header's seven controls used to be `Windows` and `macOS` buttons into
      // `/download#windows` and `/download#macos`, where every artifact is unavailable.
      mountRouted(<Navbar />);

      expect(screen.queryByRole('link', { name: /Windows/i })).toBeNull();
      expect(screen.queryByRole('link', { name: /macOS/i })).toBeNull();
    });
  });

  describe('2 — Hero', () => {
    it('renders the stage badge, the h1 headline and one primary call to action', () => {
      mountRouted(<Hero />);

      expect(screen.getByText('Early access · Paper trading by default')).toBeTruthy();
      /*
        THE HEADLINE AND BOTH CTAs MOVED WITH THE POSITIONING, AND NOT COSMETICALLY.

        "Build crypto trading bots without writing code" described one of the three things this
        product is. It said nothing about the strategy marketplace — a public, unauthenticated
        route — or about publishing to it for a share of subscription revenue, so a hero built on
        it sold roughly a third of the platform and the two paid capabilities that justify its two
        highest plans were invisible on the first screen.

        "Build It. Trade It. Earn From It." names all three. The three product paths that follow it
        (asserted below) are what keep that from being a slogan: each one is a capability with code
        behind it, and EARN carries its plan boundary on the same line rather than in a footnote.
      */
      expect(
        screen.getByRole('heading', {
          level: 1,
          name: 'Build It. Trade It. Earn From It.',
        }),
      ).toBeTruthy();

      expect(
        screen.getByRole('link', { name: /Build Your First Strategy Free/ }),
      ).toBeTruthy();
      expect(
        screen.getByRole('link', { name: /Explore Strategy Marketplace/ }),
      ).toBeTruthy();
    });

    it('names the three product paths, and qualifies the one that needs a plan', () => {
      mountRouted(<Hero />);

      ['Build', 'Discover', 'Earn'].forEach((path) =>
        expect(screen.getByText(path), `the ${path} path is missing`).toBeTruthy(),
      );

      // Publishing is a Pro Quant and Business capability. A visitor reading "earn from it" on the
      // first screen is owed that before they choose a plan, not after they have bought one.
      expect(
        screen.getByText(/Pro Quant and Business/),
      ).toBeTruthy();
    });

    it('renders the pipeline frame, labelled as a diagram rather than a screenshot', () => {
      mountRouted(<Hero />);

      // The frame is the hero's whole lower half. If it rendered nothing the headline above
      // would still be here, which is the half-empty case this file exists for.
      //
      // `Order` and not `Order intent`: the five stage labels were shortened in the design
      // pass because three of them ("Crossover AND threshold", "Position cap · drawdown stop",
      // "Limit buy · trailing stop") wrapped to two lines in a 184px column and left the row
      // visibly ragged. The detail each one lost moved to the line beneath it.
      ['Market feed', 'Indicator', 'Logic gate', 'Risk gate', 'Order'].forEach((stage) =>
        expect(screen.getByText(stage)).toBeTruthy(),
      );

      expect(screen.getByText(/a diagram of the node stages/)).toBeTruthy();
    });
  });

  describe('3 — ProofStrip', () => {
    it('renders all six capability proofs', () => {
      mountBare(<ProofStrip />);

      expect(screen.getByText('Built on named, inspectable infrastructure')).toBeTruthy();

      ['Binance', 'CCXT.pro', 'VectorBT', 'Monte Carlo', 'Encrypted vault', 'Audit trail'].forEach(
        (label) => expect(screen.getByText(label)).toBeTruthy(),
      );
    });

    it('claims one certified exchange rather than a count of many', () => {
      // `core/exchange_certification.py` registers exactly one venue at
      // LEVEL_5_PRODUCTION_READY. The old page said "50+ exchanges" in three places.
      mountBare(<ProofStrip />);

      expect(screen.getByText('Production-certified venue')).toBeTruthy();
      expect(screen.queryByText(/50\+/)).toBeNull();
    });
  });

  describe('4 — HowItWorks', () => {
    it('renders the pipeline heading and all four numbered steps', () => {
      mountRouted(<HowItWorks />);

      expect(
        screen.getByRole('heading', {
          level: 2,
          name: 'Four steps, and you cannot skip the third',
        }),
      ).toBeTruthy();

      [
        'Build the logic',
        'Backtest and stress it',
        'Forward-test on paper',
        'Go live when ready',
      ].forEach((title) => expect(screen.getByText(title)).toBeTruthy());

      // Zero-padded ordinals rather than the `STEP 1` labels this asserted before. The word
      // was redundant beside a heading that already says "Four steps", and the design pass
      // reclaimed the room for the stage icon.
      ['01', '02', '03', '04'].forEach((step) =>
        expect(screen.getByText(step)).toBeTruthy(),
      );
    });

    it('gives each step its own hue, so the stage language is not one flat cyan', () => {
      // The four hues repeat on `TrustSection`'s four pillars in the same order, which is what
      // makes them a language rather than decoration: cyan build, amber research, indigo paper,
      // teal live. The indigo is `--color-env-paper`, the app's own environment colour for
      // simulated execution, so the hue a visitor meets here is the hue on their first paper
      // deployment.
      //
      // Asserted on the class, because that is where the decision lives — Tailwind v4 scans
      // source as text, so an interpolated `text-${hue}` would emit no CSS and every icon would
      // silently fall back to inherit. This is the assertion that would catch that.
      const { container } = mountRouted(<HowItWorks />);
      const hues = ['text-brand', 'text-status-warning', 'text-env-paper', 'text-status-profit'];

      for (const hue of hues) {
        expect(container.querySelector(`.${hue}`), `${hue} is not on the page`).not.toBeNull();
      }
    });
  });

  describe('5 — TrustSection', () => {
    it('renders the four capability pillars and the three secondary cards', () => {
      mountBare(<TrustSection />);

      expect(
        screen.getByRole('heading', {
          level: 2,
          name: 'Four things that decide whether a bot survives',
        }),
      ).toBeTruthy();

      [
        'A canvas, not a code editor',
        'Research that argues back',
        'Paper trading is the default',
        'Risk gates run before the order',
      ].forEach((title) => expect(screen.getByText(title)).toBeTruthy());

      // The one durable claim inherited from the deleted `ModernTradingSection`.
      ['Direct exchange routing', 'Machine learning as a node', 'Signal tracing'].forEach(
        (title) => expect(screen.getByText(title)).toBeTruthy(),
      );
    });

    it('advertises no order type the backend does not implement', () => {
      mountBare(<TrustSection />);

      // Verified absent from `backend_app/` before these claims were removed.
      expect(screen.queryByText(/TWAP/)).toBeNull();
      expect(screen.queryByText(/Iceberg/)).toBeNull();
      expect(screen.queryByText(/FIX/)).toBeNull();
    });
  });

  describe('6 — ProductTour', () => {
    it('renders the tour heading, its four tabs and the default tab body', () => {
      mountRouted(<ProductTour />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'What you actually work in' }),
      ).toBeTruthy();

      ['Strategy builder', 'Research', 'Paper trading', 'Risk controls'].forEach((label) =>
        expect(screen.getByRole('tab', { name: label })).toBeTruthy(),
      );

      // `builder` is the default, so its panel copy must be on the surface too — a tab strip
      // above an empty frame is the "renders only chrome" case.
      expect(
        screen.getByRole('heading', { level: 3, name: 'Seven block families, connected by type' }),
      ).toBeTruthy();
      expect(screen.getByRole('tabpanel')).toBeTruthy();
    });

    it('wires the tabs to their panel, which the previous version did not', () => {
      // It put `role="tab"` on buttons with no `tablist` parent, no `aria-controls` and no id
      // on the panel, so a screen reader announced four tabs belonging to nothing.
      mountRouted(<ProductTour />);

      const tablist = screen.getByRole('tablist', { name: 'Product areas' });
      expect(tablist).toBeTruthy();

      const selected = screen.getByRole('tab', { selected: true });
      const panel = screen.getByRole('tabpanel');

      expect(selected.getAttribute('aria-controls')).toBe(panel.getAttribute('id'));
      expect(panel.getAttribute('aria-labelledby')).toBe(selected.getAttribute('id'));
    });
  });

  describe('7 — MarketplaceSection', () => {
    it('renders the three steps and links to the public library route', () => {
      mountRouted(<MarketplaceSection />);

      expect(
        screen.getByRole('heading', { level: 2, name: /Don’t Build Everything From Scratch/ }),
      ).toBeTruthy();

      // Discover → Subscribe → Deploy. The two-column "if you are looking / if you are
      // publishing" layout is gone: publishing now has its own section (`CreatorSection`), because
      // a creator pitch sharing a panel with a buyer pitch gave neither room to state its plan
      // boundary.
      ['Discover', 'Subscribe', 'Deploy'].forEach((heading) =>
        expect(screen.getByRole('heading', { level: 3, name: heading })).toBeTruthy(),
      );

      const link = screen.getByRole('link', { name: /Explore Marketplace/ });
      expect(link.getAttribute('href')).toBe('/marketplace');
    });

    it('states where subscribing starts, so the boundary is not discovered from a 403', () => {
      mountRouted(<MarketplaceSection />);
      expect(screen.getByText(/Marketplace subscriptions start with Trader/)).toBeTruthy();
    });

    it('claims the revenue share only where the settlement ledger backs it', () => {
      /*
        THIS ASSERTION IS INVERTED, AND THE INVERSION IS THE POINT.

        It previously required that NO revenue share appear anywhere on this section, because
        `.kiro/specs/marketplace-subscriptions-paper-trading/requirements.md` classified
        `create_marketplace_checkout` BROKEN, `renew_subscription` BROKEN (it granted access with
        no payment), the 90/10 split BROKEN (it queried two columns that did not exist and
        returned a fabricated zero through a bare `except`) and the payout ledger MISSING.
        Advertising a revenue stream with no settlement behind it would have been the most
        expensive sentence on the page, and withholding it was right.

        All three are implemented now, and each has a file to point at:

          * `backend/marketplace/checkout_service.create_checkout` prices from the Listing's
            integer `price_minor` and returns a real provider session.
          * `backend/marketplace/settlement_service.settle` is the single writer of
            `marketplace_settlements` (migration `008_marketplace_settlement.sql`) and the only
            path permitted to set `library_subscriptions.status = 'active'` — enforced by
            `trg_subscription_transition_guard` in the database, not by convention.
          * `backend/marketplace/money.split_ninety_ten` computes the creator's 90% and takes the
            platform fee as the integer RESIDUAL, and `chk_settlement_conserved` makes a row that
            does not sum to the amount charged unrepresentable.

        So the claim moves to `CreatorSection`, where it belongs, and this section keeps its own
        job: what a BUYER does. What stays forbidden here is any earnings OUTCOME — no subscriber
        counts, no revenue figures, no "top creators earn ₹X" — because nothing in this repository
        produces those numbers.
      */
      mountRouted(<MarketplaceSection />);

      expect(screen.queryByText(/90%/)).toBeNull();
      expect(screen.queryByText(/creators earn/i)).toBeNull();
      expect(screen.queryByText(/per month/i)).toBeNull();
    });
  });

  describe('7b — CreatorSection', () => {
    it('states the published split and the plans that can publish', () => {
      mountRouted(<CreatorSection />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'Turn Your Strategy Into Recurring Income' }),
      ).toBeTruthy();

      expect(screen.getByText('90%')).toBeTruthy();
      expect(screen.getByText('10%')).toBeTruthy();
      expect(
        screen.getByText('Marketplace publishing is available to Pro Quant and Business users.'),
      ).toBeTruthy();
    });

    it('describes the share as net revenue rather than gross payment', () => {
      // `settlement_service` records reversals as their own settlement rows, which net out of a
      // creator's earnings. "90% of every rupee" would be a claim about gross payment that the
      // settlement path does not make.
      mountRouted(<CreatorSection />);
      expect(
        screen.getByText(/payment, refund and chargeback handling/),
      ).toBeTruthy();
    });

    it('claims no earnings outcome', () => {
      const { container } = mountRouted(<CreatorSection />);
      // A published commercial term can be stated. An outcome cannot: nothing here measures one.
      expect(container.textContent).not.toMatch(/₹[\d,]+ (a|per) month/);
      expect(container.textContent).not.toMatch(/average earnings/i);
    });

    it('routes an unentitled visitor to signup rather than to a surface that would refuse them', () => {
      // Anonymous on this page, so `useFeature` reports not-permitted and the CTA is signup. A
      // button leading to a publishing surface the caller cannot use is a 403 with extra steps.
      mountRouted(<CreatorSection />);
      const cta = screen.getByRole('link', { name: /Become a Strategy Creator/ });
      expect(cta.getAttribute('href')).toBe('/signup');
    });
  });

  describe('7c — WorkflowSection', () => {
    it('renders the seven stages and marks where each becomes available', () => {
      mountRouted(<WorkflowSection />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'From Idea to Trading System' }),
      ).toBeTruthy();

      ['Build', 'Backtest', 'Paper trade', 'Automate', 'Optimize', 'Publish', 'Earn'].forEach(
        (stage) => expect(screen.getByText(stage), `${stage} is missing`).toBeTruthy(),
      );

      // The plan boundary is part of the diagram, not a footnote: a visitor following the arc from
      // Build to Earn should meet it here rather than in a refusal.
      expect(screen.getAllByText('All plans').length).toBe(3);
      expect(screen.getAllByText('Pro Quant+').length).toBe(2);
    });
  });

  describe('8 — SecuritySection', () => {
    it('renders the security heading and the four controls it claims', () => {
      mountRouted(<SecuritySection />);

      expect(
        screen.getByRole('heading', {
          level: 2,
          name: 'Controls that are implemented, described plainly',
        }),
      ).toBeTruthy();

      [
        'Exchange keys are encrypted, never plaintext',
        'Per-tenant isolation at the database',
        'Role-based access control',
        'Audit trail on orders and strategy edits',
      ].forEach((title) => expect(screen.getByText(title)).toBeTruthy());
    });

    it('claims no certification it does not hold', () => {
      // The section opened with an "Enterprise Security" pill. No SOC 2, ISO 27001 or
      // pen-test report exists in this repository.
      mountRouted(<SecuritySection />);

      expect(screen.queryByText('Enterprise Security')).toBeNull();
      expect(screen.getByText(/We hold no third-party security certification yet/)).toBeTruthy();
    });
  });

  describe('9 — DownloadSection', () => {
    /** The four withdrawn artifacts, in the order the section renders them. */
    const WITHDRAWN = ['windows', 'macos', 'linuxAppImage', 'linuxDeb'];

    it('renders the platform heading, the web card, and every artifact with its declared reason', () => {
      mountRouted(<DownloadSection />);

      expect(
        screen.getByRole('heading', {
          level: 2,
          name: 'Trade in the browser today, on your desktop shortly',
        }),
      ).toBeTruthy();

      // The one thing this section can offer, and the link the download suite also pins.
      expect(screen.getByRole('heading', { level: 3, name: 'Web platform' })).toBeTruthy();
      expect(screen.getByRole('link', { name: /Launch in browser/ }).getAttribute('href')).toBe(
        '/app',
      );

      // One `unavailable` marker per withdrawn artifact — no more, no fewer.
      expect(screen.getAllByText('Not available')).toHaveLength(WITHDRAWN.length);

      WITHDRAWN.forEach((platform) => {
        const field = artifactFieldFor(platform);
        expect(screen.getByText(field.label)).toBeTruthy();
        // The declared human reason, which is what makes the marker a withdrawal rather than
        // a blank card (Requirement 19.3).
        expect(screen.getByText(field.reason)).toBeTruthy();
      });
    });

    it('announces desktop as a real platform without promising a file', () => {
      // The section that was proposed for deletion and kept. Both halves have to be true at
      // once: desktop is coming, and no installer is served today.
      mountRouted(<DownloadSection />);

      expect(screen.getByText('In final testing')).toBeTruthy();
      expect(screen.getByText(/Native terminals for Windows, macOS and Linux/)).toBeTruthy();
      expect(screen.getByText('Available now · free tier included')).toBeTruthy();
    });
  });

  describe('10 — Pricing', () => {
    it('renders the pricing headline, all five plan names and their rupee figures', () => {
      // The five INR figures match `backend_app/core/subscription_engine.py`, and
      // `landing_page_pricing_crash_regression.test.jsx` owns them in depth — including the
      // published annual prices and the capacity table. What is checked here is only that this
      // section renders at all and names every plan, which is this file's job.
      //
      // `Institutional` became `Business`: the DISPLAY name of the same ₹2,499 plan, whose stored
      // identifier (`enterprise`) is deliberately unchanged. `Enterprise` is the new, quoted plan
      // above it.
      mountRouted(<Pricing />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'Start Small. Scale When You Need To.' }),
      ).toBeTruthy();

      ['Free', 'Trader', 'Pro Quant', 'Business', 'Enterprise'].forEach((name) =>
        expect(screen.getAllByText(name).length, `${name} is missing`).toBeGreaterThan(0),
      );
      ['₹0', '₹499', '₹999', '₹2,499'].forEach((price) =>
        expect(screen.getByText(price)).toBeTruthy(),
      );
      expect(screen.getByText('Most Popular')).toBeTruthy();
    });
  });

  describe('11 — FAQ', () => {
    it('renders the questions heading and the first question of each group', () => {
      mountRouted(<FAQ />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'Questions worth asking first' }),
      ).toBeTruthy();

      expect(
        screen.getByRole('button', { name: 'Do I need to know how to code?' }),
      ).toBeTruthy();
      expect(
        screen.getByRole('button', { name: 'Which exchanges can I connect?' }),
      ).toBeTruthy();

      // Both groups open their first item by default, so an answer must be on the surface.
      expect(screen.getByText(/Strategies are built by connecting blocks on a canvas/)).toBeTruthy();
    });

    it('answers the desktop and exchange questions truthfully', () => {
      mountRouted(<FAQ />);

      // The old answer named five venues as "supported" and claimed "50+ exchanges".
      expect(
        screen.getByRole('button', { name: 'Is the desktop app available?' }),
      ).toBeTruthy();
      expect(screen.queryByText(/50\+ exchanges/)).toBeNull();
      // "Elite" was a tier from an older price list that no longer exists.
      expect(screen.queryByText(/Elite/)).toBeNull();
    });
  });

  describe('12 — FinalCTA', () => {
    it('renders the closing badge, heading and a single call to action', () => {
      mountRouted(<FinalCTA />);

      expect(screen.getByText('Early access is open')).toBeTruthy();
      expect(
        screen.getByRole('heading', { level: 2, name: 'Build your first strategy today' }),
      ).toBeTruthy();

      // ONE. It offered three, two of which led to unavailable installers.
      const links = screen.getAllByRole('link');
      const downloads = links.filter((a) => /download/i.test(a.getAttribute('href') ?? ''));
      expect(downloads).toHaveLength(0);
      expect(screen.getByRole('link', { name: /Start free/ })).toBeTruthy();
    });
  });

  describe('13 — Footer', () => {
    it('renders the contentinfo landmark, its column headings, the legal links and the disclaimer', () => {
      mountRouted(<Footer />);

      expect(screen.getByRole('contentinfo', { name: 'Footer' })).toBeTruthy();

      ['Platform', 'Get started', 'Legal'].forEach((column) =>
        expect(screen.getByRole('navigation', { name: column })).toBeTruthy(),
      );

      ['Privacy Policy', 'Terms of Service', 'Risk Disclosure', 'Refund Policy'].forEach((label) =>
        expect(screen.getByRole('link', { name: label })).toBeTruthy(),
      );

      // Verbatim, and the page's only legally load-bearing copy.
      expect(
        screen.getByText(/Algorithmic trading involves substantial risk of loss/),
      ).toBeTruthy();
      expect(screen.getByText(/not a registered investment adviser/)).toBeTruthy();
    });

    it('names no installer file and offers the library route', () => {
      // `Windows (.exe)` and `macOS (.dmg)` were links into withdrawn artifacts. A file
      // extension in a label is a promise of a file.
      mountRouted(<Footer />);

      expect(screen.queryByText(/\.exe/)).toBeNull();
      expect(screen.queryByText(/\.dmg/)).toBeNull();
      expect(screen.getByRole('link', { name: 'Strategy library' }).getAttribute('href')).toBe(
        '/marketplace',
      );
    });
  });
});

// ---------------------------------------------------------------------------
// The two removals that need holding, asserted over the whole page
// ---------------------------------------------------------------------------
//
// Section-level assertions above say what each part renders. These two say what the page as a
// whole must NOT render, which is the half that rots first: a removal nothing asserts is a
// removal the next contributor undoes without noticing.

describe('Landing_Surface — the withdrawn claims stay withdrawn', () => {
  const mountPage = () =>
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );

  it('carries no invented performance figure', () => {
    // Every one of these was on the page, in brand colour, under a 10px "illustrative"
    // caption. None came from a backtest, and the footer on the same page states VyomQuant
    // is not a registered investment adviser.
    const { container } = mountPage();
    const text = container.textContent;

    for (const figure of [
      '2.1',
      '67.4',
      '8.2',
      '28.4',
      '2.14',
      '3.08',
      '1.82',
      '89,420.50',
      '10,000.00',
    ]) {
      expect(text, `the withdrawn figure ${figure} is back on the page`).not.toContain(figure);
    }

    // The words too, since a different number under the same label is the same claim.
    for (const label of ['Sharpe', 'Sortino', 'Win Rate', 'Profit Factor', 'Net Return']) {
      expect(text, `${label} is being reported as a result again`).not.toContain(label);
    }
  });

  it('links into no withdrawn installer, from any section', () => {
    // Eleven sites advertised these across `Hero`, `Navbar`, `FinalCTA` and `Footer`, while
    // `DownloadSection` said on the same page that the builds are not published.
    const { container } = mountPage();

    for (const anchor of container.querySelectorAll('a[href]')) {
      const href = anchor.getAttribute('href');
      expect(href, `${href} points into releases/`).not.toContain('releases');
      expect(href, `${href} deep-links a withdrawn artifact`).not.toMatch(/#(windows|macos)$/);
    }
    expect(container.querySelectorAll('[download]')).toHaveLength(0);
  });

  it('resolves every in-page scroll target it offers', () => {
    // `Footer` shipped `{ label: 'Platform Capabilities', href: '#features' }` for as long as
    // the file existed. No element ever had that id, so `querySelector('#features')` returned
    // null and the button was inert — a control that looks live and does nothing.
    const { container } = mountPage();

    const targets = [
      '#platform',
      '#workflow',
      '#tour',
      '#marketplace',
      '#security',
      '#download',
      '#pricing',
      '#faq',
    ];
    for (const target of targets) {
      expect(container.querySelector(target), `${target} has no section`).not.toBeNull();
    }
    expect(container.querySelector('#features')).toBeNull();
  });
});
