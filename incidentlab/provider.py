"""Bounded provider client with explicit Anthropic and native OpenRouter adapters."""
import json
import re
import time
import httpx
from . import config
from .provider_adapters import AnthropicAdapter, OpenRouterAdapter, InvalidToolOutput


class ProviderFailure(RuntimeError):
    def __init__(self, message="Model request failed; provider details withheld", category="provider_failure", diagnostic=None):
        self.category = category
        self.diagnostic = diagnostic or {}
        super().__init__(message)


class ModelOutputRejected(ProviderFailure):
    pass


class RetryableProviderFailure(ProviderFailure):
    pass


def safe_diagnostic(response, value):
    error = value.get("error", {}) if isinstance(value, dict) else {}
    metadata = error.get("metadata", {}) if isinstance(error, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    result = {"http_status": response.status_code}
    for name, field in [("generation_id", response.headers.get("x-generation-id") or value.get("id")),
                        ("provider", metadata.get("provider_name") or value.get("provider"))]:
        if isinstance(field, str) and re.fullmatch(r"[A-Za-z0-9_. /-]{1,120}", field):
            result[name] = field
    return result


class ModelProvider:
    parallel = False

    def __init__(self, transport=None):
        provider = config.model_provider()
        adapters = {"anthropic": AnthropicAdapter, "openrouter": OpenRouterAdapter}
        if provider not in adapters:
            raise ProviderFailure(category="unsupported_provider")
        self.adapter = adapters[provider]()
        self.timeout_seconds, self.output_tokens, self.response_limit = 25, 2400, 128000
        self.client = httpx.Client(base_url=self.adapter.origin, headers=self.adapter.headers(config.provider_key()),
            timeout=httpx.Timeout(self.timeout_seconds, connect=5), follow_redirects=False, trust_env=False, transport=transport)

    def complete(self, system, messages, tools):
        started = time.monotonic()
        payload = self.adapter.request(config.model(), system, messages, tools, self.output_tokens, self.parallel)
        try:
            with self.client.stream("POST", self.adapter.path, json=payload) as response:
                parts, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.response_limit:
                        raise ProviderFailure(category="response_size")
                    parts.append(chunk)
                try:
                    value = json.loads(b"".join(parts))
                except ValueError:
                    if response.status_code in {408, 502, 503, 504}:
                        raise RetryableProviderFailure(category="transient_http_" + str(response.status_code)) from None
                    raise ProviderFailure(category="http_status_" + str(response.status_code) if response.is_error else "response_json") from None
                if not isinstance(value, dict):
                    raise ProviderFailure(category="response_validation")
                diagnostic = safe_diagnostic(response, value)
                if response.is_error or value.get("error"):
                    # Inspect privately; never expose raw error bodies, generated arguments or credentials.
                    error = value.get("error", {})
                    text = json.dumps(error).lower()
                    markers = ("tool call validation", "tool_use_failed", "failed to call a function", "did not match schema")
                    if response.status_code in {400, 422, 502} and any(m in text for m in markers):
                        raise ModelOutputRejected(category="upstream_tool_schema", diagnostic=diagnostic)
                    if response.status_code in {408, 502, 503, 504}:
                        raise RetryableProviderFailure(category="transient_http_" + str(response.status_code), diagnostic=diagnostic)
                    raise ProviderFailure(category="http_status_" + str(response.status_code), diagnostic=diagnostic)
                try:
                    content, calls, usage = self.adapter.response(value)
                except (InvalidToolOutput, KeyError, ValueError, TypeError):
                    raise ModelOutputRejected(category="invalid_tool_response", diagnostic=diagnostic) from None
            return {"content": content, "calls": calls, "usage": {
                "input_tokens": int(usage.get("input_tokens", 0)), "output_tokens": int(usage.get("output_tokens", 0))},
                "model": str(value.get("model", config.model())), "latency_ms": int((time.monotonic()-started)*1000)}
        except httpx.TimeoutException:
            raise RetryableProviderFailure(category="request_timeout") from None
        except httpx.HTTPError:
            raise ProviderFailure(category="transport_failure") from None
        except (KeyError, TypeError, ValueError):
            raise ProviderFailure(category="response_validation") from None

    def close(self):
        self.client.close()


# Backward-compatible import for existing runner/tests; wire format is selected by adapter.
AnthropicProvider = ModelProvider

def create_provider(transport=None):
    return ModelProvider(transport)
