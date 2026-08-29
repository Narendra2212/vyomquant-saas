/**
 * Builder Validation Context
 *
 * PHASE H: Professional validation with continuous error checking
 * Shows errors directly on canvas, never generic popups
 *
 * Local checks are advisory only; the backend graph validator is authoritative
 * (`design.md` → Frontend advisory vs backend authority).
 *
 * This context used to read a hardcoded block table (`blockRegistry.BlockRegistry`). That
 * table is gone: it was the second source of truth behind SB-03 and SB-04. Block shape is
 * now read from the descriptor the palette records on the node itself (`data.block_id`,
 * `data.category`, `data.inputs`, `data.outputs` — the same fields `canonicalGraph.js`
 * serializes). A node carrying no descriptor is **undetermined**, not invalid: guessing from
 * `node.type` is exactly the label-derived inference this rework exists to remove, and
 * flagging every such node would be a local opinion the backend never asked for.
 *
 * Connection type legality is deliberately not decided here. Task 3.8 adds
 * `connectionLegality.js`, which evaluates R1–R8 against the `compatibility_matrix` the
 * registry ships, so the client and the backend apply one rule instead of two.
 */

import { createContext, useContext, useState, useCallback, useMemo } from 'react';
import { AlertTriangle, AlertCircle, CheckCircle } from 'lucide-react';
import { C } from '../components/ui-legacy/primitives';

const ValidationContext = createContext(null);

/**
 * The registry descriptor recorded on a canvas node, or null when the node carries none.
 * Never derived from `node.type` or `data.label`.
 */
const descriptorOf = (node) => {
  const data = node?.data;
  if (!data || typeof data !== 'object') return null;
  const blockId = data.block_id || data.descriptor?.block_id || null;
  if (!blockId) return null;
  const descriptor = data.descriptor && typeof data.descriptor === 'object' ? data.descriptor : {};
  const inputs = Array.isArray(data.inputs) ? data.inputs : descriptor.inputs;
  const outputs = Array.isArray(data.outputs) ? data.outputs : descriptor.outputs;
  return {
    block_id: blockId,
    category: data.category || descriptor.category || null,
    inputs: Array.isArray(inputs) ? inputs : [],
    outputs: Array.isArray(outputs) ? outputs : [],
  };
};

export const useValidation = () => {
  const context = useContext(ValidationContext);
  if (!context) throw new Error("useValidation must be used within ValidationProvider");
  return context;
};

