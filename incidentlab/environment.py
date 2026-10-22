"""Seeded, isolated synthetic service. No shell, network or production actions."""

import copy

SCENARIOS = {
    f"{symptom}-{mode}": {
        "name": f'{label} · {"Planner + executor" if mode=="team" else "Single-agent baseline"}',
        "symptom": symptom,
        "topology": "planner_executor" if mode == "team" else "single_agent",
        "seed": 7,
    }
    for symptom, label in [
        ("latency", "Checkout latency"),
        ("errors", "Checkout error spike"),
        ("outage", "Inventory dependency outage"),
    ]
    for mode in ["team", "solo"]
}
CAUSES = {
    "latency": "pool_exhaustion",
    "errors": "bad_release",
    "outage": "inventory_failure",
}
ACTION_TARGET = {
    "restart_pool": "pool",
    "rollback_release": "release",
    "restore_inventory": "inventory",
}


def initialize(scenario_id):
    definition = SCENARIOS[scenario_id]
    return {
        "seed": definition["seed"],
        "cause": CAUSES[definition["symptom"]],
        "pool_exhausted": definition["symptom"] == "latency",
        "bad_release": definition["symptom"] == "errors",
        "inventory_down": definition["symptom"] == "outage",
        "revision": 0,
        "steps": 0,
        "actions": 0,
        "healthy_checks": 0,
        "last_action_revision": None,
        "load": 70,
        "load_changed": False,
    }


def metrics(s):
    return {
        "service": "checkout",
        "latency_ms": (
            900 if s["pool_exhausted"] else 700 if s["inventory_down"] else 120
        ),
        "error_rate": (
            0.25 if s["bad_release"] else 0.18 if s["inventory_down"] else 0.01
        ),
        "pool_utilization_percent": 99 if s["pool_exhausted"] else 45,
        "inventory_available": not s["inventory_down"],
        "load_percent": s["load"],
        "revision": s["revision"],
        "simulated": True,
    }


def healthy(s):
    m = metrics(s)
    return m["latency_ms"] < 250 and m["error_rate"] < 0.03 and m["inventory_available"]


def execute(state, tool, args, observations):
    s = copy.deepcopy(state)
    if args["revision"] != s["revision"]:
        raise ValueError("Stale environment revision; observe and replan")
    if tool == "act":
        target = ACTION_TARGET[args["action"]]
        if not any(
            o["id"] in args["evidence_ids"]
            and o["kind"] == "probe"
            and o["data"].get("target") == target
            for o in observations
        ):
            raise ValueError("Action requires a matching observed diagnostic probe")
    s["steps"] += 1
    s["revision"] += 1
    # Exogenous change is deterministic and identical for solo/team.
    if s["cause"] == "pool_exhaustion" and s["steps"] >= 2 and not s["load_changed"]:
        s["load"] = 90
        s["load_changed"] = True
    if tool == "read_metrics":
        if s["last_action_revision"] is not None and healthy(s):
            s["healthy_checks"] += 1
        elif not healthy(s):
            s["healthy_checks"] = 0
        data = metrics(s)
        kind = "metrics"
    elif tool == "read_logs":
        entries = []
        if s["pool_exhausted"]:
            entries.append("checkout DB connection acquisition timed out")
        if s["bad_release"]:
            entries.append("checkout release r42 raises validation errors")
        if s["inventory_down"]:
            entries.append("inventory dependency request failed")
        data = {
            "entries": entries or ["checkout requests completed normally"],
            "revision": s["revision"],
        }
        kind = "logs"
    elif tool == "probe":
        target = args["target"]
        issue = {
            "pool": s["pool_exhausted"],
            "release": s["bad_release"],
            "inventory": s["inventory_down"],
        }[target]
        data = {
            "target": target,
            "issue_detected": issue,
            "detail": {
                "pool": "Connection acquisition pressure",
                "release": "Release r42 error comparison",
                "inventory": "Inventory health probe",
            }[target],
            "revision": s["revision"],
        }
        kind = "probe"
    elif tool == "act":
        action = args["action"]
        before = healthy(s)
        if action == "restart_pool":
            if not s["pool_exhausted"]:
                s["inventory_down"] = True
            s["pool_exhausted"] = False
        elif action == "rollback_release":
            if not s["bad_release"]:
                s["pool_exhausted"] = True
            s["bad_release"] = False
        elif action == "restore_inventory":
            s["inventory_down"] = False
        s["actions"] += 1
        s["healthy_checks"] = 0
        s["last_action_revision"] = s["revision"]
        data = {
            "action": action,
            "applied": True,
            "metrics_after": metrics(s),
            "was_healthy": before,
            "revision": s["revision"],
        }
        kind = "action"
    else:
        raise ValueError("Unknown simulator operation")
    return s, kind, data


def recovered(s):
    return healthy(s) and s["healthy_checks"] >= 2
