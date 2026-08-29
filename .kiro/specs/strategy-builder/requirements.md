# Requirements Document

## Introduction

The Strategy Builder is the visual authoring surface where a user composes a trading strategy
from typed blocks, validates it, saves it as an immutable version, trains any ML/DL components
it declares, and hands that version to a separate consumer (Backtester or Live Deployment) for
execution. The strategy definition itself carries no exchange identity: exchange, account
credentials, risk profile and execution configuration are bound at deployment time.

These requirements are derived from the approved design document
(`.kiro/specs/strategy-builder/design.md`), which established that this is a
**consolidation effort, not a greenfield build**. Roughly 400 KB of production DAG,
execution, ML, market-data and validation code already exists; the dominant problem is the
absence of a single canonical DAG/block model, which lets the frontend, two separate backend
compilers and the runtime disagree about what a strategy *is*.

Six defects recorded in the design have direct observable user impact and are therefore
expressed as requirements rather than as implementation notes:

| Defect | Observable impact |
|---|---|
| SB-01 | Two competing compilers, so a graph can be accepted on one path and rejected on another |
| SB-02 | Cloning a strategy silently discards its identity hash and compiled plan |
| SB-03 | The Feature Engineering palette section is permanently empty |
| SB-04 | Backend-runnable blocks (`wma`, `hma`, `catboost`, `autoencoder`) cannot be selected |
| SB-05 | Three frontend graph formats; two silently discard DATA/MATH/FEATURE nodes, all three drop port identity |
| SB-06 | Exchange and market identity are hardcoded into saved strategies, with `"binance"` / `"BTC/USDT"` / `"15m"` fallbacks firing in normal operation |

**Scope boundary.** This document specifies the Strategy Builder only. Backtester execution
and Marketplace listing are out of scope and are gated behind the design's 42 acceptance
criteria. Existing authentication, authorization, row-level security, tenant isolation,
financial invariants, execution safeguards and risk controls are preserved unchanged; no
requirement in this document weakens any of them.

Traceability from each requirement to the design's acceptance criteria and correctness
properties is recorded in the **Traceability** section at the end of this document.

---

## Glossary

- **Strategy_Builder**: The client-side authoring surface — canvas, palette, inspector and status strip.
- **Strategy_Builder_API**: The backend HTTP surface serving the Strategy_Builder (registry, validate, versions, training, models, deploy, data quality).
- **Block_Registry**: The backend-authoritative catalogue of block descriptors, published to clients as one versioned response. The single source of truth for what a block is.
- **Block_Descriptor**: One entry in the Block_Registry: block identifier, category, input and output ports, parameter specifications, permitted predecessor and successor categories, execution semantics, warmup function, runtime reference, serialization mode, leakage classification and capability flags.
- **Parameter_Specification**: The declarative description of one block parameter: key, label, control type, required flag, default, minimum, maximum, step, option set, unit, example, help text, dependencies and warmup effect.
- **Canonical_Graph**: The single schema shared by serializer, API, validator, compiler, runtime, version consumers and database. Comprises a graph envelope, node specifications and port-addressed edge specifications.
- **Graph_Serializer**: The single client-side component converting canvas state to a Canonical_Graph and back.
- **Graph_Validator**: The backend rule engine that evaluates a Canonical_Graph against the Block_Registry and returns a Validation_Report.
- **Strategy_Compiler**: The single backend component that validates a Canonical_Graph and emits a Compiled_Plan.
- **Compiled_Plan**: The execution artifact emitted by the Strategy_Compiler: identity hash, execution order, execution levels, node index, resolved inbound edges, category node lists, feature pipeline order, warmup bar count, required models and dependency map.
- **Identity_Hash**: The `dag_hash` value identifying a strategy's meaning, computed from schema version, node set, block identifiers, categories, parameters and port wiring.
- **Validation_Report**: The structured result of validation: validity verdict, error collection, warning collection and a summary. Each entry carries a machine-readable code, a severity, a target and a corrective hint.
- **Port_Type**: The type vocabulary governing connection legality: `OHLCV_FRAME`, `PRICE_SERIES`, `SCALAR_SERIES`, `BOOLEAN_SERIES`, `FEATURE_MATRIX`, `PREDICTION`, `SIGNAL`, `TRADE_INTENT`, `SCALAR`.
- **Block_Category**: `DATA`, `INDICATOR`, `MATH`, `LOGIC`, `FEATURE_ENGINEERING`, `ML_DL`, `ACTION`.
- **Terminal_Block**: A block with no output ports and no permitted successor categories. ACTION blocks are terminal.
- **Variadic_Port**: An input port accepting between two and N connections.
- **Upstream_Closure**: The set of all nodes from which a given node is reachable by following edges backwards.
- **Warmup_Bars**: The minimum number of leading bars that must be discarded before a node on an action path produces a trustworthy value. Warmups compose along a path.
- **Asset_Discovery_Service**: The backend service publishing the discoverable tradeable market universe with search, filtering and pagination.
- **Market_Data_Pipeline**: The validated market-data path: adapters, structural and integrity validation, gap handling and delivery to the DAG_Runtime.
- **Feed_State**: The reported liveness of market data: `LIVE`, `DELAYED`, `STALE`, `DISCONNECTED` or `INSUFFICIENT_DATA`.
- **Feature_Matrix**: An aligned two-dimensional feature output carrying a strictly increasing timestamp index, unique stable column names, a warmup offset and per-column provenance.
- **Leakage_Validator**: The rule set rejecting forward-looking information on any path into a model node.
- **ML_Training_Policy**: The backend component resolving and enforcing training admission gates and resource caps.
- **Training_Service**: The backend component creating, reporting and cancelling training jobs.
- **Training_Worker**: The out-of-request process that executes a training job.
- **Model_Version**: A recorded trained artifact bound to one immutable strategy version and one model node, carrying artifact reference, checksum, size, serialization format, feature schema, hyperparameters and metrics.
- **Deployment_Service**: The backend component binding an immutable version to an exchange account, risk configuration and execution configuration, and governing deployment lifecycle.
- **Deployment_Binding**: The record of version, exchange account reference, risk configuration reference, execution configuration and mode. Holds no credential material.
- **DAG_Runtime**: The execution stack consuming a Compiled_Plan: DAG engine, parallel engine and event loop.
- **Trade_Intent**: A proposed order emitted by an ACTION node, subject to the existing execution guard and risk controls.
- **Persistence_Layer**: The relational store holding strategies, versions, training jobs, model versions, deployments and registry snapshots, with row-level ownership policies.
- **Credential_Vault**: The existing store of exchange credentials, addressed by exchange account identifier.
- **Version_Consumer**: A component that loads an immutable strategy version in order to execute it — the DAG_Runtime under a live or paper deployment, or the Backtester. The Backtester itself is outside this specification's scope; it appears here only as a consumer of the shared artifact.

