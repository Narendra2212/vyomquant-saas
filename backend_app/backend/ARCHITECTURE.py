"""
╔══════════════════════════════════════════════════════════════════════════╗
║  ALGO22 — 3-LAYER ARCHITECTURE                                           ║
║                                                                          ║
║  LAYER 1 — BACKEND (this folder: backend/)                               ║
║  ─────────────────────────────────────────────────────────────────────   ║
║  Pure computation. Zero HTTP/WebSocket/database code.                    ║
║  Can be unit-tested without any network.                                 ║
║                                                                          ║
║  Files:                                                                  ║
║    indicators_backend.py      Numba indicator functions + registry       ║
║    ml_models.py               XGBoost, LightGBM, RF, CatBoost,          ║
║                               LSTM, GRU, Transformer, Autoencoder       ║
║    backtesting_engine.py      VectorBT backtest runner                   ║
║    data_processing_engine.py  Numba tick→OHLCV aggregator               ║
║    strategy_builder.py        Blueprint AND/OR tree evaluator            ║
║    risk_manager.py            Institutional circuit breaker              ║
║    master_executor.py         BotRunner live loop orchestrator           ║
║    alert_engine.py            Discord + Telegram dispatcher              ║
║    fleet_manager.py           Multi-user bot lifecycle manager           ║
║                                                                          ║
║  LAYER 2 — CONNECTION LAYER (connection/)                                ║
║  ─────────────────────────────────────────────────────────────────────   ║
║  Exchange I/O, HTTP routing, WebSocket endpoints, auth, encryption.      ║
║  Depends on Layer 1 — never the other way around.                        ║
║                                                                          ║
║  Files:                                                                  ║
║    connection_engine.py       CCXT.pro exchange connector                ║
║    data_seeking_engine.py     WebSocket + REST market data               ║
║    order_execution_engine.py  CCXT order placement                       ║
║    security_vault.py          AES-256 key encryption (Supabase)          ║
║    telemetry_engine.py        QuestDB time-series R/W                    ║
║    main.py                    FastAPI app + routers + WS endpoints       ║
║    routers/                   REST route handlers                        ║
║    websockets/                WebSocket route handlers                   ║
║    core/state.py              Global singleton container (app_state)     ║
║                                                                          ║
║  LAYER 3 — FRONTEND (frontend/)                                          ║
║  ─────────────────────────────────────────────────────────────────────   ║
║  React + TypeScript UI. Calls Layer 2 via REST + WebSocket.              ║
║  Zero Python.                                                            ║
║                                                                          ║
║  Files:                                                                  ║
║    Algo22HyperFuturistic.jsx   Main React app (all 21 pages)             ║
║    api.js                      REST + WS client service layer            ║
║                                                                          ║
║  DEPENDENCY RULE:                                                        ║
║    Frontend → Connection Layer → Backend                                 ║
║    Backend NEVER imports from Connection Layer or Frontend.              ║
║    Connection Layer NEVER imports from Frontend.                         ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
