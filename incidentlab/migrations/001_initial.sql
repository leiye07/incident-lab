CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY,applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS executions(
 id uuid PRIMARY KEY,fingerprint text NOT NULL,scenario_id text NOT NULL,
 status text NOT NULL CHECK(status IN('created','queued','running','succeeded','failed','cancelled','timed_out')),
 configuration jsonb NOT NULL,versions jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),started_at timestamptz,completed_at timestamptz,
 cancel_requested boolean NOT NULL DEFAULT false,next_seq integer NOT NULL DEFAULT 1,result jsonb,evaluations jsonb NOT NULL DEFAULT '[]',failure_code text
);
CREATE TABLE IF NOT EXISTS evidence_events(execution_id uuid REFERENCES executions(id),seq integer NOT NULL,occurred_at timestamptz NOT NULL DEFAULT now(),kind text NOT NULL,status text,data jsonb NOT NULL,PRIMARY KEY(execution_id,seq));
CREATE TABLE IF NOT EXISTS runtime_health(id text PRIMARY KEY,last_seen timestamptz NOT NULL DEFAULT now(),execution_ready boolean NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS environment_states(execution_id uuid PRIMARY KEY REFERENCES executions(id),seed integer NOT NULL,revision integer NOT NULL,state jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS observations(execution_id uuid REFERENCES executions(id),id text NOT NULL,revision integer NOT NULL,kind text NOT NULL,data jsonb NOT NULL,PRIMARY KEY(execution_id,id));
CREATE TABLE IF NOT EXISTS plans(execution_id uuid REFERENCES executions(id),revision integer NOT NULL,actor text NOT NULL,payload jsonb NOT NULL,PRIMARY KEY(execution_id,revision));
CREATE TABLE IF NOT EXISTS tool_operations(execution_id uuid REFERENCES executions(id),operation_index integer NOT NULL,plan_revision integer NOT NULL,tool text NOT NULL,arguments jsonb NOT NULL,observation_id text NOT NULL,valid boolean NOT NULL,PRIMARY KEY(execution_id,operation_index),FOREIGN KEY(execution_id,plan_revision) REFERENCES plans(execution_id,revision),FOREIGN KEY(execution_id,observation_id) REFERENCES observations(execution_id,id));
CREATE TABLE IF NOT EXISTS working_memory(execution_id uuid PRIMARY KEY REFERENCES executions(id),payload jsonb NOT NULL);
INSERT INTO schema_migrations(version) VALUES(1) ON CONFLICT DO NOTHING;