---

## Requirements

### Requirement 1: Canonical graph model and lossless serialization

**User Story:** As a strategy author, I want the strategy I draw on the canvas to be exactly
the strategy that is saved, validated and executed, so that no block or connection I created
is silently discarded (SB-05).

#### Acceptance Criteria

1. THE Graph_Serializer SHALL emit every canvas node into the Canonical_Graph for every Block_Category, including DATA, MATH and FEATURE_ENGINEERING nodes.
2. THE Graph_Serializer SHALL emit each connection with a source node identifier, a source port name, a target node identifier and a target port name.
3. THE Graph_Serializer SHALL copy each node's block identifier and category from the Block_Descriptor recorded for that node.
4. WHEN a Canonical_Graph is converted to canvas form and converted back to canonical form, THE Graph_Serializer SHALL produce a graph equal to the original graph in schema version, node set, node parameters, port lists and edge set.
5. WHEN a Canonical_Graph is serialized to its wire format and parsed back, THE Strategy_Builder_API SHALL produce a graph equal to the original graph.
6. THE Strategy_Builder, Strategy_Builder_API, Graph_Validator, Strategy_Compiler, DAG_Runtime and Persistence_Layer SHALL each read the Canonical_Graph using the field names defined by the canonical schema.
7. THE Strategy_Builder_API SHALL mint each node identifier once at node creation and SHALL retain that identifier across edits, edge references, execution order, model bindings, traces and audit records.
8. THE Strategy_Compiler SHALL exclude node presentation fields, comprising canvas position, display label and collapsed state, from the Identity_Hash computation.
9. WHEN a stored graph declares schema version 1, THE Persistence_Layer SHALL migrate that graph to schema version 2 at read time and SHALL leave the stored record unchanged until the author saves a new version.
10. IF a schema version 1 node references a block identifier absent from the Block_Registry, THEN THE Persistence_Layer SHALL load the version with validation state `INVALID` and SHALL report an `UNRESOLVED_BLOCK` error naming that node.
11. IF a stored graph declares a schema version other than 1 or 2, THEN THE Persistence_Layer SHALL reject the graph with an `UNSUPPORTED_SCHEMA_VERSION` error naming the declared version.

---

### Requirement 2: Stable strategy identity

**User Story:** As a strategy author, I want a strategy's identity to depend only on its
meaning, so that moving blocks on the canvas does not create a different strategy and changing
a parameter does.

#### Acceptance Criteria

1. WHEN two Canonical_Graphs differ only in node presentation fields, THE Strategy_Compiler SHALL compute the same Identity_Hash for both graphs.
2. WHEN two Canonical_Graphs differ in node set, block identifier, node category, parameter value, port wiring or schema version, THE Strategy_Compiler SHALL compute different Identity_Hash values.
3. WHEN one Canonical_Graph is compiled repeatedly within one process and across separate processes, THE Strategy_Compiler SHALL return an identical Identity_Hash and an identical execution order for every compilation.
4. THE Strategy_Compiler SHALL publish the Identity_Hash on the Compiled_Plan as a readable data field.
5. THE Strategy_Compiler SHALL publish exactly one serialization path for the Compiled_Plan.

---

### Requirement 3: Single compilation authority

**User Story:** As a strategy author, I want one answer to whether my strategy is executable,
so that a strategy accepted when I save it is not rejected when it is cloned, deployed or run
(SB-01).

#### Acceptance Criteria

1. THE Strategy_Builder_API SHALL compile and validate every submitted Canonical_Graph through one Strategy_Compiler entry point.
2. WHEN one Canonical_Graph is submitted through the validate path, the save path, the clone path, the deployment path or the backtest load path, THE Strategy_Compiler SHALL return the same validity verdict and the same error code set for every path.
3. WHEN a Canonical_Graph passes validation, THE Strategy_Compiler SHALL return an execution order containing every node of the graph exactly once and placing the source node of every edge before that edge's target node.
4. WHEN a Canonical_Graph passes validation, THE Strategy_Compiler SHALL return execution levels in which every node's predecessors appear in an earlier level.
5. IF a Canonical_Graph fails any validation stage, THEN THE Strategy_Compiler SHALL raise a validation error carrying the complete Validation_Report and SHALL return no Compiled_Plan.
6. IF a Canonical_Graph fails validation, THEN THE Strategy_Builder_API SHALL persist no strategy record, version record, Compiled_Plan, Identity_Hash or training job for that request.
7. IF an unexpected error occurs on a validation, compilation or persistence path, THEN THE Strategy_Builder_API SHALL return a machine-readable error code together with a human-readable message, and SHALL persist no partial record.
8. THE Strategy_Compiler SHALL compute the Warmup_Bars for an action path as the sum of each node's own warmup along that path.

---

### Requirement 4: Backend-authoritative block palette covering all seven categories

**User Story:** As a strategy author, I want the palette to offer every block the platform can
actually run, so that I can build any strategy the engine already supports (SB-03, SB-04).

#### Acceptance Criteria

1. THE Block_Registry SHALL serve Block_Descriptors for all seven Block_Categories from one backend endpoint.
2. THE Block_Registry SHALL hold at least one Block_Descriptor in each of the seven Block_Categories.
3. THE Block_Registry SHALL hold at least 15 FEATURE_ENGINEERING Block_Descriptors.
4. THE Block_Registry SHALL hold a Block_Descriptor for every indicator implemented in the platform indicator library, including `wma` and `hma`.
5. WHERE a model library is importable in the running environment, THE Block_Registry SHALL publish that library's model blocks, including `catboost` and `autoencoder`.
6. WHERE a model library is absent from the running environment, THE Block_Registry SHALL omit that library's model blocks from the served response.
7. THE Block_Registry SHALL resolve every published Block_Descriptor's runtime reference to a callable during startup.
8. IF a published Block_Descriptor's runtime reference resolves to no callable, THEN THE Block_Registry SHALL fail startup and SHALL name the offending Block_Descriptor.
9. IF any Block_Category holds zero Block_Descriptors, THEN THE Block_Registry SHALL fail startup and SHALL name that Block_Category.
10. THE Block_Registry SHALL generate ACTION Block_Descriptors from the order types the execution layer supports.
11. THE Strategy_Builder SHALL render the palette from the served Block_Registry response.
12. IF the Block_Registry request fails, THEN THE Strategy_Builder SHALL display a retryable error state for the palette and SHALL display zero block entries.
13. WHEN an author enters a palette search query, THE Strategy_Builder SHALL match that query against block display name, block identifier, description and category across all seven Block_Categories.
14. THE Strategy_Builder SHALL display each palette entry's input Port_Types and output Port_Types.
15. THE Block_Registry SHALL serve the response with a registry version value and an entity tag header, and SHALL keep the gzipped response at 250 KB or smaller.
16. THE Persistence_Layer SHALL retain a Block_Registry snapshot for each registry version.

