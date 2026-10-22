import os, uuid
import pytest
from fastapi.testclient import TestClient
from incidentlab import db
from incidentlab.api import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("Disposable PostgreSQL TEST_DATABASE_URL required")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    token = tmp_path / "demo"
    token.write_text("test-only-" + "x" * 40)
    key = tmp_path / "provider"
    key.write_text("TEST-ONLY-NOT-A-REAL-KEY")
    monkeypatch.setenv("DEMO_TOKEN_FILE", str(token))
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(key))
    monkeypatch.setenv("MODEL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("INCIDENTLAB_MODEL", "test-model-not-production")
    db.migrate()
    with db.connect() as c:
        c.execute(
            "TRUNCATE tool_operations,working_memory,plans,observations,environment_states,evidence_events,executions,runtime_health CASCADE"
        )
        c.execute(
            "INSERT INTO runtime_health(id,execution_ready) VALUES('worker',true)"
        )
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers["Authorization"] = "Bearer " + token.read_text()
        yield client


def submit(client, scenario="latency-team"):
    eid = str(uuid.uuid4())
    r = client.post("/demo/v1/executions", json={"id": eid, "scenario_id": scenario})
    assert r.status_code == 202, r.text
    return eid
