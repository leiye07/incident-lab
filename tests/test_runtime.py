"""Test providers are explicit fixtures; no production model or recovery is fabricated."""

import json, uuid, subprocess, sys
import pytest, httpx
from incidentlab import db, worker, environment as sim
from incidentlab.engine import run, Investigator, BudgetExceeded, tool_schema
from incidentlab.contracts import PlanInput, ReadInput, FinishInput
from incidentlab.provider import AnthropicProvider, ProviderFailure
from conftest import submit


class ScriptedProvider:
    def __init__(self, sid):
        self.sid = sid
        self.contexts = []

    def complete(self, system, messages, tools):
        context = json.loads(messages[0]["content"])
        self.contexts.append((system, context))
        obs = context["observations"]
        rev = context["revision"]
        symptom = self.sid.split("-")[0]
        target = {"latency": "pool", "errors": "release", "outage": "inventory"}[
            symptom
        ]
        action = {
            "pool": "restart_pool",
            "release": "rollback_release",
            "inventory": "restore_inventory",
        }[target]
        cause = sim.CAUSES[symptom]
        probe = next(
            (o for o in obs if o["kind"] == "probe" and o["data"]["target"] == target),
            None,
        )
        actions = [o for o in obs if o["kind"] == "action"]
        checks = [
            o
            for o in obs
            if o["kind"] == "metrics"
            and actions
            and o["revision"] > actions[-1]["revision"]
        ]
        name = (
            "probe"
            if not probe
            else (
                "act"
                if not actions
                else "read_metrics" if len(checks) < 2 else "finish_report"
            )
        )
        args = {"revision": rev}
        if name == "probe":
            args["target"] = target
        if name == "act":
            args.update(action=action, evidence_ids=[probe["id"]])
        plan = {
            "hypotheses": [
                {
                    "cause": "Test-only hypothesis from observed symptoms",
                    "observation_ids": [obs[-1]["id"]],
                }
            ],
            "steps": [
                {
                    "tool": name if name != "finish_report" else "read_metrics",
                    "purpose": "Test-only bounded step",
                }
            ],
            "finish_recommended": name == "finish_report",
        }
        names = [t["name"] for t in tools]
        if names == ["record_plan"]:
            name = "record_plan"
            args = plan
        elif name == "finish_report":
            args = {
                "diagnosis": cause,
                "summary": "Test-only scripted report, not live model analysis.",
                "uncertainties": ["Synthetic fixture only."],
                "observation_ids": [o["id"] for o in obs],
            }
        elif "decide" in names:
            name, args = "decide", {"plan": plan, "tool": name, "arguments": args}
        return {
            "calls": [{"id": str(uuid.uuid4()), "name": name, "input": args}],
            "usage": {"input_tokens": 100, "output_tokens": 40},
            "model": "TEST-ONLY-SCRIPTED",
            "latency_ms": 1,
        }


@pytest.mark.parametrize("sid", list(sim.SCENARIOS))
def test_persisted_full_episode_for_all_presets(client, sid):
    eid = submit(client, sid)
    with db.connect() as c:
        db.transition(c, eid, "running")
    provider = ScriptedProvider(sid)
    result = run(eid, provider)
    data = client.get("/demo/v1/executions/" + eid).json()
    assert (
        result.recovered and result.diagnosis_correct and data["status"] == "succeeded"
    )
    assert result.model_usage.model_calls == (10 if sid.endswith("-team") else 5)
    assert [e["seq"] for e in data["events"]] == list(range(1, len(data["events"]) + 1))
    assert any(e["kind"] == "handoff_recorded" for e in data["events"]) == sid.endswith(
        "-team"
    )
    assert (
        next(e for e in data["evaluations"] if e["category"] == "semantic")["status"]
        == "unknown"
    )
    assert all(
        "pool_exhausted" not in json.dumps(context)
        and "healthy_checks" not in json.dumps(context)
        for _, context in provider.contexts
    )
    with db.connect() as c:
        assert (
            c.execute(
                "SELECT count(*) n FROM tool_operations WHERE execution_id=%s", (eid,)
            ).fetchone()["n"]
            == 4
        )
        assert (
            c.execute(
                "SELECT revision FROM environment_states WHERE execution_id=%s", (eid,)
            ).fetchone()["revision"]
            == 4
        )
        assert c.execute(
            "SELECT payload FROM working_memory WHERE execution_id=%s", (eid,)
        ).fetchone()["payload"]["attempts"]


