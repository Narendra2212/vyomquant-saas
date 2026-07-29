import React, { useState, useEffect } from "react";
import { Monitor, Smartphone, Maximize } from "lucide-react";
import { C } from "./ui-legacy/primitives";

export default function DesktopOnlyOverlay({ children }) {
  const [isNarrow, setIsNarrow] = useState(window.innerWidth < 1000);

  useEffect(() => {
    const handleResize = () => {
      setIsNarrow(window.innerWidth < 1000);
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  if (!isNarrow) {
    return <>{children}</>;
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", display: "flex", flex: 1, overflow: "hidden" }}>
      {/* Underlying content blurred */}
      <div style={{ filter: "blur(8px)", opacity: 0.5, pointerEvents: "none", width: "100%", height: "100%", display: "flex", flex: 1, overflow: "hidden" }}>
        {children}
      </div>

      {/* Overlay */}
      <div style={{
        position: "absolute",
        top: 0, left: 0, right: 0, bottom: 0,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(8, 10, 14, 0.85)", // C.bg0 with opacity
        backdropFilter: "blur(4px)",
        padding: 32,
        textAlign: "center"
      }}>
        <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 16, padding: "40px 32px", maxWidth: 440, display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div style={{ display: "flex", gap: 16, marginBottom: 24, color: C.cyan, alignItems: "center" }}>
            <Smartphone size={32} style={{ opacity: 0.5 }} />
            <Maximize size={20} style={{ opacity: 0.7 }} />
            <Monitor size={48} />
          </div>
          
          <h2 style={{ color: C.t1, fontSize: 20, fontWeight: 900, marginBottom: 12 }}>Desktop Optimized</h2>
          <p style={{ color: C.t2, fontSize: 13, lineHeight: 1.6, marginBottom: 24 }}>
            Algo22 Terminal is a dense, professional-grade trading environment designed for desktop screens. 
          </p>
          <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16, width: "100%" }}>
            <p style={{ color: C.t3, fontSize: 11, marginBottom: 8 }}>Please maximize your window or visit us on a larger device to continue.</p>
            <p style={{ color: C.cyan, fontSize: 11, fontWeight: 700 }}>Minimum width: 1000px</p>
          </div>
        </div>
      </div>
    </div>
  );
}
