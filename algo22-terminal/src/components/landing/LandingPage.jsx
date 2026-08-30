import React from 'react'
import Navbar from './Navbar'
import Hero from './Hero'
import TrustSection from './TrustSection'
import ScreenshotsSection from './ScreenshotsSection'
import HowItWorks from './HowItWorks'
import ModernTradingSection from './ModernTradingSection'
import SecuritySection from './SecuritySection'
import FounderSection from './FounderSection'
import DownloadSection from './DownloadSection'
import Pricing from './Pricing'
import FAQ from './FAQ'
import Waitlist from './Waitlist'
import FinalCTA from './FinalCTA'
import Footer from './Footer'

export default function LandingPage() {
  return (
    <div className="relative">
      <Navbar />
      <Hero />
      <TrustSection />
      <ScreenshotsSection />
      <HowItWorks />
      <ModernTradingSection />
      <SecuritySection />
      <FounderSection />
      <DownloadSection />
      <Pricing />
      <FAQ />
      <Waitlist />
      <FinalCTA />
      <Footer />
    </div>
  )
}