---

### Requirement 5: Registry-driven block configuration

**User Story:** As a strategy author, I want every block's settings explained where I configure
them, so that I can set a block correctly without reading source code and without a hidden
default changing how my strategy trades.

#### Acceptance Criteria

1. THE Strategy_Builder SHALL generate each block's parameter form from the Parameter_Specifications the Block_Registry publishes for that block.
2. THE Strategy_Builder SHALL display for each parameter its label, control type, unit, permitted range or option set, example value and help text.
3. THE Strategy_Builder SHALL display every required parameter of the selected block without requiring the author to expand a collapsed section.
4. WHERE a parameter changes trading behaviour, comprising traded symbol, timeframe, order quantity, quantity type, order price, trigger price and model confidence threshold, THE Block_Registry SHALL publish that parameter as required with no default value.
5. WHERE a Parameter_Specification declares a default value, THE Strategy_Builder SHALL display that value together with an indication that the value is the default.
6. THE Strategy_Builder SHALL constrain each parameter control to the minimum, maximum, step and option set declared in its Parameter_Specification.
7. THE Graph_Validator SHALL evaluate each parameter value against the same Parameter_Specification the Strategy_Builder used to render that parameter's control.
8. IF a parameter value violates its required flag, control type, minimum, maximum, option set or declared cross-field dependency, THEN THE Graph_Validator SHALL report an error naming the node, the field, the expected constraint and the received value.
9. THE Block_Registry SHALL publish each indicator's parameters with the ranges and warmup behaviour specific to that indicator.
10. THE Block_Registry SHALL publish one output port per distinct output of a multi-output indicator.

---

### Requirement 6: Connection legality with immediate, actionable rejection

**User Story:** As a strategy author, I want an illegal connection refused as I draw it with a
reason I can act on, so that I learn the type system by using it instead of by decoding a
failure at save time.

#### Acceptance Criteria

1. WHILE an author drags a connection, THE Strategy_Builder SHALL indicate which target ports accept the dragged source port before the author releases that connection.
2. IF a proposed connection's source Port_Type is incompatible with its target Port_Type, THEN THE Strategy_Builder SHALL refuse the connection and SHALL state the source Port_Type, the target Port_Type and a corrective action.
3. IF a proposed connection's target Block_Category is absent from the source block's permitted successor categories, THEN THE Strategy_Builder SHALL refuse the connection and SHALL name both Block_Categories.
4. IF a proposed connection originates from a Terminal_Block, THEN THE Strategy_Builder SHALL refuse the connection and SHALL state that the source block is terminal.
5. IF a proposed connection targets a non-variadic input port that already holds a connection, THEN THE Strategy_Builder SHALL refuse the connection and SHALL state that the port accepts one connection.
6. IF a proposed connection joins a node to itself, THEN THE Strategy_Builder SHALL refuse the connection and SHALL name the node.
7. IF a proposed connection references a source port or target port absent from the Block_Descriptor, THEN THE Strategy_Builder SHALL refuse the connection and SHALL name the unknown port.
8. IF a proposed connection would create a cycle, THEN THE Strategy_Builder SHALL refuse the connection and SHALL name the nodes forming that cycle together with the refused connection.
9. THE Block_Registry SHALL publish the Port_Type compatibility rules the Graph_Validator applies.
10. WHEN a connection is legal under the rules the Strategy_Builder applies, THE Graph_Validator SHALL accept that connection.
11. WHEN a connection is illegal under the rules the Strategy_Builder applies, THE Graph_Validator SHALL reject that connection.
12. THE Graph_Validator SHALL re-evaluate every connection legality rule on every request that validates, saves, clones, compiles or deploys a Canonical_Graph.
13. THE Graph_Validator SHALL recompute node port lists, edge Port_Types, validation state, Identity_Hash and execution order from the Block_Registry for every submitted graph.
14. IF a submitted Canonical_Graph contains a cycle, THEN THE Graph_Validator SHALL reject that graph at connection time, edit time, save time, validate time, compile time, deployment time and plan-load time.
15. WHEN a cycle is reported, THE Graph_Validator SHALL name the ordered node sequence forming that cycle.

---

### Requirement 7: Branching and merging strategy topologies

**User Story:** As a strategy author, I want to fan one signal into several branches and merge
several branches into one decision, so that I can express a real strategy instead of a single
linear chain.

#### Acceptance Criteria

1. THE Strategy_Compiler SHALL compile Canonical_Graphs in which one node output port feeds several downstream nodes.
2. THE Strategy_Compiler SHALL compile Canonical_Graphs in which several node output ports converge on one downstream node.
3. THE Strategy_Compiler SHALL compile Canonical_Graphs holding more than one ACTION node.
4. THE Graph_Validator SHALL accept any node adjacency permitted by the Port_Type compatibility rules and the Block_Descriptor successor categories, including a DATA node feeding a FEATURE_ENGINEERING node and an ML_DL node feeding a MATH node or a LOGIC node.
5. WHEN a Canonical_Graph holds at least one DATA node and at least one ACTION node reachable from a DATA node, THE Graph_Validator SHALL record the execution path stage as satisfied.
6. IF a Canonical_Graph holds zero DATA nodes or zero ACTION nodes, THEN THE Graph_Validator SHALL report an error naming the missing Block_Category.
7. IF a node has no path to an ACTION node and no path from a DATA node, THEN THE Graph_Validator SHALL report that node as orphaned or unreachable.
8. IF a required input port of any node holds no connection, THEN THE Graph_Validator SHALL report a `REQUIRED_INPUT_MISSING` error naming the node and the port.
9. IF an ACTION node's signal input is fed by a node that is neither a LOGIC node nor a producer of trade-signal type, THEN THE Graph_Validator SHALL report an `ACTION_INPUT_PROVENANCE` error naming the node.
10. IF one ACTION node's Upstream_Closure reaches two different traded symbols without a portfolio allocation node, THEN THE Graph_Validator SHALL report an error naming both symbols.

