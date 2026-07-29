# HEATMAP WIRING PROOF

## File Modified
`algo22-terminal/src/pages/PremiumDashboard.jsx`

## Functions Modified
- `loadData()` inside the `useEffect` hook.
- `PnlHeatmap` component definition.

## Code Snippet (After)
```javascript
        // Load heatmap
        try {
          const heatData = await endpoints.user.getHeatmap(3);
          if (heatData) {
            setHeatmapData(heatData);
          }
        } catch (e) {
          console.error("Failed to load heatmap", e);
        }
```

```jsx
// Passed as prop
<PnlHeatmap data={heatmapData} />

// Component modification
const PnlHeatmap = ({ data }) => {
  const days = data ? data.map(d => d.pnl_usd || 0) : [];

  if (days.length === 0) {
    return (
      <div className="bg-[#131722] border border-[#202938] rounded-xl p-5 shadow-lg group flex flex-col h-full items-center justify-center">
         <Activity size={24} className="text-[#8B949E] mb-2" />
         <p className="text-[#8B949E] text-xs font-mono">No Heatmap Data Available</p>
      </div>
    );
  }
// ...
```

## Payload Trace
```json
[
  {"date": "2023-10-01T00:00:00Z", "pnl_usd": 150.20},
  {"date": "2023-10-02T00:00:00Z", "pnl_usd": -45.10},
  {"date": "2023-10-03T00:00:00Z", "pnl_usd": 200.00}
]
```
