from typing import Literal, Any, Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


ScenarioId = Literal[
    "latency-team",
    "latency-solo",
    "errors-team",
    "errors-solo",
    "outage-team",
    "outage-solo",
]
Topology = Literal["planner_executor", "single_agent"]
Status = Literal["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]
Tool = Literal["read_metrics", "read_logs", "probe", "act"]


class StartRequest(Strict):
    id: UUID
    scenario_id: ScenarioId


class Hypothesis(Strict):
    cause: str = Field(min_length=1, max_length=200)
    observation_ids: list[str] = Field(min_length=1, max_length=17)


class Step(Strict):
    tool: Tool
    purpose: str = Field(min_length=1, max_length=300, description="Why this operation is needed. A step contains tool and purpose only, never operation arguments.")


class PlanInput(Strict):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=8)
    steps: list[Step] = Field(min_length=1, max_length=8)
    finish_recommended: bool = False


class ReadInput(Strict):
    revision: int = Field(ge=0)


class ProbeInput(ReadInput):
    target: Literal["pool", "release", "inventory"]


class ActionInput(ReadInput):
    action: Literal["restart_pool", "rollback_release", "restore_inventory"]
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class FinishInput(Strict):
    diagnosis: Literal["pool_exhaustion", "bad_release", "inventory_failure", "unknown"]
    summary: str = Field(min_length=1, max_length=1400)
    uncertainties: list[Annotated[str, Field(max_length=400)]] = Field(max_length=6)
    observation_ids: list[str] = Field(min_length=1, max_length=17)


class Observation(Strict):
    id: str
    revision: int = Field(ge=0)
    kind: Literal["metrics", "logs", "probe", "action", "rejection"]
    data: dict[str, Any]


class PlanRecord(PlanInput):
    revision: int = Field(ge=1)
    actor: Literal["planner", "single_agent"]


class Usage(Strict):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    model_calls: int = Field(ge=0, le=20)


class IncidentReport(FinishInput):
    contract_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["incident_report"] = "incident_report"
    simulated: Literal[True] = True
    scenario_id: ScenarioId
    topology: Topology
    seed: int
    recovered: bool
    diagnosis_correct: bool
    observations: list[Observation] = Field(max_length=17)
    plans: list[PlanRecord] = Field(max_length=10)
    action_count: int = Field(ge=0, le=16)
    simulated_steps: int = Field(ge=0, le=16)
    invalid_calls: int = Field(ge=0, le=16)
    model_usage: Usage
    versions: dict[str, str]


class Event(Strict):
    seq: int = Field(ge=1)
    occurred_at: AwareDatetime
    kind: Literal[
        "state_transition",
        "plan_recorded",
        "handoff_recorded",
        "tool_called",
        "tool_completed",
        "observation_recorded",
        "memory_updated",
        "context_assembled",
        "model_completed",
        "validation_rejected",
        "evaluation_recorded",
    ]
    status: Status | None = None
    data: dict


class Evaluation(Strict):
    id: UUID
    category: Literal[
        "deterministic", "trajectory", "domain", "semantic", "operational"
    ]
    status: Literal["passed", "failed", "unknown", "skipped", "errored"]
    evaluator: str
    evaluator_version: str
    configuration: dict
    metrics: dict
    provenance: dict
    reason: str


class Execution(Strict):
    integration_version: Literal["1.2"] = "1.2"
    id: UUID
    status: Status
    events: list[Event] = Field(max_length=200)
    result: IncidentReport | None = None
    evaluations: list[Evaluation] = Field(max_length=8)
    failure_code: str | None = None
