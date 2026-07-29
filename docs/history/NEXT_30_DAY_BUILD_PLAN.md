# NEXT 30 DAY BUILD PLAN

## Strategic Objective
Focus strictly on wiring existing backend infrastructure to the frontend UI to achieve a credible, revenue-generating launch state. Do not build new backend architectures; expose what already exists. 

### Ranking Criteria Applied
1. Revenue Impact
2. Launch Impact
3. Engineering Effort
4. Payment Provider Credibility
5. Investor Credibility

---

## PRIORITY 1: Visual DAG Builder (Weeks 1-2)
**Goal:** Transform the static UI mockup into a functional strategy generator.
* **Why First:** This is the core differentiator of the platform. Without this, users cannot create strategies, meaning they cannot backtest or trade. 
* **Revenue/Launch Impact:** **CRITICAL**. Without the builder, there is no product.
* **Engineering Effort:** **High**. Requires complex ReactFlow state management and serialization to the backend JSON schema.
* **Action Items:**
  - Build node configuration modals (e.g., parameter inputs for RSI, MACD).
  - Implement JSON serialization of ReactFlow nodes/edges.
  - Wire the "Save" button to `POST /api/strategies/`.
  - Wire the "Run Simulation" button to `POST /api/strategies/backtest`.

---

## PRIORITY 2: Live Trading Unlock (Week 3)
**Goal:** Enable users to deploy their built DAG strategies to live exchanges.
* **Why Second:** Once strategies can be built, they must be executable to generate subscriber ROI (Revenue).
* **Revenue/Launch Impact:** **CRITICAL**. The primary reason users will pay for subscriptions.
* **Engineering Effort:** **Medium**. The backend `bot_runner` and `ExecutionEngine` are already built and tested.
* **Action Items:**
  - Build a "Deploy Bot" dashboard UI.
  - Wire frontend to `POST /api/strategies/{id}/deploy`.
  - Safely lift the "SYSTEM FREEZE PROTOCOL" for authenticated/paying users.
  - Expose real-time portfolio WebSocket events to the frontend.

---

## PRIORITY 3: ML Training (XGBoost) (Week 3)
**Goal:** Deliver on the "AI/ML Copilot" marketing claims using existing XGBoost infrastructure.
* **Why Third:** Strong for investor credibility and marketing (Payment Providers).
* **Revenue/Launch Impact:** **High**. Drives upgrades to higher pricing tiers.
* **Engineering Effort:** **Medium**. Backend is fully ready; only UI is missing.
* **Action Items:**
  - Create an ML Training dashboard in the frontend.
  - Wire to `POST /api/strategies/train-ml`.
  - Implement WebSocket listeners to show training progress and completion.

---

## PRIORITY 4: Desktop Applications (Week 4)
**Goal:** Release native macOS and Windows binaries.
* **Why Fourth:** Desktop apps signal "institutional grade" software to investors and payment processors, establishing massive trust.
* **Revenue/Launch Impact:** **Medium**. Web app suffices for launch, but desktop aids retention.
* **Engineering Effort:** **Low-Medium**. Tauri wrapper already exists.
* **Action Items:**
  - Configure GitHub Actions for cross-platform compilation.
  - Set up Apple Developer Code Signing and Notarization.
  - Set up Windows Authenticode signing.

---

## DEFERRED TO V2: LSTM & Transformer Models
**Goal:** True deep learning forecasting.
* **Why Deferred:** The backend code exists, but wiring the APIs, handling massive GPU compute requirements, and building the UI is too high-risk for a 30-day window. XGBoost satisfies the "Machine Learning" marketing claim for the V1 launch.
* **Action Items (Post-Launch):**
  - Integrate `LSTMStrategyBlock` and `TransformerStrategyBlock` into the `/train-ml` API route.
  - Build out dedicated GPU infrastructure scaling.

---

## Conclusion
**What is already true?** 
The backend execution engine, portfolio caching, DAG validation compiler, and XGBoost training mechanics are fully functional. 

The next 30 days must be ruthlessly focused on **Frontend API Wiring**. If we bridge the gap between the beautiful React UI and the robust Python backend, the platform is ready for launch.
