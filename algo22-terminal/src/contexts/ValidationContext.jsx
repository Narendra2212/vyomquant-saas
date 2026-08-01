/**
 * Builder Validation Context
 * 
 * PHASE H: Professional validation with continuous error checking
 * Shows errors directly on canvas, never generic popups
 */

import { createContext, useContext, useState, useCallback, useMemo } from 'react';
import { AlertTriangle, AlertCircle, CheckCircle } from 'lucide-react';
import { C } from '../components/ui-legacy/primitives';
import { BlockRegistry, validateConnection, StreamTypes } from '../lib/blockRegistry';

const ValidationContext = createContext(null);

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

    nodes.forEach(node => {
      const block = BlockRegistry[node.type];
      if (!block) {
        newErrors.push({
          id: `error-node-${node.id}`,
          nodeId: node.id,
          type: 'invalid_block',
          message: `Unknown block type: ${node.type}`,
          severity: 'error'
        });
        return;
      }

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

      // Validate node parameters
      if (block.validation) {
        const validationResult = block.validation(node.data?.params || {});
        if (!validationResult.valid) {
          newErrors.push({
            id: `error-params-${node.id}`,
            nodeId: node.id,
            type: 'invalid_params',
            message: validationResult.error,
            severity: 'error'
          });
        }
      }
    });

    // Check for missing data source
    const hasDataSource = nodes.some(n => {
      const block = BlockRegistry[n.type];
      return block && block.category === 'data';
    });
    if (!hasDataSource) {
      newErrors.push({
        id: 'error-no-data-source',
        type: 'no_data_source',
        message: 'Strategy must have at least one data source block',
        severity: 'error'
      });
    }

    // Check for missing action
    const hasAction = nodes.some(n => {
      const block = BlockRegistry[n.type];
      return block && block.category === 'action';
    });
    if (!hasAction) {
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

      const connectionValidation = validateConnection(
        sourceNode.type,
        targetNode.type,
        sourceNode.data?.params?.output,
        targetNode.data?.params?.input
      );

      if (!connectionValidation.valid) {
        newErrors.push({
          id: `error-edge-type-${edge.id}`,
          edgeId: edge.id,
          type: 'type_mismatch',
          message: connectionValidation.error,
          severity: 'error'
        });
      }
    });

    // Check for ML blocks without features
    const mlNodes = nodes.filter(n => {
      const block = BlockRegistry[n.type];
      return block && (block.category === 'ml' || block.category === 'dl');
    });
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
    const actionNodes = nodes.filter(n => {
      const block = BlockRegistry[n.type];
      return block && block.category === 'action';
    });
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

  const validateNode = useCallback((node) => {
    const block = BlockRegistry[node.type];
    if (!block) {
      return { valid: false, error: 'Unknown block type' };
    }

    if (block.validation) {
      return block.validation(node.data?.params || {});
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