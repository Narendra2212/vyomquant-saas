import { useState, useCallback } from 'react'

const EMAIL_REGEX = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/
const NAME_REGEX = /^[\p{L}\s'-]{2,60}$/u
const TELEGRAM_REGEX = /^@?[a-zA-Z0-9_]{5,32}$/

export function useWaitlistValidation() {
  const [errors, setErrors] = useState({})
  const [touched, setTouched] = useState({})

  const validate = useCallback((field, value) => {
    const newErrors = { ...errors }

    switch (field) {
      case 'name':
        if (!value || value.trim().length < 2) {
          newErrors.name = 'Name must be at least 2 characters.'
        } else if (!NAME_REGEX.test(value.trim())) {
          newErrors.name = 'Name contains invalid characters.'
        } else if (value.trim().length > 60) {
          newErrors.name = 'Name must be under 60 characters.'
        } else {
          delete newErrors.name
        }
        break

      case 'email':
        if (!value || !value.trim()) {
          newErrors.email = 'Email is required.'
        } else if (!EMAIL_REGEX.test(value.trim())) {
          newErrors.email = 'Please enter a valid email address.'
        } else if (value.trim().length > 254) {
          newErrors.email = 'Email is too long.'
        } else {
          delete newErrors.email
        }
        break

      case 'telegram':
        if (value && value.trim() && !TELEGRAM_REGEX.test(value.trim())) {
          newErrors.telegram = 'Invalid Telegram username. Use @username or username (5-32 chars, letters, numbers, underscores).'
        } else {
          delete newErrors.telegram
        }
        break

      case 'experience_level':
        if (!value) {
          newErrors.experience_level = 'Please select your experience level.'
        } else {
          delete newErrors.experience_level
        }
        break

      case 'trader_type':
        if (!value) {
          newErrors.trader_type = 'Please select your trader type.'
        } else {
          delete newErrors.trader_type
        }
        break

      case 'monthly_volume':
        if (!value) {
          newErrors.monthly_volume = 'Please select your expected monthly volume.'
        } else {
          delete newErrors.monthly_volume
        }
        break

      default:
        break
    }

    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }, [errors])

  const validateAll = useCallback((values) => {
    const newErrors = {}

    if (!values.name || values.name.trim().length < 2) {
      newErrors.name = 'Name must be at least 2 characters.'
    } else if (!NAME_REGEX.test(values.name.trim())) {
      newErrors.name = 'Name contains invalid characters.'
    }

    if (!values.email || !values.email.trim()) {
      newErrors.email = 'Email is required.'
    } else if (!EMAIL_REGEX.test(values.email.trim())) {
      newErrors.email = 'Please enter a valid email address.'
    }

    if (values.telegram && values.telegram.trim() && !TELEGRAM_REGEX.test(values.telegram.trim())) {
      newErrors.telegram = 'Invalid Telegram username.'
    }

    if (!values.experience_level) {
      newErrors.experience_level = 'Please select your experience level.'
    }

    if (!values.trader_type) {
      newErrors.trader_type = 'Please select your trader type.'
    }

    if (!values.monthly_volume) {
      newErrors.monthly_volume = 'Please select your expected monthly volume.'
    }

    setErrors(newErrors)
    setTouched({ name: true, email: true, telegram: true, experience_level: true, trader_type: true, monthly_volume: true })
    return Object.keys(newErrors).length === 0
  }, [])

  const touch = useCallback((field) => {
    setTouched(prev => ({ ...prev, [field]: true }))
  }, [])

  const clear = useCallback(() => {
    setErrors({})
    setTouched({})
  }, [])

  return { errors, touched, validate, validateAll, touch, clear }
}