@pytest.mark.parametrize("sid", ["latency-team", "errors-team", "outage-team"])
def test_environment_determinism_and_recovery_not_acknowledgment(sid):
    state = sim.initialize(sid)
    assert state == sim.initialize(sid)
    target = {"latency": "pool", "errors": "release", "outage": "inventory"}[
        sid.split("-")[0]
    ]
    action = {v: k for k, v in sim.ACTION_TARGET.items()}[target]
    state, kind, data = sim.execute(
        state, "probe", {"revision": 0, "target": target}, []
    )
    obs = [{"id": "probe", "kind": kind, "data": data}]
    state, _, _ = sim.execute(
        state, "act", {"revision": 1, "action": action, "evidence_ids": ["probe"]}, obs
    )
    assert not sim.recovered(state)
    state, _, _ = sim.execute(state, "read_metrics", {"revision": 2}, obs)
    assert not sim.recovered(state)
    state, _, _ = sim.execute(state, "read_metrics", {"revision": 3}, obs)
    assert sim.recovered(state)


def test_invalid_revision_and_unprobed_action_do_not_mutate():
    state = sim.initialize("latency-team")
    old = dict(state)
    with pytest.raises(ValueError):
        sim.execute(
            state,
            "act",
            {"revision": 0, "action": "restart_pool", "evidence_ids": ["invented"]},
            [],
        )
    with pytest.raises(ValueError):
        sim.execute(state, "read_metrics", {"revision": 9}, [])
    assert state == old


def test_wrong_action_has_real_simulated_consequence():
    state = sim.initialize("errors-team")
    state, kind, data = sim.execute(
        state, "probe", {"revision": 0, "target": "pool"}, []
    )
    state, _, data = sim.execute(
        state,
        "act",
        {"revision": 1, "action": "restart_pool", "evidence_ids": ["p"]},
        [{"id": "p", "kind": kind, "data": data}],
    )
    assert not sim.recovered(state) and state["inventory_down"] and state["bad_release"]


def test_tool_rejection_and_unknown_report_references(client):
    eid = submit(client)
    with db.connect() as c:
        db.transition(c, eid, "running")
    agent = Investigator(eid, "latency-team", ScriptedProvider("latency-team"))
    agent.init()
    plan = agent.plan(
        PlanInput(
            hypotheses=[{"cause": "Observed latency", "observation_ids": ["obs-000"]}],
            steps=[{"tool": "probe", "purpose": "Probe before acting"}],
        ),
        "planner",
    )
    agent.operate("read_metrics", ReadInput(revision=0), plan)
    assert agent.invalid == 1 and agent.state["revision"] == 0
    with pytest.raises(ValueError):
        agent.finish(
            FinishInput(
                diagnosis="unknown",
                summary="Unresolved",
                uncertainties=[],
                observation_ids=["invented"],
            )
        )
    data = client.get("/demo/v1/executions/" + eid).json()
    assert any(e["kind"] == "validation_rejected" for e in data["events"])


def test_unresolved_report_is_completed_but_domain_failed(client):
    eid = submit(client)
    with db.connect() as c:
        db.transition(c, eid, "running")
    agent = Investigator(eid, "latency-team", ScriptedProvider("latency-team"))
    agent.init()
    r = agent.finish(
        FinishInput(
            diagnosis="unknown",
            summary="Insufficient investigation",
            uncertainties=["Need diagnostics"],
            observation_ids=["obs-000"],
        )
    )
    assert not r.recovered and not r.diagnosis_correct
    data = client.get("/demo/v1/executions/" + eid).json()
    assert data["status"] == "succeeded"
    assert (
        next(e for e in data["evaluations"] if e["category"] == "domain")["status"]
        == "failed"
    )


def test_budgets_and_terminal_guard(client):
    eid = submit(client)
    with db.connect() as c:
        db.transition(c, eid, "running")
    agent = Investigator(eid, "latency-team", ScriptedProvider("latency-team"))
    agent.init()
    agent.usage["model_calls"] = 20
    with pytest.raises(BudgetExceeded):
        agent.call("planner", {"record_plan": PlanInput})
    with db.connect() as c:
        db.transition(c, eid, "cancelled")
    with pytest.raises(BudgetExceeded):
        agent.guard()


