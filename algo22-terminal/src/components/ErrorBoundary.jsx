import React from "react";
import * as Sentry from "@sentry/react";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
    this.setState({ errorInfo });
    Sentry.captureException(error, { extra: errorInfo });
  }

  handleCopyError = () => {
    const errorText = `Error: ${this.state.error?.message || 'Unknown error'}\n\nStack:\n${this.state.error?.stack || 'No stack trace'}\n\nComponent Stack:\n${this.state.errorInfo?.componentStack || 'No component stack'}`;
    navigator.clipboard.writeText(errorText);
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          background: "#010608",
          color: "#6b9bb8",
          fontFamily: "monospace",
          padding: 40
        }}>
          <div style={{ fontSize: 64, marginBottom: 20 }}>⚠️</div>
          <div style={{ fontSize: 20, fontWeight: 900, marginBottom: 8, color: "#00d4ff" }}>
            Application Error
          </div>
          <div style={{ fontSize: 12, marginBottom: 24, textAlign: "center", maxWidth: 500, lineHeight: 1.6 }}>
            An unexpected error occurred. The application has been prevented from crashing to preserve your data.
          </div>

          {/* Error Details */}
          <div style={{
            background: "#0a1014",
            border: "1px solid #1a2530",
            borderRadius: 8,
            padding: 16,
            maxWidth: 600,
            width: "100%",
            marginBottom: 20,
            maxHeight: 300,
            overflow: "auto"
          }}>
            <div style={{ fontSize: 10, fontWeight: 700, marginBottom: 8, color: "#ff4757" }}>
              ERROR DETAILS
            </div>
            <div style={{ fontSize: 10, color: "#ff6b81", marginBottom: 12, wordBreak: "break-word" }}>
              {this.state.error?.message || "Unknown error"}
            </div>

            {this.state.error?.stack && (
              <div style={{ fontSize: 9, color: "#4a5568", marginBottom: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {this.state.error.stack}
              </div>
            )}

            {this.state.errorInfo?.componentStack && (
              <div style={{ fontSize: 9, color: "#4a5568", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {this.state.errorInfo.componentStack}
              </div>
            )}
          </div>

          {/* Action Buttons */}
          <div style={{ display: "flex", gap: 12 }}>
            <button
              onClick={() => window.location.reload()}
              style={{
                background: "#00d4ff22",
                border: "1px solid #00d4ff55",
                color: "#00d4ff",
                borderRadius: 6,
                padding: "10px 20px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
                fontFamily: "monospace",
                transition: "all 0.2s"
              }}
              onMouseOver={(e) => e.target.style.background = "#00d4ff33"}
              onMouseOut={(e) => e.target.style.background = "#00d4ff22"}
            >
              Reload Page
            </button>

            <button
              onClick={this.handleCopyError}
              style={{
                background: "#1a2530",
                border: "1px solid #2d3b4a",
                color: "#6b9bb8",
                borderRadius: 6,
                padding: "10px 20px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
                fontFamily: "monospace",
                transition: "all 0.2s"
              }}
              onMouseOver={(e) => e.target.style.background = "#2d3b4a"}
              onMouseOut={(e) => e.target.style.background = "#1a2530"}
            >
              Copy Error
            </button>
          </div>

          <div style={{ fontSize: 10, marginTop: 16, color: "#4a5568" }}>
            Timestamp: {new Date().toISOString()}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