---

### Requirement 8: Single-pass, targeted validation reporting

**User Story:** As a strategy author, I want every problem with my strategy reported at once
and pointed at the exact block, connection or field, so that fixing my strategy is a task
rather than a guessing game.

#### Acceptance Criteria

1. WHEN a Canonical_Graph is submitted for validation, THE Graph_Validator SHALL evaluate all eleven validation stages and SHALL return every error and warning found in one Validation_Report.
2. THE Graph_Validator SHALL attach to each Validation_Report entry a machine-readable code and a severity.
3. THE Graph_Validator SHALL attach to each Validation_Report entry the node identifier, edge identifier or field name the entry concerns.
4. THE Graph_Validator SHALL attach to each Validation_Report entry a plain-language message and a corrective hint stating an action the author can take.
5. THE Graph_Validator SHALL report a Validation_Report holding at least one error as invalid, and a Validation_Report holding only warnings as valid.
6. WHEN the computed Warmup_Bars exceeds the bars available for the configured data range, THE Graph_Validator SHALL emit a warning stating the required bar count and the available bar count.
7. WHEN validation completes, THE Graph_Validator SHALL report the node count, the edge count and the computed Warmup_Bars.
8. THE Strategy_Builder SHALL mark each node and each connection named in the Validation_Report with the severity reported for that node or connection.
9. THE Strategy_Builder SHALL display the corrective hint text the Graph_Validator returned.
10. WHEN an author edits the Canonical_Graph, THE Strategy_Builder SHALL mark the graph unvalidated and SHALL request backend validation within 400 milliseconds of the author's last edit.
11. THE Strategy_Builder SHALL display a validation summary, a Feed_State, a save state and a training state in the builder status strip.

---

### Requirement 9: Immutable strategy versions

**User Story:** As a strategy author, I want a saved strategy version to stay exactly as I
saved it, so that a live deployment cannot change behaviour because I edited the strategy
afterwards.

#### Acceptance Criteria

1. WHEN an author saves a strategy, THE Strategy_Builder_API SHALL validate the Canonical_Graph, compile it, and persist the graph, the Compiled_Plan, the Identity_Hash, the schema version, the compiler version, the registry version, the Warmup_Bars, the validation state and the Validation_Report as one version record.
2. THE Persistence_Layer SHALL reject any update to a read-only version's graph, Compiled_Plan, Identity_Hash or schema version.
3. THE Persistence_Layer SHALL reject any version record whose validation state is `VALID` and whose Identity_Hash or Compiled_Plan is absent.
4. WHEN an author edits a version whose lifecycle state is `DEPLOYED`, `RUNNING` or `PAUSED`, THE Strategy_Builder_API SHALL create a new draft from that version and SHALL leave the existing version record unchanged.
5. THE Deployment_Service SHALL reference a specific version identifier for every deployment.
6. THE Strategy_Builder_API SHALL permit only the lifecycle transitions defined for the strategy lifecycle state machine.
7. IF a requested lifecycle transition is absent from the defined state machine, THEN THE Strategy_Builder_API SHALL reject that request with a conflict response naming the current state.
8. THE Strategy_Builder_API SHALL record actor, timestamp and reason in the audit trail for every version creation, lifecycle transition, deployment action and training job creation or cancellation.
9. WHILE a version's lifecycle state is `DEPLOYED`, `RUNNING` or `PAUSED`, THE Strategy_Builder SHALL present that version's canvas as read-only.

---

### Requirement 10: Clone preserves compiled state

**User Story:** As a strategy author, I want a cloned strategy to arrive with its compiled
state intact or to tell me plainly that it did not, so that I never deploy a clone that looks
saved but carries nothing (SB-02).

#### Acceptance Criteria

1. WHEN an author clones a strategy whose Canonical_Graph compiles, THE Strategy_Builder_API SHALL persist that clone with a present Identity_Hash and a present Compiled_Plan.
2. IF a cloned Canonical_Graph fails to compile, THEN THE Strategy_Builder_API SHALL persist that clone with validation state `INVALID`, SHALL attach the Validation_Report, and SHALL return the failure in the response warning collection.
3. THE Strategy_Builder_API SHALL report clone-time compilation failures to the caller through a typed error path.
4. IF a version's validation state is other than `VALID`, or its Identity_Hash is absent, or its Compiled_Plan is absent, THEN THE Deployment_Service SHALL refuse to deploy that version and SHALL name the missing prerequisite.
5. THE Persistence_Layer SHALL reject a clone record that combines validation state `VALID` with an absent Identity_Hash or an absent Compiled_Plan.

---

### Requirement 11: Live discoverable asset universe

**User Story:** As a strategy author, I want to search the markets the platform can actually
trade, so that my choice of asset is not limited to a short hardcoded list.

#### Acceptance Criteria

1. THE Asset_Discovery_Service SHALL return the tradeable market universe assembled from the exchange adapters the platform supports.
2. THE Asset_Discovery_Service SHALL support filtering by free-text search, base currency, quote currency, market type and active status.
3. THE Asset_Discovery_Service SHALL return paginated results with a total count and a continuation cursor.
4. THE Asset_Discovery_Service SHALL return for each asset its canonical symbol, base currency, quote currency, market type, active flag, price precision, amount precision, minimum notional and minimum amount.
5. THE Asset_Discovery_Service SHALL refresh the market universe on a schedule outside the request path and SHALL serve each request from the cached universe.
6. IF the cached market universe is empty and a refresh attempt fails, THEN THE Asset_Discovery_Service SHALL return an `ASSET_UNIVERSE_UNAVAILABLE` error.
7. THE Strategy_Builder SHALL populate the asset selector from the Asset_Discovery_Service response.
8. THE Block_Registry SHALL publish the timeframe set the platform data pipeline supports, and THE Strategy_Builder SHALL populate the timeframe selector from that set.

---

### Requirement 12: Exchange-agnostic strategy definition

**User Story:** As a strategy author, I want the strategy I save to describe only trading
logic, so that the same strategy can be deployed to a different exchange account without being
rewritten, and so that a saved strategy never trades a market I did not choose (SB-06).

#### Acceptance Criteria

