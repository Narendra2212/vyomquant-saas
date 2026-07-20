# ONBOARDING PLAN - ALGO22 TERMINAL

## The Goal
Reduce time-to-first-success. Non-technical users must be able to understand the platform, connect an exchange, and run a simulation within minutes of their first login.

## First Login Experience Flow

### Introduction Modal
- A premium, welcoming modal explaining what Algo22 is.
- "Welcome to Algo22. Let's get your trading automated in 4 easy steps."

### Step 1: Connect Exchange
- **Action:** Prompt user to input API keys.
- **UX:** Provide direct links to Binance/Bybit API creation pages. Show a secure, masked input field with a trust indicator ("Keys are encrypted client-side").

### Step 2: Create Strategy
- **Action:** Introduce the Strategy Builder.
- **UX:** Offer a one-click "Use Template" option (e.g., "Basic Moving Average Crossover"). Do not force them to build from scratch.

### Step 3: Enable Paper Trading
- **Action:** Toggle on Paper Trading mode.
- **UX:** Highlight the paper trading toggle. Explain that no real funds are at risk.

### Step 4: Run First Simulation
- **Action:** Hit the "Play/Start" button on the strategy.
- **UX:** Show a celebratory micro-animation when the simulation starts generating data.

## Persistent Guidance Elements

### Onboarding Progress Bar
- A sleek progress bar at the top of the dashboard tracking the 4 steps.
- Users receive an achievement/badge upon completion.

### Empty States
- **Strategies:** "You have no active strategies. [Create Strategy from Template]"
- **Portfolio:** "Connect an exchange to view your portfolio, or enable paper trading."
- **Executions:** "No executions yet. Start a strategy to see trades."

### Interactive Walkthrough
- A tool-tip based tour highlighting the Navigation sidebar, Strategy Builder, and Risk settings for users who click "Show me around."
