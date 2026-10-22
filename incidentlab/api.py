import hashlib, hmac, json, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from psycopg.types.json import Jsonb
from . import config, db, environment
from .contracts import StartRequest, Execution
from .engine import versions


@asynccontextmanager
async def lifespan(app):
    db.migrate()
    yield


app = FastAPI(
    title="IncidentLab demo integration",
    version="1.2",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)
bearer = HTTPBearer(auto_error=False)


def auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    token = config.demo_token()
    if (
        len(token) < 32
        or not credentials
        or not hmac.compare_digest(credentials.credentials, token)
    ):
        raise HTTPException(401, "Scoped integration credential required")


@app.middleware("http")
async def size_limit(request, call_next):
    if request.method == "POST":
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 4096:
                return JSONResponse({"detail": "Request too large"}, 413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def safe_error(request, exc):
    return JSONResponse({"detail": "IncidentLab temporarily unavailable"}, 503)


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/readyz")
def ready():
    with db.connect() as c:
        c.execute("SELECT version FROM schema_migrations WHERE version=1").fetchone()
    return {"status": "ready"}


def worker_ready(c):
    return bool(
        c.execute(
            "SELECT 1 FROM runtime_health WHERE id='worker' AND execution_ready AND last_seen>now()-interval '15 seconds'"
        ).fetchone()
    )


@app.get("/demo/v1/capabilities", dependencies=[Depends(auth)])
def capabilities():
    with db.connect() as c:
        ready = worker_ready(c)
    return {
        "integration_version": "1.2",
        "application_id": "incidentlab",
        "scenario_ids": list(environment.SCENARIOS),
        "cancellation": True,
        "execution_ready": config.admission_configured() and ready,
        "artifact_type": "incident_report",
        "artifact_version": "1.0",
        "evidence_version": "1.2",
        "runtime_code_sha": hashlib.sha256(
            b"".join(
                p.read_bytes()
                for p in sorted(
                    __import__("pathlib").Path(__file__).parent.glob("*.py")
                )
            )
        ).hexdigest(),
    }


@app.get("/demo/v1/scenarios", dependencies=[Depends(auth)])
def scenarios():
    return [
        {
            "id": sid,
            "name": value["name"],
            "topology": value["topology"],
            "simulated": True,
            "revision": "1",
            "parameters": {},
        }
        for sid, value in environment.SCENARIOS.items()
    ]


def execution(c, eid):
    row = c.execute("SELECT * FROM executions WHERE id=%s", (eid,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown execution")
    events = c.execute(
        "SELECT seq,occurred_at,kind,status,data FROM evidence_events WHERE execution_id=%s ORDER BY seq",
        (eid,),
    ).fetchall()
    return Execution(
        id=eid,
        status=row["status"],
        events=events,
        result=row["result"],
        evaluations=row["evaluations"],
        failure_code=row["failure_code"],
    ).model_dump(mode="json")


@app.post("/demo/v1/executions", dependencies=[Depends(auth)], status_code=202)
def start(body: StartRequest):
    fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    with db.connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(763302)")
        row = c.execute(
            "SELECT fingerprint FROM executions WHERE id=%s", (body.id,)
        ).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                raise HTTPException(
                    409, "Execution ID already used for another request"
                )
            return execution(c, body.id)
        if not config.admission_configured() or not worker_ready(c):
            raise HTTPException(
                503, "Model execution disabled, unconfigured or worker offline"
            )
        counts = c.execute(
            "SELECT count(*) FILTER(WHERE status IN ('queued','running')) active,count(*) FILTER(WHERE created_at>now()-interval '1 hour') hourly FROM executions"
        ).fetchone()
        if counts["active"] >= 4 or counts["hourly"] >= 60:
            raise HTTPException(429, "Execution quota reached")
        c.execute(
            "INSERT INTO executions(id,fingerprint,scenario_id,status,configuration,versions) VALUES (%s,%s,%s,'created',%s,%s)",
            (
                body.id,
                fingerprint,
                body.scenario_id,
                Jsonb(
                    {
                        "timeout_seconds": 180,
                        "model_turns": 20,
                        "tool_calls": 16,
                        "context_byte_limit": 80000,
                    }
                ),
                Jsonb(versions()),
            ),
        )
        db.transition(c, body.id, "queued")
        return execution(c, body.id)


@app.get("/demo/v1/executions/{eid}", dependencies=[Depends(auth)])
def get(eid: uuid.UUID):
    with db.connect() as c:
        return execution(c, eid)


@app.post("/demo/v1/executions/{eid}/cancel", dependencies=[Depends(auth)])
def cancel(eid: uuid.UUID):
    with db.connect() as c:
        row = c.execute(
            "SELECT status FROM executions WHERE id=%s FOR UPDATE", (eid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Unknown execution")
        if row["status"] == "queued":
            db.transition(c, eid, "cancelled")
        elif row["status"] == "running":
            c.execute("UPDATE executions SET cancel_requested=true WHERE id=%s", (eid,))
        return execution(c, eid)