1. THE Strategy_Builder_API SHALL persist Canonical_Graphs, Compiled_Plans and training configurations holding no exchange identifier, API key, secret or passphrase.
2. THE Block_Registry SHALL publish the DATA Block_Descriptor with no exchange parameter.
3. THE Block_Registry SHALL publish the DATA block's symbol parameter and timeframe parameter as required with no default value.
4. IF a Canonical_Graph's DATA node holds no symbol value or no timeframe value, THEN THE Strategy_Builder_API SHALL reject the save request with a validation error naming that node and the missing field.
5. THE Strategy_Compiler SHALL resolve an ACTION node's traded symbol from the DATA node reached through that ACTION node's Upstream_Closure.
6. THE Training_Service SHALL resolve its data source from the training configuration recorded with the training job.
7. THE Strategy_Compiler SHALL exclude exchange availability metadata from the Identity_Hash computation.
8. THE Block_Registry SHALL publish ACTION Block_Descriptors with no traded-asset parameter.

---

### Requirement 13: Deployment binding and lifecycle control

**User Story:** As a strategy author, I want to choose the exchange account, risk profile and
execution settings when I deploy, so that one validated strategy can run under different
accounts and risk limits without being edited.

#### Acceptance Criteria

1. WHEN an author deploys a version, THE Deployment_Service SHALL record the version identifier, the exchange account identifier, the risk configuration identifier, the execution configuration and the mode as one Deployment_Binding.
2. THE Deployment_Service SHALL confirm that the version, the exchange account and the risk configuration each belong to the requesting user before creating the Deployment_Binding.
3. IF a version's lifecycle state is other than `READY`, THEN THE Deployment_Service SHALL refuse the deployment with a conflict response naming the current state and the outstanding prerequisite.
4. IF the target exchange account lists no market for a symbol the version trades, THEN THE Deployment_Service SHALL refuse the deployment and SHALL name that symbol and that account.
5. IF the target exchange account supports no market data at the version's timeframe, THEN THE Deployment_Service SHALL refuse the deployment and SHALL name that timeframe and that account.
6. WHERE a Deployment_Binding mode is `live`, THE Deployment_Service SHALL require an exchange account identifier.
7. WHERE a Deployment_Binding mode is `live`, THE Deployment_Service SHALL reject a data configuration that fills market data gaps with synthetic candles.
8. THE Deployment_Service SHALL support the deploy, pause, resume and stop transitions for a Deployment_Binding.
9. THE Deployment_Service SHALL resolve exchange credentials from the Credential_Vault by exchange account identifier inside the execution process.
10. THE Deployment_Service SHALL report each Deployment_Binding's state as one of `DEPLOYING`, `RUNNING`, `PAUSED`, `STOPPED` or `FAILED`.

---

### Requirement 14: ML and DL training admission gate

**User Story:** As a strategy author, I want to be told before training starts whether my data
is sufficient and by exactly how much it falls short, so that I can fix the gap instead of
waiting for a failure.

#### Acceptance Criteria

1. THE Block_Registry SHALL publish for each model block its minimum feature column count, minimum training row count, sequence length where the model consumes sequences, hyperparameter specifications, recommended epoch count, maximum safe epoch count and serialization format.
2. WHEN a training request is evaluated, THE ML_Training_Policy SHALL measure usable feature columns and usable rows from the fetched dataset after removing Warmup_Bars and the label horizon.
3. IF usable feature columns are fewer than 5, or fewer than the model block's declared minimum feature column count, THEN THE ML_Training_Policy SHALL block training, SHALL create no training job, and SHALL return the required and available feature column counts.
4. IF usable rows are fewer than the row count required after reserving the validation fraction, the test fraction and the embargo bars, THEN THE ML_Training_Policy SHALL block training, SHALL create no training job, and SHALL return the required and available row counts.
5. WHERE a model block belongs to the sequence family, THE ML_Training_Policy SHALL additionally require that the training split holds at least that model's sequence length plus its minimum training row count.
6. IF the training split for a sequence model holds fewer rows than the sequence length plus the minimum training row count, THEN THE ML_Training_Policy SHALL block training and SHALL return the sequence length together with the available training row count.
7. IF the measured market data quality level for the training dataset is `POOR` or `UNUSABLE`, THEN THE Training_Service SHALL block training and SHALL return the data quality report.
8. IF the produced feature set fails the feature schema check for the declared model block, THEN THE Training_Service SHALL block training and SHALL return the failing feature issues.
9. THE Strategy_Builder SHALL display each blocking training message with its required quantity and its available quantity.
10. WHEN a Canonical_Graph declares no model node, THE Strategy_Builder_API SHALL set the saved version's lifecycle state to `READY` and SHALL report that training is not required.

---

### Requirement 15: Asynchronous training with truthful progress

**User Story:** As a strategy author, I want training to run in the background with progress I
can trust and a way to stop it, so that I can keep working and know where the run actually
stands.

#### Acceptance Criteria

1. WHEN training is admitted, THE Training_Service SHALL create one queued training job per model node and SHALL return the job identifier without waiting for training to finish.
2. THE Training_Service SHALL report each job's status as one of `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED` or `CANCELLED`.
3. THE Training_Service SHALL report for each job the dataset row count, the usable row count, the feature column count, the feature names, the split sizes, the model block identifier, the total epoch count, the current epoch, the training loss, the validation loss, the progress fraction, the cancellable flag and the failure reason.
4. THE Training_Service SHALL derive each job's reported progress fraction from completed epochs.
5. WHILE fewer than three epochs have completed, THE Training_Service SHALL report the estimated remaining time as absent.
6. WHILE measured epoch durations have a coefficient of variation of 0.35 or greater, THE Training_Service SHALL report the estimated remaining time as absent.
7. WHEN an author requests cancellation, THE Training_Worker SHALL stop at the next epoch boundary, SHALL set the job status to `CANCELLED` and SHALL bind no Model_Version.
8. IF a job's elapsed duration exceeds its permitted duration, THEN THE Training_Worker SHALL stop that job, SHALL set the status to `FAILED` and SHALL record `MAX_DURATION_EXCEEDED` as the failure reason.
9. IF a Training_Worker heartbeat becomes stale beyond the configured threshold, THEN THE Training_Service SHALL set that job's status to `FAILED` with reason `WORKER_LOST` and SHALL return the version to its pre-training lifecycle state.
10. IF a training job fails, THEN THE Training_Service SHALL record a classified failure reason and SHALL leave the version undeployable.
11. THE Training_Service SHALL publish job status changes and per-epoch progress over the existing multiplexed realtime connection.
12. WHEN a save request queues training, THE Strategy_Builder_API SHALL state in the save response that training was queued and SHALL include the job identifier.
13. THE Training_Service SHALL hold at most one job in status `QUEUED` or `RUNNING` for a given version and model node.
14. THE Training_Service SHALL record the training configuration and a dataset fingerprint with each job.

