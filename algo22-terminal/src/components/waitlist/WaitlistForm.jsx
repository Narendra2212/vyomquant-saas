import React, { useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, CheckCircle, AlertCircle, Loader2, User, Mail, MessageCircle, BarChart3, Briefcase, TrendingUp } from 'lucide-react'
import { waitlistApi } from '../../lib/waitlistApi'
import { useWaitlistValidation } from '../../hooks/useWaitlistValidation'

const TRADER_TYPE_OPTIONS = [
  { value: 'retail', label: 'Retail Quant' },
  { value: 'discretionary', label: 'Discretionary Trader' },
  { value: 'prop', label: 'Proprietary Trading Firm' },
  { value: 'institutional', label: 'Institutional / Family Office' },
]

const EXPERIENCE_OPTIONS = [
  { value: 'beginner', label: 'Beginner — New to algorithmic trading' },
  { value: 'intermediate', label: 'Intermediate — Some backtesting experience' },
  { value: 'advanced', label: 'Advanced — Live systematic strategies' },
  { value: 'professional', label: 'Professional — Institutional or prop desk' },
]

const VOLUME_OPTIONS = [
  { value: '<100k', label: 'Less than $100k' },
  { value: '100k-1M', label: '$100k — $1M' },
  { value: '1M-10M', label: '$1M — $10M' },
  { value: '>10M', label: 'Over $10M' },
]

