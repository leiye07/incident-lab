import os
from pathlib import Path


def secret(name, default):
    try:
        return Path(os.getenv(name, default)).read_text().strip()
    except OSError:
        return ""


def demo_token():
    return secret("DEMO_TOKEN_FILE", "/run/secrets/demo_token")


def provider_key():
    return secret("MODEL_API_KEY_FILE", os.getenv("ANTHROPIC_API_KEY_FILE", "/run/secrets/anthropic_api_key"))


def model():
    return os.getenv("INCIDENTLAB_MODEL", "").strip()


def configured():
    return (
        os.getenv("MODEL_EXECUTION_ENABLED", "false").lower() == "true"
        and bool(model())
        and bool(provider_key())
        and len(demo_token()) >= 32
    )


def admission_configured():
    # API does not need the provider key; worker readiness certifies its configuration.
    return (
        os.getenv("MODEL_EXECUTION_ENABLED", "false").lower() == "true"
        and bool(model())
        and len(demo_token()) >= 32
    )


def model_provider():
    return os.getenv("MODEL_PROVIDER", "anthropic").strip().lower()