def test_scoped_api_idempotency_discovery_and_readiness(client, monkeypatch):
    assert (
        client.get(
            "/demo/v1/capabilities", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert client.get("/demo/v1/capabilities").json()["integration_version"] == "1.2"
    public = client.get("/demo/v1/scenarios").text
    assert "pool_exhausted" not in public and "bad_release" not in public
    eid = submit(client)
    body = {"id": eid, "scenario_id": "latency-team"}
    assert client.post("/demo/v1/executions", json=body).json()["id"] == eid
    assert (
        client.post(
            "/demo/v1/executions", json={**body, "scenario_id": "errors-team"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/demo/v1/executions", json={**body, "prompt": "override"}
        ).status_code
        == 422
    )
    assert client.post("/demo/v1/executions", content="x" * 5000).status_code == 413
    monkeypatch.setenv("MODEL_EXECUTION_ENABLED", "false")
    assert (
        client.post(
            "/demo/v1/executions",
            json={"id": str(uuid.uuid4()), "scenario_id": "latency-team"},
        ).status_code
        == 503
    )


def test_admission_quota_and_queued_cancellation(client):
    ids = [submit(client) for _ in range(4)]
    assert (
        client.post(
            "/demo/v1/executions",
            json={"id": str(uuid.uuid4()), "scenario_id": "latency-team"},
        ).status_code
        == 429
    )
    assert (
        client.post("/demo/v1/executions/" + ids[0] + "/cancel", json={}).json()[
            "status"
        ]
        == "cancelled"
    )
    with pytest.raises(ValueError):
        with db.connect() as c:
            db.transition(c, ids[0], "running")


@pytest.mark.parametrize("outcome", ["timed_out", "cancelled", "failed"])
def test_actual_subprocess_deadline_cancel_failure(client, monkeypatch, outcome):
    eid = submit(client)
    with db.connect() as c:
        db.transition(c, eid, "running")
        if outcome == "cancelled":
            c.execute("UPDATE executions SET cancel_requested=true WHERE id=%s", (eid,))
    monkeypatch.setattr(worker, "STOP", False)
    original = subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        p = original(
            [
                sys.executable,
                "-c",
                (
                    "import time; time.sleep(10)"
                    if outcome != "failed"
                    else "raise SystemExit(1)"
                ),
            ],
            **kwargs
        )
        children.append(p)
        return p

    monkeypatch.setattr(worker.subprocess, "Popen", spawn)
    worker.execute(eid, timeout_seconds=0.15)
    assert client.get("/demo/v1/executions/" + eid).json()["status"] == outcome
    assert all(p.poll() is not None for p in children)


def test_restart_interrupts_without_replaying_model(client, monkeypatch):
    eid = submit(client)
    with db.connect() as c:
        db.transition(c, eid, "running")
    monkeypatch.setattr(worker, "STOP", False)

    def stop(*args):
        worker.STOP = True

    monkeypatch.setattr(worker.time, "sleep", stop)
    monkeypatch.setenv("MODEL_EXECUTION_ENABLED", "false")
    worker.main()
    assert (
        client.get("/demo/v1/executions/" + eid).json()["failure_code"]
        == "interrupted_no_automatic_retry"
    )


def test_provider_wire_protocol_and_sanitized_errors(client):
    def handler(req):
        payload = json.loads(req.content)
        assert payload["tool_choice"] == {
            "type": "auto",
            "disable_parallel_tool_use": True,
        }
        assert payload["model"] == "test-model-not-production"
        return httpx.Response(
            200,
            json={
                "stop_reason": "tool_use",
                "model": "TEST-ONLY",
                "content": [
                    {
                        "id": "one",
                        "type": "tool_use",
                        "name": "read_metrics",
                        "input": {"revision": 0},
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        )

    p = AnthropicProvider(httpx.MockTransport(handler))
    assert (
        p.complete(
            "Test",
            [{"role": "user", "content": "Test"}],
            [tool_schema("read_metrics", ReadInput)],
        )["calls"][0]["name"]
        == "read_metrics"
    )
    p.close()
    p = AnthropicProvider(
        httpx.MockTransport(lambda req: httpx.Response(401, text="private-key-detail"))
    )
    with pytest.raises(ProviderFailure) as exc:
        p.complete("Test", [], [])
    assert "private-key-detail" not in str(exc.value)
    p.close()


def test_model_cannot_set_recovery_or_grade():
    with pytest.raises(ValueError):
        FinishInput(
            diagnosis="unknown",
            summary="Test",
            uncertainties=[],
            observation_ids=["obs-000"],
            recovered=True,
        )
