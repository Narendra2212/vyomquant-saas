export const serializeReactFlowToDAG = (nodes, edges) => {
  const serializedNodes = nodes.map((n) => {
    const backendTypeMap = {
      source: "input",
      indicator: "indicator",
      operator: "logic",
      logic: "logic",
      mlmodel: "ml",
      action: "action",
    };

    const nodeType = backendTypeMap[n.type] || n.type;
    const baseNode = {
      id: n.id,
      type: nodeType,
      label: n.data?.label || null,
    };

    if (nodeType === "input") {
      baseNode.symbol = n.data?.params?.symbol;
      baseNode.timeframe = n.data?.params?.timeframe;
    } else if (nodeType === "indicator") {
      baseNode.indicator = n.data?.label ? n.data.label.toLowerCase() : null;
      baseNode.params = n.data?.params || {};
    } else if (nodeType === "ml") {
      baseNode.model_id = n.data?.label;
      baseNode.confidence_threshold = n.data?.params?.confidence || 0.7;
    } else if (nodeType === "logic") {
      const gateOrOp = n.data?.params?.gate || n.data?.params?.operation;
      if (gateOrOp) {
        // Map common math ops to GT/LT/etc if needed, or pass directly
        baseNode.operator = gateOrOp.toUpperCase();
      }
    } else if (nodeType === "action") {
      const actionLabel = n.data?.label?.toLowerCase() || "";
      if (actionLabel.includes("buy")) baseNode.action = "buy";
      else if (actionLabel.includes("sell")) baseNode.action = "sell";
      else if (actionLabel.includes("hold")) baseNode.action = "hold";
      
      baseNode.order_type = n.data?.params?.order_type?.toLowerCase() || "market";
      
      const sizePct = n.data?.params?.size_pct;
      if (sizePct !== undefined) {
        // Assume frontend gives percentage as 15 for 15%
        baseNode.amount = sizePct / 100.0;
      }
    }

    return baseNode;
  }).filter(n => ["input", "indicator", "logic", "ml", "action"].includes(n.type));

  const serializedEdges = edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.label || null,
    condition: e.condition || null,
  }));

  return { nodes: serializedNodes, edges: serializedEdges };
};
