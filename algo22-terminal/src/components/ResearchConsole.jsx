/**
 * Research Console - Professional Strategy Research Interface
 * 
 * PHASE L: Professional Research Console
 * Optimization, Heatmaps, Charts, Trade Replay, Reports, Parameter Comparison,
 * Walk Forward Results, Benchmark Comparison, Monte Carlo Results
 */

import React, { useState, useEffect } from "react";
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";
import { Activity, TrendingUp, BarChart3, Zap, Target, AlertTriangle, CheckCircle, Play, RefreshCw, Download, ChevronDown, ChevronRight } from "lucide-react";
import { Tag2, PanelTitle } from "../components/ui-legacy/primitives";
import { token } from "../design/tokens";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { post } from "../api";

const ResearchConsole = ({ strategyId, versionId, executionGraph }) => {
  const [activeTab, setActiveTab] = useState("optimization");
  const [isRunning, setIsRunning] = useState(false);
  const [results, setResults] = useState(null);
  const [report, setReport] = useState(null);
  
  // Optimization parameters
  const [optimizationMethod, setOptimizationMethod] = useState("grid_search");
  const [nIterations, setNIterations] = useState(50);
  const [parameters, setParameters] = useState({
    rsi_window: { range: [10, 30], n_points: 20 },
    macd_fast: { range: [8, 16], n_points: 9 },
    macd_slow: { range: [20, 30], n_points: 11 },
    stop_loss: { range: [0.01, 0.10], n_points: 10 },
    take_profit: { range: [0.05, 0.30], n_points: 10 }
  });
  
  const [dateRange, setDateRange] = useState({
    start_date: "2023-01-01",
    end_date: "2023-12-31"
  });
  
  const [simulationConfig, setSimulationConfig] = useState({
    initial_capital: 10000,
    commission: 0.001,
    slippage: 0.0005,
    risk_per_trade: 0.01,
    max_drawdown: 0.2,
    daily_loss_limit: 0.05,
    n_monte_carlo: 100
  });
  
  const runOptimization = async () => {
    setIsRunning(true);
    setResults(null);
    
    try {
      const response = await post(`/api/strategies/${strategyId}/optimize`, {
        execution_graph: executionGraph,
        optimization_method: optimizationMethod,
        validation_method: "time_series_cv",
        parameters: {
          ...parameters,
          ...dateRange
        },
        n_iterations: nIterations,
        n_trials: 10,
        training_window_days: 180,
        validation_window_days: 30,
        test_window_days: 30,
        ...dateRange,
        ...simulationConfig,
        run_walk_forward: true,
        run_monte_carlo: true,
        n_monte_carlo: simulationConfig.n_monte_carlo,
        run_sensitivity: true,
        run_benchmark: true
      });
      
      setResults(response);
      setReport(response.research_report);
      
    } catch (error) {
      console.error("Optimization failed:", error);
    } finally {
      setIsRunning(false);
    }
  };
  
  const renderOptimizationResults = () => {
    if (!report || !report.optimization_results) return null;
    
    const bestResult = report.optimization_results.reduce((best, current) => 
      current.metrics.sharpe_ratio > best.metrics.sharpe_ratio ? current : best
    );
    
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* Best Parameters */}
        <Card className="p-5">
          <PanelTitle title="Best Parameters" sub="Optimized configuration" />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, marginTop: 12 }}>
            {Object.entries(report.best_parameters || {}).map(([key, value]) => (
              <div key={key} style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
                <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                  {key}
                </div>
                <div style={{ fontSize: 14, fontWeight: 600, color: token.content.primary }}>
                  {typeof value === "number" ? value.toFixed(4) : value}
                </div>
              </div>
            ))}
          </div>
        </Card>
        
        {/* Optimization Chart */}
        <Card className="p-5">
          <PanelTitle title="Optimization Progress" sub="Sharpe Ratio across iterations" />
          <div style={{ height: 300, marginTop: 12 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={report.optimization_results.map((r, i) => ({
                iteration: i + 1,
                sharpe: r.metrics.sharpe_ratio || 0,
                return: r.metrics.total_return_pct || 0
              }))}>
                <CartesianGrid strokeDash="3" stroke={token.line.default} />
                <XAxis dataKey="iteration" stroke={token.content.muted} />
                <YAxis stroke={token.content.muted} />
                <Tooltip contentStyle={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 8 }} />
                <Legend />
                <Line type="monotone" dataKey="sharpe" stroke={token.brand.base} strokeWidth={2} name="Sharpe Ratio" />
                <Line type="monotone" dataKey="return" stroke={token.status.profit.fg} strokeWidth={2} name="Return %" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>
    );
  };
  
  const renderWalkForward = () => {
    if (!report || !report.walk_forward_results) return null;
    
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <Card className="p-5">
          <PanelTitle title="Walk Forward Analysis" sub="Training vs Testing performance" />
          <div style={{ height: 300, marginTop: 12 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={report.walk_forward_results.map(r => ({
                iteration: r.iteration,
                train: r.train_metrics.total_return_pct || 0,
                test: r.test_metrics.total_return_pct || 0
              }))}>
                <CartesianGrid strokeDash="3" stroke={token.line.default} />
                <XAxis dataKey="iteration" stroke={token.content.muted} />
                <YAxis stroke={token.content.muted} />
                <Tooltip contentStyle={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 8 }} />
                <Legend />
                <Bar dataKey="train" fill={token.brand.base} name="Training %" />
                <Bar dataKey="test" fill={token.status.profit.fg} name="Testing %" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>
    );
  };
  
  const renderMonteCarlo = () => {
    if (!report || !report.monte_carlo_results) return null;
    
    const mcResult = report.monte_carlo_results[0];
    
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <Card className="p-5">
          <PanelTitle title="Monte Carlo Simulation" sub={`${mcResult.n_simulations} simulations`} />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginTop: 12 }}>
            <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
              <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                Mean Return
              </div>
              <div style={{ fontSize: 18, fontWeight: 700, color: token.content.primary }}>
                {(mcResult.metrics.total_return_pct || 0).toFixed(2)}%
              </div>
            </div>
            <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
              <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                95% CI Lower
              </div>
              <div style={{ fontSize: 18, fontWeight: 700, color: token.status.warning.fg }}>
                {(mcResult.confidence_intervals.total_return_pct?.[0] || 0).toFixed(2)}%
              </div>
            </div>
            <div style={{ background: token.surface.inset, padding: 12, borderRadius: 8 }}>
              <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 4 }}>
                95% CI Upper
              </div>
              <div style={{ fontSize: 18, fontWeight: 700, color: token.status.profit.fg }}>
                {(mcResult.confidence_intervals.total_return_pct?.[1] || 0).toFixed(2)}%
              </div>
            </div>
          </div>
        </Card>
      </div>
    );
  };
  
  const renderSensitivity = () => {
    if (!report || !report.sensitivity_results) return null;
    
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {report.sensitivity_results.map(result => (
          <Card key={result.parameter_name} cls="p-5">
            <PanelTitle title={result.parameter_name} sub="Parameter sensitivity analysis" />
            <div style={{ height: 250, marginTop: 12 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={result.parameter_values.map((val, i) => ({
                  value: val,
                  sharpe: result.metrics_surface.sharpe_ratio?.[i] || 0,
                  return: result.metrics_surface.total_return_pct?.[i] || 0
                }))}>
                  <CartesianGrid strokeDash="3" stroke={token.line.default} />
                  <XAxis dataKey="value" stroke={token.content.muted} label={result.parameter_name} />
                  <YAxis stroke={token.content.muted} />
                  <Tooltip contentStyle={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 8 }} />
                  <Legend />
                  <Line type="monotone" dataKey="sharpe" stroke={token.brand.base} strokeWidth={2} name="Sharpe" />
                  <Line type="monotone" dataKey="return" stroke={token.status.profit.fg} strokeWidth={2} name="Return %" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        ))}
      </div>
    );
  };
  
  const renderStrategyScore = () => {
    if (!report || !report.strategy_score) return null;
    
    const score = report.strategy_score;
    
    return (
      <Card className="p-5">
        <PanelTitle title="Strategy Quality Score" sub={`Overall: ${(report.overall_quality_score * 100).toFixed(1)}%`} />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 12, marginTop: 12 }}>
          {Object.entries(score).map(([key, value]) => (
            <div key={key} style={{ textAlign: "center" }}>
              <div style={{ 
                width: 80, 
                height: 80, 
                borderRadius: "50%", 
                background: `conic-gradient(${token.brand.base} ${value * 360}deg, ${token.surface.inset} 0deg)`,
                margin: "0 auto 8px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center"
              }}>
                <div style={{
                  width: 60,
                  height: 60,
                  borderRadius: "50%",
                  background: token.surface.raised,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 18,
                  fontWeight: 700,
                  color: token.content.primary
                }}>
                  {(value * 100).toFixed(0)}
                </div>
              </div>
              <div style={{ fontSize: 9, color: token.content.muted, fontFamily: "monospace", textTransform: "uppercase" }}>
                {key.replace("_", " ")}
              </div>
            </div>
          ))}
        </div>
        
        {/* Warnings */}
        {report.warnings && report.warnings.length > 0 && (
          <div style={{ marginTop: 16, padding: 12, background: `${token.status.loss.fg}20`, border: `1px solid ${token.status.loss.fg}`, borderRadius: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <AlertTriangle size={16} style={{ color: token.status.loss.fg }} />
              <span style={{ fontSize: 11, fontWeight: 600, color: token.status.loss.fg }}>Warnings</span>
            </div>
            {report.warnings.map((warning, i) => (
              <div key={i} style={{ fontSize: 10, color: token.content.primary, marginLeft: 24 }}>
                • {warning}
              </div>
            ))}
          </div>
        )}
        
        {/* Deployment Gate */}
        <div style={{ marginTop: 16, padding: 12, background: report.deployment_approved ? `${token.status.profit.fg}20` : `${token.status.loss.fg}20`, border: `1px solid ${report.deployment_approved ? token.status.profit.fg : token.status.loss.fg}`, borderRadius: 8, display: "flex", alignItems: "center", gap: 8 }}>
          {report.deployment_approved ? (
            <CheckCircle size={20} style={{ color: token.status.profit.fg }} />
          ) : (
            <AlertTriangle size={20} style={{ color: token.status.loss.fg }} />
          )}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: report.deployment_approved ? token.status.profit.fg : token.status.loss.fg }}>
              {report.deployment_approved ? "Deployment Approved" : "Deployment Gate Failed"}
            </div>
            <div style={{ fontSize: 9, color: token.content.secondary }}>
              {report.deployment_gate_reason}
            </div>
          </div>
        </div>
      </Card>
    );
  };
  
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, height: "100%" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Activity size={24} style={{ color: token.brand.base }} />
          <span style={{ fontSize: 18, fontWeight: 700, color: token.content.primary }}>Research Console</span>
        </div>
        <Button variant="primary" size="sm" Icon={Play} onClick={runOptimization} disabled={isRunning}>
          {isRunning ? "Running..." : "Run Optimization"}
        </Button>
      </div>
      
      {/* Tabs */}
      <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${token.line.default}`, paddingBottom: 12 }}>
        {["optimization", "walk_forward", "monte_carlo", "sensitivity", "score"].map(tab => (
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
            {tab.replace("_", " ")}
          </button>
        ))}
      </div>
      
      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {activeTab === "optimization" && renderOptimizationResults()}
        {activeTab === "walk_forward" && renderWalkForward()}
        {activeTab === "monte_carlo" && renderMonteCarlo()}
        {activeTab === "sensitivity" && renderSensitivity()}
        {activeTab === "score" && renderStrategyScore()}
        
        {!report && !isRunning && (
          <div style={{ textAlign: "center", padding: 40, color: token.content.muted }}>
            <Activity size={48} style={{ margin: "0 auto 16", opacity: 0.5 }} />
            <div style={{ fontSize: 14 }}>Run optimization to see results</div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ResearchConsole;
