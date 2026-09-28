/**
 * LandingPage — the public surface at `/`.
 *
 * FOURTEEN SECTIONS BECAME ELEVEN, AND THE ORDER CHANGED
 * -----------------------------------------------------
 * Removed, with approval:
 *
 *   ModernTradingSection  Two of its four claims were false against the backend. There is no TWAP
 *                         and no Iceberg anywhere in `backend_app/`, and the only VWAP is
 *                         `rolling_vwap` in `backend/indicators_backend.py` — an indicator block,
 *                         not an execution algorithm. No FIX implementation exists either. A third
 *                         claim depended on unpublished desktop builds. Its heading ("Built for
 *                         Modern Systematic Trading") also duplicated `TrustSection`'s ("Built for
 *                         Serious Systematic Traders"). Its one durable claim — direct CCXT.pro
 *                         routing with no third-party platform in the path — moved into
 *                         `TrustSection`.
 *
 *   Waitlist              It competed with a signup that works. The section's own copy said "you
 *                         can sign up immediately" and then asked for a waitlist registration
 *                         below it — two funnels arguing over one click. `components/waitlist/
 *                         WaitlistForm.jsx` and the `/admin/waitlist` route are untouched and
 *                         remain available for a future campaign.
 *
 *   FounderSection        A single-founder bio at position 8 of 14, where a visitor is evaluating
 *                         the product rather than the company. Nothing in it was untrue; it was in
 *                         the wrong place, and an About page is the right home for it.
 *
 * Added:
 *
 *   ProofStrip            The credibility slot under the hero. Filled with capability proof —
 *                         every item traceable to a named file — because this repository contains
 *                         no customer count, testimonial, certification or funding claim, and
 *                         inventing one is the easiest lie for a prospect to check.
 *
 *   MarketplaceSection    `/marketplace` is a PUBLIC route and the old page linked to it zero
 *                         times. It is the only place a visitor can judge real strategies without
 *                         creating an account, which makes it the most valuable thing the page was
 *                         hiding.
 *
 * Merged: `TrustSection` (6 cards) and the old `ScreenshotsSection` (4 tabs) previously pitched
 * the same four capabilities as `ModernTradingSection` and `HowItWorks` — twenty feature cards
 * across the page for four features. They now do different jobs: `TrustSection` states each
 * pillar once, and `ProductTour` shows what each one looks like in use.
 *
 * Renamed: `ScreenshotsSection.jsx` → `ProductTour.jsx`. There are no product screenshots on
 * this page and there will not be any — that is a settled product decision, not an outstanding
 * task — so a filename promising them misleads the next reader. The ratchet entries that named
 * the old path moved with it.
 *
 * ORDER, AND WHY
 * --------------
 * Hero → proof → workflow → capability → tour → marketplace → security → platforms → pricing →
 * FAQ → close. Doubt is highest immediately after the hero, so proof comes second; the sequence
 * ("four steps, and you cannot skip the third") comes before the feature detail, because a visitor
 * needs the shape of the product before its inventory. Pricing sits after every claim it has to
 * justify, and the FAQ absorbs the objections that survive pricing.
 *
 * `Pricing` is the one section carried over untouched. Its four INR tiers are a published
 * commitment matching `backend_app/core/subscription_engine.py`, and
 * `tests/unit/landing_page_pricing_crash_regression.test.jsx` pins them. A redesign is not a
 * reason to disturb a price list.
 *
 * `main` with `id="main-content"` gives the page a single landmark and a skip target, which the
 * previous bare `<div className="relative">` did not provide.
 */

import React from 'react'

import Navbar from './Navbar'
import Hero from './Hero'
import ProofStrip from './ProofStrip'
import HowItWorks from './HowItWorks'
import TrustSection from './TrustSection'
import ProductTour from './ProductTour'
import MarketplaceSection from './MarketplaceSection'
import SecuritySection from './SecuritySection'
import DownloadSection from './DownloadSection'
import Pricing from './Pricing'
import FAQ from './FAQ'
import FinalCTA from './FinalCTA'
import Footer from './Footer'

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-surface-canvas">
      <Navbar />
      <main id="main-content">
        <Hero />
        <ProofStrip />
        <HowItWorks />
        <TrustSection />
        <ProductTour />
        <MarketplaceSection />
        <SecuritySection />
        <DownloadSection />
        <Pricing />
        <FAQ />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  )
}