export default function WaitlistForm() {
  const [formData, setFormData] = useState({ name: '', email: '', telegram: '', trader_type: '', experience_level: '', monthly_volume: '' })
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [submitError, setSubmitError] = useState(null)
  const [existingEntry, setExistingEntry] = useState(null)

  const { errors, touched, validate, validateAll, touch, clear } = useWaitlistValidation()

  const handleChange = useCallback((field, value) => {
    setFormData(prev => ({ ...prev, [field]: value }))
    if (touched[field]) {
      validate(field, value)
    }
  }, [touched, validate])

  const handleBlur = useCallback((field) => {
    touch(field)
    validate(field, formData[field])
  }, [touch, validate, formData])

  const handleSubmit = async (e) => {
    e.preventDefault()
    
    if (!validateAll(formData)) return

    setSubmitting(true)
    setSubmitError(null)

    try {
      const urlParams = new URLSearchParams(window.location.search)
      const submitData = {
        ...formData,
        utm_source: urlParams.get('utm_source'),
        utm_medium: urlParams.get('utm_medium')
      }

      await waitlistApi.submit(submitData)
      setSubmitted(true)
    } catch (err) {
      if (err.message.includes('already on the waitlist')) {
        setExistingEntry(true)
      } else {
        setSubmitError(err.message || 'Something went wrong. Please try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  if (submitted || existingEntry) {
    return (
      <div className="card-surface p-8 text-center border-accent-profit/20 bg-accent-profit/5">
        <div className="w-16 h-16 rounded-full bg-accent-profit/20 flex items-center justify-center mx-auto mb-6">
          <CheckCircle className="w-8 h-8 text-accent-profit" />
        </div>
        <h3 className="text-2xl font-bold text-text-primary mb-3">
          {existingEntry ? "You're already on the list!" : "You're on the waitlist!"}
        </h3>
        <p className="text-text-secondary leading-relaxed mb-8 max-w-md mx-auto">
          {existingEntry 
            ? `We've already saved a spot for ${formData.email}. We'll notify you as soon as your access is ready.`
            : `Thank you for requesting early access. We review applications daily and will send an invitation link to ${formData.email} soon.`}
        </p>
        <button
          onClick={() => {
            setFormData({ name: '', email: '', telegram: '', trader_type: '', experience_level: '', monthly_volume: '' })
            setSubmitted(false)
            setExistingEntry(false)
            clear()
          }}
          className="text-sm font-semibold text-accent-cyan hover:text-accent-cyan/80 transition-colors"
        >
          Submit another application
        </button>
      </div>
    )
  }

  return (
    <div className="card-surface p-8 border border-border-default shadow-xl">
      <form onSubmit={handleSubmit} className="space-y-5">
        
        {submitError && (
          <div className="p-4 rounded-lg bg-accent-loss/10 border border-accent-loss/20 flex items-start gap-3 text-sm text-text-primary">
            <AlertCircle className="w-5 h-5 text-accent-loss flex-shrink-0 mt-0.5" />
            <p>{submitError}</p>
          </div>
        )}

        <div className="grid grid-cols-2 gap-5">
          <div className="space-y-1.5 col-span-2 sm:col-span-1">
            <label className="text-xs font-semibold text-text-secondary ml-1">Full Name <span className="text-accent-loss">*</span></label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
              <input
                type="text"
                value={formData.name}
                onChange={e => handleChange('name', e.target.value)}
                onBlur={() => handleBlur('name')}
                placeholder="Narendra Tripathi"
                className={`w-full bg-bg-primary border ${errors.name ? 'border-accent-loss' : 'border-border-active'} rounded-xl py-2.5 pl-10 pr-4 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-cyan transition-colors`}
              />
            </div>
            {errors.name && <p className="text-[10px] text-accent-loss ml-1">{errors.name}</p>}
          </div>

          <div className="space-y-1.5 col-span-2 sm:col-span-1">
            <label className="text-xs font-semibold text-text-secondary ml-1">Email Address <span className="text-accent-loss">*</span></label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
              <input
                type="email"
                value={formData.email}
                onChange={e => handleChange('email', e.target.value)}
                onBlur={() => handleBlur('email')}
                placeholder="you@example.com"
                className={`w-full bg-bg-primary border ${errors.email ? 'border-accent-loss' : 'border-border-active'} rounded-xl py-2.5 pl-10 pr-4 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-cyan transition-colors`}
              />
            </div>
            {errors.email && <p className="text-[10px] text-accent-loss ml-1">{errors.email}</p>}
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-semibold text-text-secondary ml-1">Trader Type <span className="text-accent-loss">*</span></label>
          <div className="relative">
            <Briefcase className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <select
              value={formData.trader_type}
              onChange={e => handleChange('trader_type', e.target.value)}
              onBlur={() => handleBlur('trader_type')}
              className={`w-full bg-bg-primary border ${errors.trader_type ? 'border-accent-loss' : 'border-border-active'} rounded-xl py-2.5 pl-10 pr-4 text-sm ${formData.trader_type ? 'text-text-primary' : 'text-text-muted'} focus:outline-none focus:border-accent-cyan transition-colors appearance-none`}
            >
              <option value="" disabled>Select Trader Type</option>
              {TRADER_TYPE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value} className="text-text-primary">{opt.label}</option>
              ))}
            </select>
          </div>
          {errors.trader_type && <p className="text-[10px] text-accent-loss ml-1">{errors.trader_type}</p>}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-semibold text-text-secondary ml-1">Experience Level <span className="text-accent-loss">*</span></label>
          <div className="relative">
            <BarChart3 className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <select
              value={formData.experience_level}
              onChange={e => handleChange('experience_level', e.target.value)}
              onBlur={() => handleBlur('experience_level')}
              className={`w-full bg-bg-primary border ${errors.experience_level ? 'border-accent-loss' : 'border-border-active'} rounded-xl py-2.5 pl-10 pr-4 text-sm ${formData.experience_level ? 'text-text-primary' : 'text-text-muted'} focus:outline-none focus:border-accent-cyan transition-colors appearance-none`}
            >
              <option value="" disabled>Select your experience level</option>
              {EXPERIENCE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value} className="text-text-primary">{opt.label}</option>
              ))}
            </select>
          </div>
          {errors.experience_level && <p className="text-[10px] text-accent-loss ml-1">{errors.experience_level}</p>}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-semibold text-text-secondary ml-1">Expected Monthly Volume <span className="text-accent-loss">*</span></label>
          <div className="relative">
            <TrendingUp className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <select
              value={formData.monthly_volume}
              onChange={e => handleChange('monthly_volume', e.target.value)}
              onBlur={() => handleBlur('monthly_volume')}
              className={`w-full bg-bg-primary border ${errors.monthly_volume ? 'border-accent-loss' : 'border-border-active'} rounded-xl py-2.5 pl-10 pr-4 text-sm ${formData.monthly_volume ? 'text-text-primary' : 'text-text-muted'} focus:outline-none focus:border-accent-cyan transition-colors appearance-none`}
            >
              <option value="" disabled>Select Expected Volume</option>
              {VOLUME_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value} className="text-text-primary">{opt.label}</option>
              ))}
            </select>
          </div>
          {errors.monthly_volume && <p className="text-[10px] text-accent-loss ml-1">{errors.monthly_volume}</p>}
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="w-full inline-flex items-center justify-center gap-2 px-6 py-3.5 mt-2 rounded-xl bg-text-primary text-bg-primary font-bold text-sm hover:bg-text-secondary transition-all disabled:opacity-70 disabled:cursor-not-allowed group"
        >
          {submitting ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <>
              Request Early Access
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </>
          )}
        </button>

        <p className="text-center text-xs text-text-muted font-mono mt-4">
          By requesting access, you agree to our <Link to="/legal/privacy" className="underline hover:text-text-primary">Privacy Policy</Link>.
        </p>
      </form>
    </div>
  )
}
