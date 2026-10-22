from pathlib import Path
import json
from incidentlab.api import app
from incidentlab.contracts import (
    StartRequest,
    Execution,
    Event,
    IncidentReport,
    PlanInput,
    Evaluation,
)

out = Path(__file__).resolve().parents[1] / "docs/contracts"
out.mkdir(parents=True, exist_ok=True)
(out / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
for model in [StartRequest, Execution, Event, IncidentReport, PlanInput, Evaluation]:
    (out / (model.__name__ + ".schema.json")).write_text(
        json.dumps(model.model_json_schema(), indent=2) + "\n"
    )
