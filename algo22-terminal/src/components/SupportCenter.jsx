import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { 
  MessageSquare, Plus, Search, Filter, X, 
  Clock, AlertCircle, CheckCircle, ChevronRight, ChevronDown,
  Send, Archive, RefreshCw, Loader2, HelpCircle, Shield,
  FileText, Activity, Layers, Link2, Paperclip, Check, ArrowLeft
} from "lucide-react";
// Requirement 1.1: the single token source. This page used to declare a competing
// local `C`, then read the `ui-legacy/primitives` shim; it reads `design/tokens.js`
// directly now (task 27.2). The layout is unchanged — every value below is the one
// the shim already returned (§17.2).
import { token } from "../design/tokens";

const STATUS_CONFIG = {
  open: { bg: "rgba(255, 71, 87, 0.12)", text: "#ff4757", label: "OPEN", icon: AlertCircle },
  in_progress: { bg: "rgba(255, 215, 0, 0.12)", text: "#ffd700", label: "IN PROGRESS", icon: Clock },
  waiting_for_user: { bg: "rgba(0, 212, 255, 0.12)", text: "#00d4ff", label: "WAITING ON YOU", icon: MessageSquare },
  waiting_for_support: { bg: "rgba(255, 140, 0, 0.12)", text: "#ff8c00", label: "WAITING ON SUPPORT", icon: Clock },
  resolved: { bg: "rgba(0, 212, 170, 0.12)", text: "#00d4aa", label: "RESOLVED", icon: CheckCircle },
  closed: { bg: "rgba(42, 74, 94, 0.2)", text: "#6b9bb8", label: "CLOSED", icon: Archive }
};

const PRIORITY_CONFIG = {
  low: { bg: "rgba(42, 74, 94, 0.2)", text: "#6b9bb8" },
  medium: { bg: "rgba(0, 212, 255, 0.12)", text: "#00d4ff" },
  high: { bg: "rgba(255, 140, 0, 0.15)", text: "#ff8c00" },
  urgent: { bg: "rgba(255, 71, 87, 0.15)", text: "#ff4757" }
};

const CATEGORIES = {
  general: { label: "General", icon: HelpCircle },
  technical: { label: "Technical", icon: Activity },
  trading: { label: "Trading / Execution", icon: Layers },
  billing: { label: "Billing", icon: FileText },
  security: { label: "Security & 2FA", icon: Shield },
  feature: { label: "Feature Request", icon: Plus }
};

