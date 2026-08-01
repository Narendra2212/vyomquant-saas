import React from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  LayoutDashboard, Activity, Hexagon, Cpu, Layers, Link2,
  Shield, CreditCard, BookOpen, HelpCircle, Bell,
  Zap, ExternalLink, LogOut
} from "lucide-react";
import { C } from "./ui-legacy/primitives";

export const NAV = [
  { id: "dashboard",     lbl: "Dashboard",       Icon: LayoutDashboard, g: "core" },
  { id: "strategies",    lbl: "Strategies",       Icon: Layers,          g: "core" },
  { id: "signal-trace",  lbl: "Signal Trace",     Icon: Activity,        g: "core" },
  { id: "marketplace",   lbl: "Marketplace",      Icon: Hexagon,         g: "core" },
  { id: "builder",       lbl: "Strategy Builder", Icon: Cpu,             g: "core" },
  { id: "exchange",      lbl: "Exchanges",        Icon: Link2,           g: "vault" },
  { id: "risk",          lbl: "Risk Settings",    Icon: Shield,          g: "vault" },
  { id: "billing",       lbl: "Billing",          Icon: CreditCard,      g: "vault" },
  { id: "docs",          lbl: "Docs",             Icon: BookOpen,        g: "platform" },
  { id: "support",       lbl: "Support",          Icon: HelpCircle,      g: "platform" },
  { id: "notifications", lbl: "Notifications",    Icon: Bell,            g: "platform" },
];

export const G_LABELS = { core: "Command Center", vault: "Vault", platform: "Platform" };

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const groups = ["core", "vault", "platform"];

  const handleNavClick = (n) => {
    if (n.id === "docs") {
      window.open("https://docs.algo22.io", "_blank");
    } else {
      navigate(`/app/${n.id}`);
    }
  };

  const handleLogout = () => {
    sessionStorage.removeItem("token");
    navigate("/");
  };

  return (
    <aside style={{ background: C.bg1, borderRight: `1px solid ${C.border}`, width: 210, display: "flex", flexDirection: "column", flexShrink: 0, minHeight: "100vh" }} aria-label="Main Navigation">
      <div style={{ borderBottom: `1px solid ${C.border}`, padding: "14px 16px" }} className="flex items-center gap-2.5">
        <div style={{ background: "linear-gradient(135deg,#00d4ff,#0055ff)", borderRadius: 8, padding: "6px 7px", flexShrink: 0 }}>
          <Zap size={15} color="#000" strokeWidth={2.5} aria-hidden="true" />
        </div>
        <div>
          <div style={{ color: C.t1 }} className="text-base font-black tracking-tight leading-none">VYOM<span style={{ color: C.cyan }}>QUANT</span></div>
          <div style={{ color: C.t3 }} className="text-[8px] font-mono tracking-widest mt-0.5">QUANT INFRASTRUCTURE</div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3" style={{ scrollbarWidth: "none" }}>
        {groups.map(g => (
          <div key={g} className="mb-3">
            <div style={{ color: C.t3 }} className="text-[8px] font-mono font-black tracking-widest uppercase px-2 mb-1">{G_LABELS[g]}</div>
            {NAV.filter(n => n.g === g).map(n => {
              const active = location.pathname === `/app/${n.id}`;
              return (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => handleNavClick(n)}
                  aria-current={active ? "page" : undefined}
                  aria-label={n.lbl}
                  style={{
                    background: active ? "rgba(0,212,255,0.07)" : "transparent",
                    color: active ? C.cyan : C.t2,
                    borderLeft: `2px solid ${active ? C.cyan : "transparent"}`,
                    width: "100%", display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 8px", borderRadius: "0 8px 8px 0", fontSize: 11,
                    fontFamily: "monospace", fontWeight: 600, transition: "all 0.15s",
                    cursor: "pointer", marginBottom: 1,
                  }}
                  className="hover:text-cyan-400 hover:bg-cyan-500/5 focus:outline-none focus:ring-1 focus:ring-cyan-400"
                >
                  <n.Icon size={12} aria-hidden="true" />
                  <span>{n.lbl}</span>
                  {n.id === "docs" && <ExternalLink size={10} style={{ marginLeft: 4, color: C.t3 }} aria-hidden="true" />}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      <div style={{ borderTop: `1px solid ${C.border}`, padding: 10 }}>
        <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10 }} className="p-2 flex items-center gap-2">
          <div style={{ background: C.bg4, border: `1px solid ${C.border}`, borderRadius: "50%", width: 26, height: 26, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 900, color: C.accent, flexShrink: 0 }}>N</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: C.t1 }} className="text-xs font-bold truncate">Neo_Quant</div>
            <div style={{ color: C.t3 }} className="text-[8px] font-mono">PRO TIER</div>
          </div>
          <button
            type="button"
            aria-label="Log out"
            onClick={handleLogout}
            style={{ background: "transparent", border: "none", cursor: "pointer", padding: 4 }}
            className="hover:text-red-400 focus:outline-none focus:ring-1 focus:ring-red-400 rounded"
          >
            <LogOut size={11} style={{ color: C.t3 }} aria-hidden="true" />
          </button>
        </div>
      </div>
    </aside>
  );
}