---

### Requirement 16: Backend-enforced training resource caps

**User Story:** As a platform operator, I want training resource limits enforced on the server,
so that a modified client cannot consume shared training capacity beyond its entitlement.

#### Acceptance Criteria

1. THE ML_Training_Policy SHALL resolve each user's effective caps for epochs, rows, feature columns, per-user concurrent jobs, global concurrent jobs, duration, memory and model artifact size.
2. THE ML_Training_Policy SHALL set the effective epoch cap to the lesser of the model block's maximum safe epoch count and the user's entitlement epoch cap.
3. IF a training request exceeds any effective cap, THEN THE ML_Training_Policy SHALL reject that request and SHALL return the requested value together with the permitted value for the exceeded cap.
4. THE Training_Worker SHALL re-evaluate the effective caps before the first epoch of every job.
5. WHILE the global concurrent job limit is reached, THE ML_Training_Policy SHALL hold newly admitted jobs in the queue and SHALL report a queue position estimate.
6. THE Training_Worker SHALL execute each job under process isolation, memory monitoring and deterministic seeding.
7. THE ML_Training_Policy SHALL apply the existing entitlement and quota controls in addition to the caps in this requirement.

---

### Requirement 17: Model versioning and per-node binding

**User Story:** As a strategy author, I want each trained model tied to the exact strategy
version and block it was trained for, so that retraining never changes what a running
deployment is using.

#### Acceptance Criteria

1. WHEN a training job completes, THE Training_Service SHALL record a Model_Version holding the artifact reference, artifact checksum, artifact size, serialization format, feature schema, hyperparameters and the training, validation and test metrics.
2. THE Training_Service SHALL bind each recorded Model_Version to one immutable strategy version and one model node.
3. WHEN a model node is retrained, THE Training_Service SHALL create a new Model_Version record and SHALL leave in place the artifact referenced by any running deployment.
4. THE Persistence_Layer SHALL hold at most one active Model_Version per strategy version and model node.
5. WHEN every model node of a version is bound to an active Model_Version, THE Strategy_Builder_API SHALL set that version's lifecycle state to `READY`.
6. IF a model artifact's computed checksum differs from its recorded checksum, THEN THE DAG_Runtime SHALL mark that model node as awaiting a model and THE Deployment_Service SHALL hold the deployment out of the running state.
7. IF a Model_Version's feature schema differs from the feature output of the version's current Canonical_Graph, THEN THE Deployment_Service SHALL refuse the deployment and SHALL report the expected and actual feature columns.
8. THE Strategy_Builder_API SHALL serve model artifacts through an ownership-checked endpoint and SHALL sanitize artifact file names before use.
9. THE Strategy_Builder_API SHALL store model artifacts in object storage addressed by artifact reference and checksum.

---

### Requirement 18: Data leakage protection

**User Story:** As a strategy author, I want the platform to stop me building a model that
reads the future, so that a strategy that looks profitable in testing is not built on
information it will never have when trading.

#### Acceptance Criteria

1. THE Block_Registry SHALL publish a leakage risk classification for every FEATURE_ENGINEERING Block_Descriptor.
2. IF a shift block on a path into a model node shifts by a negative bar count, THEN THE Leakage_Validator SHALL report a `LOOKAHEAD_SHIFT` error naming that node.
3. IF a block whose leakage classification is review-required lies on a path into a model node, THEN THE Leakage_Validator SHALL report a `LEAKY_FEATURE_INTO_MODEL` error naming that node.
4. IF a normalization block or a z-score block computes its statistics over the whole series, THEN THE Leakage_Validator SHALL report a `GLOBAL_STATISTIC_LEAK` error naming that node.
5. THE Training_Service SHALL split each dataset into training, validation and test ranges that are chronologically ordered, mutually disjoint, and separated by at least the configured embargo bar count.
6. THE Training_Service SHALL set the embargo bar count to at least the longest feature lookback plus the label horizon.
7. THE Training_Service SHALL construct supervised datasets in which each row's features use only bars at or before that row's timestamp.
8. THE Training_Service SHALL construct supervised datasets in which each row's label uses only bars after that row's timestamp.
9. THE Training_Service SHALL produce a supervised dataset whose feature row count equals its label count.
10. THE Training_Service SHALL discard the trailing rows for which the label horizon extends beyond the available data.
11. THE Training_Service SHALL select training rows by chronological range.
12. THE Feature_Matrix SHALL carry a strictly increasing timestamp index, unique column names, a warmup offset and the producing node identifier for each column.
13. WHEN feature outputs are combined, THE Strategy_Compiler SHALL align them on their timestamp index.

---

### Requirement 19: Validated market data and truthful feed state

**User Story:** As a strategy author, I want the builder to tell me the real state of my market
data, so that I never read a stale chart as live and never train on silently repaired data.

#### Acceptance Criteria

1. THE Market_Data_Pipeline SHALL validate every candle batch for required columns, high and low relationships against open and close, positive prices, non-negative volume, duplicate timestamps and timestamp ordering before the DAG_Runtime receives that batch.
2. THE Market_Data_Pipeline SHALL supply indicator computation with closed bars.
3. WHEN a live event arrives carrying a timestamp older than the last closed bar, THE Market_Data_Pipeline SHALL discard that event and SHALL increment the late-event count.
4. WHEN a candle arrives carrying a timestamp already received, THE Market_Data_Pipeline SHALL retain the first candle and SHALL increment the duplicate count.
5. WHEN a candle fails an integrity check, THE Market_Data_Pipeline SHALL drop that candle and SHALL record the quality issue.
6. THE Strategy_Builder SHALL report Feed_State as exactly one of `LIVE`, `DELAYED`, `STALE`, `DISCONNECTED` or `INSUFFICIENT_DATA`.
7. WHILE the realtime connection is open and the last received event is newer than 1.5 times the expected bar interval, THE Strategy_Builder SHALL report Feed_State `LIVE`.
8. WHILE the last received event is older than 1.5 times the expected bar interval, THE Strategy_Builder SHALL report Feed_State `DELAYED` and SHALL display the age of the last event together with the expected interval.
9. WHILE the last received event is older than 3 times the expected bar interval, THE Strategy_Builder SHALL report Feed_State `STALE`.
10. WHILE available bars are fewer than the compiled Warmup_Bars, THE Strategy_Builder SHALL report Feed_State `INSUFFICIENT_DATA`.
11. THE Market_Data_Pipeline SHALL admit only a market data source that delivers at least 99.99 percent bar completeness, zero timestamp monotonicity violations, zero delivered duplicates, zero delivered invalid candles and zero delivered synthetic candles.
12. WHERE more than one market data source meets the admission criteria, THE Market_Data_Pipeline SHALL select the source with the lower 99th-percentile end-to-end latency when the difference is 25 milliseconds or greater, and SHALL otherwise select the source carrying the validation layer.
13. THE Market_Data_Pipeline SHALL record the measured completeness, latency, duplication, ordering, reconnection and reliability figures together with the selection decision.
14. THE Strategy_Builder_API SHALL serve the data quality report for a version's configured data source.

