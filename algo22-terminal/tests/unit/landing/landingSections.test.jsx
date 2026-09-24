/**
 * tests/unit/landing/landingSections.test.jsx — retail-ui-simplification task 10.2.
 *
 * Requirements 13.1, 13.2, 13.3, 13.6, 18.1.
 *
 * WHAT THIS FILE IS
 * -----------------
 * The second half of a measurement, not a fix. Requirement 13 asks for the reported
 * landing-page breakage to be reproduced before anything is changed, and Requirement 13.5
 * makes "no symptom reproduces" a legitimate recorded result. Nothing here asserts a defect
 * and nothing here invents one.
 *
 * Step 1 (task 10.1, commit 62c1e0e) mounted the whole tree and checked the three in-page
 * anchors: `#pricing`, `#architecture` and `#waitlist` are all present, the tree mounts
 * without throwing, and every `Navbar` scroll target resolves to a section the page renders.
 * That ruled out a render failure and a navigation failure **in jsdom**, and reduced
 * Requirement 13.3's five candidate symptoms to three: a layout fault at a specific viewport,
 * a missing or 403 asset, and incorrect content.
 *
 * A throw-free tree is not a content-bearing tree. `LandingPage.jsx` renders all fourteen
 * sections unconditionally, with no error boundary and no lazy loading, so there is no
 * boundary that could be swallowing a throw — which leaves the other failure mode the plan
 * names: a section that mounts successfully and renders nothing, or renders only chrome.
 * An empty `<section>` throws nothing, so step 1 could not see it. This file mounts each
 * section on its own and asserts it carries its own copy.
 *
 * THIRTEEN OR FOURTEEN
 * --------------------
 * The plan's heading says "the 13 sections" and Requirement 13.2 repeats it, but the list in
 * the same clause names fourteen and `LandingPage.jsx` renders fourteen children:
 * `Navbar`, `Hero`, `TrustSection`, `ScreenshotsSection`, `HowItWorks`, `ModernTradingSection`,
 * `SecuritySection`, `FounderSection`, `DownloadSection`, `Pricing`, `FAQ`, `Waitlist`,
 * `FinalCTA`, `Footer`. **The "13" is a miscount** — Requirement 15.1 says fourteen, and the
 * tree agrees with 15.1. There are fourteen `it` blocks below, one per rendered child. None
 * was dropped to make the count match the heading.
 *
 * HOW THE ASSERTIONS AVOID BEING VACUOUS
 * --------------------------------------
 * "Renders at least one element carrying its own text" is trivially easy to write so that it
 * cannot fail. `render()` hands back a `container` for an empty div too, so
 * `expect(container).toBeDefined()` and `expect(container.textContent).toBeDefined()` both pass
 * on nothing. Task 10.1 existed precisely because three assertions in the end-to-end test were
 * vacuous in that shape — `expect(container.querySelector('#pricing')).toBeDefined()` passes
 * when the selector returns `null`.
 *
 * So every assertion here is `getByText` / `getByRole` — queries that throw when the thing is
 * absent — against a string or an accessible role that the section in question owns, read out
 * of that component's source rather than guessed. No `querySelector` plus a truthiness check.
 *
 * The teeth were checked rather than assumed. Two sections — `TrustSection` and
 * `DownloadSection` — were temporarily asserted against copy they do not contain
 * (`'Built for Unserious Systematic Traders'`, and a fabricated installer reason). Both `it`
 * blocks failed with a "Unable to find an element with the text" error naming the missing
 * string, which is the failure mode a genuinely empty section would produce. The probes were
 * reverted; only the real strings are below.
 *
 * `DownloadSection` — THE WITHDRAWAL IS LOAD-BEARING
 * -------------------------------------------------
 * production-launch-hardening task 4.3 withdrew four advertised desktop installers: all four
 * `/releases/…` URLs return 403, and the cards now render `ds/Panel`'s `unavailable` state
 * carrying the reason declared in `design/pageFields`. `tests/unit/pages/downloadSurface.test.jsx`
 * holds that guarantee — every card states unavailable **with its declared reason**, no request
 * is issued (proved at the hook and at both surfaces, with `fetch`, `XMLHttpRequest` and
 * `HTMLAnchorElement.click` all recording), and no size or checksum travels with the marker.
 *
 * Nothing in this file re-advertises an installer. The `DownloadSection` case below asserts
 * the withdrawal copy — the four `Not available` markers and the four declared reasons, read
 * from the declaration itself so there is one spelling. It asserts no installer, no version,
 * no size and no checksum, and it neither issues nor expects a request.
 *
 * THE ROUTER SPLIT IS PART OF THE MEASUREMENT
 * -------------------------------------------
 * Nine of the fourteen reach for `react-router-dom` (`Link`, directly or one layer down through
 * `WaitlistForm`) and are mounted inside `MemoryRouter`. The other five — `TrustSection`,
 * `ModernTradingSection`, `SecuritySection`, `FounderSection`, `FAQ` — import nothing from the
 * router and are mounted bare on purpose, so that a future `Link` added to one of them shows up
 * here as a failure rather than being absorbed by a blanket wrapper. No section needed any other
 * provider, and none is stubbed: `WaitlistForm`, `ds/Panel`, `usePanelState`, `design/pageFields`
 * and `ui/Accordion` are all the real modules.
 *
 * RESULT OF THE MEASUREMENT
 * -------------------------
 * All fourteen sections render their own content. **No section renders empty and none renders
 * chrome only**, so the fault is not "a section is missing" and, combined with 10.1, the reported
 * symptom is presentational or environmental rather than structural. Requirement 13.3's three
 * surviving candidates are unchanged by this step and all three still stand: a layout fault at a
 * specific viewport, a missing or 403 asset, and incorrect content. None of the three is
 * observable in jsdom — the first needs a real viewport, the second a real network, the third the
 * requester's own description of what the page should say. That is the question task 10.3 puts.
 */

