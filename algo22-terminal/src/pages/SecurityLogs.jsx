import React, { useState, useEffect } from "react";
import { Shield, ShieldCheck, AlertCircle, Clock, Search, Download } from "lucide-react";
import { C, SectionH, PanelTitle, Inp, Tag2 } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";

export default function SecurityLogs() {
  const [logs, setLogs] = useState([]);
  const [summary, setSummary] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");

  useEffect(() => {
    const controller = new AbortController();

    const fetchSecurityData = async () => {
      try {
        // SECURITY ENDPOINT QUARANTINED - Admin/God mode panel only
        // This data will be loaded when the admin panel is implemented
        setSummary({ logins_30d: 0, api_calls_24h: 0, failed_attempts: 0, active_sessions: 0 });
        setLogs([]);
      } catch (err) {
        if (err?.name !== "CanceledError") {
          console.error("Failed to fetch security logs:", err);
        }
      } finally {
        setIsLoading(false);
      }
    };
    fetchSecurityData();
    return () => controller.abort();
  }, []);

  const filteredLogs = (Array.isArray(logs) ? logs : []).filter(log => {
    const term = (searchTerm || "").toLowerCase();
    return (
      (log.event || "").toLowerCase().includes(term) ||
      (log.ip || "").toLowerCase().includes(term) ||
      (log.loc || "").toLowerCase().includes(term) ||
      (log.device || "").toLowerCase().includes(term)
    );
  });

  const handleExport = () => {
    const headers = ["Event", "IP Address", "Location", "Device", "Time", "Status"];
    const csvContent = [
      headers.join(","),
      ...filteredLogs.map(l => `"${l.event}","${l.ip}","${l.loc}","${l.device}","${l.time}","${l.status}"`)
    ].join("\n");

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `security_logs_${new Date().toISOString().split('T')[0]}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="Security Logs" sub="All authentication events and API key access records" />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 14 }}>
        {[
          { l: "Logins (30d)", v: isLoading ? "..." : summary?.logins_30d || 0, c: C.cyan },
          { l: "API Calls (24h)", v: isLoading ? "..." : summary?.api_calls_24h?.toLocaleString() || 0, c: C.purple },
          { l: "Failed Attempts", v: isLoading ? "..." : summary?.failed_attempts || 0, c: C.red },
          { l: "Active Sessions", v: isLoading ? "..." : summary?.active_sessions || 0, c: C.green },
        ].map(s => (
          <Card key={s.l} cls="p-4">
            <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 4 }}>{s.l}</div>
            <div style={{ color: s.c, fontSize: 18, fontWeight: 900, fontFamily: "monospace" }}>{s.v}</div>
          </Card>
        ))}
      </div>

      <Card>
        <div style={{ padding: "14px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 10 }}>
          <Search size={13} style={{ color: C.t3 }} />
          <input
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search events, IPs, locations..."
            style={{ background: "transparent", border: "none", outline: "none", color: C.t1, fontSize: 11, fontFamily: "monospace", flex: 1 }}
            className="placeholder:text-slate-700"
          />
          <Button variant="outline" size="xs" Icon={Download} onClick={handleExport} disabled={filteredLogs.length === 0}>Export</Button>
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {["Event", "IP Address", "Location", "Device", "Time", "Status"].map(h => (
                <th key={h} style={{ color: C.t3, fontWeight: 900, padding: "10px 14px", textAlign: "left", fontSize: 8, letterSpacing: 2, textTransform: "uppercase" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={6} style={{ padding: "20px", textAlign: "center", color: C.t3 }}>Loading security data...</td>
              </tr>
            ) : filteredLogs.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ padding: "20px", textAlign: "center", color: C.t3 }}>No security logs match your search.</td>
              </tr>
            ) : (
              filteredLogs.map(log => (
                <tr key={log.id} style={{ borderBottom: `1px solid ${C.border}15` }} className="hover:bg-white/5 transition-colors">
                  <td style={{ padding: "9px 14px", color: log.status.toLowerCase() === "failed" ? C.red : C.t1, fontWeight: 700 }}>{log.event}</td>
                  <td style={{ padding: "9px 14px", color: C.t2, fontFamily: "monospace" }}>{log.ip}</td>
                  <td style={{ padding: "9px 14px", color: C.t2 }}>{log.loc}</td>
                  <td style={{ padding: "9px 14px", color: C.t3, fontSize: 9 }}>{log.device}</td>
                  <td style={{ padding: "9px 14px", color: C.t3 }}>{log.time}</td>
                  <td style={{ padding: "9px 14px" }}>
                    <Tag2 c={log.status.toLowerCase() === "success" ? "green" : "red"}>{log.status.toUpperCase()}</Tag2>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
