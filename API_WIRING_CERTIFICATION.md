# API WIRING CERTIFICATION

## Execution
The React frontend has been successfully disconnected from all mock execution endpoints and wired directly into the production FastAPI routes.

### Routes Connected
- **Validation**: `POST /api/strategies/validate` (Added new "Validate" UI button to trigger this independently).
- **Save**: `POST /api/strategies`
- **Backtest**: `POST /api/strategies/backtest`
- **Deploy Live**: `POST /api/strategies/{id}/deploy`

### Obsolete Code Removal
The legacy deployment logic which relied on `endpoints.strategies.deployDag()` and mock payload injection `feature_engine` has been entirely removed.

All execution paths now uniformly compile the visual canvas state into the strict `DAGConfig` schema using `serializeReactFlowToDAG` and push it to the exact routes verified by `Sprint 1A`.
