import copy,json
import pytest

def row():
 return {'input':{'title':'Đại học A','lead_text':'Đại học A là một trường đại học.','sentences':[{'id':0,'text':'Đại học A là một trường đại học.'}]},
 'evaluation':{'page_id':1,'baseline_outcome':'unresolved','baseline_primary':None,'sampling_stratum':'SECRET_STRATUM','eligible':False},'input_fingerprint':'input'}

def test_prompt_uses_actual_precedence_and_has_no_labels(config):
 from vimfc.experiments.groq_classification.prompt import build_prompt
 first=build_prompt(row(),config['classification']);other=row();other['evaluation'].update(baseline_primary='Person',baseline_outcome='completed',sampling_stratum='LEAK');second=build_prompt(other,config['classification'])
 assert first==second
 assert json.loads(first['messages'][1]['content'])==row()['input']
 assert 'SECRET_STRATUM' not in json.dumps(first)
 assert first['precedence']==config['classification']['precedence']
 assert first['messages'][0]['content'].index('Landmark / Facility')<first['messages'][0]['content'].index('Award / Competition')

@pytest.mark.parametrize('mutation,status',[
 ({'evidence_sentence_ids':[999]},'invalid_evidence'),({'evidence_sentence_ids':[True]},'invalid_evidence'),
 ({'abstain':True},'invalid_schema'),({'predicted_group':'Guess'},'invalid_schema'),({'confidence':1},'invalid_schema'),
 ({'evidence_sentence_ids':[]},'invalid_evidence'),({'reason':''},'invalid_schema')])
def test_invalid_prediction_is_not_silently_accepted(mutation,status):
 from vimfc.experiments.groq_classification.prompt import validate_prediction
 value={'predicted_group':'Organization','abstain':False,'evidence_sentence_ids':[0],'reason':'Câu 0 nêu trường đại học.','ambiguity':None};value.update(mutation)
 assert validate_prediction(json.dumps(value),row())['status']==status

def test_duplicate_json_keys_and_trailing_text_rejected():
 from vimfc.experiments.groq_classification.prompt import validate_prediction
 assert validate_prediction('{"predicted_group":null,"predicted_group":"Person"}',row())['status']=='invalid_json'
 assert validate_prediction('```json {} ```',row())['status']=='invalid_json'

def test_valid_abstain_and_exact_evidence_remain_suggestions():
 from vimfc.experiments.groq_classification.prompt import validate_prediction
 value={'predicted_group':None,'abstain':True,'evidence_sentence_ids':[],'reason':'Không đủ căn cứ','ambiguity':'Thiếu định nghĩa chủ thể'}
 assert validate_prediction(json.dumps(value),row())['status']=='valid'
 assert row()['evaluation']['eligible'] is False

def test_prompt_refuses_unsupported_precedence(config):
 from vimfc.experiments.groq_classification.prompt import build_prompt
 rules=copy.deepcopy(config['classification']);rules['precedence'].append('Other')
 with pytest.raises(ValueError):build_prompt(row(),rules)
