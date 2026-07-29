# FIRST_30_DAYS_PLAN

## Week 1: Stability & Hotfixes
- **Goal**: Ensure the platform does not crash under the initial cohort load.
- **Bug Triage**: Daily triage at 16:00. Prioritize P0/P1 only. Defer all UI nitpicks.
- **Support**: High-touch support in Discord. Manually assist users with DAG creation.
- **Performance Review**: End-of-week review of Sentry and API Latency. Ensure DB is not locking.

## Week 2: Usability & Workflow
- **Goal**: Identify friction points in the Strategy Builder and Marketplace.
- **User Interviews**: Conduct 5x 30-minute interviews with the most active beta users.
- **Bug Triage**: Begin addressing P2 UI bugs (e.g. alignment, confusing error messages).
- **Feature Prioritization**: Aggregate feedback from interviews to shape Sprint 4 (Marketplace V2 vs Engine V2).

## Week 3: Performance & Optimization
- **Goal**: Tune the infrastructure to handle the next cohort (User 101 - 500).
- **Performance Review**: Optimize slow SQL queries (e.g., adding compound indexes to `library_strategies`).
- **User Interviews**: 5x 30-minute interviews with users who churned or didn't build a strategy. Why did they stop?

## Week 4: Public Beta Readiness
- **Goal**: Finalize requirements for Public Launch.
- **Bug Triage**: Achieve 0 known P0/P1 bugs.
- **Feature Prioritization**: Finalize the Public Beta Roadmap.
- **Public Beta Readiness Criteria**:
  - `>99.9%` Uptime sustained for 14 days.
  - Server costs are bounded and predictable per DAU.
  - No data corruption or safety incidents in the past 21 days.
  - Support ticket volume is manageable (`< 0.1` tickets per DAU).
