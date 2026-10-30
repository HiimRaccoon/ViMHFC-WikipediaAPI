from pathlib import Path
import pytest

@pytest.mark.parametrize('value,confirmed',[('true',True),('TRUE',True),('false',False),('',False),('1',False),('yes',False)])
def test_confirmation_is_explicit_and_not_truthiness(tmp_path,value,confirmed):
 from vimfc.experiments.groq_classification.provider import load_free_tier_confirmation
 p=tmp_path/'.env';p.write_text('GROQ_FREE_TIER_CONFIRMED='+value+'\n',encoding='utf-8')
 result=load_free_tier_confirmation(p,environ={})
 assert result['confirmed'] is confirmed and result['setting']=='GROQ_FREE_TIER_CONFIRMED'

def test_confirmation_supports_windows_dotenv_and_environment_override(tmp_path):
 from vimfc.experiments.groq_classification.provider import load_free_tier_confirmation
 p=tmp_path/'.env';p.write_bytes(b'\xef\xbb\xbfexport GROQ_FREE_TIER_CONFIRMED="true" # local\r\n')
 assert load_free_tier_confirmation(p,environ={})['confirmed'] is True
 assert load_free_tier_confirmation(p,environ={'GROQ_FREE_TIER_CONFIRMED':'false'})['confirmed'] is False

def test_confirmation_missing_duplicate_or_key_alone_does_not_authorize(tmp_path):
 from vimfc.experiments.groq_classification.provider import load_free_tier_confirmation,ProviderError
 p=tmp_path/'.env';p.write_text('GROQ_API_KEY=synthetic\n',encoding='utf-8')
 assert load_free_tier_confirmation(p,environ={})['confirmed'] is False
 p.write_text('GROQ_FREE_TIER_CONFIRMED=true\nGROQ_FREE_TIER_CONFIRMED=false\n',encoding='utf-8')
 with pytest.raises(ProviderError):load_free_tier_confirmation(p,environ={})

def test_classifier_confirmation_has_no_file_or_image_dependency():
 from vimfc.experiments.groq_classification.classifier import validate_free_confirmation
 from vimfc.experiments.groq_classification.store import Pause
 assert validate_free_confirmation(True)['basis']=='explicit_user_confirmation'
 for value in (False,None,'true',1):
  with pytest.raises(Pause):validate_free_confirmation(value)
