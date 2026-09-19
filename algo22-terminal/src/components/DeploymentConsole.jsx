/**
 * Deployment Console - Professional Strategy Deployment Interface
 * 
 * PHASE J: Strategy Page
 * Every strategy displays:
 * - Deployment Status
 * - Worker
 * - Exchange
 * - Runtime
 * - Health
 * - Latency
 * - CPU
 * - Memory
 * - Logs
 * - Restart
 * - Pause
 * - Resume
 * - Stop
 */

import React, { useState, useEffect } from "react";
import { Play, Pause, Square, RotateCcw, Server, Activity, Cpu, Zap, AlertTriangle, CheckCircle, Clock, ChevronDown, ChevronRight } from "lucide-react";
import { Tag2, PanelTitle } from "../components/ui-legacy/primitives";
import { token } from "../design/tokens";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { post, get } from "../api";

const DeploymentConsole = ({ strategyId, versionId, executionGraph }) => {
  const [activeTab, setActiveTab] = useState("deployments");
  const [deployments, setDeployments] = useState([]);
  const [selectedDeployment, setSelectedDeployment] = useState(null);
  const [isDeploying, setIsDeploying] = useState(false);
  
  const [deploymentConfig, setDeploymentConfig] = useState({
    environment: "paper",
    exchange_id: null,
    exchange_symbol: "BTC/USDT",
    worker_region: "us-east-1",
    initial_capital: 10000,
    risk_per_trade: 0.01,
    max_drawdown: 0.2,
    daily_loss_limit: 0.05
  });
  
  const loadDeployments = async () => {
    try {
      const response = await get(`/api/strategies/${strategyId}/deployments`);
      setDeployments(response.deployments || []);
    } catch (error) {
      console.error("Failed to load deployments:", error);
    }
  };
  
  const deployStrategy = async () => {
    setIsDeploying(true);
    
    try {
      const response = await post(`/api/strategies/${strategyId}/deploy`, {
        execution_graph: executionGraph,
        ...deploymentConfig
      });
      
      if (response.status === "deployed") {
        await loadDeployments();
        setSelectedDeployment(response.deployment_id);
      }
    } catch (error) {
      console.error("Deployment failed:", error);
    } finally {
      setIsDeploying(false);
    }
  };
  
  const pauseDeployment = async (deploymentId) => {
    try {
      await post(`/api/deployments/${deploymentId}/pause`);
      await loadDeployments();
    } catch (error) {
      console.error("Failed to pause deployment:", error);
    }
  };
  
  const resumeDeployment = async (deploymentId) => {
    try {
      await post(`/api/deployments/${deploymentId}/resume`);
      await loadDeployments();
    } catch (error) {
      console.error("Failed to resume deployment:", error);
    }
  };
  
  const restartDeployment = async (deploymentId) => {
    try {
      await post(`/api/deployments/${deploymentId}/restart`);
      await loadDeployments();
    } catch (error) {
      console.error("Failed to restart deployment:", error);
    }
  };
  
  const stopDeployment = async (deploymentId) => {
    try {
      await post(`/api/deployments/${deploymentId}/stop`);
      await loadDeployments();
    } catch (error) {
      console.error("Failed to stop deployment:", error);
    }
  };
  
  const getStatusColor = (status) => {
    switch (status) {
      case "running": return token.status.profit.fg;
      case "paused": return token.status.warning.fg;
      case "stopped": return token.content.muted;
      case "failed": return token.status.loss.fg;
      case "starting": return token.brand.base;
      case "stopping": return token.status.warning.fg;
      case "restarting": return token.brand.base;
      default: return token.content.muted;
    }
  };
  
  const getStatusIcon = (status) => {
    switch (status) {
      case "running": return <CheckCircle size={16} />;
      case "paused": return <Pause size={16} />;
      case "stopped": return <Square size={16} />;
      case "failed": return <AlertTriangle size={16} />;
      case "starting": return <Activity size={16} />;
      case "stopping": return <Activity size={16} />;
      case "restarting": return <RotateCcw size={16} />;
      default: return <Activity size={16} />;
    }
  };
  
  useEffect(() => {
    loadDeployments();
    const interval = setInterval(loadDeployments, 5000); // Poll every 5 seconds
    return () => clearInterval(interval);
  }, [strategyId]);
  
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, height: "100%" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Server size={24} style={{ color: token.brand.base }} />
          <span style={{ fontSize: 18, fontWeight: 700, color: token.content.primary }}>Deployment Console</span>
        </div>
        <Button variant="primary" size="sm" Icon={Play} onClick={deployStrategy} disabled={isDeploying}>
          {isDeploying ? "Deploying..." : "Deploy Strategy"}
        </Button>
      </div>
      
      {/* Tabs */}
      <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${token.line.default}`, paddingBottom: 12 }}>
        {["deployments", "worker", "health", "logs"].map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: "8px 16px",
              background: activeTab === tab ? token.surface.inset : "transparent",
              border: activeTab === tab ? `1px solid ${token.line.default}` : "none",
              borderRadius: 6,
              fontSize: 11,
              fontFamily: "monospace",
              fontWeight: 600,
              color: activeTab === tab ? token.content.primary : token.content.muted,
              cursor: "pointer",
              textTransform: "uppercase"
            }}
          >
            {tab}
          </button>
        ))}
      </div>
      
      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {activeTab === "deployments" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {deployments.length === 0 ? (
              <div style={{ textAlign: "center", padding: 40, color: token.content.muted }}>
                <Server size={48} style={{ margin: "0 auto 16", opacity: 0.5 }} />
                <div style={{ fontSize: 14 }}>No active deployments</div>
              </div>
            ) : (
              deployments.map(deployment => (
                <Card key={deployment.deployment_id} cls="p-5">
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      {getStatusIcon(deployment.status)}
                      <span style={{ fontSize: 14, fontWeight: 600, color: token.content.primary }}>
                        {deployment.environment.toUpperCase()}
                      </span>
                      <Tag2 cls={getStatusColor(deployment.status)}>{deployment.status}</Tag2>
                    </div>
                    <div style={{ display: "flex", gap: 4 }}>
                      {deployment.status === "running" && (
                        <>
                          <Button variant="secondary" size="xs" Icon={Pause} onClick={() => pauseDeployment(deployment.deployment_id)}>
                            Pause
                          </Button>
                          <Button variant="secondary" size="xs" Icon={RotateCcw} onClick={() => restartDeployment(deployment.deployment_id)}>
                            Restart
                          </Button>
                        </>
                      )}
                      {deployment.status === "paused" && (
                        <Button variant="secondary" size="xs" Icon={Play} onClick={() => resumeDeployment(deployment.deployment_id)}>
                          Resume
                        </Button>
                      )}
                      {(deployment.status === "running" || deployment.status === "paused") && (
                        <Button variant="danger" size="xs" Icon={Square} onClick={() => stopDeployment(deployment.deployment_id)}>
                          Stop
                        </Button>
                      )}
                    </div>
                  </div>
                  
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
                    <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
                      <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                        Worker
                      </div>
                      <div style={{ fontSize: 12, color: token.content.primary }}>
                        {deployment.worker || "N/A"}
                      </div>
                    </div>
                    <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
                      <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                        Started
                      </div>
                      <div style={{ fontSize: 12, color: token.content.primary }}>
                        {new Date(deployment.started_at).toLocaleString()}
                      </div>
                    </div>
                    <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
                      <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                        CPU
                      </div>
                      <div style={{ fontSize: 12, color: token.content.primary }}>
                        {deployment.health?.cpu_percent?.toFixed(1) || 0}%
                      </div>
                    </div>
                    <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
                      <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                        Memory
                      </div>
                      <div style={{ fontSize: 12, color: token.content.primary }}>
                        {deployment.health?.memory_percent?.toFixed(1) || 0}%
                      </div>
                    </div>
                    <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
                      <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                        Latency
                      </div>
                      <div style={{ fontSize: 12, color: token.content.primary }}>
                        {deployment.health?.latency_ms?.toFixed(1) || 0}ms
                      </div>
                    </div>
                    <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
                      <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                        Uptime
                      </div>
                      <div style={{ fontSize: 12, color: token.content.primary }}>
                        {Math.floor((deployment.health?.uptime_seconds || 0) / 3600)}h
                      </div>
                    </div>
                  </div>
                </Card>
              ))
            )}
          </div>
        )}
        
        {activeTab === "worker" && selectedDeployment && (
          <Card className="p-5">
            <PanelTitle title="Worker Status" sub="Worker health and metrics" />
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, color: token.content.muted }}>
                Worker details for selected deployment
              </div>
            </div>
          </Card>
        )}
        
        {activeTab === "health" && selectedDeployment && (
          <Card className="p-5">
            <PanelTitle title="Health Monitoring" sub="System health metrics" />
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, color: token.content.muted }}>
                Health metrics for selected deployment
              </div>
            </div>
          </Card>
        )}
        
        {activeTab === "logs" && selectedDeployment && (
          <Card className="p-5">
            <PanelTitle title="Execution Logs" sub="Runtime logs and events" />
            <div style={{ marginTop: 12, fontFamily: "monospace", fontSize: 10, color: token.content.secondary }}>
              <div>Logs will be displayed here</div>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
};

export default DeploymentConsole;
