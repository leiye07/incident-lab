# IncidentLab — System 3

Independent containerized planner–executor experiment in a seeded synthetic checkout incident environment. Real model-directed decisions, bounded simulator tools, environmental feedback, durable per-run memory and independent deterministic evaluation. No production actions, hidden chain-of-thought, cross-run learning or claim of team superiority.

Read docs/01-architecture.md, docs/02-model-and-data.md and docs/03-contract.md in that order. SQL is incidentlab/migrations/001_initial.sql; generated schemas/OpenAPI are docs/contracts. See docs/VALIDATION.md for completed checks and remaining real-provider/container acceptance.

## Start independently

Run `python scripts/init_demo.py` once to create a private secrets directory, scoped demo token and empty provider-key file without printing credential values. Provision secrets/anthropic_api_key locally using your secret management process. Do not commit credentials or send them through chat. Set an explicit INCIDENTLAB_MODEL and MODEL_EXECUTION_ENABLED=true in .env, then `docker compose up --build`. API binds loopback port 8030. Execution is disabled by default; no mock provider exists in the production runner. Readiness also requires a live configured worker heartbeat.

The API receives only the demo token; the worker receives the provider key. The distinct scoped demo token is copied through secure provisioning to agents-lab-api/secrets/incidentlab_demo_token, never to the browser/dashboard. Use the same private showroom-integration network and compose.integration.yaml overlay as the other application services. Set API service INCIDENTLAB_URL=http://incidentlab-demo:8030, INCIDENTLAB_TOKEN_FILE=/run/secrets/incidentlab_demo_token and INCIDENTLAB_EXECUTION_ENABLED=true. Domain databases are not shared.

Compose's database password is a documented local-development value on an internal network. Production deployment must provision its own private credential and preserve its durable volume. The scaffold follows the separate existing EnergyOps/ContractLens service boundary; neither service requires modification.

## Scenarios and tools

Six fixed presets: latency-team/solo, errors-team/solo, outage-team/solo. Team uses isolated planner/executor model contexts; solo emits a plan and selected operation in one model decision. Both use identical simulator seeds and global limits: 180s, 20 model calls, 16 environment operations, 10 plan revisions. Tools: read_metrics, read_logs, probe, act, finish_report. Actions are simulator-only restart_pool, rollback_release, restore_inventory, each requiring a matching observed diagnostic probe and current revision. Finish can be unresolved: report completion is separate from verified recovery.

The report records public observations, plans, outcome metrics, diagnosis correctness, actual model usage, scenario/topology/seed and version hashes. The domain evaluator derives recovery from hidden simulator state and two healthy post-action readings. Semantic explanation quality remains unknown. A single-agent comparison requires real paired runs with matching versions/model/seed; no benefit is claimed from test fixtures.

## Development

Install requirements-dev.lock into a virtual environment. Set TEST_DATABASE_URL to a disposable IncidentLab-only PostgreSQL database, then `pytest -q`. Tests truncate runtime tables; never use a production or sandbox DB. `PYTHONPATH=. python scripts/export_contracts.py` regenerates wire contracts. Test providers are explicit fixtures, not production fallbacks.

## OpenRouter provider

Use `MODEL_PROVIDER=openrouter` in the runtime `.env` with an explicit OpenRouter model ID
(e.g. `openai/gpt-oss-120b`). The production Compose overlay passes this setting to the worker.
The existing `secrets/anthropic_api_key` file is the provider credential mount; for OpenRouter it must
contain the OpenRouter key. It is sent using bearer authentication only to `https://openrouter.ai/api/v1/chat/completions`.
The default `MODEL_PROVIDER=anthropic` keeps the direct Anthropic endpoint and authentication.
Request/response bounds, tool schemas, execution budgets and sanitized errors remain enforced.

Provider wire formats are implemented by explicit `AnthropicAdapter` and `OpenRouterAdapter`
classes in `provider_adapters.py`, selected by `create_provider()`. The native OpenRouter adapter
converts tool definitions, assistant function calls, tool results and provider usage; the Anthropic
adapter retains Messages format. Schema rejections surfaced as HTTP errors are classified separately
and corrected within existing model-call limits. No raw provider error text is exposed in evidence.

Temporary provider 408/502/503/504 responses and timeouts can be retried up to three attempts within the existing model-call budget; rejected requests count toward that budget, and only provider-reported token usage is summed. Authentication, credit, rate-limit and configuration errors are not automatically retried.
