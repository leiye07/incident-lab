import os, json, uuid
from pathlib import Path
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
TRANSITIONS = {
    "created": {"queued"},
    "queued": {"running", "cancelled", "failed", "timed_out"},
    "running": TERMINAL,
}


@contextmanager
def connect():
    with psycopg.connect(
        os.environ["DATABASE_URL"],
        row_factory=dict_row,
        connect_timeout=3,
        options="-c statement_timeout=3000 -c lock_timeout=3000",
    ) as c:
        yield c


def append(c, eid, kind, data, status=None):
    row = c.execute(
        "SELECT next_seq FROM executions WHERE id=%s FOR UPDATE", (eid,)
    ).fetchone()
    if row["next_seq"] > 200:
        raise ValueError("Evidence budget exceeded")
    c.execute(
        "INSERT INTO evidence_events(execution_id,seq,kind,status,data) VALUES (%s,%s,%s,%s,%s)",
        (eid, row["next_seq"], kind, status, Jsonb(data)),
    )
    c.execute("UPDATE executions SET next_seq=next_seq+1 WHERE id=%s", (eid,))


def record(eid, kind, data):
    if len(json.dumps(data).encode()) > 24000:
        raise ValueError("Evidence payload too large")
    with connect() as c:
        row = c.execute(
            "SELECT status FROM executions WHERE id=%s FOR UPDATE", (eid,)
        ).fetchone()
        if row["status"] != "running":
            raise RuntimeError("Execution is not running")
        append(c, eid, kind, data)


def transition(c, eid, target, reason=None):
    row = c.execute(
        "SELECT status FROM executions WHERE id=%s FOR UPDATE", (eid,)
    ).fetchone()
    if target not in TRANSITIONS.get(row["status"], set()):
        raise ValueError("Invalid execution transition")
    c.execute(
        "UPDATE executions SET status=%s,started_at=CASE WHEN %s='running' THEN now() ELSE started_at END,completed_at=CASE WHEN %s IN ('succeeded','failed','cancelled','timed_out') THEN now() ELSE completed_at END,failure_code=%s WHERE id=%s",
        (target, target, target, reason, eid),
    )
    append(
        c, eid, "state_transition", {"before": row["status"], "after": target}, target
    )


def migrate():
    with connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(763300)")
        for file in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            c.execute(file.read_text())
