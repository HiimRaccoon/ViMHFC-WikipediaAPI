"""Pinned CPU-only Qwen tokenization; estimates are never provider actual usage."""
import hashlib,json
from functools import lru_cache
from pathlib import Path
from .store import Pause

MODEL='qwen/qwen3.8-27b'
REVISION='366a23fd246985933964e9d21ac63eb3a93190ab'
FILE_HASHES={
 'tokenizer.json':'0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3',
 'tokenizer_config.json':'b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27',
}
ROOT=Path(__file__).resolve().parents[5]
DEFAULT_MANIFEST=ROOT/'data/tokenizers/qwen3.8-27b-366a23f/manifest.json'


def estimate_components(payload,*,chat_text,count):
 """Count the chat template and full grammar description; reserve max output.

 Groq may wrap schemas differently. Counting the complete response-format JSON
 is a declared estimate for that overhead, not an assertion about their template.
 """
 output=payload.get('max_completion_tokens')
 if type(output) is not int or output<=0:raise ValueError('Finite output reservation required')
 chat=count(chat_text)
 schema=count(json.dumps(payload.get('response_format',{}),ensure_ascii=False,sort_keys=True,separators=(',',':')))
 return {'format':'vimfc-token-estimate-1','method':'pinned-qwen-chat-template-plus-schema',
  'chat_template_tokens':chat,'schema_tokens':schema,'estimated_prompt_tokens':chat+schema,
  'output_reserved_tokens':output,'reserved_tokens':chat+schema+output,'actual_tokens':None,
  'estimate_is_actual':False,'provider_template_revision_pinned':False}


class QwenTokenEstimator:
 def __init__(self,manifest_path=DEFAULT_MANIFEST):
  try:
   path=Path(manifest_path);manifest=json.loads(path.read_text(encoding='utf-8'))
   if (manifest.get('format')!='vimfc-groq-tokenizer-1' or manifest.get('model_id')!=MODEL
       or manifest.get('revision')!=REVISION or manifest.get('repository')!='Qwen/Qwen3.8-27B'):
    raise ValueError('Tokenizer identity differs')
   content={}
   for name,expected in FILE_HASHES.items():
    data=(path.parent/name).read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected or manifest['files'][name]['sha256']!=expected:
     raise ValueError('Tokenizer checksum differs')
    content[name]=data.decode('utf-8')
   from tokenizers import Tokenizer
   from jinja2 import StrictUndefined
   from jinja2.sandbox import ImmutableSandboxedEnvironment
   env=ImmutableSandboxedEnvironment(trim_blocks=True,lstrip_blocks=True,undefined=StrictUndefined,extensions=['jinja2.ext.loopcontrols'])
   def fail(message):raise ValueError('Unsupported chat template input')
   env.globals['raise_exception']=fail
   env.filters['tojson']=lambda value,**kwargs:json.dumps(value,ensure_ascii=False,**{k:v for k,v in kwargs.items() if k!='ensure_ascii'})
   # Explicitly use the official config's embedded template. The standalone
   # template at this repo commit contains an undefined upstream test; never
   # patch it or guess a replacement test at runtime.
   template=json.loads(content['tokenizer_config.json'])['chat_template']
   if hashlib.sha256(template.encode('utf-8')).hexdigest()!='c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041':
    raise ValueError('Embedded template checksum differs')
   self.template=env.from_string(template)
   self.tokenizer=Tokenizer.from_str(content['tokenizer.json'])
   self.manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
  except (OSError,ValueError,KeyError,ImportError) as error:
   raise Pause('tokenizer_unavailable_or_tampered') from None

 def estimate(self,payload):
  if payload.get('model')!=MODEL:raise Pause('tokenizer_model_unsupported')
  messages=payload.get('messages',[])
  if not messages or any(m.get('role') not in ('system','user') or not isinstance(m.get('content'),str) for m in messages):
   raise Pause('tokenizer_input_unsupported')
  try:
   rendered=self.template.render(messages=messages,tools=None,add_generation_prompt=True,
    enable_thinking=payload.get('reasoning_effort')!='none',preserve_thinking=False,
    bos_token='',eos_token='<|im_end|>')
   estimate=estimate_components(payload,chat_text=rendered,count=lambda value:len(self.tokenizer.encode(value,add_special_tokens=False).ids))
  except Exception:raise Pause('tokenizer_render_failed') from None
  return {**estimate,'model_id':MODEL,'tokenizer_repository':'Qwen/Qwen3.8-27B','tokenizer_revision':REVISION,
   'tokenizer_sha256':FILE_HASHES['tokenizer.json'],'tokenizer_manifest_sha256':self.manifest_sha256,
   'chat_template_source':'tokenizer_config.json#chat_template'}


@lru_cache(maxsize=2)
def _estimator(manifest_path):return QwenTokenEstimator(manifest_path)


def estimate_request(payload,manifest_path=None):
 # No inference-time downloads, remote Python code, model weights or GPU.
 return _estimator(str(manifest_path or DEFAULT_MANIFEST)).estimate(payload)
