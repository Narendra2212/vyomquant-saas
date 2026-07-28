import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Lock } from "lucide-react";
import { C, Btn, Inp, Card } from "../components/ui-legacy/primitives";

export default function UpdatePasswordPage() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  const handleUpdate = async (e) => {
    e.preventDefault();
    if (!password) { setError("Password cannot be empty."); return; }
    setLoading(true); setError(""); setSuccess(false);

    try {
      const { supabase } = await import('../supabase');
      const { error } = await supabase.auth.updateUser({ password });
      
      if (error) throw error;
      
      setSuccess(true);
      setTimeout(() => navigate("/app/dashboard"), 2000);
    } catch (err) {
      setError(err.message || "Failed to update password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 36, width: 450 }}>
        <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 22, marginBottom: 20, textAlign: "center" }}>
          Reset Password
        </h1>
        {success ? (
          <div style={{ color: C.green, fontSize: 13, textAlign: "center", background: `${C.green}12`, padding: 12, borderRadius: 8 }}>
            Password updated! Redirecting...
          </div>
        ) : (
          <form onSubmit={handleUpdate} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Inp
              lbl="New Password"
              ph="â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢"
              type="password"
              icon={Lock}
              val={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
            />
            <Btn v="primary" sz="md" cls="w-full justify-center mt-2" disabled={loading}>
              {loading ? "Updating..." : "Update Password"}
            </Btn>
            {!!error && <div style={{ marginTop: 10, color: C.red, fontSize: 12, textAlign: "center" }}>{error}</div>}
          </form>
        )}
      </div>
    </div>
  );
}

// ==============================
//  MASTER APP ROUTER & LAYOUT SHELL
// ==============================

