import hashlib, json, time, uuid
from pathlib import Path
from pydantic import Field, ValidationError
from psycopg.types.json import Jsonb
from . import db, config, environment as sim
from .provider import ModelOutputRejected, RetryableProviderFailure
from .contracts import (
    Strict,
    Tool,
    PlanInput,
    PlanRecord,
    ReadInput,
    ProbeInput,
    ActionInput,
    FinishInput,
    IncidentReport,
    Usage,
    Evaluation,
)


class BudgetExceeded(RuntimeError):
    pass


class ModelOutputError(ValueError):
    """Recoverable model error; category never contains model text or credentials."""
    def __init__(self, category):
        self.category = category
        super().__init__(category)


class SoloInput(Strict):
    plan: PlanInput
    tool: Tool
    arguments: dict


TOOLS = {
    "read_metrics": ReadInput,
    "read_logs": ReadInput,
    "probe": ProbeInput,
    "act": ActionInput,
}
ROOT = Path(__file__).parent


def versions():
    return {
        "runtime": "1.0",
        "provider": config.model_provider(),
        "usage_accounting": "All model requests counted; tokens include reported usage only. Upstream-rejected responses may omit token usage.",
        "simulator": "1.0",
        "tools": "1.0",
        "model": config.model(),
        "planner_prompt": hashlib.sha256(
            (ROOT / "planner.txt").read_bytes()
        ).hexdigest(),
        "executor_prompt": hashlib.sha256(
            (ROOT / "executor.txt").read_bytes()
        ).hexdigest(),
        "solo_prompt": hashlib.sha256((ROOT / "solo.txt").read_bytes()).hexdigest(),
        "simulator_sha256": hashlib.sha256(
            (ROOT / "environment.py").read_bytes()
        ).hexdigest(),
    }


def tool_schema(name, model):
    return {
        "name": name,
        "description": {
            "read_metrics": "Observe actual current synthetic metrics; two healthy post-action readings verify recovery.",
            "read_logs": "Read synthetic checkout log entries.",
            "probe": "Run a diagnostic probe against an allowlisted component.",
            "act": "Apply one reversible synthetic action supported by matching probe evidence.",
            "record_plan": "Record a public hypothesis/plan revision.",
            "decide": "Record hypothesis/plan and select one environment operation.",
            "finish_report": "Finish with a grounded diagnosis and uncertainties; grader derives recovery.",
        }[name],
        "input_schema": model.model_json_schema(),
    }


