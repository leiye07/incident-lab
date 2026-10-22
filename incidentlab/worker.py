"""Application-owned controller; the showroom never executes agent workflows."""

import signal, subprocess, sys, time, threading
from . import db, config

STOP = False
CHILD = None


def stop(*_):
    global STOP
    STOP = True
    if CHILD and CHILD.poll() is None:
        CHILD.kill()


def execute(eid, timeout_seconds=180, guard=None):
    global CHILD
    expired = threading.Event()
    process = subprocess.Popen(
        [sys.executable, "-m", "incidentlab.runner", str(eid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    CHILD = process

    def deadline():
        if process.poll() is None:
            expired.set()
            try:
                process.kill()
            except ProcessLookupError:
                pass

    timer = threading.Timer(timeout_seconds, deadline)
    timer.daemon = True
    timer.start()
    outcome = None
    try:
        while process.poll() is None:
            if guard:
                guard()
            with db.connect() as c:
                cancel = c.execute(
                    "SELECT cancel_requested FROM executions WHERE id=%s", (eid,)
                ).fetchone()["cancel_requested"]
                c.execute(
                    "INSERT INTO runtime_health(id,execution_ready) VALUES ('worker',%s) ON CONFLICT(id) DO UPDATE SET last_seen=now(),execution_ready=excluded.execution_ready",
                    (config.configured(),),
                )
            if STOP or cancel:
                outcome = "cancelled"
                process.kill()
                break
            time.sleep(0.2)
        process.wait(timeout=5)
        if expired.is_set():
            outcome = "timed_out"
        elif STOP:
            outcome = "cancelled"
        with db.connect() as c:
            state = c.execute(
                "SELECT status,failure_code FROM executions WHERE id=%s FOR UPDATE",
                (eid,),
            ).fetchone()
            if state["status"] == "running":
                db.transition(
                    c,
                    eid,
                    outcome or "failed",
                    state["failure_code"] or "execution_stopped",
                )
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        CHILD = None


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    db.migrate()
    import psycopg, os

    lock = psycopg.connect(
        os.environ["DATABASE_URL"], autocommit=True, connect_timeout=3
    )
    if not lock.execute("SELECT pg_try_advisory_lock(763303)").fetchone()[0]:
        raise RuntimeError("Another worker holds controller lock")
    with db.connect() as c:
        rows = c.execute(
            "SELECT id FROM executions WHERE status='running' FOR UPDATE"
        ).fetchall()
        for row in rows:
            db.transition(c, row["id"], "failed", "interrupted_no_automatic_retry")
    try:
        while not STOP:
            lock.execute("SELECT 1")
            with db.connect() as c:
                c.execute(
                    "INSERT INTO runtime_health(id,execution_ready) VALUES ('worker',%s) ON CONFLICT(id) DO UPDATE SET last_seen=now(),execution_ready=excluded.execution_ready",
                    (config.configured(),),
                )
                row = (
                    c.execute(
                        "SELECT id FROM executions WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                    ).fetchone()
                    if config.configured()
                    else None
                )
                if row:
                    db.transition(c, row["id"], "running")
            if row:
                execute(row["id"], guard=lambda: lock.execute("SELECT 1"))
            else:
                time.sleep(0.5)
    finally:
        if CHILD and CHILD.poll() is None:
            CHILD.kill()
            CHILD.wait(timeout=5)
        lock.close()


if __name__ == "__main__":
    main()
