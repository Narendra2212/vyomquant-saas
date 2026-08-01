import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { 
  MessageSquare, Plus, Search, Filter, X, 
  Clock, AlertCircle, CheckCircle, ChevronRight,
  Send, Archive, RefreshCw, Loader2
} from "lucide-react";

// Institutional Design System Palette
const C = {
  bg: "#010608", bg0: "#010608", bg1: "#040d14", bg2: "#071018", bg3: "#0b1724", bg4: "#0f1e2e",
  border: "#0f2035", cyan: "#00d4ff", t1: "#e8f4ff", t2: "#6b9bb8", t3: "#2a4a5e", t4: "#152535",
  green: "#00d4aa", yellow: "#ffd700", red: "#ff4757", orange: "#ff8c00"
};

const STATUS_COLORS = {
  open: { bg: "#ff475715", text: "#ff4757", icon: AlertCircle },
  in_progress: { bg: "#ffd70015", text: "#ffd700", icon: Clock },
  resolved: { bg: "#00d4aa15", text: "#00d4aa", icon: CheckCircle },
  closed: { bg: "#2a4a5e15", text: "#2a4a5e", icon: Archive }
};

const PRIORITY_COLORS = {
  low: { bg: "#2a4a5e15", text: "#2a4a5e" },
  medium: { bg: "#00d4ff15", text: "#00d4ff" },
  high: { bg: "#ff8c0015", text: "#ff8c00" },
  urgent: { bg: "#ff475715", text: "#ff4757" }
};

const CATEGORY_LABELS = {
  general: "General",
  technical: "Technical",
  billing: "Billing",
  security: "Security",
  feature: "Feature Request"
};

