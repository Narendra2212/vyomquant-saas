# ADR-0004: Strategy Library Design

## Status
Accepted

## Context
As the platform scaled, the necessity to store, version, and share trading strategies created a requirement for a centralized Marketplace/Library system. The initial design stored strategies directly coupled to the user's active session, meaning they couldn't be efficiently shared or cloned by other users without duplicating records entirely in the active databases.

## Decision
We implemented a separated Architecture for the Strategy Library. A strategy in the library exists as a decoupled `Blueprint`. When a user wants to run a strategy, the `Blueprint` is cloned into a `DAGConfig` tied strictly to their tenant workspace. 

## Consequences
- **Positive**: We can support a public Strategy Marketplace where creators can publish immutable Blueprints, allowing safe public consumption.
- **Positive**: Modifications to an active user strategy do not corrupt the original marketplace Blueprint.
- **Negative**: Increased storage footprint as Blueprints must be explicitly cloned into runtime configurations rather than executing by reference.