class Investigator:
    def __init__(self, eid, scenario_id, provider):
        self.eid = eid
        self.sid = scenario_id
        self.definition = sim.SCENARIOS[scenario_id]
        self.provider = provider
        self.state = sim.initialize(scenario_id)
        self.observations = []
        self.plans = []
        self.memory = {"hypotheses": [], "attempts": [], "recent_observation_ids": []}
        self.usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 0}
        self.invalid = 0
        self.operations = 0
        self.last_feedback = None
        self.last_actor = "planner"
        self.started = time.monotonic()
        self.effective_models = set()

    def guard(self):
        if time.monotonic() - self.started >= 175:
            raise BudgetExceeded("Deadline budget")
        with db.connect() as c:
            row = c.execute(
                "SELECT status,cancel_requested FROM executions WHERE id=%s",
                (self.eid,),
            ).fetchone()
        if row["status"] != "running" or row["cancel_requested"]:
            raise BudgetExceeded("Execution stopped")

    def init(self):
        observation = {
            "id": "obs-000",
            "revision": 0,
            "kind": "metrics",
            "data": sim.metrics(self.state),
        }
        self.observations = [observation]
        self.memory["recent_observation_ids"] = [observation["id"]]
        with db.connect() as c:
            active = c.execute(
                "SELECT status,cancel_requested FROM executions WHERE id=%s FOR UPDATE",
                (self.eid,),
            ).fetchone()
            if active["status"] != "running" or active["cancel_requested"]:
                raise BudgetExceeded("Execution stopped")
            c.execute(
                "INSERT INTO environment_states VALUES(%s,%s,%s,%s)",
                (self.eid, self.state["seed"], 0, Jsonb(self.state)),
            )
            c.execute(
                "INSERT INTO observations VALUES(%s,%s,%s,%s,%s)",
                (self.eid, observation["id"], 0, "metrics", Jsonb(observation["data"])),
            )
            c.execute(
                "INSERT INTO working_memory VALUES(%s,%s)",
                (self.eid, Jsonb(self.memory)),
            )
            db.append(c, self.eid, "observation_recorded", {"observation": observation})

    def reject_model_output(self, actor, exc):
        category = "schema_validation" if isinstance(exc, ValidationError) else exc.category
        self.last_feedback = {
            "category": category,
            "instruction": "Correct the response using exactly one allowed tool and its schema. Cite only supplied observation IDs. Use environment_revision for operation revision; current_plan.revision is only a plan version.",
        }
        if isinstance(exc, ValidationError):
            fields = {"hypotheses", "cause", "observation_ids", "steps", "tool", "purpose", "finish_recommended", "revision", "target", "action", "evidence_ids", "plan", "arguments", "diagnosis", "summary", "uncertainties"}
            self.last_feedback["schema_errors"] = [
                {"fields": [part for part in error["loc"] if isinstance(part, int) or part in fields], "type": error["type"]}
                for error in exc.errors(include_input=False, include_context=False, include_url=False)[:8]
            ]
        db.record(self.eid, "validation_rejected", {
            "actor": actor, "tool": "unrecognized",
            "reason": "Model output rejected: " + category + ". Correct using the supplied schemas and observed evidence. " + json.dumps(getattr(exc, "diagnostic", {}), sort_keys=True),
        })

    def call(self, actor, models, plan=None):
        self.last_actor = actor
        for attempt in range(3):
            try:
                result = self._call_once(actor, models, plan)
                self.last_feedback = None
                return result
            except RetryableProviderFailure as exc:
                db.record(self.eid, "validation_rejected", {"actor": actor, "tool": "unrecognized", "reason": "Temporary provider failure: " + exc.category + "; no tool ran. " + json.dumps(exc.diagnostic, sort_keys=True)})
                if attempt == 2:
                    raise
                time.sleep(0.25 * (attempt + 1))
            except (ModelOutputError, ValidationError, ModelOutputRejected) as exc:
                self.reject_model_output(actor, exc)
                if attempt == 2:
                    raise ModelOutputError("validation_retries_exhausted") from None

    def _call_once(self, actor, models, plan=None):
        self.guard()
        if self.usage["model_calls"] >= 20:
            raise BudgetExceeded("Model-call budget")
        public = {
            "task": self.definition["name"],
            "simulated": True,
            "revision": self.state["revision"],
            "environment_revision": self.state["revision"],
            "revision_rule": "Every operation revision must equal environment_revision; current_plan.revision is NOT an environment revision.",
            "last_validation_feedback": self.last_feedback,
            "allowed_probe_targets": ["pool", "release", "inventory"],
            "observations": self.observations,
            "working_memory": self.memory,
            "current_plan": plan,
            "operations_remaining": 16 - self.operations,
            "plan_revisions_remaining": 10 - len(self.plans),
            "allowed_actions": list(sim.ACTION_TARGET),
            "operation_schemas": {
                name: model.model_json_schema() for name, model in TOOLS.items()
            },
            "verification": "Two healthy metrics observations after the last action are required. Recovery is graded independently.",
        }
        if actor == "planner":
            public.pop("operation_schemas")
            public["plan_format"] = {
                "hypothesis_keys": ["cause", "observation_ids"],
                "step_keys": ["tool", "purpose"],
                "rule": "Planning steps describe tool and purpose only. Do not include args, arguments, target or revision in a plan step; executor supplies operation parameters.",
            }
        system = (
            ROOT
            / (
                {"planner": "planner", "executor": "executor", "single_agent": "solo"}[
                    actor
                ]
                + ".txt"
            )
        ).read_text()
        messages = [
            {"role": "user", "content": json.dumps(public, separators=(",", ":"))}
        ]
        payload = json.dumps(messages, separators=(",", ":")).encode()
        schemas = [tool_schema(name, model) for name, model in models.items()]
        size = len(
            json.dumps(
                {"system": system, "messages": messages, "tools": schemas},
                separators=(",", ":"),
            ).encode()
        )
        if size > 80000:
            raise BudgetExceeded("Context budget")
        db.record(
            self.eid,
            "context_assembled",
            {
                "actor": actor,
                "turn": self.usage["model_calls"] + 1,
                "utf8_bytes": size,
                "byte_limit": 80000,
                "prompt_sha256": hashlib.sha256(system.encode()).hexdigest(),
                "context_sha256": hashlib.sha256(payload).hexdigest(),
                "observation_ids": [o["id"] for o in self.observations],
                "ledger_entries": len(self.memory["attempts"]),
                "output_token_limit": 2400,
            },
        )
        self.usage["model_calls"] += 1
        response = self.provider.complete(system, messages, schemas)
        for k in ["input_tokens", "output_tokens"]:
            value = response["usage"][k]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("Invalid model usage")
            self.usage[k] += value
        self.effective_models.add(str(response["model"]))
        db.record(
            self.eid,
            "model_completed",
            {
                "actor": actor,
                "turn": self.usage["model_calls"],
                "model": str(response["model"]),
                "usage": response["usage"],
                "latency_ms": response["latency_ms"],
            },
        )
        calls = response["calls"]
        if len(calls) != 1 or calls[0].get("name") not in models:
            raise ModelOutputError("tool_selection")
        call = calls[0]
        value = models[call["name"]].model_validate(call["input"])
        return call["name"], value

    def plan(self, value, actor):
        if len(self.plans) >= 10:
            raise BudgetExceeded("Plan budget")
        known = {o["id"] for o in self.observations}
        if any(not set(h.observation_ids) <= known for h in value.hypotheses):
            raise ModelOutputError("unobserved_plan_evidence")
        record = PlanRecord(
            **value.model_dump(), revision=len(self.plans) + 1, actor=actor
        ).model_dump(mode="json")
        self.plans.append(record)
        self.memory["hypotheses"] = record["hypotheses"]
        with db.connect() as c:
            active = c.execute(
                "SELECT status,cancel_requested FROM executions WHERE id=%s FOR UPDATE",
                (self.eid,),
            ).fetchone()
            if active["status"] != "running" or active["cancel_requested"]:
                raise BudgetExceeded("Execution stopped")
            c.execute(
                "INSERT INTO plans VALUES(%s,%s,%s,%s)",
                (self.eid, record["revision"], actor, Jsonb(record)),
            )
            c.execute(
                "UPDATE working_memory SET payload=%s WHERE execution_id=%s",
                (Jsonb(self.memory), self.eid),
            )
            db.append(c, self.eid, "plan_recorded", {"actor": actor, "plan": record})
            if actor == "planner":
                db.append(
                    c,
                    self.eid,
                    "handoff_recorded",
                    {
                        "from_actor": "planner",
                        "to_actor": "executor",
                        "plan_revision": record["revision"],
                        "observation_ids": [o["id"] for o in self.observations],
                    },
                )
        return record

    def operate(self, name, value, plan):
        self.guard()
        if self.operations >= 16:
            raise BudgetExceeded("Tool budget")
        self.operations += 1
        args = value.model_dump(mode="json")
        actor = (
            "executor"
            if self.definition["topology"] == "planner_executor"
            else "single_agent"
        )
        db.record(
            self.eid,
            "tool_called",
            {
                "actor": actor,
                "name": name,
                "arguments": args,
                "call_number": self.operations,
                "plan_revision": plan["revision"],
            },
        )
        valid = True
        try:
            if name not in [step["tool"] for step in plan["steps"]]:
                raise ValueError("Tool is not permitted by the current plan")
            state, kind, data = sim.execute(self.state, name, args, self.observations)
        except ValueError:
            valid = False
            self.invalid += 1
            state = self.state
            kind = "rejection"
            data = {
                "reason": (f"Stale operation revision {args['revision']}; use environment_revision {self.state['revision']}, not plan revision {plan['revision']}." if args["revision"] != self.state["revision"] else "Operation rejected: check plan permission and matching probe evidence.")
            }
        observation = {
            "id": f"obs-{self.operations:03d}",
            "revision": state["revision"],
            "kind": kind,
            "data": data,
        }
        self.state = state
        self.observations.append(observation)
        self.memory["attempts"].append(
            {"tool": name, "observation_id": observation["id"], "valid": valid}
        )
        self.memory["recent_observation_ids"] = [
            o["id"] for o in self.observations[-8:]
        ]
        with db.connect() as c:
            row = c.execute(
                "SELECT status,cancel_requested FROM executions WHERE id=%s FOR UPDATE",
                (self.eid,),
            ).fetchone()
            if row["status"] != "running" or row["cancel_requested"]:
                raise BudgetExceeded("Execution stopped")
            c.execute(
                "UPDATE environment_states SET revision=%s,state=%s WHERE execution_id=%s",
                (state["revision"], Jsonb(state), self.eid),
            )
            c.execute(
                "INSERT INTO observations VALUES(%s,%s,%s,%s,%s)",
                (
                    self.eid,
                    observation["id"],
                    observation["revision"],
                    kind,
                    Jsonb(data),
                ),
            )
            c.execute(
                "INSERT INTO tool_operations VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (
                    self.eid,
                    self.operations,
                    plan["revision"],
                    name,
                    Jsonb(args),
                    observation["id"],
                    valid,
                ),
            )
            c.execute(
                "UPDATE working_memory SET payload=%s WHERE execution_id=%s",
                (Jsonb(self.memory), self.eid),
            )
            if not valid:
                db.append(
                    c,
                    self.eid,
                    "validation_rejected",
                    {"actor": actor, "tool": name, "reason": data["reason"]},
                )
            db.append(c, self.eid, "observation_recorded", {"observation": observation})
            db.append(
                c,
                self.eid,
                "tool_completed",
                {
                    "actor": actor,
                    "name": name,
                    "observation_id": observation["id"],
                    "valid": valid,
                },
            )
            db.append(c, self.eid, "memory_updated", {"memory": self.memory})

    def finish(self, value):
        self.guard()
        known = {o["id"] for o in self.observations}
        if not set(value.observation_ids) <= known:
            raise ModelOutputError("unobserved_report_evidence")
        v = versions()
        v["effective_models"] = ",".join(sorted(self.effective_models))
        report = IncidentReport(
            **value.model_dump(),
            scenario_id=self.sid,
            topology=self.definition["topology"],
            seed=self.state["seed"],
            recovered=sim.recovered(self.state),
            diagnosis_correct=value.diagnosis == self.state["cause"],
            observations=self.observations,
            plans=self.plans,
            action_count=self.state["actions"],
            simulated_steps=self.state["steps"],
            invalid_calls=self.invalid,
            model_usage=Usage(**self.usage),
            versions=v,
        )
        evaluations = evaluate(report, time.monotonic() - self.started)
        with db.connect() as c:
            row = c.execute(
                "SELECT status,cancel_requested FROM executions WHERE id=%s FOR UPDATE",
                (self.eid,),
            ).fetchone()
            if row["status"] != "running" or row["cancel_requested"]:
                raise BudgetExceeded("Execution stopped")
            c.execute(
                "UPDATE executions SET result=%s,evaluations=%s WHERE id=%s",
                (Jsonb(report.model_dump(mode="json")), Jsonb(evaluations), self.eid),
            )
            db.append(c, self.eid, "evaluation_recorded", {"reports": evaluations})
            db.transition(c, self.eid, "succeeded")
        return report


