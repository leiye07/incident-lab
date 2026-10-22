"""Small documented Messages API boundary. No fake provider in production."""

import json, time
import httpx
from . import config


class ProviderFailure(RuntimeError):
    pass


class AnthropicProvider:
    def __init__(self, transport=None):
        self.client = httpx.Client(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": config.provider_key(),
                "anthropic-version": "2023-06-01",
            },
            timeout=httpx.Timeout(25, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def complete(self, system, messages, tools):
        started = time.monotonic()
        try:
            with self.client.stream(
                "POST",
                "/v1/messages",
                json={
                    "model": config.model(),
                    "max_tokens": 2400,
                    "system": system,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
                },
            ) as response:
                response.raise_for_status()
                parts = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 128000:
                        raise ProviderFailure("Provider response exceeded limit")
                    parts.append(chunk)
                value = json.loads(b"".join(parts))
            if value.get("stop_reason") != "tool_use":
                raise ProviderFailure("Provider did not complete a tool call")
            content = value["content"]
            if not isinstance(content, list) or len(content) > 10:
                raise ProviderFailure("Invalid provider response")
            calls = [c for c in content if c.get("type") == "tool_use"]
            if not calls:
                raise ProviderFailure("Missing tool call")
            usage = value.get("usage", {})
            return {
                "content": content,
                "calls": calls,
                "usage": {
                    "input_tokens": int(usage.get("input_tokens", 0)),
                    "output_tokens": int(usage.get("output_tokens", 0)),
                },
                "model": str(value.get("model", config.model())),
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            raise ProviderFailure(
                "Model request failed; provider details withheld"
            ) from None

    def close(self):
        self.client.close()