export default function SupportCenter() {
  const navigate = useNavigate();
  const [view, setView] = useState('list'); // 'list', 'create', 'detail'
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [categoryFilter, setCategoryFilter] = useState('all');
  
  // Create form
  const [formData, setFormData] = useState({
    subject: '',
    description: '',
    category: 'general',
    priority: 'medium'
  });
  const [submitting, setSubmitting] = useState(false);
  
  // Comment form
  const [commentText, setCommentText] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);

  // Load tickets on mount
  useEffect(() => {
    loadTickets();
  }, []);

  const loadTickets = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await api.support.getTickets();
      setTickets(response.tickets || []);
    } catch (err) {
      console.error('Failed to load tickets:', err);
      setError('Failed to load tickets. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const loadTicketDetail = async (ticketId) => {
    try {
      const ticket = await api.support.getTicket(ticketId);
      setSelectedTicket(ticket);
      setView('detail');
    } catch (err) {
      console.error('Failed to load ticket:', err);
      setError('Failed to load ticket details.');
    }
  };

  const handleCreateTicket = async (e) => {
    e.preventDefault();
    if (!formData.subject.trim() || !formData.description.trim()) return;
    
    setSubmitting(true);
    try {
      const response = await api.support.createTicket({
        subject: formData.subject.trim(),
        description: formData.description.trim(),
        category: formData.category,
        priority: formData.priority
      });
      
      // Refresh tickets and switch to list view
      await loadTickets();
      setView('list');
      setFormData({ subject: '', description: '', category: 'general', priority: 'medium' });
    } catch (err) {
      console.error('Failed to create ticket:', err);
      setError('Failed to create ticket. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleAddComment = async (e) => {
    e.preventDefault();
    if (!commentText.trim() || !selectedTicket) return;
    
    setSubmittingComment(true);
    try {
      await api.support.addComment(selectedTicket.id, { message: commentText.trim() });
      
      // Reload ticket detail
      await loadTicketDetail(selectedTicket.id);
      setCommentText('');
    } catch (err) {
      console.error('Failed to add comment:', err);
      setError('Failed to add comment. Please try again.');
    } finally {
      setSubmittingComment(false);
    }
  };

  const handleCloseTicket = async () => {
    if (!selectedTicket) return;
    
    try {
      await api.support.updateTicket(selectedTicket.id, 'closed');
      await loadTicketDetail(selectedTicket.id);
    } catch (err) {
      console.error('Failed to close ticket:', err);
      setError('Failed to close ticket. Please try again.');
    }
  };

  const handleReopenTicket = async () => {
    if (!selectedTicket) return;
    
    try {
      await api.support.updateTicket(selectedTicket.id, 'reopen');
      await loadTicketDetail(selectedTicket.id);
    } catch (err) {
      console.error('Failed to reopen ticket:', err);
      setError('Failed to reopen ticket. Please try again.');
    }
  };

  const filteredTickets = tickets.filter(ticket => {
    const matchesSearch = ticket.subject.toLowerCase().includes(searchQuery.toLowerCase()) ||
                         ticket.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'all' || ticket.status === statusFilter;
    const matchesCategory = categoryFilter === 'all' || ticket.category === categoryFilter;
    return matchesSearch && matchesStatus && matchesCategory;
  });

  // ── LIST VIEW ─────────────────────────────────────────────────────────────
  if (view === 'list') {
    return (
      <div style={{ padding: 24, overflowY: "auto", flex: 1 }}>
        {/* Header */}
        <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h2 style={{ color: C.t1, fontSize: 20, fontWeight: 900, marginBottom: 4 }}>Support Center</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>Manage your support tickets and requests</p>
          </div>
          <button 
            onClick={() => setView('create')}
            style={{ 
              background: C.cyan, color: "#000", border: "none", 
              padding: "10px 16px", borderRadius: 8, fontSize: 12, 
              fontWeight: 900, fontFamily: "monospace", cursor: "pointer",
              display: 'flex', alignItems: 'center', gap: 8
            }}
          >
            <Plus size={16} />
            New Ticket
          </button>
        </div>

        {/* Filters */}
        <div style={{ 
          background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 12, 
          padding: 16, marginBottom: 20, display: 'flex', gap: 16, alignItems: 'center'
        }}>
          <div style={{ flex: 1, position: 'relative' }}>
            <Search size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: C.t3 }} />
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search tickets..."
              style={{ 
                width: "100%", background: C.bg3, border: `1px solid ${C.border}`, 
                color: C.t1, padding: "10px 12px 10px 36px", borderRadius: 6, 
                fontSize: 11, fontFamily: "monospace", outline: "none" 
              }}
            />
          </div>
          
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            style={{ 
              background: C.bg3, border: `1px solid ${C.border}`, 
              color: C.t1, padding: "10px 12px", borderRadius: 6, 
              fontSize: 11, fontFamily: "monospace", outline: "none", cursor: "pointer"
            }}
          >
            <option value="all">All Status</option>
            <option value="open">Open</option>
            <option value="in_progress">In Progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
          
          <select
            value={categoryFilter}
            onChange={e => setCategoryFilter(e.target.value)}
            style={{ 
              background: C.bg3, border: `1px solid ${C.border}`, 
              color: C.t1, padding: "10px 12px", borderRadius: 6, 
              fontSize: 11, fontFamily: "monospace", outline: "none", cursor: "pointer"
            }}
          >
            <option value="all">All Categories</option>
            <option value="general">General</option>
            <option value="technical">Technical</option>
            <option value="billing">Billing</option>
            <option value="security">Security</option>
            <option value="feature">Feature</option>
          </select>
          
          {(searchQuery || statusFilter !== 'all' || categoryFilter !== 'all') && (
            <button 
              onClick={() => { setSearchQuery(''); setStatusFilter('all'); setCategoryFilter('all'); }}
              style={{ 
                background: 'transparent', border: `1px solid ${C.border}`, 
                color: C.t2, padding: "10px 12px", borderRadius: 6, 
                fontSize: 11, fontFamily: "monospace", cursor: "pointer"
              }}
            >
              Clear
            </button>
          )}
        </div>

        {/* Error */}
        {error && (
          <div style={{ 
            background: `${C.red}15`, border: `1px solid ${C.red}40`, 
            borderRadius: 8, padding: 12, marginBottom: 20, color: C.red, 
            fontSize: 11, fontFamily: "monospace" 
          }}>
            {error}
          </div>
        )}

        {/* Loading */}
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
            <Loader2 className="animate-spin" style={{ color: C.cyan }} />
          </div>
        ) : filteredTickets.length === 0 ? (
          <div style={{ 
            textAlign: 'center', padding: 60, color: C.t3, 
            fontSize: 12, fontFamily: "monospace" 
          }}>
            {tickets.length === 0 ? 'No support tickets yet. Create your first ticket.' : 'No tickets match your filters.'}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {filteredTickets.map(ticket => {
              const statusConfig = STATUS_COLORS[ticket.status] || STATUS_COLORS.open;
              const priorityConfig = PRIORITY_COLORS[ticket.priority] || PRIORITY_COLORS.medium;
              const StatusIcon = statusConfig.icon;
              
              return (
                <div 
                  key={ticket.id}
                  onClick={() => loadTicketDetail(ticket.id)}
                  style={{ 
                    background: C.bg2, border: `1px solid ${C.border}`, 
                    borderRadius: 12, padding: 16, cursor: 'pointer',
                    transition: 'all 0.2s', display: 'flex', gap: 16,
                    alignItems: 'center'
                  }}
                >
                  <div style={{ 
                    background: statusConfig.bg, color: statusConfig.text, 
                    padding: 8, borderRadius: 8, display: 'flex' 
                  }}>
                    <StatusIcon size={20} />
                  </div>
                  
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ 
                      color: C.t1, fontSize: 13, fontWeight: 700, 
                      marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' 
                    }}>
                      {ticket.subject}
                    </div>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                      <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>
                        {CATEGORY_LABELS[ticket.category] || ticket.category}
                      </span>
                      <span style={{ 
                        color: priorityConfig.text, fontSize: 10, fontFamily: "monospace",
                        background: priorityConfig.bg, padding: '2px 6px', borderRadius: 4
                      }}>
                        {ticket.priority.toUpperCase()}
                      </span>
                      <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                        {new Date(ticket.created_at).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                  
                  <ChevronRight size={20} style={{ color: C.t3 }} />
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  // ── CREATE VIEW ───────────────────────────────────────────────────────────
  if (view === 'create') {
    return (
      <div style={{ padding: 24, overflowY: "auto", flex: 1 }}>
        {/* Header */}
        <div style={{ marginBottom: 24, display: 'flex', alignItems: 'center', gap: 12 }}>
          <button 
            onClick={() => setView('list')}
            style={{ background: 'transparent', border: 'none', color: C.t2, cursor: 'pointer' }}
          >
            <X size={20} />
          </button>
          <h2 style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>Create Support Ticket</h2>
        </div>

        {/* Form */}
        <form onSubmit={handleCreateTicket} style={{ maxWidth: 600 }}>
          <div style={{ marginBottom: 20 }}>
            <label style={{ 
              color: C.t2, fontSize: 10, fontFamily: "monospace", 
              fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", 
              display: "block", marginBottom: 8 
            }}>
              Subject
            </label>
            <input
              value={formData.subject}
              onChange={e => setFormData({...formData, subject: e.target.value})}
              placeholder="Brief description of your issue"
              maxLength={200}
              style={{ 
                width: "100%", background: C.bg3, border: `1px solid ${C.border}`, 
                color: C.t1, padding: "12px 14px", borderRadius: 8, 
                fontSize: 12, fontFamily: "monospace", outline: "none" 
              }}
              required
            />
            <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", marginTop: 4, textAlign: 'right' }}>
              {formData.subject.length}/200
            </div>
          </div>

          <div style={{ marginBottom: 20, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div>
              <label style={{ 
                color: C.t2, fontSize: 10, fontFamily: "monospace", 
                fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", 
                display: "block", marginBottom: 8 
              }}>
                Category
              </label>
              <select
                value={formData.category}
                onChange={e => setFormData({...formData, category: e.target.value})}
                style={{ 
                  width: "100%", background: C.bg3, border: `1px solid ${C.border}`, 
                  color: C.t1, padding: "12px 14px", borderRadius: 8, 
                  fontSize: 12, fontFamily: "monospace", outline: "none", cursor: "pointer"
                }}
              >
                <option value="general">General</option>
                <option value="technical">Technical</option>
                <option value="billing">Billing</option>
                <option value="security">Security</option>
                <option value="feature">Feature Request</option>
              </select>
            </div>

            <div>
              <label style={{ 
                color: C.t2, fontSize: 10, fontFamily: "monospace", 
                fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", 
                display: "block", marginBottom: 8 
              }}>
                Priority
              </label>
              <select
                value={formData.priority}
                onChange={e => setFormData({...formData, priority: e.target.value})}
                style={{ 
                  width: "100%", background: C.bg3, border: `1px solid ${C.border}`, 
                  color: C.t1, padding: "12px 14px", borderRadius: 8, 
                  fontSize: 12, fontFamily: "monospace", outline: "none", cursor: "pointer"
                }}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </div>
          </div>

          <div style={{ marginBottom: 20 }}>
            <label style={{ 
              color: C.t2, fontSize: 10, fontFamily: "monospace", 
              fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", 
              display: "block", marginBottom: 8 
            }}>
              Description
            </label>
            <textarea
              value={formData.description}
              onChange={e => setFormData({...formData, description: e.target.value})}
              placeholder="Describe your issue in detail. Include steps to reproduce, error messages, and any relevant context."
              minLength={20}
              maxLength={5000}
              style={{ 
                width: "100%", background: C.bg3, border: `1px solid ${C.border}`, 
                color: C.t1, padding: "12px 14px", borderRadius: 8, 
                fontSize: 12, fontFamily: "monospace", minHeight: 200, 
                outline: "none", resize: "vertical" 
              }}
              required
            />
            <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", marginTop: 4, textAlign: 'right' }}>
              {formData.description.length}/5000
            </div>
          </div>

          {/* Error */}
          {error && (
            <div style={{ 
              background: `${C.red}15`, border: `1px solid ${C.red}40`, 
              borderRadius: 8, padding: 12, marginBottom: 20, color: C.red, 
              fontSize: 11, fontFamily: "monospace" 
            }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', gap: 12 }}>
            <button
              type="submit"
              disabled={submitting}
              style={{ 
                background: submitting ? C.t3 : C.cyan, color: "#000", border: "none", 
                padding: "12px 24px", borderRadius: 8, fontSize: 12, 
                fontWeight: 900, fontFamily: "monospace", cursor: submitting ? "not-allowed" : "pointer",
                opacity: submitting ? 0.6 : 1 
              }}
            >
              {submitting ? <Loader2 className="animate-spin" size={16} /> : 'Submit Ticket'}
            </button>
            <button
              type="button"
              onClick={() => setView('list')}
              style={{ 
                background: 'transparent', border: `1px solid ${C.border}`, 
                color: C.t2, padding: "12px 24px", borderRadius: 8, 
                fontSize: 12, fontWeight: 900, fontFamily: "monospace", cursor: "pointer"
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    );
  }

  // ── DETAIL VIEW ───────────────────────────────────────────────────────────
  if (view === 'detail' && selectedTicket) {
    const statusConfig = STATUS_COLORS[selectedTicket.status] || STATUS_COLORS.open;
    const StatusIcon = statusConfig.icon;
    
    return (
      <div style={{ padding: 24, overflowY: "auto", flex: 1 }}>
        {/* Header */}
        <div style={{ marginBottom: 24, display: 'flex', alignItems: 'center', gap: 12 }}>
          <button 
            onClick={() => setView('list')}
            style={{ background: 'transparent', border: 'none', color: C.t2, cursor: 'pointer' }}
          >
            <X size={20} />
          </button>
          <h2 style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>Ticket Details</h2>
        </div>

        {/* Error */}
        {error && (
          <div style={{ 
            background: `${C.red}15`, border: `1px solid ${C.red}40`, 
            borderRadius: 8, padding: 12, marginBottom: 20, color: C.red, 
            fontSize: 11, fontFamily: "monospace" 
          }}>
            {error}
          </div>
        )}

        {/* Ticket Info */}
        <div style={{ 
          background: C.bg2, border: `1px solid ${C.border}`, 
          borderRadius: 12, padding: 20, marginBottom: 20 
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
            <div style={{ flex: 1 }}>
              <h3 style={{ color: C.t1, fontSize: 16, fontWeight: 700, marginBottom: 8 }}>
                {selectedTicket.subject}
              </h3>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ 
                  display: 'flex', alignItems: 'center', gap: 6,
                  background: statusConfig.bg, color: statusConfig.text, 
                  padding: '4px 10px', borderRadius: 6, fontSize: 11, 
                  fontFamily: "monospace", fontWeight: 900 
                }}>
                  <StatusIcon size={14} />
                  {selectedTicket.status.toUpperCase()}
                </span>
                <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>
                  {CATEGORY_LABELS[selectedTicket.category] || selectedTicket.category}
                </span>
                <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>
                  {new Date(selectedTicket.created_at).toLocaleString()}
                </span>
              </div>
            </div>
            
            {selectedTicket.status !== 'closed' ? (
              <button 
                onClick={handleCloseTicket}
                style={{ 
                  background: `${C.red}15`, border: `1px solid ${C.red}40`, 
                  color: C.red, padding: "8px 16px", borderRadius: 6, 
                  fontSize: 11, fontFamily: "monospace", fontWeight: 900, 
                  cursor: "pointer" 
                }}
              >
                Close Ticket
              </button>
            ) : (
              <button 
                onClick={handleReopenTicket}
                style={{ 
                  background: `${C.green}15`, border: `1px solid ${C.green}40`, 
                  color: C.green, padding: "8px 16px", borderRadius: 6, 
                  fontSize: 11, fontFamily: "monospace", fontWeight: 900, 
                  cursor: "pointer" 
                }}
              >
                Reopen
              </button>
            )}
          </div>
          
          <div style={{ 
            color: C.t1, fontSize: 12, lineHeight: 1.6, 
            whiteSpace: 'pre-wrap', fontFamily: "monospace" 
          }}>
            {selectedTicket.description}
          </div>
        </div>

        {/* Comments */}
        <div style={{ marginBottom: 20 }}>
          <h4 style={{ color: C.t1, fontSize: 14, fontWeight: 900, marginBottom: 16 }}>
            Comments ({selectedTicket.comments?.length || 0})
          </h4>
          
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {selectedTicket.comments?.map(comment => (
              <div 
                key={comment.id}
                style={{ 
                  background: comment.is_staff ? `${C.cyan}10` : C.bg3, 
                  border: `1px solid ${comment.is_staff ? `${C.cyan}30` : C.border}`, 
                  borderRadius: 8, padding: 16 
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ 
                    color: comment.is_staff ? C.cyan : C.t1, fontSize: 11, 
                    fontFamily: "monospace", fontWeight: 900 
                  }}>
                    {comment.is_staff ? 'Support Team' : 'You'}
                  </span>
                  <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                    {new Date(comment.created_at).toLocaleString()}
                  </span>
                </div>
                <div style={{ color: C.t1, fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                  {comment.message}
                </div>
              </div>
            ))}
            
            {(!selectedTicket.comments || selectedTicket.comments.length === 0) && (
              <div style={{ 
                color: C.t3, fontSize: 11, fontFamily: "monospace", 
                textAlign: 'center', padding: 20 
              }}>
                No comments yet
              </div>
            )}
          </div>
        </div>

        {/* Add Comment Form */}
        {selectedTicket.status !== 'closed' && (
          <form onSubmit={handleAddComment}>
            <textarea
              value={commentText}
              onChange={e => setCommentText(e.target.value)}
              placeholder="Add a comment..."
              maxLength={2000}
              style={{ 
                width: "100%", background: C.bg3, border: `1px solid ${C.border}`, 
                color: C.t1, padding: "12px 14px", borderRadius: 8, 
                fontSize: 12, fontFamily: "monospace", minHeight: 100, 
                outline: "none", resize: "vertical", marginBottom: 12 
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                {commentText.length}/2000
              </span>
              <button
                type="submit"
                disabled={submittingComment || !commentText.trim()}
                style={{ 
                  background: submittingComment ? C.t3 : C.cyan, color: "#000", 
                  border: "none", padding: "10px 20px", borderRadius: 8, 
                  fontSize: 11, fontWeight: 900, fontFamily: "monospace", 
                  cursor: submittingComment || !commentText.trim() ? "not-allowed" : "pointer",
                  display: 'flex', alignItems: 'center', gap: 8,
                  opacity: submittingComment || !commentText.trim() ? 0.6 : 1 
                }}
              >
                {submittingComment ? <Loader2 className="animate-spin" size={14} /> : <Send size={14} />}
                Send
              </button>
            </div>
          </form>
        )}
      </div>
    );
  }

  return null;
}
