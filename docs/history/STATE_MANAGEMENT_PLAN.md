# STATE MANAGEMENT PLAN

The DAG Builder requires careful synchronization between the ReactFlow canvas (which controls rendering, positions, and connections) and the node configuration data.

## 1. Core State Hooks
The primary state will reside in `FlowCanvas.jsx` (or a dedicated context) using standard ReactFlow hooks:

```javascript
const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
```

## 2. ReactFlow Node Data Schema
ReactFlow expects each node to have `id`, `position`, `type`, and `data`. The `data` object is where we store the exact parameters required by the backend.

### Standardized `node.data` Schema
To ensure smooth serialization, the `data` object must maintain the fields required by the backend `DAGNode`.

```javascript
{
  id: "node-123",
  type: "indicator", // Maps to custom node component AND backend NodeType
  position: { x: 100, y: 100 },
  data: {
    label: "RSI",          // Used for UI
    indicator: "rsi",      // Used for Backend
    params: {              // Used for Backend
      period: 14
    }
  }
}
```

## 3. Node Selection and Properties State
We must track which node is currently selected to render the appropriate form in the Properties Panel.

```javascript
const [selectedNode, setSelectedNode] = useState(null);

const onSelectionChange = useCallback(({ nodes }) => {
  if (nodes.length === 1) {
    setSelectedNode(nodes[0]);
  } else {
    setSelectedNode(null);
  }
}, []);
```

## 4. Mutating Node Data (The Form Update Flow)
When a user updates a value in the `NodeConfigPanel.jsx`, we must push that change back into the specific ReactFlow node's `data` object without mutating state directly.

```javascript
const updateNodeData = (nodeId, newData) => {
  setNodes((nds) =>
    nds.map((node) => {
      if (node.id === nodeId) {
        return {
          ...node,
          data: {
            ...node.data,
            ...newData, // Merge new form values
          },
        };
      }
      return node;
    })
  );
};
```

## 5. Global State Requirements
To handle the strategy as a whole, the following state must be maintained at the `StrategyBuilder.jsx` container level:

- `strategyName`: string
- `isSaving`: boolean
- `isValidating`: boolean
- `validationErrors`: array of objects
- `backtestResults`: object (populated after successful simulation)