export default function SupportCenter() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('tickets'); // 'tickets', 'faqs', 'create', 'detail'
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [tickets, setTickets] = useState([]);
  const [faqs, setFaqs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [faqLoading, setFaqLoading] = useState(false);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);
  
  // Expanded FAQ items
  const [expandedFaq, setExpandedFaq] = useState(null);
  const [faqCategory, setFaqCategory] = useState('all');
  const [faqSearch, setFaqSearch] = useState('');

  // Filters for tickets
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [priorityFilter, setPriorityFilter] = useState('all');
  
  // Create ticket form
  const [formData, setFormData] = useState({
    subject: '',
    description: '',
    category: 'trading',
    priority: 'medium',
    related_feature: '',
    strategy_id: '',
    order_id: '',
    attachment_name: ''
  });
  const [submitting, setSubmitting] = useState(false);
  
  // Comment / reply form
  const [commentText, setCommentText] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);

  // Load tickets on mount
  const loadTickets = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await api.support.getTickets({
        status: statusFilter !== 'all' ? statusFilter : undefined,
        category: categoryFilter !== 'all' ? categoryFilter : undefined,
        priority: priorityFilter !== 'all' ? priorityFilter : undefined,
        search: searchQuery || undefined
      });
      setTickets(response.tickets || []);
    } catch (err) {
      console.error('Failed to load tickets:', err);
      setError('Failed to load support tickets. Please refresh.');
    } finally {
      setLoading(false);
    }
  }, [statusFilter, categoryFilter, priorityFilter, searchQuery]);

  const loadFaqs = useCallback(async () => {
    try {
      setFaqLoading(true);
      const res = await api.support.getFaqs({
        category: faqCategory !== 'all' ? faqCategory : undefined,
        search: faqSearch || undefined
      });
      setFaqs(res.faqs || []);
    } catch (err) {
      console.error('Failed to load FAQs:', err);
    } finally {
      setFaqLoading(false);
    }
  }, [faqCategory, faqSearch]);

  useEffect(() => {
    loadTickets();
  }, [loadTickets]);

  useEffect(() => {
    if (activeTab === 'faqs') {
      loadFaqs();
    }
  }, [activeTab, loadFaqs]);

  const loadTicketDetail = async (ticketId) => {
    try {
      setLoading(true);
      setError(null);
      const ticket = await api.support.getTicket(ticketId);
      setSelectedTicket(ticket);
      setActiveTab('detail');
    } catch (err) {
      console.error('Failed to load ticket:', err);
      setError('Failed to load ticket details.');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateTicket = async (e) => {
    e.preventDefault();
    if (!formData.subject.trim() || !formData.description.trim()) {
      setError('Subject and description are required.');
      return;
    }
    
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        subject: formData.subject.trim(),
        description: formData.description.trim(),
        category: formData.category,
        priority: formData.priority,
        related_feature: formData.related_feature.trim() || undefined,
        strategy_id: formData.strategy_id.trim() || undefined,
        order_id: formData.order_id.trim() || undefined,
      };

      if (formData.attachment_name.trim()) {
        payload.attachment = {
          filename: formData.attachment_name.trim(),
          file_size: 1024,
          content_type: 'application/octet-stream'
        };
      }

      const res = await api.support.createTicket(payload);
      setSuccessMessage(`Ticket #${res.ticket_id} created successfully! Our team will review shortly.`);
      setTimeout(() => setSuccessMessage(null), 5000);
      
      // Reset form
      setFormData({
        subject: '',
        description: '',
        category: 'trading',
        priority: 'medium',
        related_feature: '',
        strategy_id: '',
        order_id: '',
        attachment_name: ''
      });

      await loadTickets();
      setActiveTab('tickets');
    } catch (err) {
      console.error('Failed to create ticket:', err);
      setError(err?.response?.data?.detail || err?.message || 'Failed to create ticket. Please check your inputs.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleAddComment = async (e) => {
    e.preventDefault();
    if (!commentText.trim() || !selectedTicket) return;
    
    setSubmittingComment(true);
    setError(null);
    try {
      await api.support.addComment(selectedTicket.id, { message: commentText.trim() });
      await loadTicketDetail(selectedTicket.id);
      setCommentText('');
    } catch (err) {
      console.error('Failed to add comment:', err);
      setError(err?.response?.data?.detail || 'Failed to submit comment. Please try again.');
    } finally {
      setSubmittingComment(false);
    }
  };

  const handleStatusTransition = async (newStatus) => {
    if (!selectedTicket) return;
    try {
      await api.support.updateTicket(selectedTicket.id, newStatus);
      await loadTicketDetail(selectedTicket.id);
      await loadTickets();
    } catch (err) {
      console.error('Failed to update ticket status:', err);
      setError('Failed to update status.');
    }
  };

  return (
    <div style={{ background: token.surface.panel, minHeight: "100%", display: "flex", flexDirection: "column", flex: 1, padding: 24 }}>
      {/* Top Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, borderBottom: `1px solid ${token.line.default}`, paddingBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ background: "rgba(0, 212, 255, 0.1)", border: `1px solid ${token.brand.base}40`, padding: 8, borderRadius: 10 }}>
            <HelpCircle size={22} color={token.brand.base} />
          </div>
          <div>
            <h1 style={{ color: token.content.primary, fontSize: 18, fontWeight: 900, fontFamily: "monospace", margin: 0 }}>
              INSTITUTIONAL SUPPORT & HELP CENTER
            </h1>
            <div style={{ color: token.content.secondary, fontSize: 11, fontFamily: "monospace", marginTop: 2 }}>
              Server-authoritative diagnostic support, execution tracing, and knowledge base
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => { setActiveTab('faqs'); setSelectedTicket(null); }}
            style={{
              background: activeTab === 'faqs' ? "rgba(0, 212, 255, 0.15)" : token.surface.inset,
              color: activeTab === 'faqs' ? token.brand.base : token.content.secondary,
              border: `1px solid ${activeTab === 'faqs' ? token.brand.base : token.line.default}`,
              padding: "8px 14px", borderRadius: 8, fontSize: 11, fontWeight: 700,
              fontFamily: "monospace", cursor: "pointer", display: "flex", alignItems: "center", gap: 6
            }}
          >
            <HelpCircle size={13} />
            Knowledge Base & FAQs
          </button>

          <button
            onClick={() => { setActiveTab('tickets'); setSelectedTicket(null); loadTickets(); }}
            style={{
              background: activeTab === 'tickets' ? "rgba(0, 212, 255, 0.15)" : token.surface.inset,
              color: activeTab === 'tickets' ? token.brand.base : token.content.secondary,
              border: `1px solid ${activeTab === 'tickets' ? token.brand.base : token.line.default}`,
              padding: "8px 14px", borderRadius: 8, fontSize: 11, fontWeight: 700,
              fontFamily: "monospace", cursor: "pointer", display: "flex", alignItems: "center", gap: 6
            }}
          >
            <MessageSquare size={13} />
            My Tickets ({tickets.length})
          </button>

          <button
            onClick={() => { setActiveTab('create'); setSelectedTicket(null); }}
            style={{
              background: activeTab === 'create' ? token.status.profit.fg : token.brand.base,
              color: "#000", border: "none",
              padding: "8px 16px", borderRadius: 8, fontSize: 11, fontWeight: 900,
              fontFamily: "monospace", cursor: "pointer", display: "flex", alignItems: "center", gap: 6
            }}
          >
            <Plus size={14} strokeWidth={3} />
            New Support Request
          </button>
        </div>
      </div>

      {/* Global Alerts */}
      {successMessage && (
        <div style={{ background: "rgba(0, 212, 170, 0.12)", border: `1px solid ${token.status.profit.fg}40`, borderRadius: 8, padding: "10px 16px", marginBottom: 16, display: "flex", alignItems: "center", gap: 8, color: token.status.profit.fg, fontSize: 12, fontFamily: "monospace" }}>
          <CheckCircle size={16} />
          {successMessage}
        </div>
      )}

      {error && (
        <div style={{ background: "rgba(255, 71, 87, 0.12)", border: `1px solid ${token.status.loss.fg}40`, borderRadius: 8, padding: "10px 16px", marginBottom: 16, display: "flex", alignItems: "center", gap: 8, color: token.status.loss.fg, fontSize: 12, fontFamily: "monospace" }}>
          <AlertCircle size={16} />
          <span style={{ flex: 1 }}>{error}</span>
          <button onClick={() => setError(null)} style={{ background: "transparent", border: "none", color: token.status.loss.fg, cursor: "pointer" }}><X size={14} /></button>
        </div>
      )}

      {/* TAB 1: KNOWLEDGE BASE & FAQS */}
      {activeTab === 'faqs' && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Search and Category Filter */}
          <div style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 12, padding: 16, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
            <div style={{ flex: 1, minWidth: 260, position: "relative" }}>
              <Search size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: token.content.muted }} />
              <input
                value={faqSearch}
                onChange={e => setFaqSearch(e.target.value)}
                placeholder="Search diagnostic topics, risk rules, CCXT endpoints..."
                style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "9px 12px 9px 36px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none" }}
              />
            </div>

            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {["all", "trading", "exchanges", "risk", "security", "technical", "billing"].map(cat => (
                <button
                  key={cat}
                  onClick={() => setFaqCategory(cat)}
                  style={{
                    background: faqCategory === cat ? "rgba(0, 212, 255, 0.12)" : token.surface.inset,
                    color: faqCategory === cat ? token.brand.base : token.content.secondary,
                    border: `1px solid ${faqCategory === cat ? token.brand.base : token.line.default}`,
                    padding: "6px 10px", borderRadius: 6, fontSize: 10, fontFamily: "monospace", fontWeight: 700,
                    textTransform: "uppercase", cursor: "pointer"
                  }}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          {/* FAQ Accordion */}
          {faqLoading ? (
            <div style={{ textAlign: "center", padding: 40 }}><Loader2 size={24} className="animate-spin" color={token.brand.base} /></div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {faqs.map(item => {
                const isExpanded = expandedFaq === item.id;
                return (
                  <div
                    key={item.id}
                    style={{
                      background: token.surface.raised, border: `1px solid ${isExpanded ? token.brand.base : token.line.default}`,
                      borderRadius: 10, padding: "14px 18px", transition: "all 0.15s", cursor: "pointer"
                    }}
                    // A pointer-only disclosure cannot be opened without a mouse. It is a
                    // button that expands a region, so it gets the role, aria-expanded, a
                    // tab stop and Enter/Space activation.
                    role="button"
                    aria-expanded={isExpanded}
                    tabIndex={0}
                    onClick={() => setExpandedFaq(isExpanded ? null : item.id)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
                        // Space scrolls the page by default, which would move the entry out
                        // from under the reader who just opened it.
                        event.preventDefault();
                        setExpandedFaq(isExpanded ? null : item.id);
                      }
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span style={{ background: "rgba(0, 212, 255, 0.1)", color: token.brand.base, fontSize: 9, fontFamily: "monospace", fontWeight: 900, padding: "2px 6px", borderRadius: 4, textTransform: "uppercase" }}>
                          {item.category}
                        </span>
                        <span style={{ color: token.content.primary, fontSize: 13, fontWeight: 700, fontFamily: "monospace" }}>
                          {item.question}
                        </span>
                      </div>
                      {isExpanded ? <ChevronDown size={16} color={token.brand.base} /> : <ChevronRight size={16} color={token.content.muted} />}
                    </div>

                    {isExpanded && (
                      <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${token.line.default}`, color: token.content.secondary, fontSize: 12, lineHeight: 1.6, fontFamily: "monospace" }}>
                        {item.answer}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* TAB 2: MY TICKETS (LIST VIEW) */}
      {activeTab === 'tickets' && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Filter Bar */}
          <div style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 12, padding: 14, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
            <div style={{ flex: 1, minWidth: 200, position: "relative" }}>
              <Search size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: token.content.muted }} />
              <input
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder="Search ticket subject, error codes, ID..."
                style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "8px 10px 8px 32px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none" }}
              />
            </div>

            {/* Status Filter */}
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "8px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none", cursor: "pointer" }}
            >
              <option value="all">Status: All</option>
              <option value="open">Open</option>
              <option value="in_progress">In Progress</option>
              <option value="waiting_for_user">Waiting on You</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>

            {/* Category Filter */}
            <select
              value={categoryFilter}
              onChange={e => setCategoryFilter(e.target.value)}
              style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "8px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none", cursor: "pointer" }}
            >
              <option value="all">Category: All</option>
              <option value="trading">Trading / Execution</option>
              <option value="technical">Technical</option>
              <option value="billing">Billing</option>
              <option value="security">Security</option>
              <option value="general">General</option>
            </select>

            {/* Priority Filter */}
            <select
              value={priorityFilter}
              onChange={e => setPriorityFilter(e.target.value)}
              style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "8px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none", cursor: "pointer" }}
            >
              <option value="all">Priority: All</option>
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>

            <button
              onClick={loadTickets}
              style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.secondary, padding: "8px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", cursor: "pointer", display: "flex", alignItems: "center", gap: 6 }}
            >
              <RefreshCw size={12} />
              Refresh
            </button>
          </div>

          {/* Ticket Listing */}
          {loading ? (
            <div style={{ textAlign: "center", padding: 60 }}><Loader2 size={24} className="animate-spin" color={token.brand.base} /></div>
          ) : tickets.length === 0 ? (
            <div style={{ background: token.surface.raised, border: `1px dashed ${token.line.default}`, borderRadius: 12, padding: 60, textAlign: "center" }}>
              <MessageSquare size={32} color={token.content.muted} style={{ margin: "0 auto 12px" }} />
              <div style={{ color: token.content.primary, fontSize: 14, fontWeight: 700, fontFamily: "monospace", marginBottom: 6 }}>No Support Tickets Found</div>
              <div style={{ color: token.content.secondary, fontSize: 11, fontFamily: "monospace", marginBottom: 16 }}>Need assistance with exchange keys, order routing, or risk rules? Submit a ticket.</div>
              <button
                onClick={() => setActiveTab('create')}
                style={{ background: token.brand.base, color: "#000", border: "none", padding: "8px 16px", borderRadius: 8, fontSize: 11, fontWeight: 900, fontFamily: "monospace", cursor: "pointer" }}
              >
                Create New Ticket
              </button>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {tickets.map(t => {
                const status = STATUS_CONFIG[t.status] || STATUS_CONFIG.open;
                const priority = PRIORITY_CONFIG[t.priority] || PRIORITY_CONFIG.medium;
                const StatusIcon = status.icon;

                return (
                  <div
                    key={t.id}
                    // A pointer-only row cannot be opened without a mouse. It activates a
                    // ticket, so it gets the button role, a tab stop and Enter/Space.
                    role="button"
                    tabIndex={0}
                    aria-label={`Open ticket ${t.subject || t.id}`}
                    onClick={() => loadTicketDetail(t.id)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
                        // Space scrolls the page by default, which would move the list out
                        // from under the row just activated.
                        event.preventDefault();
                        loadTicketDetail(t.id);
                      }
                    }}
                    style={{
                      background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 10,
                      padding: "14px 18px", display: "flex", alignItems: "center", gap: 14,
                      cursor: "pointer", transition: "all 0.15s"
                    }}
                    className="hover:border-cyan-500/40"
                  >
                    <div style={{ background: status.bg, color: status.text, padding: 8, borderRadius: 8 }}>
                      <StatusIcon size={16} />
                    </div>

                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                        <span style={{ color: token.content.muted, fontSize: 10, fontFamily: "monospace", fontWeight: 700 }}>#{t.id}</span>
                        <span style={{ color: token.content.primary, fontSize: 13, fontWeight: 700, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {t.subject}
                        </span>
                        {t.has_unread && (
                          <span style={{ background: token.brand.base, color: "#000", fontSize: 9, fontWeight: 900, padding: "1px 5px", borderRadius: 10, fontFamily: "monospace" }}>
                            NEW REPLY
                          </span>
                        )}
                      </div>

                      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ background: status.bg, color: status.text, fontSize: 9, fontWeight: 900, fontFamily: "monospace", padding: "2px 6px", borderRadius: 4 }}>
                          {status.label}
                        </span>
                        <span style={{ background: priority.bg, color: priority.text, fontSize: 9, fontWeight: 900, fontFamily: "monospace", padding: "2px 6px", borderRadius: 4, textTransform: "uppercase" }}>
                          {t.priority}
                        </span>
                        <span style={{ color: token.content.muted, fontSize: 10, fontFamily: "monospace" }}>
                          Category: {CATEGORIES[t.category]?.label || t.category}
                        </span>
                        <span style={{ color: token.content.muted, fontSize: 10, fontFamily: "monospace" }}>
                          Updated: {new Date(t.updated_at || t.created_at).toLocaleString()}
                        </span>
                      </div>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: 6, color: token.content.secondary, fontSize: 11, fontFamily: "monospace" }}>
                      <MessageSquare size={13} />
                      <span>{t.comment_count || 0}</span>
                      <ChevronRight size={16} color={token.content.muted} style={{ marginLeft: 4 }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* TAB 3: CREATE TICKET */}
      {activeTab === 'create' && (
        <div style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 12, padding: 24, maxWidth: 800 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20, borderBottom: `1px solid ${token.line.default}`, paddingBottom: 12 }}>
            <button
              onClick={() => setActiveTab('tickets')}
              style={{ background: "transparent", border: "none", color: token.content.secondary, cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
            >
              <ArrowLeft size={16} />
              <span style={{ fontSize: 11, fontFamily: "monospace" }}>Back to Tickets</span>
            </button>
            <h2 style={{ color: token.content.primary, fontSize: 16, fontWeight: 900, fontFamily: "monospace", margin: 0 }}>Create Support Request</h2>
          </div>

          <form onSubmit={handleCreateTicket} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {/* Subject */}
            <div>
              <label htmlFor="support-ticket-subject" style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
                Issue Subject *
              </label>
              <input
                id="support-ticket-subject"
                value={formData.subject}
                onChange={e => setFormData({ ...formData, subject: e.target.value })}
                placeholder="E.g., Bybit Testnet WebSocket Disconnect on BTCUSDT Linear"
                maxLength={200}
                required
                style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "10px 12px", borderRadius: 6, fontSize: 12, fontFamily: "monospace", outline: "none" }}
              />
            </div>

            {/* Category & Priority */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <div>
                <label htmlFor="support-ticket-category" style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
                  Category *
                </label>
                <select
                  id="support-ticket-category"
                  value={formData.category}
                  onChange={e => setFormData({ ...formData, category: e.target.value })}
                  style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "10px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none", cursor: "pointer" }}
                >
                  <option value="trading">Trading / Live Execution</option>
                  <option value="technical">Technical / DAG Compiler</option>
                  <option value="security">Security / 2FA / Keys</option>
                  <option value="billing">Billing / Invoices</option>
                  <option value="feature">Feature Request</option>
                  <option value="general">General Question</option>
                </select>
              </div>

              <div>
                <label htmlFor="support-ticket-priority" style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
                  Priority Level *
                </label>
                <select
                  id="support-ticket-priority"
                  value={formData.priority}
                  onChange={e => setFormData({ ...formData, priority: e.target.value })}
                  style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "10px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none", cursor: "pointer" }}
                >
                  <option value="low">Low (General question)</option>
                  <option value="medium">Medium (Standard issue)</option>
                  <option value="high">High (Execution latency / error)</option>
                  <option value="urgent">Urgent (Trading outage)</option>
                </select>
              </div>
            </div>

            {/* Optional References */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <div>
                <label htmlFor="support-ticket-strategy-id" style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
                  Strategy ID (Optional)
                </label>
                <input
                  id="support-ticket-strategy-id"
                  value={formData.strategy_id}
                  onChange={e => setFormData({ ...formData, strategy_id: e.target.value })}
                  placeholder="strat_..."
                  style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "10px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none" }}
                />
              </div>

              <div>
                <label htmlFor="support-ticket-order-id" style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
                  Order / Execution ID (Optional)
                </label>
                <input
                  id="support-ticket-order-id"
                  value={formData.order_id}
                  onChange={e => setFormData({ ...formData, order_id: e.target.value })}
                  placeholder="ord_..."
                  style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "10px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none" }}
                />
              </div>
            </div>

            {/* Description */}
            <div>
              <label htmlFor="support-ticket-description" style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
                Description & Reproduction Steps *
              </label>
              <textarea
                id="support-ticket-description"
                value={formData.description}
                onChange={e => setFormData({ ...formData, description: e.target.value })}
                placeholder="Provide detailed diagnostic logs, steps to reproduce, or order context. Never include raw API secrets or private keys."
                minLength={10}
                maxLength={5000}
                required
                style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "12px", borderRadius: 6, fontSize: 12, fontFamily: "monospace", outline: "none", minHeight: 140, resize: "vertical" }}
              />
              <div style={{ color: token.content.muted, fontSize: 10, fontFamily: "monospace", textAlign: "right", marginTop: 4 }}>
                {formData.description.length} / 5000 chars
              </div>
            </div>

            {/* Safe Attachment Simulation */}
            <div>
              <label htmlFor="support-ticket-attachment-name" style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
                Attachment Filename / Log Trace (Optional)
              </label>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <Paperclip size={14} color={token.content.muted} />
                <input
                  id="support-ticket-attachment-name"
                  value={formData.attachment_name}
                  onChange={e => setFormData({ ...formData, attachment_name: e.target.value })}
                  placeholder="e.g. execution_trace_bybit.log or signal_screenshot.png"
                  style={{ flex: 1, background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "8px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none" }}
                />
              </div>
            </div>

            {/* Submit Actions */}
            <div style={{ display: "flex", gap: 12, justifyContent: "flex-end", marginTop: 8 }}>
              <button
                type="button"
                onClick={() => setActiveTab('tickets')}
                style={{ background: "transparent", border: `1px solid ${token.line.default}`, color: token.content.secondary, padding: "10px 18px", borderRadius: 8, fontSize: 11, fontWeight: 700, fontFamily: "monospace", cursor: "pointer" }}
              >
                Cancel
              </button>

              <button
                type="submit"
                disabled={submitting}
                style={{ background: submitting ? token.content.muted : token.brand.base, color: "#000", border: "none", padding: "10px 22px", borderRadius: 8, fontSize: 11, fontWeight: 900, fontFamily: "monospace", cursor: submitting ? "not-allowed" : "pointer", display: "flex", alignItems: "center", gap: 6 }}
              >
                {submitting ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                Submit Request
              </button>
            </div>
          </form>
        </div>
      )}

      {/* TAB 4: TICKET DETAIL */}
      {activeTab === 'detail' && selectedTicket && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Detail Header Card */}
          <div style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 12, padding: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <button
                  onClick={() => { setActiveTab('tickets'); loadTickets(); }}
                  style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.secondary, padding: "6px 10px", borderRadius: 6, cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
                >
                  <ArrowLeft size={14} />
                  <span style={{ fontSize: 10, fontFamily: "monospace" }}>Back</span>
                </button>
                <span style={{ color: token.content.muted, fontSize: 11, fontFamily: "monospace", fontWeight: 700 }}>#{selectedTicket.id}</span>
              </div>

              {/* Status Actions */}
              <div style={{ display: "flex", gap: 8 }}>
                {selectedTicket.status !== 'closed' ? (
                  <button
                    onClick={() => handleStatusTransition('closed')}
                    style={{ background: "rgba(255, 71, 87, 0.12)", border: `1px solid ${token.status.loss.fg}40`, color: token.status.loss.fg, padding: "6px 12px", borderRadius: 6, fontSize: 11, fontWeight: 700, fontFamily: "monospace", cursor: "pointer" }}
                  >
                    Close Ticket
                  </button>
                ) : (
                  <button
                    onClick={() => handleStatusTransition('reopen')}
                    style={{ background: "rgba(0, 212, 170, 0.12)", border: `1px solid ${token.status.profit.fg}40`, color: token.status.profit.fg, padding: "6px 12px", borderRadius: 6, fontSize: 11, fontWeight: 700, fontFamily: "monospace", cursor: "pointer" }}
                  >
                    Reopen Ticket
                  </button>
                )}
              </div>
            </div>

            <h2 style={{ color: token.content.primary, fontSize: 16, fontWeight: 900, fontFamily: "monospace", marginBottom: 8 }}>
              {selectedTicket.subject}
            </h2>

            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
              <span style={{ background: STATUS_CONFIG[selectedTicket.status]?.bg || STATUS_CONFIG.open.bg, color: STATUS_CONFIG[selectedTicket.status]?.text || STATUS_CONFIG.open.text, fontSize: 9, fontWeight: 900, fontFamily: "monospace", padding: "2px 6px", borderRadius: 4 }}>
                {STATUS_CONFIG[selectedTicket.status]?.label || selectedTicket.status.toUpperCase()}
              </span>
              <span style={{ background: PRIORITY_CONFIG[selectedTicket.priority]?.bg || PRIORITY_CONFIG.medium.bg, color: PRIORITY_CONFIG[selectedTicket.priority]?.text || PRIORITY_CONFIG.medium.text, fontSize: 9, fontWeight: 900, fontFamily: "monospace", padding: "2px 6px", borderRadius: 4, textTransform: "uppercase" }}>
                {selectedTicket.priority}
              </span>
              <span style={{ color: token.content.secondary, fontSize: 11, fontFamily: "monospace" }}>
                Category: {CATEGORIES[selectedTicket.category]?.label || selectedTicket.category}
              </span>
              <span style={{ color: token.content.muted, fontSize: 11, fontFamily: "monospace" }}>
                Created: {new Date(selectedTicket.created_at).toLocaleString()}
              </span>
            </div>

            <div style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, borderRadius: 8, padding: 14, color: token.content.primary, fontSize: 12, fontFamily: "monospace", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
              {selectedTicket.description}
            </div>

            {/* Optional Metadata display */}
            {(selectedTicket.strategy_id || selectedTicket.order_id || selectedTicket.related_feature) && (
              <div style={{ display: "flex", gap: 16, marginTop: 12, color: token.content.muted, fontSize: 10, fontFamily: "monospace" }}>
                {selectedTicket.strategy_id && <span>Strategy ID: <code style={{ color: token.brand.base }}>{selectedTicket.strategy_id}</code></span>}
                {selectedTicket.order_id && <span>Order ID: <code style={{ color: token.brand.base }}>{selectedTicket.order_id}</code></span>}
                {selectedTicket.related_feature && <span>Feature: <code style={{ color: token.brand.base }}>{selectedTicket.related_feature}</code></span>}
              </div>
            )}
          </div>

          {/* Conversation Thread */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <h3 style={{ color: token.content.primary, fontSize: 13, fontWeight: 900, fontFamily: "monospace", margin: "4px 0" }}>
              CONVERSATION THREAD ({selectedTicket.comments?.length || 0})
            </h3>

            {(!selectedTicket.comments || selectedTicket.comments.length === 0) ? (
              <div style={{ background: token.surface.raised, border: `1px dashed ${token.line.default}`, borderRadius: 8, padding: 24, textAlign: "center", color: token.content.muted, fontSize: 11, fontFamily: "monospace" }}>
                No replies yet. Our engineering & support team will respond shortly.
              </div>
            ) : (
              selectedTicket.comments.map(c => (
                <div
                  key={c.id}
                  style={{
                    background: c.is_staff ? "rgba(0, 212, 255, 0.06)" : token.surface.raised,
                    border: `1px solid ${c.is_staff ? "rgba(0, 212, 255, 0.3)" : token.line.default}`,
                    borderRadius: 8, padding: 14
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={{ color: c.is_staff ? token.brand.base : token.content.primary, fontSize: 11, fontWeight: 900, fontFamily: "monospace" }}>
                        {c.is_staff ? "⚡ VyomQuant Support Team" : "You"}
                      </span>
                      {c.is_staff && (
                        <span style={{ background: "rgba(0, 212, 255, 0.2)", color: token.brand.base, fontSize: 8, fontWeight: 900, padding: "1px 5px", borderRadius: 3, fontFamily: "monospace" }}>
                          STAFF
                        </span>
                      )}
                    </div>
                    <span style={{ color: token.content.muted, fontSize: 10, fontFamily: "monospace" }}>
                      {new Date(c.created_at).toLocaleString()}
                    </span>
                  </div>

                  <div style={{ color: token.content.primary, fontSize: 12, lineHeight: 1.6, fontFamily: "monospace", whiteSpace: "pre-wrap" }}>
                    {c.message}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Reply Form */}
          {selectedTicket.status !== 'closed' ? (
            <form onSubmit={handleAddComment} style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 10, padding: 16 }}>
              <label htmlFor="support-ticket-reply" style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", display: "block", marginBottom: 8 }}>
                Post Reply
              </label>
              <textarea
                id="support-ticket-reply"
                value={commentText}
                onChange={e => setCommentText(e.target.value)}
                placeholder="Type your reply or additional diagnostic information..."
                maxLength={2000}
                required
                style={{ width: "100%", background: token.surface.inset, border: `1px solid ${token.line.default}`, color: token.content.primary, padding: "10px 12px", borderRadius: 6, fontSize: 11, fontFamily: "monospace", outline: "none", minHeight: 90, resize: "vertical", marginBottom: 8 }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ color: token.content.muted, fontSize: 10, fontFamily: "monospace" }}>{commentText.length} / 2000 chars</span>
                <button
                  type="submit"
                  disabled={submittingComment || !commentText.trim()}
                  style={{
                    background: submittingComment || !commentText.trim() ? token.content.muted : token.brand.base,
                    color: "#000", border: "none", padding: "8px 18px", borderRadius: 6,
                    fontSize: 11, fontWeight: 900, fontFamily: "monospace",
                    cursor: submittingComment || !commentText.trim() ? "not-allowed" : "pointer",
                    display: "flex", alignItems: "center", gap: 6
                  }}
                >
                  {submittingComment ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                  Send Reply
                </button>
              </div>
            </form>
          ) : (
            <div style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 8, padding: 12, textAlign: "center", color: token.content.muted, fontSize: 11, fontFamily: "monospace" }}>
              This ticket is closed. Click "Reopen Ticket" above to continue the conversation.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
