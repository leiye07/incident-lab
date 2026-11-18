import json
import httpx
import pytest
from incidentlab.provider import AnthropicProvider, ProviderFailure
from incidentlab import config

@pytest.mark.parametrize("name,host,path,header", [
    ("anthropic", "api.anthropic.com", "/v1/messages", "x-api-key"),
    ("openrouter", "openrouter.ai", "/api/v1/messages", "authorization"),
])
def test_provider_routing_and_tool_response(monkeypatch, name, host, path, header):
    monkeypatch.setenv("MODEL_PROVIDER", name)
    monkeypatch.setattr(config, "provider_key", lambda: "unit-test-key")
    monkeypatch.setattr(config, "model", lambda: "explicit/test-model")
    def handler(request):
        assert request.url.host == host
        assert request.url.path == path
        assert request.headers[header] == ("Bearer unit-test-key" if name == "openrouter" else "unit-test-key")
        assert ("x-api-key" in request.headers) == (name == "anthropic")
        body = json.loads(request.content)
        assert body["model"] == "explicit/test-model"
        return httpx.Response(200, json={"stop_reason":"tool_use", "content":[{"type":"tool_use", "id":"t1", "name":"check", "input":{}}], "usage":{"input_tokens":3,"output_tokens":4}})
    p = AnthropicProvider(httpx.MockTransport(handler))
    try:
        result = p.complete("system", [{"role":"user","content":"test"}], [])
        assert result["calls"][0]["name"] == "check"
        assert result["usage"] == {"input_tokens":3,"output_tokens":4}
    finally:
        p.close()

def test_openrouter_errors_hide_credentials(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
    monkeypatch.setattr(config, "provider_key", lambda: "private-unit-test-key")
    p = AnthropicProvider(httpx.MockTransport(lambda request: httpx.Response(401, text="private-unit-test-key")))
    try:
        with pytest.raises(ProviderFailure) as error:
            p.complete("test", [], [])
        assert "private-unit-test-key" not in str(error.value)
    finally:
        p.close()

def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "unknown")
    with pytest.raises(ProviderFailure):
        AnthropicProvider()