---

### Requirement 20: Runtime readiness and financial safety

**User Story:** As a strategy author, I want the runtime to stay silent rather than act on
incomplete or invalid values, so that a warming indicator or an arithmetic edge case cannot
place an order.

#### Acceptance Criteria

1. WHILE any node in an ACTION node's Upstream_Closure lacks a required input, has unmet warmup or awaits a Model_Version, THE DAG_Runtime SHALL emit no Trade_Intent from that ACTION node.
2. THE DAG_Runtime SHALL validate each node output series against its declared Port_Type before making that series available to downstream nodes.
3. THE DAG_Runtime SHALL produce arithmetic results holding only finite values or not-a-number values.
4. WHEN a division by a zero-valued denominator, a square root of a negative value, a logarithm of a non-positive value or an arithmetic overflow occurs, THE DAG_Runtime SHALL write a not-a-number value for that bar and SHALL record the condition against the node.
5. IF a Trade_Intent numeric field holds a not-a-number value or an infinite value, THEN THE DAG_Runtime SHALL block that Trade_Intent, SHALL send no order and SHALL record the incident.
6. IF a Trade_Intent quantity is zero or negative, THEN THE DAG_Runtime SHALL block that Trade_Intent and SHALL send no order.
7. WHEN a cross-above or cross-below comparison encounters a not-a-number value on either input series at the current bar or the previous bar, THE DAG_Runtime SHALL report false for that bar.
8. THE DAG_Runtime SHALL evaluate every Trade_Intent through the existing execution guard, risk engine, balance checks and idempotency controls.
9. IF a global kill switch activates or a guard trip occurs, THEN THE Deployment_Service SHALL move the affected running deployments to `STOPPED` and SHALL preserve the recorded reason.
10. WHEN market data for a required bar is absent, THE DAG_Runtime SHALL hold the nodes depending on that bar in a warming state.
11. WHEN one input of a merging node is ready and another input of that node is warming, THE DAG_Runtime SHALL hold that merging node in a warming state.
12. THE Strategy_Builder SHALL display each node's runtime state, comprising warming with a bar count, ready, awaiting a model and training.

---

### Requirement 21: Authorization, tenant isolation and secret containment

**User Story:** As a platform operator, I want every Strategy Builder surface to enforce the
platform's existing access controls, so that adding the builder adds no path to another
tenant's strategies, models or credentials.

#### Acceptance Criteria

1. THE Strategy_Builder_API SHALL require an authenticated user for every registry, asset discovery, validation, compilation, version, clone, training, model, deployment and data quality endpoint.
2. THE Strategy_Builder_API SHALL filter every strategy, version, training job, Model_Version, deployment and exchange account read and write by the requesting user's identity.
3. THE Persistence_Layer SHALL enforce row-level ownership policies on strategy, version, training job and Model_Version records.
4. IF a user requests a resource owned by another user, THEN THE Strategy_Builder_API SHALL respond with a not-authorized status or a not-found status and SHALL return no resource data.
5. THE Strategy_Builder_API SHALL authorize every realtime channel subscription against the owner of the referenced resource.
6. IF a user subscribes to a realtime channel for a resource owned by another user, THEN THE Strategy_Builder_API SHALL refuse that subscription and SHALL report the refusal.
7. THE Strategy_Builder_API SHALL exclude exchange credentials from every API response, every realtime frame and every log entry.
8. THE Strategy_Builder_API SHALL apply the existing rate limits to every endpoint this specification adds.
9. THE Strategy_Builder_API SHALL keep the existing authentication, authorization, row-level security, risk, execution-guard and idempotency controls in force for every path this specification adds.
10. THE Strategy_Compiler and Graph_Validator SHALL operate without importing the execution engine, the exchange executor or the Credential_Vault.

---

### Requirement 22: Builder and Backtester separation over one shared artifact

**User Story:** As a strategy author, I want saving to be saving, and I want a backtest and a
live run to describe the same strategy, so that what I test is what trades.

#### Acceptance Criteria

1. WHEN an author saves a strategy version, THE Strategy_Builder_API SHALL create no backtest record and SHALL start no backtest job.
2. THE Strategy_Builder SHALL operate without depending on any backtest execution module.
3. THE Strategy_Builder_API SHALL serve every Version_Consumer the same stored Compiled_Plan and the same Identity_Hash for a given version.
4. WHEN two Version_Consumers evaluate one version over identical market data, THE DAG_Runtime SHALL produce the same Trade_Intent sequence for both Version_Consumers.
5. IF a stored Compiled_Plan's Identity_Hash differs from the hash recomputed from that version's Canonical_Graph, THEN THE Version_Consumer SHALL recompile the graph before execution.

---

### Requirement 23: Pushed realtime updates

**User Story:** As a strategy author, I want validation, training and market updates to arrive
as they happen over one connection, so that the builder stays responsive without polling.

#### Acceptance Criteria

1. THE Strategy_Builder SHALL maintain one realtime connection per browser session and SHALL multiplex the market, validation, training, strategy, deployment and execution channels over that connection.
2. WHEN the Strategy_Builder closes a builder view, THE Strategy_Builder SHALL unsubscribe the channels that view subscribed.
3. WHEN an authentication token is refreshed, THE Strategy_Builder SHALL reauthenticate the existing connection.
4. IF the realtime connection closes, THEN THE Strategy_Builder SHALL reconnect using exponential backoff bounded at 30 seconds with added jitter, and SHALL report Feed_State `DISCONNECTED`.
5. WHEN the realtime connection is re-established, THE Strategy_Builder SHALL resubscribe its channels and SHALL request a state snapshot.
6. WHILE the realtime connection is closed, THE Strategy_Builder SHALL poll status at an interval of 30 seconds.

