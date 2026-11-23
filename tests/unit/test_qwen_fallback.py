import httpx
import pytest
from vimfc.pipeline.qwen_fallback import QwenFallback

CONFIG=dict(endpoint='http://127.0.0.1:18865',model_id='unsloth/Qwen3.5-4B-GGUF',
 model_revision='e87f176479d0855a907a41277aca2f8ee7a09523',base_model='Qwen/Qwen3.5-4B')
ARTICLE=dict(title='A',extraction=dict(full_lead='A là một nhà khoa học.'))

@pytest.mark.parametrize('body,group,status',[
 ('{"group":"Person","uncertainty":false}','Person','suggested'),
 ('{"group":"Person","uncertainty":true}',None,'unknown'),
 ('{"group":"Unknown","uncertainty":false}',None,'unknown'),
 ('{"group":"Fake","uncertainty":false}',None,'failed'),
 ('{"group":"Person","uncertainty":"false"}',None,'failed'),
 ('not JSON',None,'failed'),
])
def test_schema_and_uncertainty_keep_qwen_separate(body,group,status):
    gate=QwenFallback(CONFIG,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={
        'choices':[{'message':{'content':body}}]})))
    result,raw=gate.classify(ARTICLE)
    assert result['suggested_primary_entity_type']==group and result['classification_status']==status
    assert result['classification_source']=='qwen_suggestion' and result['model_revision']==CONFIG['model_revision']
    assert raw['request']['messages'][-1]['content'] and 'subtypes' not in result
    assert raw['request']['chat_template_kwargs']=={'enable_thinking':False}
    gate.close()

def test_model_failure_does_not_drop_article():
    def fail(request):raise httpx.ConnectError('model unavailable')
    gate=QwenFallback(CONFIG,transport=httpx.MockTransport(fail))
    result,_=gate.classify(ARTICLE)
    assert result['classification_status']=='failed' and result['suggested_primary_entity_type'] is None
    assert ARTICLE['extraction']['full_lead']=='A là một nhà khoa học.'
    gate.close()

def test_no_external_inference_endpoint():
    with pytest.raises(ValueError):QwenFallback(dict(CONFIG,endpoint='https://external.invalid'))
