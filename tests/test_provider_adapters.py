import json
import httpx
import pytest
from incidentlab.provider_adapters import OpenRouterAdapter, InvalidToolOutput
from incidentlab.provider import AnthropicProvider, ModelOutputRejected, ProviderFailure
from incidentlab import config


def test_native_tool_results_and_reasoning_round_trip():
    adapter=OpenRouterAdapter()
    value={'choices':[{'message':{'tool_calls':[{'type':'function','id':'one','function':{'name':'read','arguments':'{"revision":0}'}}], 'reasoning_details':[{'type':'reasoning.encrypted','data':'opaque'}]}}], 'usage':{'prompt_tokens':7,'completion_tokens':8}}
    content,calls,usage=adapter.response(value)
    assert calls[0]['input']=={'revision':0}
    assert usage=={'input_tokens':7,'output_tokens':8}
    original=[{'role':'assistant','content':content},{'role':'user','content':[{'type':'tool_result','tool_use_id':'one','content':'rejected','is_error':True},{'type':'text','text':'budget reminder'}]}]
    payload=adapter.request('model','system',original,[],4096,False)
    assert payload['messages'][1]['reasoning_details']==value['choices'][0]['message']['reasoning_details']
    assert payload['messages'][2]=={'role':'tool','tool_call_id':'one','content':'Tool validation error: rejected'}
    assert payload['messages'][3]['content']=='budget reminder'
    assert original[1]['content'][0]['content']=='rejected'
    assert payload['parallel_tool_calls'] is False
    assert 'provider' not in payload  # Do not exclude viable endpoints with incomplete parameter metadata.


@pytest.mark.parametrize('arguments',['not-json','[]','null'])
def test_invalid_tool_arguments_rejected(arguments):
    value={'choices':[{'message':{'tool_calls':[{'type':'function','id':'t','function':{'name':'read','arguments':arguments}}]}}]}
    with pytest.raises(InvalidToolOutput):OpenRouterAdapter().response(value)


def test_upstream_schema_rejection_is_not_a_transport_failure(monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openrouter')
    monkeypatch.setattr(config,'provider_key',lambda:'secret-test-key')
    response={'error':{'message':'Tool call validation failed: secret-test-key', 'metadata':{'provider_name':'Groq'}}}
    p=AnthropicProvider(httpx.MockTransport(lambda r:httpx.Response(502,json=response,headers={'x-generation-id':'gen-unit-test'})))
    try:
        with pytest.raises(ModelOutputRejected) as error:p.complete('system',[],[])
        assert error.value.category=='upstream_tool_schema'
        assert error.value.diagnostic=={'http_status':502,'provider':'Groq','generation_id':'gen-unit-test'}
        assert 'secret-test-key' not in str(error.value)
    finally:p.close()


def test_real_server_error_stays_provider_failure(monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openrouter')
    p=AnthropicProvider(httpx.MockTransport(lambda r:httpx.Response(502,json={'error':{'message':'server overloaded'}})))
    try:
        with pytest.raises(ProviderFailure) as error:p.complete('system',[],[])
        assert not isinstance(error.value,ModelOutputRejected)
        assert error.value.category=='transient_http_502'
    finally:p.close()
