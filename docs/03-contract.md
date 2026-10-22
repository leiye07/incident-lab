# IncidentLab integration contract — 1.2

Transport is private fixed-origin HTTP behind the sandbox API. Every /demo/v1 endpoint requires the distinct scoped IncidentLab bearer token (minimum 32 chars), never a provider key. Inputs are strict Pydantic JSON; no unknown fields. POST bodies <=4096 bytes. All responses no-store, sanitized errors. Discovery has no evaluator labels.

| Method / endpoint | Request | Response |
|---|---|---|
| GET /healthz | none | process ok |
| GET /readyz | none | schema initialized |
| GET /demo/v1/capabilities | scoped bearer | application_id=incidentlab, integration_version=1.2, artifact_type=incident_report, artifact_version=1.0, scenario_ids, cancellation, execution_ready, runtime_code_sha |
| GET /demo/v1/scenarios | scoped bearer | six fixed symptom/topology presets, no private truth |
| POST /demo/v1/executions | {id:UUID,scenario_id:enum} | 202 execution envelope; identical replay returns current execution; different request under same UUID=409 |
| GET /demo/v1/executions/:id | scoped bearer | execution envelope |
| POST /demo/v1/executions/:id/cancel | {} + scoped bearer | queued cancellation is terminal; running sets request flag, controller must confirm |

Scenario IDs: latency-team, latency-solo, errors-team, errors-solo, outage-team, outage-solo. Topology/seed are preset-owned, not arbitrary visitor parameters. Runtime quotas: four active, sixty admissions/hour. Execution-ready requires model flag, explicit model ID and live configured worker heartbeat. Invalid input=422; auth=401; missing execution=404; capacity=429; unconfigured/offline=503.

Execution envelope: integration_version=1.2, id, status, ordered events (max200), result optional, evaluations, failure_code optional. Lifecycle created→queued→running→succeeded/failed/cancelled/timed_out; queued may fail/cancel/time out. Events: state_transition, plan_recorded, handoff_recorded, tool_called, tool_completed, observation_recorded, memory_updated, context_assembled, model_completed, validation_rejected, evaluation_recorded. Each includes sequence, aware timestamp, kind, optional lifecycle status and bounded allowlisted public data. Transitions require contiguous source sequence. Arbitrary upstream data and nested secret/raw-prompt fields are rejected by sandbox ingestion.

Incident report 1.0: artifact_type=incident_report, scenario_id, topology, seed, diagnosis, summary, uncertainties, recovered (environment-derived), diagnosis_correct (grader-derived), observation_ids, public observations, recorded plans, action/step/invalid-call counts, actual model usage, versions and simulated=true. Status succeeded means report completed, not recovered. Source IDs must resolve; report scenario/topology must match requested preset. No hidden cause/state is included in the integration envelope.

Evaluations preserve categories deterministic, trajectory, domain, semantic, operational and statuses passed, failed, unknown, skipped, errored. Semantic stays unknown; domain recovery can fail even when execution succeeds. Unknown is never normalized to passed.

Generated OpenAPI and JSON schemas accompany implementation. Canonical sandbox evidence remains 1.0; forecast/contract adapters are unchanged. The TypeScript Cloudflare proxy is unchanged because /api/v1 traffic already goes to the containerized API service.
