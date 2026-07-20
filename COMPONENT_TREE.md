# FRONTEND COMPONENT TREE: DAG BUILDER

This specifies the exact React component hierarchy required to implement the production DAG Builder using ReactFlow, replacing the current static mockup.

```text
src/
└── components/
    └── StrategyBuilder/
        ├── StrategyBuilder.jsx             (Main Container)
        │
        ├── Canvas/                         (ReactFlow Wrapper)
        │   ├── FlowCanvas.jsx              (Handles ReactFlow state, DND)
        │   └── custom_nodes/               (Visually distinct node components)
        │       ├── InputNode.jsx           (Displays Symbol/Timeframe)
        │       ├── IndicatorNode.jsx       (Displays Indicator name & params)
        │       ├── LogicNode.jsx           (Displays Operator & logic icon)
        │       ├── MLNode.jsx              (Displays Model ID & Threshold)
        │       └── ActionNode.jsx          (Displays Buy/Sell & Order type)
        │
        ├── Sidebar/
        │   ├── PaletteSidebar.jsx          (Left sidebar container)
        │   ├── NodeSearch.jsx              (Search bar for nodes)
        │   └── PaletteCategory.jsx         (Accordion for Categories)
        │
        ├── PropertiesPanel/
        │   ├── NodeConfigPanel.jsx         (Right sidebar container)
        │   ├── forms/                      (Dynamic forms based on selected node)
        │   │   ├── InputNodeForm.jsx       (Symbol, Timeframe dropdowns)
        │   │   ├── IndicatorNodeForm.jsx   (Period, Fast/Slow, StdDev inputs)
        │   │   ├── LogicNodeForm.jsx       (Operator dropdown, scalar threshold)
        │   │   ├── MLNodeForm.jsx          (Model registry select, threshold slider)
        │   │   └── ActionNodeForm.jsx      (Action type, order type, amount inputs)
        │   └── ValidationFeedback.jsx      (Displays local validation errors)
        │
        └── Toolbar/
            ├── TopToolbar.jsx              (Top bar container)
            ├── StrategyNameEditor.jsx      (Editable strategy name)
            └── ActionButtons.jsx           (Save, Validate, Backtest, Deploy buttons)
```

## Component Responsibilities

1. **`StrategyBuilder.jsx`**: 
   - Manages the overarching state (strategy ID, name).
   - Manages API interactions (`saveStrategy`, `validateStrategy`, `runBacktest`).
   
2. **`FlowCanvas.jsx`**:
   - Manages `nodes` and `edges` state using `useNodesState` and `useEdgesState`.
   - Handles `onDrop` events from the Palette to instantiate new nodes.
   - Registers `custom_nodes` so ReactFlow renders them with data bindings.
   
3. **`PaletteSidebar.jsx`**:
   - Holds the draggables. When a user drags a node, it uses HTML5 `dataTransfer` to pass the `type` (e.g., `indicator`, `logic`).

4. **`NodeConfigPanel.jsx`**:
   - Listens to `onSelectionChange` from ReactFlow.
   - When a node is selected, renders the corresponding form from `forms/`.
   - Dispatches an `updateNodeData` function to merge form changes directly into the selected node's `data` object in the ReactFlow state.
