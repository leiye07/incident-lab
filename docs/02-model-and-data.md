# Model and persistent data design

## Model boundary

Use an explicit operator-configured Anthropic model ID via INCIDENTLAB_MODEL; no guessed default. Mount provider credential only into the worker. API admission relies on worker configuration heartbeat. Planner and executor can use the same model with isolated prompts/contexts; this tests architectural separation without confounding model differences. Solo baseline uses the same configured model and budgets. A real HTTPS Messages API provider performs bounded tool-use calls; scripted providers exist in tests only.

Planner output: hypothesis list (cause candidate + supporting observation IDs), steps (tool name + purpose), and finish recommendation. Executor output: one allowlisted environment operation with validated arguments, or finish_report with diagnosis, summary, uncertainties and observation IDs. Neither role can run shell commands, choose service URLs or modify evaluator truth. Planner sees no private simulator snapshot; executor sees only public observations and current plan. Tool results and logs are untrusted data.

## PostgreSQL schema

- schema_migrations: applied revision marker, serialized migration advisory lock.
- executions: UUID PK, request fingerprint, approved scenario, lifecycle/timestamps, immutable configuration and version manifest, cancellation flag, next event sequence, result/evaluations, sanitized failure code.
- evidence_events: (execution_id,seq) PK, occurred_at, typed kind, optional lifecycle status, bounded public JSON. Row-locked sequence allocation; append-only.
- runtime_health: worker heartbeat and readiness flag, API never mounts model key.
- environment_states: execution UUID FK/PK, private JSON state, seed, revision. Contains hidden cause and simulator variables, never returned through integration endpoints.
- observations: (execution_id,id) PK, revision, public kind/data. Stable observation IDs support plan/report references and independent grounding checks.
- plans: (execution_id,revision) PK, actor, typed plan JSON, evidence refs; each revision persisted before execution.
- tool_operations: (execution_id,operation_index) PK, plan_revision FK, tool, arguments, observation_id FK, validity and simulated outcome metadata. Allows trajectory grading and audit.
- working_memory: execution UUID FK/PK; bounded structured hypotheses/attempt summaries and recent observation IDs. Updated per operation; no sharing between executions.

Persist snapshot, observation, operation, memory and matching evidence atomically. Model calls occur outside transactions. The child owns one execution; lifecycle row locks reject writes after cancellation/terminal transition. Quotas and idempotency share transaction/advisory locks. Terminal result and evaluations commit with succeeded transition. Unique source sequences allow sandbox deduplication. Cancelled/timed-out partial observations remain visible as events; no fabricated final artifact is created.

Private scenario definitions and simulator state are not part of the public manifest. A new simulator/prompt/tool change changes its hash/version. Existing forecast and contract report tables remain in the sandbox DB; IncidentLab never queries them. Sandbox only adds incident_report as an artifact discriminator (its existing text kind column needs no destructive migration).
