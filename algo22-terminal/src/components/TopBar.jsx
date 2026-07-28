import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Bell } from "lucide-react";
import { C, LiveStatusV2 } from "./ui-legacy/primitives";

export default function TopBar() {
  const navigate = useNavigate();
  const [t, setT] = useState(new Date());
  useEffect(() => { const id = setInterval(() => setT(new Date()), 1000); return () => clearInterval(id); }, []);

  return (
    <div style={{ background: C.bg1, borderBottom: `1px solid ${C.border}`, height: 44, display: "flex", alignItems: "center", justifyContent: "flex-end", padding: "0 16px", gap: 16, flexShrink: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <LiveStatusV2 status="running" />
        <span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>{t.toUTCString().slice(17, 25)} UTC</span>
        <button
          type="button"
          aria-label="View notifications (3 unread)"
          onClick={() => navigate("/app/notifications")}
          onMouseEnter={(e) => (e.currentTarget.style.color = C.accent)}
          onMouseLeave={(e) => (e.currentTarget.style.color = C.t2)}
          style={{ position: "relative", color: C.t2, background: "transparent", border: "none", cursor: "pointer", transition: "color 0.15s ease" }}
          className="focus:outline-none focus:ring-2 focus:ring-cyan-400 rounded"
        >
          <Bell size={14} aria-hidden="true" />
          <span style={{ position: "absolute", top: -4, right: -4, background: C.loss, borderRadius: "50%", width: 12, height: 12, fontSize: 8, display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", fontWeight: 900 }}>3</span>
        </button>
        <div style={{ width: 1, height: 16, background: C.border, margin: "0 4px" }} aria-hidden="true" />
        <button
          type="button"
          aria-label="View profile"
          onClick={() => navigate("/app/profile")}
          onMouseEnter={(e) => (e.currentTarget.style.color = C.t1)}
          onMouseLeave={(e) => (e.currentTarget.style.color = C.t2)}
          style={{ display: "flex", alignItems: "center", gap: 8, background: "transparent", border: "none", color: C.t2, cursor: "pointer", transition: "color 0.15s ease" }}
          className="focus:outline-none focus:ring-2 focus:ring-cyan-400 rounded p-0.5"
        >
          <div style={{ background: C.bg4, border: `1px solid ${C.border}`, borderRadius: "50%", width: 24, height: 24, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 900, color: C.accent }}>N</div>
          <span style={{ fontSize: 11, fontFamily: "monospace", fontWeight: 600 }}>Profile</span>
        </button>
      </div>
    </div>
  );
}