import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import Navbar from '../../../src/components/landing/Navbar';
import Hero from '../../../src/components/landing/Hero';
import TrustSection from '../../../src/components/landing/TrustSection';
import ScreenshotsSection from '../../../src/components/landing/ScreenshotsSection';
import HowItWorks from '../../../src/components/landing/HowItWorks';
import ModernTradingSection from '../../../src/components/landing/ModernTradingSection';
import SecuritySection from '../../../src/components/landing/SecuritySection';
import FounderSection from '../../../src/components/landing/FounderSection';
import DownloadSection from '../../../src/components/landing/DownloadSection';
import Pricing from '../../../src/components/landing/Pricing';
import FAQ from '../../../src/components/landing/FAQ';
import Waitlist from '../../../src/components/landing/Waitlist';
import FinalCTA from '../../../src/components/landing/FinalCTA';
import Footer from '../../../src/components/landing/Footer';

import { artifactFieldFor } from '../../../src/components/download/PlatformArtifact';

afterEach(cleanup);

/** For the nine sections that render a router `Link`. */
const mountRouted = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

/**
 * For the five that import nothing from `react-router-dom`. Deliberately unwrapped: a `Link`
 * added to one of these later throws "useHref may be used only in the context of a Router",
 * and this file is where that shows up.
 */
const mountBare = (ui) => render(ui);

