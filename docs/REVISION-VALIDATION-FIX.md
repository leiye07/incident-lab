# IncidentLab provider and revision validation

The investigated run 619e57b0-3fe1-4943-96cb-b2020c46a1bb belongs to IncidentLab, not AdaptiveOS.

## Changes

- Explicit environment_revision in role contexts; plan revision is a separate counter. Stale operation revisions remain rejected rather than silently rewritten.
- Planner context describes hypothesis keys cause/observation_ids and step keys tool/purpose. Executor operation-argument schemas are omitted from planner context.
- Native OpenRouter Chat Completions and direct Anthropic Messages adapters normalize tool calls, tool results and usage.
- Invalid tool selection, schema violations, missing tool calls and unobserved references receive bounded correction feedback.
- Temporary provider 408/502/503/504 responses and timeouts receive at most three consecutive attempts with short backoff. Authentication, credit, rate-limit and configuration failures are not retried.
- Every attempted request consumes the existing 20-call run allowance. Token totals include only provider-reported usage; upstream-rejected requests may omit usage.
- Diagnostics persist safe categories and provider identifiers, never raw exception strings, generated arguments, prompts or credentials.
- Tool schemas allow eight hypotheses/plan steps and references to the complete 17-observation run history.

## Verification

44 tests passed on disposable PostgreSQL, including schema correction, stale operation recovery, retry accounting, tool transcript normalization and diagnostic sanitization.

Live run bca43f83-9a89-48ba-9e1c-53c7d1f74ad7 succeeded with independently verified recovery, correct diagnosis, 11 model requests and one synchronized report. Historical failures remain stored.

## Known remaining issues

Later run 75b94108-512a-46b9-8218-83a8ec39f815 exhausted the 20-request budget after recovering the simulated environment, before producing its final report. It also exposed that action validation accepts a matching historical probe without requiring it to remain fresh and positive. Fresh positive probe enforcement, explicit remaining-call guidance and a reserved reporting phase were investigated but are not implemented in this change set. Successful live verification is not a guarantee that every model-directed run succeeds.
