import sys, uuid
from . import db
from .engine import run, BudgetExceeded
from .provider import AnthropicProvider, ProviderFailure


def main(eid):
    provider = AnthropicProvider()
    try:
        run(eid, provider)
    except Exception as exc:
        code = (
            "budget_exhausted"
            if isinstance(exc, BudgetExceeded)
            else (
                "provider_failure"
                if isinstance(exc, ProviderFailure)
                else "execution_error"
            )
        )
        with db.connect() as c:
            c.execute(
                "UPDATE executions SET failure_code=%s WHERE id=%s AND status='running'",
                (code, eid),
            )
        raise SystemExit(1) from None
    finally:
        provider.close()


if __name__ == "__main__":
    main(uuid.UUID(sys.argv[1]))
