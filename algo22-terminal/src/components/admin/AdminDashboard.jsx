import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { 
    Search, Download, ChevronLeft, ChevronRight, 
    Trash2, Edit3, Loader2, AlertCircle, Users, BarChart3,
    CheckCircle, Clock, Mail, X, ArrowDown, MessageCircle
} from 'lucide-react'
import { waitlistAdminApi } from '../../lib/waitlistApi'
import { downloadAnalyticsApi } from '../../lib/downloadAnalyticsApi'

const STATUS_COLORS = {
    pending: 'bg-accent-gold-dim text-accent-gold border-accent-gold/20',
    approved: 'bg-accent-profit-dim text-accent-profit border-accent-profit/20',
    invited: 'bg-accent-cyan-dim text-accent-cyan border-accent-cyan/20',
    converted: 'bg-bg-elevated text-text-muted border-border-default',
}

const STATUS_ICONS = {
    pending: Clock,
    approved: CheckCircle,
    invited: Mail,
    converted: Users,
}

export default function AdminDashboard() {
    const [activeTab, setActiveTab] = useState('waitlist')
    const [entries, setEntries] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [page, setPage] = useState(1)
    const [total, setTotal] = useState(0)
    const [limit] = useState(50)
    const [filters, setFilters] = useState({ status: '', experience_level: '', search: '' })
    const [analytics, setAnalytics] = useState(null)
    const [editingId, setEditingId] = useState(null)
    const [editNotes, setEditNotes] = useState('')
    const [downloadStats, setDownloadStats] = useState(null)
    const [downloadLoading, setDownloadLoading] = useState(false)

    const fetchData = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const result = await waitlistAdminApi.getAll({ ...filters, page, limit })
            setEntries(result.data)
            setTotal(result.total)
            const analyticsData = await waitlistAdminApi.getAnalytics()
            setAnalytics(analyticsData)
        } catch (err) { setError(err.message) } finally { setLoading(false) }
    }, [filters, page, limit])

    const fetchDownloadStats = useCallback(async () => {
        setDownloadLoading(true)
        try {
            const stats = await downloadAnalyticsApi.getStats({ days: 30 })
            setDownloadStats(stats)
        } catch (err) { setError(err.message) } finally { setDownloadLoading(false) }
    }, [])

    useEffect(() => {
        if (activeTab === 'waitlist') fetchData()
        else fetchDownloadStats()
    }, [activeTab, fetchData, fetchDownloadStats])

    const handleStatusChange = async (id, newStatus) => {
        try { await waitlistAdminApi.updateStatus(id, newStatus); fetchData() }
        catch (err) { setError(err.message) }
    }

    const handleDelete = async (id) => {
        if (!confirm('Delete this entry permanently?')) return
        try { await waitlistAdminApi.delete(id); fetchData() }
        catch (err) { setError(err.message) }
    }

    const handleExport = () => {
        const csv = [
            ['ID', 'Name', 'Email', 'Telegram', 'Experience', 'Status', 'Created At', 'Notes'].join(','),
            ...entries.map(e => [e.id, `"${e.name}"`, e.email, e.telegram || '', e.experience_level, e.status, new Date(e.created_at).toISOString(), `"${(e.notes || '').replace(/"/g, '""')}"`].join(','))
        ].join('\n')
        const blob = new Blob([csv], { type: 'text/csv' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `vyomquant-waitlist-${new Date().toISOString().split('T')[0]}.csv`
        a.click()
        URL.revokeObjectURL(url)
    }

    const totalPages = Math.ceil(total / limit)

    return (
        <div className="min-h-screen bg-bg-primary pt-20 pb-12">
            <div className="section-container">
                <div className="section-inner">
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
                        <div>
                            <h1 className="text-2xl font-bold text-text-primary">Admin Dashboard</h1>
                            <p className="text-sm text-text-secondary mt-1">{activeTab === 'waitlist' ? `${total} waitlist entries` : 'Download analytics'}</p>
                        </div>
                        <Link to="/" className="btn-ghost text-sm">Back to Site</Link>
                    </div>

                    <div className="flex gap-2 mb-8">
                        <button onClick={() => setActiveTab('waitlist')} className={`px-5 py-2.5 rounded-xl text-sm font-medium transition-all ${activeTab === 'waitlist' ? 'bg-accent-cyan-dim text-accent-cyan border border-accent-cyan/30' : 'text-text-secondary hover:text-text-primary border border-transparent hover:bg-bg-elevated'}`}>
                            <Users className="w-4 h-4 inline mr-2" />Waitlist
                        </button>
                        <button onClick={() => setActiveTab('downloads')} className={`px-5 py-2.5 rounded-xl text-sm font-medium transition-all ${activeTab === 'downloads' ? 'bg-accent-cyan-dim text-accent-cyan border border-accent-cyan/30' : 'text-text-secondary hover:text-text-primary border border-transparent hover:bg-bg-elevated'}`}>
                            <ArrowDown className="w-4 h-4 inline mr-2" />Downloads
                        </button>
                    </div>

                    {error && (
                        <div className="mb-6 p-4 rounded-xl bg-accent-loss-dim border border-accent-loss/20 flex items-center gap-3">
                            <AlertCircle className="w-5 h-5 text-accent-loss" />
                            <p className="text-sm text-text-primary">{error}</p>
                        </div>
                    )}

                    {activeTab === 'waitlist' ? (
                        <>
                            {analytics && (
                                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                                    <div className="card-surface p-4"><div className="text-xs font-mono text-text-muted uppercase mb-1">Total</div><div className="text-2xl font-black text-text-primary">{analytics.total}</div></div>
                                    <div className="card-surface p-4"><div className="text-xs font-mono text-text-muted uppercase mb-1">Pending</div><div className="text-2xl font-black text-accent-gold">{analytics.byStatus.pending || 0}</div></div>
                                    <div className="card-surface p-4"><div className="text-xs font-mono text-text-muted uppercase mb-1">Approved</div><div className="text-2xl font-black text-accent-profit">{analytics.byStatus.approved || 0}</div></div>
                                    <div className="card-surface p-4"><div className="text-xs font-mono text-text-muted uppercase mb-1">Converted</div><div className="text-2xl font-black text-accent-cyan">{analytics.byStatus.converted || 0}</div></div>
                                </div>
                            )}
                            {analytics && (
                                <div className="card-surface p-5 mb-8">
                                    <h3 className="text-sm font-bold text-text-primary mb-4">Experience Distribution</h3>
                                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                                        {['beginner', 'intermediate', 'advanced', 'professional'].map(level => {
                                            const count = analytics.byExperience[level] || 0
                                            const pct = analytics.total > 0 ? Math.round((count / analytics.total) * 100) : 0
                                            return (
                                                <div key={level}>
                                                    <div className="flex justify-between text-xs mb-1"><span className="text-text-secondary capitalize">{level}</span><span className="text-text-primary font-mono">{count}</span></div>
                                                    <div className="h-2 bg-bg-elevated rounded-full overflow-hidden"><div className="h-full bg-accent-cyan rounded-full transition-all duration-500" style={{ width: `${pct}%` }} /></div>
                                                </div>
                                            )
                                        })}
                                    </div>
                                </div>
                            )}
                            <div className="card-surface p-4 mb-6">
                                <div className="flex flex-col sm:flex-row gap-3">
                                    <div className="relative flex-1">
                                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
                                        <input type="text" placeholder="Search by name, email, or telegram..." value={filters.search} onChange={e => setFilters(prev => ({ ...prev, search: e.target.value }))}
                                            className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-bg-elevated border border-border-default text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-cyan/50" />
                                    </div>
                                    <div className="flex gap-3">
                                        <select value={filters.status} onChange={e => setFilters(prev => ({ ...prev, status: e.target.value }))} className="px-4 py-2.5 rounded-xl bg-bg-elevated border border-border-default text-sm text-text-primary focus:outline-none focus:border-accent-cyan/50">
                                            <option value="">All Status</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="invited">Invited</option><option value="converted">Converted</option>
                                        </select>
                                        <select value={filters.experience_level} onChange={e => setFilters(prev => ({ ...prev, experience_level: e.target.value }))} className="px-4 py-2.5 rounded-xl bg-bg-elevated border border-border-default text-sm text-text-primary focus:outline-none focus:border-accent-cyan/50">
                                            <option value="">All Levels</option><option value="beginner">Beginner</option><option value="intermediate">Intermediate</option><option value="advanced">Advanced</option><option value="professional">Professional</option>
                                        </select>
                                    </div>
                                </div>
                            </div>
                            <div className="flex justify-end mb-4">
                                <button onClick={handleExport} className="btn-ghost text-sm"><Download className="w-4 h-4 mr-2" />Export CSV</button>
                            </div>
                            <div className="card-surface overflow-hidden">
                                <div className="overflow-x-auto">
                                    <table className="w-full">
                                        <thead><tr className="border-b border-border-default">
                                            <th className="text-left text-xs font-mono text-text-muted uppercase tracking-wider px-4 py-3">Name</th>
                                            <th className="text-left text-xs font-mono text-text-muted uppercase tracking-wider px-4 py-3">Email</th>
                                            <th className="text-left text-xs font-mono text-text-muted uppercase tracking-wider px-4 py-3">Telegram</th>
                                            <th className="text-left text-xs font-mono text-text-muted uppercase tracking-wider px-4 py-3">Experience</th>
                                            <th className="text-left text-xs font-mono text-text-muted uppercase tracking-wider px-4 py-3">Status</th>
                                            <th className="text-left text-xs font-mono text-text-muted uppercase tracking-wider px-4 py-3">Date</th>
                                            <th className="text-right text-xs font-mono text-text-muted uppercase tracking-wider px-4 py-3">Actions</th>
                                        </tr></thead>
                                        <tbody>
                                            {loading ? <tr><td colSpan={7} className="text-center py-12"><Loader2 className="w-6 h-6 animate-spin text-accent-cyan mx-auto" /></td></tr> :
                                            entries.length === 0 ? <tr><td colSpan={7} className="text-center py-12 text-text-secondary text-sm">No entries found matching your criteria.</td></tr> :
                                            entries.map(entry => {
                                                const StatusIcon = STATUS_ICONS[entry.status] || Clock
                                                return (
                                                    <tr key={entry.id} className="border-b border-border-default/50 hover:bg-bg-elevated/50 transition-colors">
                                                        <td className="px-4 py-3 text-sm text-text-primary font-medium">{entry.name}</td>
                                                        <td className="px-4 py-3 text-sm text-text-secondary font-mono">{entry.email}</td>
                                                        <td className="px-4 py-3 text-sm text-text-secondary font-mono">
                                                            {entry.telegram ? (
                                                                <span className="flex items-center gap-1">
                                                                    <MessageCircle className="w-3 h-3 text-accent-cyan" />
                                                                    {entry.telegram}
                                                                </span>
                                                            ) : (
                                                                <span className="text-text-muted">—</span>
                                                            )}
                                                        </td>
                                                        <td className="px-4 py-3"><span className="text-xs capitalize text-text-secondary bg-bg-elevated px-2 py-1 rounded-lg">{entry.experience_level}</span></td>
                                                        <td className="px-4 py-3"><span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border font-medium ${STATUS_COLORS[entry.status]}`}><StatusIcon className="w-3 h-3" />{entry.status}</span></td>
                                                        <td className="px-4 py-3 text-xs text-text-muted font-mono">{new Date(entry.created_at).toLocaleDateString()}</td>
                                                        <td className="px-4 py-3">
                                                            <div className="flex items-center justify-end gap-2">
                                                                {entry.status === 'pending' && <button onClick={() => handleStatusChange(entry.id, 'approved')} className="p-1.5 rounded-lg hover:bg-accent-profit-dim text-text-muted hover:text-accent-profit transition-colors" title="Approve"><CheckCircle className="w-4 h-4" /></button>}
                                                                {entry.status === 'approved' && <button onClick={() => handleStatusChange(entry.id, 'invited')} className="p-1.5 rounded-lg hover:bg-accent-cyan-dim text-text-muted hover:text-accent-cyan transition-colors" title="Mark Invited"><Mail className="w-4 h-4" /></button>}
                                                                <button onClick={() => { setEditingId(entry.id); setEditNotes(entry.notes || '') }} className="p-1.5 rounded-lg hover:bg-accent-cyan-dim text-text-muted hover:text-accent-cyan transition-colors" title="Edit Notes"><Edit3 className="w-4 h-4" /></button>
                                                                <button onClick={() => handleDelete(entry.id)} className="p-1.5 rounded-lg hover:bg-accent-loss-dim text-text-muted hover:text-accent-loss transition-colors" title="Delete"><Trash2 className="w-4 h-4" /></button>
                                                            </div>
                                                        </td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                                {totalPages > 1 && (
                                    <div className="flex items-center justify-between px-4 py-3 border-t border-border-default">
                                        <span className="text-xs text-text-muted">Showing {((page - 1) * limit) + 1}–{Math.min(page * limit, total)} of {total}</span>
                                        <div className="flex items-center gap-2">
                                            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="p-2 rounded-lg border border-border-default text-text-muted hover:text-text-primary hover:bg-bg-elevated disabled:opacity-30 disabled:cursor-not-allowed transition-colors"><ChevronLeft className="w-4 h-4" /></button>
                                            <span className="text-xs text-text-secondary font-mono px-2">{page} / {totalPages}</span>
                                            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="p-2 rounded-lg border border-border-default text-text-muted hover:text-text-primary hover:bg-bg-elevated disabled:opacity-30 disabled:cursor-not-allowed transition-colors"><ChevronRight className="w-4 h-4" /></button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </>
                    ) : (
                        <>
                            {downloadLoading ? <div className="card-surface p-12 text-center"><Loader2 className="w-6 h-6 animate-spin text-accent-cyan mx-auto" /></div> :
                            downloadStats ? (
                                <>
                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                                        <div className="card-surface p-4"><div className="text-xs font-mono text-text-muted uppercase mb-1">Total Downloads (30d)</div><div className="text-2xl font-black text-text-primary">{downloadStats.total}</div></div>
                                        <div className="card-surface p-4"><div className="text-xs font-mono text-text-muted uppercase mb-1">Windows</div><div className="text-2xl font-black text-accent-cyan">{downloadStats.byPlatform.windows || 0}</div></div>
                                        <div className="card-surface p-4"><div className="text-xs font-mono text-text-muted uppercase mb-1">macOS</div><div className="text-2xl font-black text-accent-gold">{downloadStats.byPlatform.macos || 0}</div></div>
                                        <div className="card-surface p-4"><div className="text-xs font-mono text-text-muted uppercase mb-1">Linux</div><div className="text-2xl font-black text-accent-profit">{downloadStats.byPlatform.linux || 0}</div></div>
                                    </div>
                                    <div className="card-surface p-6 mb-8">
                                        <h3 className="text-sm font-bold text-text-primary mb-4">Downloads by Version</h3>
                                        <div className="space-y-3">
                                            {Object.entries(downloadStats.byVersion).map(([version, count]) => {
                                                const pct = downloadStats.total > 0 ? Math.round((count / downloadStats.total) * 100) : 0
                                                return (
                                                    <div key={version}>
                                                        <div className="flex justify-between text-xs mb-1"><span className="text-text-secondary font-mono">{version}</span><span className="text-text-primary font-mono">{count} ({pct}%)</span></div>
                                                        <div className="h-2 bg-bg-elevated rounded-full overflow-hidden"><div className="h-full bg-accent-cyan rounded-full transition-all duration-500" style={{ width: `${pct}%` }} /></div>
                                                    </div>
                                                )
                                            })}
                                            {Object.keys(downloadStats.byVersion).length === 0 && <p className="text-sm text-text-secondary text-center py-4">No download data yet.</p>}
                                        </div>
                                    </div>
                                    <div className="card-surface p-6">
                                        <h3 className="text-sm font-bold text-text-primary mb-4">Daily Download Trend (30 Days)</h3>
                                        <div className="flex items-end gap-1 h-40">
                                            {Object.entries(downloadStats.byDay).sort(([a], [b]) => a.localeCompare(b)).map(([day, count]) => {
                                                const maxCount = Math.max(...Object.values(downloadStats.byDay))
                                                const height = maxCount > 0 ? (count / maxCount) * 100 : 0
                                                return (
                                                    <div key={day} className="flex-1 flex flex-col items-center gap-1 group">
                                                        <div className="w-full bg-bg-elevated rounded-t-sm relative h-32">
                                                            <div className="absolute bottom-0 left-0 right-0 bg-accent-cyan/60 rounded-t-sm transition-all duration-300 group-hover:bg-accent-cyan" style={{ height: `${height}%` }} />
                                                        </div>
                                                        <span className="text-[10px] font-mono text-text-muted">{day.slice(5)}</span>
                                                    </div>
                                                )
                                            })}
                                        </div>
                                    </div>
                                </>
                            ) : <div className="card-surface p-12 text-center text-text-secondary">No download data available.</div>}
                        </>
                    )}
                </div>
            </div>

            {editingId && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
                    <div className="card-surface w-full max-w-md p-6">
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="text-lg font-bold text-text-primary">Edit Notes</h3>
                            <button onClick={() => setEditingId(null)} className="text-text-muted hover:text-text-primary"><X className="w-5 h-5" /></button>
                        </div>
                        <textarea value={editNotes} onChange={e => setEditNotes(e.target.value)} rows={4}
                            className="w-full px-4 py-3 rounded-xl bg-bg-elevated border border-border-default text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-cyan/50 resize-none mb-4"
                            placeholder="Add internal notes..." />
                        <div className="flex justify-end gap-3">
                            <button onClick={() => setEditingId(null)} className="btn-ghost text-sm">Cancel</button>
                            <button onClick={async () => { try { await waitlistAdminApi.updateStatus(editingId, undefined, editNotes); setEditingId(null); fetchData() } catch (err) { setError(err.message) } }} className="btn-primary text-sm">Save Notes</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
