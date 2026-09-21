import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Lock } from "lucide-react";
import { Inp } from "../components/common/primitives";
import { token } from "../design/tokens";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
// The one auth-redirect origin derivation (see `config.js`).
import { getAuthRedirectUrl } from "../config";

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
      // Second argument, same reason as `App.jsx`'s copy of this screen: it is where
      // `emailRedirectTo` lives, so an email change cannot fall back to the Site URL.
      const { error } = await supabase.auth.updateUser(
        { password },
        { emailRedirectTo: getAuthRedirectUrl() },
      );
      
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
    <div style={{ background: token.surface.canvas, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 18, padding: 36, width: 450 }}>
        <h1 style={{ color: token.content.primary, fontWeight: 900, fontSize: 22, marginBottom: 20, textAlign: "center" }}>
          Reset Password
        </h1>
        {success ? (
          <div style={{ color: token.status.profit.fg, fontSize: 13, textAlign: "center", background: `${token.status.profit.fg}12`, padding: 12, borderRadius: 8 }}>
            Password updated! Redirecting...
          </div>
        ) : (
          <form onSubmit={handleUpdate} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Inp
              lbl="New Password"
              ph="•••••••••••"
              type="password"
              icon={Lock}
              val={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
            />
            <Button variant="primary" size="md" cls="w-full justify-center mt-2" disabled={loading}>
              {loading ? "Updating..." : "Update Password"}
            </Button>
            {!!error && <div style={{ marginTop: 10, color: token.status.loss.fg, fontSize: 12, textAlign: "center" }}>{error}</div>}
          </form>
        )}
      </div>
    </div>
  );
}

// ==============================
//  MASTER APP ROUTER & LAYOUT SHELL
// ==============================

