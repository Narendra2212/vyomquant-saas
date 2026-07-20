import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "./api";
import { BookOpen, Mail } from "lucide-react";

// Institutional Design System Palette
const C = {
  bg:  "#010608", bg0: "#010608", bg1: "#040d14", bg2: "#071018", bg3: "#0b1724", bg4: "#0f1e2e",
  border: "#0f2035", cyan: "#00d4ff", t1: "#e8f4ff", t2: "#6b9bb8", t3: "#2a4a5e", t4: "#152535",
};

const SupportPage = () => {
  const navigate = useNavigate();
  const [tickets, setTickets] = useState([]);
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsLoading(true);
    try {
      const data = await api.support.createTicket({ subject, message, priority: "medium" });
      setTickets([data, ...tickets]);
      setSubject('');
      setMessage('');
    } catch (err) {
      console.error("Failed to create ticket:", err);
      // Show error but don't crash
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={{ padding: 24, overflowY: "auto", flex: 1 }}>
      <div style={{ marginBottom: 20 }}>
        <h2 style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>Support Center</h2>
        <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginTop: 3 }}>Get help from our team or find answers in the community</p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* Left Column - Form */}
        <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
          <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 900, marginBottom: 20 }}>Open New Ticket</h3>
          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 8 }}>Subject</label>
              <input
                value={subject}
                onChange={e => setSubject(e.target.value)}
                placeholder="Describe your issue briefly"
                style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, padding: "12px 14px", borderRadius: 8, fontSize: 11, fontFamily: "monospace", outline: "none", transition: "border 0.2s" }}
                required
              />
            </div>
            <div>
              <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 8 }}>Message</label>
              <textarea
                value={message}
                onChange={e => setMessage(e.target.value)}
                placeholder="Describe your issue in detail..."
                style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, padding: "12px 14px", borderRadius: 8, fontSize: 11, fontFamily: "monospace", minHeight: 140, outline: "none", resize: "vertical", transition: "border 0.2s" }}
                required
              />
            </div>
            <button type="submit" disabled={isLoading} style={{ background: isLoading ? C.t3 : C.cyan, color: "#000", border: "none", padding: "12px", borderRadius: 8, fontSize: 12, fontWeight: 900, fontFamily: "monospace", cursor: isLoading ? "not-allowed" : "pointer", marginTop: 8, letterSpacing: 1, boxShadow: `0 0 15px ${C.cyan}40`, opacity: isLoading ? 0.6 : 1 }}>
              {isLoading ? "Submitting..." : "Submit Ticket"}
            </button>
          </form>
        </div>

        {/* Right Column - Tickets & Resources */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24, flex: 1 }}>
            <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 900, marginBottom: 20 }}>Your Tickets</h3>
            {tickets.length === 0 ? (
              <div style={{ color: C.t3, fontSize: 11, fontFamily: "monospace", textAlign: "center", padding: "30px 0" }}>No support tickets opened yet.</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {tickets.map(t => (
                  <div key={t.id} style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 16px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ color: C.t1, fontSize: 12, fontWeight: 700 }}>{t.subject}</span>
                    <span style={{ color: C.cyan, fontSize: 9, fontFamily: "monospace", padding: "4px 8px", background: `${C.cyan}15`, borderRadius: 6, fontWeight: 900 }}>{t.status || "OPEN"}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
            <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 900, marginBottom: 16 }}>Quick Resources</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div onClick={() => navigate('/docs')} style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px", cursor: "pointer", display: "flex", alignItems: "center", gap: 12, transition: "all 0.2s" }}>
                <BookOpen size={16} style={{ color: C.cyan }} />
                <div>
                  <div style={{ color: C.t1, fontSize: 11, fontWeight: 900 }}>Documentation</div>
                  <div style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", marginTop: 2 }}>Full guides & tutorials</div>
                </div>
              </div>
              <div onClick={() => window.location.href = 'mailto:support@algo22.io'} style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px", cursor: "pointer", display: "flex", alignItems: "center", gap: 12, transition: "all 0.2s" }}>
                <Mail size={16} style={{ color: C.cyan }} />
                <div>
                  <div style={{ color: C.t1, fontSize: 11, fontWeight: 900 }}>Email Support</div>
                  <div style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", marginTop: 2 }}>support@algo22.io</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SupportPage;
