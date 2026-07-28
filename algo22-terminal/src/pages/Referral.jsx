import React, { useState, useEffect } from "react";
import { Gift, Copy, Award, Users, ArrowUpRight } from "lucide-react";
import { endpoints } from "../api";
import { C, Card, SectionH, PanelTitle, Btn, Toast, ToastContainer } from "../components/ui-legacy/primitives";
export default function Referral() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    const controller = new AbortController();

    const fetchReferralStats = async () => {
      try {
        const data = await endpoints.user.getReferralStats();
        setStats(data);
      } catch (err) {
        if (err?.name !== "CanceledError") {
          console.error("Failed to load referral stats:", err);
        }
      } finally {
        setLoading(false);
      }
    };

    fetchReferralStats();
    return () => controller.abort();
  }, []);

  // Clear toast after 2.5 seconds
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(timer);
  }, [toast]);

  const handleCopy = async () => {
    if (!stats?.referral_link) return;
    try {
      await navigator.clipboard.writeText(stats.referral_link);
      setToast({ type: "success", msg: "Referral link copied to clipboard!" });
    } catch (err) {
      setToast({ type: "error", msg: "Failed to copy link." });
    }
  };

  const handleTwitterShare = () => {
    if (!stats?.referral_link) return;
    const text = encodeURIComponent("Trade at the speed of alpha with VyomQuant's institutional quant terminal. Join here: ");
    window.open(`https://twitter.com/intent/tweet?text=${text}&url=${encodeURIComponent(stats.referral_link)}`, '_blank');
  };

  const handleEmailShare = () => {
    if (!stats?.referral_link) return;
    const subject = encodeURIComponent("Invitation: VyomQuant Quant Terminal");
    const body = encodeURIComponent(`I've been using VyomQuant to automate my trading strategies. You can get a free trial using my institutional referral link here:\n\n${stats.referral_link}`);
    window.location.href = `mailto:?subject=${subject}&body=${body}`;
  };

  const displayLink = loading ? "Loading secure link..." : (stats?.referral_link || "No link generated yet.");

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, position: "relative" }}>
      <SectionH title="Referral Program" sub="Earn 20% lifetime commission on every referred subscription" />

      {/* Feature Deferred / Coming Soon Banner */}
      <div style={{ background: "rgba(0, 212, 255, 0.08)", border: "1px solid rgba(0, 212, 255, 0.25)", borderRadius: 12, padding: "14px 18px", marginBottom: 20, display: "flex", alignItems: "flex-start", gap: 12 }}>
        <Gift size={18} style={{ color: C.cyan, flexShrink: 0, marginTop: 2 }} />
        <div>
          <div style={{ color: C.cyan, fontSize: 12, fontWeight: 800, fontFamily: "monospace", textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>
            Referral Program ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Coming Soon (Phase 2)
          </div>
          <div style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", lineHeight: 1.5 }}>
            Automated referral attribution tracking and affiliate payout processing are currently undergoing final security audits. Your unique referral link is reserved below for preview. Full tracking will activate in Phase 2 (Task P4-2).
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 20 }}>
        {[
          { l: "Total Referrals", v: loading ? "..." : stats?.total_referrals || 0, c: C.cyan, I: Users },
          { l: "Active Subs", v: loading ? "..." : stats?.active_subs || 0, c: C.green, I: CheckCircle },
          { l: "Total Earned", v: loading ? "..." : `$${(stats?.total_earned || 0).toLocaleString()}`, c: C.gold, I: DollarSign },
          { l: "Pending Payout", v: loading ? "..." : `$${(stats?.pending_payout || 0).toLocaleString()}`, c: C.orange, I: Clock }
        ].map(s => (
          <Card key={s.l} cls="p-4">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>{s.l}</span>
              <s.I size={12} style={{ color: s.c }} />
            </div>
            <div style={{ color: s.c, fontSize: 20, fontWeight: 900 }}>{s.v}</div>
          </Card>
        ))}
      </div>

      <Card cls="p-6 mb-12">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <PanelTitle title="Your Referral Link (Reserved)" />
          <span style={{ background: `${C.cyan}15`, border: `1px solid ${C.cyan}33`, color: C.cyan, fontSize: 9, fontFamily: "monospace", fontWeight: 700, padding: "2px 8px", borderRadius: 4 }}>
            PREVIEW ONLY
          </span>
        </div>
        <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 16px", display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <Link2 size={14} style={{ color: C.cyan, flexShrink: 0 }} />
          <code style={{ color: loading ? C.t3 : C.cyan, fontSize: 11, fontFamily: "monospace", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {displayLink}
          </code>
          <Btn v="outline" sz="xs" Icon={Copy} onClick={handleCopy} disabled={loading || !stats?.referral_link}>Copy</Btn>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Btn v="outline" sz="sm" Icon={Send} onClick={handleTwitterShare} disabled={loading || !stats?.referral_link}>Share on Twitter</Btn>
          <Btn v="outline" sz="sm" Icon={Mail} onClick={handleEmailShare} disabled={loading || !stats?.referral_link}>Share via Email</Btn>
        </div>
      </Card>

      {/* Toast Notification */}
      {toast && (
        <div style={{ position: "fixed", right: 24, bottom: 24, background: toast.type === "success" ? `${C.green}20` : `${C.red}20`, border: `1px solid ${toast.type === "success" ? `${C.green}55` : `${C.red}55`}`, color: toast.type === "success" ? C.green : C.red, borderRadius: 8, padding: "10px 16px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, zIndex: 120, boxShadow: "0 10px 30px rgba(0,0,0,0.5)" }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
//  PAGE: PROFILE
// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â

