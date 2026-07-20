import { supabase } from './supabase'

export const waitlistApi = {
  /**
   * Submit a new waitlist entry
   */
  async submit(data) {
    const { name, email, telegram, experience_level, trader_type, monthly_volume, utm_source, utm_medium } = data

    const { data: result, error } = await supabase
      .from('waitlist')
      .insert([
        {
          name: name.trim(),
          email: email.trim().toLowerCase(),
          telegram: telegram ? telegram.trim() : null,
          experience_level,
          trader_type,
          monthly_volume,
          utm_source: utm_source || null,
          utm_medium: utm_medium || null,
          status: 'pending',
        }
      ])
      .select('id, email, created_at')
      .single()

    if (error) {
      if (error.code === '23505') {
        throw new Error('This email is already on the waitlist.')
      }
      throw new Error(error.message || 'Failed to submit. Please try again.')
    }

    return result
  },

  /**
   * Check if email already exists
   */
  async checkEmail(email) {
    const { data, error } = await supabase
      .from('waitlist')
      .select('id, status, created_at')
      .eq('email', email.trim().toLowerCase())
      .single()

    if (error && error.code !== 'PGRST116') {
      throw new Error(error.message)
    }

    return data || null
  },

  /**
   * Get waitlist stats (public summary)
   */
  async getStats() {
    const { count, error } = await supabase
      .from('waitlist')
      .select('*', { count: 'exact', head: true })

    if (error) throw new Error(error.message)
    return { total: count || 0 }
  },
}

// Admin API (requires service role or admin JWT)
export const waitlistAdminApi = {
  /**
   * Get all waitlist entries with filtering and pagination
   */
  async getAll({ status, experience_level, search, page = 1, limit = 50 } = {}) {
    let query = supabase
      .from('waitlist')
      .select('*', { count: 'exact' })
      .order('created_at', { ascending: false })

    if (status) {
      query = query.eq('status', status)
    }
    if (experience_level) {
      query = query.eq('experience_level', experience_level)
    }
    if (search) {
      query = query.or(`name.ilike.%${search}%,email.ilike.%${search}%,telegram.ilike.%${search}%`)
    }

    const from = (page - 1) * limit
    const to = from + limit - 1
    query = query.range(from, to)

    const { data, error, count } = await query

    if (error) throw new Error(error.message)
    return { data: data || [], total: count || 0, page, limit }
  },

  /**
   * Update entry status
   */
  async updateStatus(id, status, notes = null) {
    const updates = {}
    if (status !== undefined) updates.status = status
    if (notes !== null) updates.notes = notes

    const { data, error } = await supabase
      .from('waitlist')
      .update(updates)
      .eq('id', id)
      .select()
      .single()

    if (error) throw new Error(error.message)
    return data
  },

  /**
   * Delete entry
   */
  async delete(id) {
    const { error } = await supabase
      .from('waitlist')
      .delete()
      .eq('id', id)

    if (error) throw new Error(error.message)
    return true
  },

  /**
   * Get analytics breakdown
   */
  async getAnalytics() {
    const { data, error } = await supabase
      .from('waitlist')
      .select('experience_level, status')

    if (error) throw new Error(error.message)

    const byExperience = {}
    const byStatus = {}

    data.forEach(row => {
      byExperience[row.experience_level] = (byExperience[row.experience_level] || 0) + 1
      byStatus[row.status] = (byStatus[row.status] || 0) + 1
    })

    return { byExperience, byStatus, total: data.length }
  },
}
