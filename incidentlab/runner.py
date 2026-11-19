import sys, uuid
from . import db
from .engine import run, BudgetExceeded, ModelOutputError
from pydantic import ValidationError
from .provider import create_provider, ProviderFailure, ModelOutputRejected


def main(eid):
    provider = create_provider()
    try:
        run(eid, provider)
    except Exception as exc:
        code = (
            "budget_exhausted"
            if isinstance(exc, BudgetExceeded)
            else (
                "provider_failure"
                if isinstance(exc, ProviderFailure)
                else ("invalid_model_output" if isinstance(exc, (ModelOutputError, ValidationError, ModelOutputRejected)) else "execution_error")
            )
        )
        # Never persist exception strings, model responses, credentials or raw prompts.
        category = (
            exc.category if isinstance(exc, ModelOutputError)
            else "schema_validation" if isinstance(exc, ValidationError)
            else "budget_exhausted" if isinstance(exc, BudgetExceeded)
            else exc.category if isinstance(exc, ProviderFailure)
            else "value_error" if isinstance(exc, ValueError)
            else "unexpected_runtime_error"
        )
        with db.connect() as c:
            db.append(c, eid, "validation_rejected", {
                "actor": "runtime", "tool": "unrecognized",
                "reason": "Execution stopped: " + category + "; private details withheld.",
            })
            c.execute(
                "UPDATE executions SET failure_code=%s WHERE id=%s AND status='running'",
                (code, eid),
            )
        raise SystemExit(1) from None
    finally:
        provider.close()


if __name__ == "__main__":
    main(uuid.UUID(sys.argv[1]))