def evaluate(report, elapsed):
    data = [
        (
            "deterministic",
            "passed",
            {"referenced_observations": len(report.observation_ids)},
            "Report schema and observation references validated.",
        ),
        (
            "domain",
            "passed" if report.recovered and report.diagnosis_correct else "failed",
            {
                "recovered": report.recovered,
                "diagnosis_correct": report.diagnosis_correct,
            },
            "Recovery is derived from simulator state and two healthy post-action metric readings; diagnosis is checked against private scenario truth.",
        ),
        (
            "trajectory",
            "passed" if report.invalid_calls == 0 else "failed",
            {
                "invalid_calls": report.invalid_calls,
                "actions": report.action_count,
                "simulated_steps": report.simulated_steps,
            },
            "Plan permissions, current revision and probe/action authorization checked deterministically.",
        ),
        (
            "semantic",
            "unknown",
            {},
            "Explanation quality has no independent semantic reviewer.",
        ),
        (
            "operational",
            "passed",
            {**report.model_usage.model_dump(), "execution_seconds": round(elapsed, 4)},
            "Actual provider usage recorded; monetary cost is unavailable.",
        ),
    ]
    return [
        Evaluation(
            id=uuid.uuid4(),
            category=cat,
            status=status,
            evaluator="incidentlab-" + cat,
            evaluator_version="1.0",
            configuration={"seed": report.seed, "topology": report.topology},
            metrics=metrics,
            provenance={"simulated": True, "scenario_id": report.scenario_id},
            reason=reason,
        ).model_dump(mode="json")
        for cat, status, metrics, reason in data
    ]


def run(eid, provider):
    with db.connect() as c:
        row = c.execute(
            "SELECT scenario_id FROM executions WHERE id=%s", (eid,)
        ).fetchone()
    agent = Investigator(eid, row["scenario_id"], provider)
    agent.init()
    while True:
        try:
            if agent.definition["topology"] == "planner_executor":
                _, value = agent.call("planner", {"record_plan": PlanInput})
                plan = agent.plan(value, "planner")
                name, value = agent.call(
                    "executor", {**TOOLS, "finish_report": FinishInput}, plan
                )
            else:
                name, value = agent.call(
                    "single_agent", {"decide": SoloInput, "finish_report": FinishInput}
                )
                if name == "finish_report":
                    return agent.finish(value)
                plan = agent.plan(value.plan, "single_agent")
                name, args = value.tool, value.arguments
                value = TOOLS[name].model_validate(args)
            if name == "finish_report":
                return agent.finish(value)
            agent.operate(name, value, plan)
        except (ModelOutputError, ValidationError, ModelOutputRejected) as exc:
            if isinstance(exc, ModelOutputError) and exc.category == "validation_retries_exhausted":
                raise
            agent.reject_model_output(agent.last_actor, exc)
