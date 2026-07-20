import React from 'react'
import Navbar from './Navbar'
import Hero from './Hero'
import TrustSection from './TrustSection'
import SecuritySection from './SecuritySection'
import ScreenshotsSection from './ScreenshotsSection'
import ModernTradingSection from './ModernTradingSection'
import FounderSection from './FounderSection'
import HowItWorks from './HowItWorks'
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
      <SecuritySection />
      <ScreenshotsSection />
      <ModernTradingSection />
      <FounderSection />
      <HowItWorks />
      <DownloadSection />
      <Pricing />
      <FAQ />
      <Waitlist />
      <FinalCTA />
      <Footer />
    </div>
  )
}