describe('Landing_Surface — each rendered section carries its own content (task 10.2)', () => {
  describe('1 — Navbar', () => {
    it('renders the navigation landmark, the wordmark and its five scroll targets', () => {
      mountRouted(<Navbar />);

      expect(screen.getByRole('navigation', { name: 'Main Navigation' })).toBeTruthy();
      expect(screen.getAllByText('VyomQuant').length).toBeGreaterThan(0);

      ['Platform', 'Architecture', 'Security', 'Pricing', 'FAQ'].forEach((label) => {
        expect(screen.getByRole('button', { name: label })).toBeTruthy();
      });
      expect(screen.getByRole('link', { name: 'Sign In' })).toBeTruthy();
    });
  });

  describe('2 — Hero', () => {
    it('renders the beta badge, the h1 headline and both primary calls to action', () => {
      mountRouted(<Hero />);

      expect(screen.getByText('Early Access Beta — Quantitative SaaS')).toBeTruthy();
      expect(
        screen.getByRole('heading', {
          level: 1,
          name: 'Systematic Quantitative Infrastructure Without Writing Code',
        }),
      ).toBeTruthy();
      expect(screen.getByText('Web Application')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'Explore Architecture' })).toBeTruthy();
    });
  });

  describe('3 — TrustSection', () => {
    it('renders its heading and all six capability cards', () => {
      mountBare(<TrustSection />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'Built for Serious Systematic Traders' }),
      ).toBeTruthy();

      [
        'Visual DAG Strategy Builder',
        'VectorBT-Powered Backtesting',
        'Paper Trading Environment',
        'Institutional Risk Controls',
        'Multi-Exchange Connectivity',
        'Institutional Portfolio Analytics',
      ].forEach((title) => expect(screen.getByText(title)).toBeTruthy());
    });
  });

  describe('4 — ScreenshotsSection', () => {
    it('renders the architecture heading, its four engine tabs and the default tab body', () => {
      mountRouted(<ScreenshotsSection />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'Engineered for Systematic Precision' }),
      ).toBeTruthy();

      [
        'Visual DAG Builder',
        'VectorBT Simulation',
        'Paper Trading Terminal',
        'Risk Control Center',
      ].forEach((label) => expect(screen.getByRole('tab', { name: label })).toBeTruthy());

      // The `builder` tab is the default, so its panel copy must be on the surface too —
      // a tab strip above an empty frame is the "renders only chrome" case.
      expect(
        screen.getByRole('heading', {
          level: 3,
          name: 'Design Complex Systematic Rules Visually',
        }),
      ).toBeTruthy();
    });
  });

  describe('5 — HowItWorks', () => {
    it('renders the pipeline heading and all four numbered steps', () => {
      mountRouted(<HowItWorks />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'The Systematic Pipeline' }),
      ).toBeTruthy();

      [
        'Construct DAG Rules',
        'VectorBT Simulation',
        'Configure Risk Guards',
        'Forward Test & Deploy',
      ].forEach((title) => expect(screen.getByText(title)).toBeTruthy());

      ['STEP 01', 'STEP 02', 'STEP 03', 'STEP 04'].forEach((step) =>
        expect(screen.getByText(step)).toBeTruthy(),
      );
    });
  });

  describe('6 — ModernTradingSection', () => {
    it('renders its heading and all four capability cards', () => {
      mountBare(<ModernTradingSection />);

      expect(
        screen.getByRole('heading', { level: 2, name: 'Built for Modern Systematic Trading' }),
      ).toBeTruthy();

      [
        'Native Execution Engine',
        'Secure Local Execution',
        'Extensible Infrastructure',
        'Advanced Order Types',
      ].forEach((title) => expect(screen.getByText(title)).toBeTruthy());
    });
  });

  describe('7 — SecuritySection', () => {
    it('renders the security heading and all six controls it claims', () => {
      mountBare(<SecuritySection />);

      expect(screen.getByText('Enterprise Security')).toBeTruthy();
      expect(
        screen.getByRole('heading', { level: 2, name: 'Security & Infrastructure' }),
      ).toBeTruthy();

      [
        'AES-256 Encryption',
        'Secure API Key Storage',
        'Role-Based Access Control',
        'Audit Logging',
        'Secure Authentication',
        'Protected Trading Infrastructure',
      ].forEach((title) => expect(screen.getByText(title)).toBeTruthy());
    });
  });

  describe('8 — FounderSection', () => {
    it('renders the founder heading, the name, the credential and the mission statement', () => {
      mountBare(<FounderSection />);

      expect(screen.getByRole('heading', { level: 2, name: 'Founder' })).toBeTruthy();
      expect(screen.getByRole('heading', { level: 3, name: 'Narendra Tripathi' })).toBeTruthy();
      expect(screen.getByText('Founder, VyomQuant')).toBeTruthy();
      expect(screen.getByText('NIT Andhra Pradesh Alumnus')).toBeTruthy();
      expect(
        screen.getByText(
          'Make institutional-grade systematic trading infrastructure accessible to every trader.',
        ),
      ).toBeTruthy();
    });
  });

  describe('9 — DownloadSection', () => {
    /**
     * The four withdrawn artifacts, in the order the section renders them. The reason is read
     * from `design/pageFields` through `artifactFieldFor`, which is the same lookup the
     * component uses — asserting a second copy of the sentence here would be a second place to
     * keep it in step.
     */
    const WITHDRAWN = ['windows', 'macos', 'linuxAppImage', 'linuxDeb'];

    it('renders its heading, the web platform card, and every withdrawn artifact stating unavailable with its declared reason', () => {
      mountRouted(<DownloadSection />);

      expect(screen.getByRole('heading', { level: 2, name: 'Choose Your Platform' })).toBeTruthy();

      // The one thing this section can honestly offer, and the sentence that replaced the
      // installer advertisement.
      expect(screen.getByRole('heading', { level: 3, name: 'Web Platform' })).toBeTruthy();
      expect(
        screen.getByText(
          'Trade in your browser on our high-performance web platform. The native desktop'
            + ' terminals for Windows, macOS and Linux are not published yet.',
        ),
      ).toBeTruthy();

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
  });

  describe('10 — Pricing', () => {
    it('renders the tier heading, all four plan names and their rupee figures', () => {
      mountRouted(<Pricing />);

      expect(screen.getByRole('heading', { level: 2, name: 'Infrastructure Tiers' })).toBeTruthy();

      ['Free / Sandbox', 'Trader', 'Pro Quant', 'Institutional'].forEach((name) =>
        expect(screen.getByText(name)).toBeTruthy(),
      );
      ['₹0', '₹499', '₹999', '₹2,499'].forEach((price) =>
        expect(screen.getByText(price)).toBeTruthy(),
      );
      expect(screen.getByText('Recommended')).toBeTruthy();
    });
  });

  describe('11 — FAQ', () => {
    it('renders the questions heading and the first question of each group', () => {
      mountBare(<FAQ />);

      expect(screen.getByRole('heading', { level: 2, name: 'Common Questions' })).toBeTruthy();

      expect(
        screen.getByRole('button', { name: 'Do I need coding experience to use VyomQuant?' }),
      ).toBeTruthy();
      expect(
        screen.getByRole('button', { name: 'Is my exchange API key information secure?' }),
      ).toBeTruthy();

      // Both groups open their first item by default, so an answer must be on the surface too.
      expect(
        screen.getByText(
          'No. VyomQuant is built as a visual node interface. All strategy logic is constructed'
            + ' via drag-and-drop DAG. Programming knowledge is not required.',
        ),
      ).toBeTruthy();
    });
  });

  describe('12 — Waitlist', () => {
    it('renders the waitlist heading, its three numbered steps and the real form', () => {
      mountRouted(<Waitlist />);

      expect(screen.getByText('Priority Access')).toBeTruthy();
      expect(screen.getByRole('heading', { level: 2, name: 'Stay in the Loop' })).toBeTruthy();

      ['Submit your details', 'Receive updates', 'Get priority support'].forEach((step) =>
        expect(screen.getByText(step)).toBeTruthy(),
      );

      // `WaitlistForm` is the section's whole right-hand column. If it rendered nothing the
      // heading above would still be here, which is the half-empty case this catches.
      expect(screen.getByRole('button', { name: /Request Early Access/ })).toBeTruthy();
      expect(screen.getByLabelText(/Full Name/)).toBeTruthy();
      expect(screen.getByLabelText(/Email Address/)).toBeTruthy();
    });
  });

  describe('13 — FinalCTA', () => {
    it('renders the closing badge, heading and the browser call to action', () => {
      mountRouted(<FinalCTA />);

      expect(screen.getByText('Early Access — Apply Now')).toBeTruthy();
      expect(
        screen.getByRole('heading', { level: 2, name: 'Deploy Your First System' }),
      ).toBeTruthy();
      expect(screen.getByRole('link', { name: /Get Started Free/ })).toBeTruthy();
    });
  });

  describe('14 — Footer', () => {
    it('renders the contentinfo landmark, its four column headings, the legal links and the risk disclaimer', () => {
      mountRouted(<Footer />);

      expect(screen.getByRole('contentinfo', { name: 'Footer' })).toBeTruthy();
      expect(
        screen.getByText('Institutional-grade systematic trading infrastructure.'),
      ).toBeTruthy();

      ['// PLATFORM', '// DOWNLOADS', '// COMPANY', '// LEGAL'].forEach((column) =>
        expect(screen.getByText(column)).toBeTruthy(),
      );

      ['Privacy Policy', 'Terms of Service', 'Risk Disclosure', 'Refund Policy'].forEach((label) =>
        expect(screen.getByRole('link', { name: label })).toBeTruthy(),
      );

      expect(screen.getByText(/Algorithmic trading involves substantial risk of loss/)).toBeTruthy();
    });
  });
});
