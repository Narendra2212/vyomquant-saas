import React, { useState, useEffect } from "react";
import { UserCheck, Mail, Shield, Edit2, Check, AlertCircle } from "lucide-react";
import { endpoints } from "../api";
import { C, Card, SectionH, PanelTitle, Btn, Inp } from "../components/ui-legacy/primitives";

export default function Profile() {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();

    const fetchProfile = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await endpoints.user.getProfile() || {};
        setProfile(data);
      } catch (err) {
        if (err?.name !== "CanceledError") {
          console.error("Failed to load profile:", err);
          if (err?.response?.status === 401 || err?.response?.status === 403) {
            setError("Authentication required. Please log in.");
          } else {
            setError("Failed to load profile data");
          }
          setProfile({});
        }
      } finally {
        setLoading(false);
      }
    };
    fetchProfile();
    return () => controller.abort();
  }, []);

  if (loading) {
    return (
      <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.t2, fontFamily: "monospace" }}>
          Loading profile...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.red, fontFamily: "monospace", textAlign: "center" }}>
          {error}
        </div>
      </div>
    );
  }

  // Render specific fields: id, email, role
  const fields = [
    { key: 'id', label: 'User ID' },
    { key: 'email', label: 'Email' },
    { key: 'role', label: 'Role' },
  ];

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="User Profile" sub="Your account information" />
      <Card cls="p-6">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 16 }}>
          {fields.map(({ key, label }) => (
            <div key={key} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>
                {label}
              </label>
              <input
                value={profile?.[key] !== null && profile?.[key] !== undefined ? String(profile[key]) : ""}
                readOnly
                style={{
                  background: C.bg3,
                  border: `1px solid ${C.border}`,
                  borderRadius: 8,
                  padding: "10px 12px",
                  color: C.t1,
                  fontSize: 11,
                  fontFamily: "monospace",
                  outline: "none"
                }}
              />
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