export const ValidationProvider = ({ children }) => {
  const [errors, setErrors] = useState([]);
  const [warnings, setWarnings] = useState([]);

  const validateGraph = useCallback((nodes, edges) => {
    const newErrors = [];
    const newWarnings = [];

    // Check for disconnected nodes
    const connectedNodeIds = new Set();
    edges.forEach(edge => {
      connectedNodeIds.add(edge.source);
      connectedNodeIds.add(edge.target);
    });

    // Descriptor-derived checks only run when every node carries its descriptor. A mixed
    // graph would produce half an opinion, which reads as a bug rather than as "waiting".
    const descriptors = new Map(nodes.map(node => [node.id, descriptorOf(node)]));
    const graphIsDetermined = nodes.length > 0 && nodes.every(node => descriptors.get(node.id));

    nodes.forEach(node => {
      const block = descriptors.get(node.id);
      if (!block) return;

      // Check if node has inputs but no incoming edges
      if (block.inputs.length > 0 && !connectedNodeIds.has(node.id)) {
        newErrors.push({
          id: `error-disconnected-${node.id}`,
          nodeId: node.id,
          type: 'disconnected',
          message: 'Node has no incoming connections',
          severity: 'error'
        });
      }

      // Check if node has outputs but no outgoing edges
      if (block.outputs.length > 0 && !edges.some(e => e.source === node.id)) {
        newWarnings.push({
          id: `warn-no-output-${node.id}`,
          nodeId: node.id,
          type: 'no_output',
          message: 'Node has no outgoing connections',
          severity: 'warning'
        });
      }

      // Parameter validation is not attempted locally. The registry publishes each block's
      // ParamSpec; the form (task 3.7) enforces it and the backend validator decides.
    });

    // Check for missing data source
    const hasDataSource = nodes.some(n => descriptors.get(n.id)?.category === 'DATA');
    if (graphIsDetermined && !hasDataSource) {
      newErrors.push({
        id: 'error-no-data-source',
        type: 'no_data_source',
        message: 'Strategy must have at least one data source block',
        severity: 'error'
      });
    }

    // Check for missing action
    const hasAction = nodes.some(n => descriptors.get(n.id)?.category === 'ACTION');
    if (graphIsDetermined && !hasAction) {
      newErrors.push({
        id: 'error-no-action',
        type: 'no_action',
        message: 'Strategy must have at least one action block',
        severity: 'error'
      });
    }

    // Check for circular dependencies
    const hasCycle = detectCycle(nodes, edges);
    if (hasCycle) {
      newErrors.push({
        id: 'error-cycle',
        type: 'circular_dependency',
        message: 'Strategy contains circular dependency',
        severity: 'error'
      });
    }

    // Validate all connections
    edges.forEach(edge => {
      const sourceNode = nodes.find(n => n.id === edge.source);
      const targetNode = nodes.find(n => n.id === edge.target);
      
      if (!sourceNode || !targetNode) {
        newErrors.push({
          id: `error-edge-${edge.id}`,
          edgeId: edge.id,
          type: 'invalid_connection',
          message: 'Connection references non-existent node',
          severity: 'error'
        });
        return;
      }

      // Port-type legality is decided by connectionLegality.js (task 3.8) against the
      // compatibility matrix the registry ships, so this file states no second rule.
    });

    // Check for ML blocks without features
    const mlNodes = nodes.filter(n => descriptors.get(n.id)?.category === 'ML_DL');
    mlNodes.forEach(mlNode => {
      const hasFeatureInput = edges.some(e => e.target === mlNode.id);
      if (!hasFeatureInput) {
        newErrors.push({
          id: `error-ml-no-features-${mlNode.id}`,
          nodeId: mlNode.id,
          type: 'ml_no_features',
          message: 'ML block requires feature input',
          severity: 'error'
        });
      }
    });

    // Check for action blocks without signal
    const actionNodes = nodes.filter(n => descriptors.get(n.id)?.category === 'ACTION');
    actionNodes.forEach(actionNode => {
      const hasSignalInput = edges.some(e => e.target === actionNode.id);
      if (!hasSignalInput) {
        newErrors.push({
          id: `error-action-no-signal-${actionNode.id}`,
          nodeId: actionNode.id,
          type: 'action_no_signal',
          message: 'Action block requires signal input',
          severity: 'error'
        });
      }
    });

    setErrors(newErrors);
    setWarnings(newWarnings);

    return {
      valid: newErrors.length === 0,
      errors: newErrors,
      warnings: newWarnings
    };
  }, []);

  /**
   * Local per-node check. `undetermined` when the node carries no registry descriptor —
   * distinct from invalid, because this context cannot decide without one.
   */
  const validateNode = useCallback((node) => {
    const block = descriptorOf(node);
    if (!block) {
      return { valid: true, undetermined: true, reason: 'No registry descriptor on the node' };
    }
    return { valid: true };
  }, []);

  const clearErrors = useCallback(() => {
    setErrors([]);
    setWarnings([]);
  }, []);

  const getErrorByNodeId = useCallback((nodeId) => {
    return errors.find(e => e.nodeId === nodeId);
  }, [errors]);

  const getErrorByEdgeId = useCallback((edgeId) => {
    return errors.find(e => e.edgeId === edgeId);
  }, [errors]);

  const isValid = useMemo(() => errors.length === 0, [errors.length]);

  const value = {
    errors,
    warnings,
    isValid,
    validateGraph,
    validateNode,
    clearErrors,
    getErrorByNodeId,
    getErrorByEdgeId
  };

  return (
    <ValidationContext.Provider value={value}>
      {children}
    </ValidationContext.Provider>
  );
};

// Helper function to detect cycles
function detectCycle(nodes, edges) {
  const adjacency = {};
  const nodeIds = nodes.map(n => n.id);
  
  nodeIds.forEach(id => adjacency[id] = []);
  
  edges.forEach(edge => {
    if (adjacency[edge.source]) {
      adjacency[edge.source].push(edge.target);
    }
  });

  const visited = new Set();
  const recursionStack = new Set();

  function hasCycle(nodeId) {
    visited.add(nodeId);
    recursionStack.add(nodeId);

    const neighbors = adjacency[nodeId] || [];
    for (const neighbor of neighbors) {
      if (!visited.has(neighbor)) {
        if (hasCycle(neighbor)) return true;
      } else if (recursionStack.has(neighbor)) {
        return true;
      }
    }

    recursionStack.delete(nodeId);
    return false;
  }

  for (const nodeId of nodeIds) {
    if (!visited.has(nodeId)) {
      if (hasCycle(nodeId)) return true;
    }
  }

  return false;
}