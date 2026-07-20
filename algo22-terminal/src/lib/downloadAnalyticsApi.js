import { supabase } from './supabase'

export const downloadAnalyticsApi = {
  async track(data) {
    const { version, platform = 'windows', architecture = 'x64', user_agent = navigator.userAgent, referrer = document.referrer } = data
    const params = new URLSearchParams(window.location.search)
    const utm_source = params.get('utm_source')
    const utm_medium = params.get('utm_medium')
    const utm_campaign = params.get('utm_campaign')
    const { error } = await supabase.from('download_analytics').insert([{ version, platform, architecture, user_agent, referrer: referrer || null, utm_source, utm_medium, utm_campaign }])
    if (error) console.error('Download tracking error:', error)
    return !error
  },
  async getStats({ days = 30, platform = null } = {}) {
    const startDate = new Date()
    startDate.setDate(startDate.getDate() - days)
    let query = supabase.from('download_analytics').select('*').gte('downloaded_at', startDate.toISOString())
    if (platform) query = query.eq('platform', platform)
    const { data, error } = await query
    if (error) throw new Error(error.message)
    const byVersion = {}
    const byPlatform = {}
    const byDay = {}
    const total = data.length
    data.forEach(row => {
      byVersion[row.version] = (byVersion[row.version] || 0) + 1
      byPlatform[row.platform] = (byPlatform[row.platform] || 0) + 1
      const day = row.downloaded_at.split('T')[0]
      byDay[day] = (byDay[day] || 0) + 1
    })
    return { total, byVersion, byPlatform, byDay, raw: data }
  },
}