---

### Requirement 24: Observability and node-level diagnostics

**User Story:** As a strategy author, I want to see why a block produced nothing, and as an
operator I want to see how the builder behaves in aggregate, so that a silent strategy is
diagnosable rather than mysterious.

#### Acceptance Criteria

1. THE Strategy_Builder_API SHALL record validation duration, compile duration, validation error counts by code, registry request counts, registry cache-hit counts and asset universe age.
2. THE Training_Service SHALL record job counts by status, job duration by model block, cap rejection counts by cap and insufficient-data block counts.
3. THE DAG_Runtime SHALL record per-node execution duration by Block_Category, node not-ready counts by reason and blocked non-finite Trade_Intent counts.
4. WHEN a non-finite Trade_Intent is blocked, THE Strategy_Builder_API SHALL raise an alert.
5. WHILE a live deployment reports Feed_State `STALE` for more than 3 expected bar intervals, THE Strategy_Builder_API SHALL raise an alert.
6. THE Strategy_Builder SHALL display the recorded execution trace for a selected node, comprising that node's inputs, outputs, duration and recorded failures.
7. WHEN an author requests a node preview, THE Strategy_Builder SHALL compute that preview over a bounded historical window using the same executors the DAG_Runtime uses.
8. THE Strategy_Builder SHALL display for a FEATURE_ENGINEERING node preview the produced column names and a sample of the produced values.

---

### Requirement 25: Performance and capacity budgets

**User Story:** As a strategy author, I want validation and compilation to keep pace with my
editing, so that building a large strategy stays interactive.

#### Acceptance Criteria

1. WHEN a Canonical_Graph of 200 nodes is compiled, THE Strategy_Compiler SHALL complete compilation with a 95th-percentile duration of 50 milliseconds or less.
2. WHEN a Canonical_Graph of 200 nodes is validated, THE Graph_Validator SHALL complete validation with a 95th-percentile server-side duration of 120 milliseconds or less.
3. THE Strategy_Compiler SHALL perform compilation without input or output operations.
4. IF a Canonical_Graph holds more than 200 nodes, more than 400 edges, more than 4 model nodes, more than 40 FEATURE_ENGINEERING nodes, more than 200 feature columns, or more than 16 connections on one Variadic_Port, THEN THE Graph_Validator SHALL reject that graph and SHALL name the exceeded limit together with its permitted value.
5. THE Asset_Discovery_Service SHALL serve the market universe from a cache with a time-to-live of 6 hours.
6. THE DAG_Runtime SHALL evaluate the nodes of one execution level concurrently.
7. THE DAG_Runtime SHALL reuse a memoized node result for a repeated combination of node identifier, parameter set and window end.

---

## Traceability

Chain: **requirement → design acceptance criterion → design correctness property → test**.
Design acceptance criteria are the 42 numbered items in
`design.md § Acceptance criteria (gate before Backtester / Marketplace work)`. Correctness
properties are the 26 numbered items in `design.md § Correctness properties`, each already
mapped to a test in `design.md § Testing strategy`.

| Requirement | Design acceptance criteria | Correctness properties | Defect closed |
|---|---|---|---|
| 1 Canonical graph model and lossless serialization | 1, 3, 4 | 6 | SB-05 |
| 2 Stable strategy identity | 5 | 3, 4, 5 | SB-01 |
| 3 Single compilation authority | 1, 2 | 1, 2, 3 | SB-01 |
| 4 Backend-authoritative block palette | 6, 7, 8, 9, 10, 11, 26 | 8, 9, 10 | SB-03, SB-04 |
| 5 Registry-driven block configuration | 39 | 8 | SB-04 |
| 6 Connection legality | 13, 14, 15 | 11, 12 | — |
| 7 Branching and merging topologies | 16 | 2 | — |
| 8 Single-pass validation reporting | 12, 40 | 1 | — |
| 9 Immutable strategy versions | 20 | 21 | — |
| 10 Clone preserves compiled state | 17, 18, 19 | 7 | SB-02 |
| 11 Live discoverable asset universe | 23 | — | SB-06 |
| 12 Exchange-agnostic strategy definition | 21, 22, 25 | 23 | SB-06 |
| 13 Deployment binding and lifecycle | 24 | 22 | SB-06 |
| 14 ML and DL training admission gate | 26, 27, 28 | 19 | — |
| 15 Asynchronous training with truthful progress | 30, 31 | — | — |
| 16 Backend-enforced training resource caps | 29 | 20 | — |
| 17 Model versioning and per-node binding | 32 | — | — |
| 18 Data leakage protection | 33 | 16, 17, 18 | — |
| 19 Validated market data and truthful feed state | 41 | 26 | — |
| 20 Runtime readiness and financial safety | 34, 35, 36 | 13, 14, 15 | — |
| 21 Authorization, tenant isolation, secret containment | 25, 36 | 22, 23 | — |
| 22 Builder and Backtester separation | 37, 38 | 24, 25 | SB-01 |
| 23 Pushed realtime updates | 41 | 26 | — |
| 24 Observability and node-level diagnostics | 35 | 14 | — |
| 25 Performance and capacity budgets | 42 | — | — |

Every design acceptance criterion is covered: 1–5 by Requirements 1–3; 6–11 by Requirement 4;
12–16 by Requirements 6–8; 17–20 by Requirements 9–10; 21–25 by Requirements 11–13 and 21;
26 by Requirement 4 and Requirement 14; 27–33 by Requirements 14–18; 34–36 by
Requirements 20–21; 37–38 by Requirement 22; 39–42 by Requirements 5, 8, 19 and 25.

Every correctness property is claimed by at least one requirement. Properties without a
user-facing counterpart in this document do not exist: properties 1–5 (compiler
determinism and identity) sit under Requirements 1–3, 6 (serializer round-trip) under
Requirement 1, 7 (clone) under Requirement 10, 8–10 (registry integrity) under
Requirement 4, 11–12 (legality and cycles) under Requirement 6, 13–15 (numeric and
readiness safety) under Requirement 20, 16–18 (leakage) under Requirement 18, 19–20
(training gates and caps) under Requirements 14 and 16, 21–23 (immutability, isolation,
secrets) under Requirements 9, 21 and 12, 24–25 (separation and shared artifact) under
Requirement 22, and 26 (feed honesty) under Requirements 19 and 23.
