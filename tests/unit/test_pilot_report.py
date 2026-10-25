import pytest
from tests.helpers import article
from vimfc.pipeline.report import corpus_report, template_audit, performance_report


def test_connectivity_components_deviations_and_unassessed_yield(config):
    articles=[article(1,links=[2,999]),article(2),article(3)]
    report=corpus_report(articles,[],config['quotas'])
    assert report['graph']['internal_edge_count']==1
    assert report['graph']['isolated_article_count']==1
    assert report['graph']['weak_component_sizes']==[2,1]
    assert report['valid_2hop_yield']['status']=='unassessed'
    measured=corpus_report(articles,[],config['quotas'],{'reviewed':5,'valid':3,'provenance':'external-review-file'})
    assert measured['valid_2hop_yield']['ratio']==0.6
    with pytest.raises(ValueError):
        corpus_report(articles,[],config['quotas'],{'reviewed':1,'valid':2,'provenance':'x'})


def test_template_denominator_distinct_articles_and_notyet_attempted():
    records=[{'page_id':1,'revision_id':10,'raw_hash':'a','extraction':{'outcome':'unresolved_extraction','diagnostics':[{'template':'T','arguments':{'1':'a'},'reason':'unsupported'},{'template':'T','arguments':{'1':'b'},'reason':'unsupported'}]}},
             {'page_id':2,'revision_id':20,'raw_hash':'b','extraction':{'outcome':'completed','diagnostics':[]}}, {'page_id':3,'status':'source_failed'}]
    audit=template_audit(records,cohort_size=5)
    assert audit['attempted']==2 and audit['not_attempted']==3
    assert audit['unresolved_rate']==0.5 and audit['success_rate']==0.5
    assert audit['templates'][0]['affected_articles']==1
    assert audit['provisional_outputs_validated'] is False


def test_warm_reuse_is_not_cold_production_eta():
    report=performance_report({'extract':{'active_seconds':2,'new':5,'reused':95}},elapsed=10,requests={'viwiki:acquire:revisions:requests':7},cache={'hits':9,'lookups':10},resources={'peak_rss_bytes':1000})
    assert report['stages']['extract']['new_articles_per_minute']==150
    assert report['end_to_end_seconds']==10
    assert report['cache']['hit_rate']==0.9
    assert report['production_eta']['status']=='unavailable'


def test_cold_observations_have_qualified_eta_and_explicit_request_rates():
    report=performance_report({'process':{'active_seconds':500,'new':1000,'reused':0}},elapsed=600,
        requests={'viwiki:acquire:revisions:requests':3000,'viwiki:acquire:retries':30,'viwiki:acquire:errors':40},cache={'hits':0,'lookups':3000},resources={})
    assert report['request_rates']['retry_rate']==0.01
    assert report['request_rates']['error_rate']==40/3000
    assert report['production_eta']['seconds']==48000
    assert report['production_eta']['status']=='estimate'
    assert report['production_eta']['uncertainty_seconds']==[24000,96000]


def test_audit_separates_supported_formatters_from_excluded_nonprose(config):
    from dataclasses import asdict
    from vimfc.pipeline.extract import extract_lead
    result=extract_lead('{{Infobox person|name=X}}Ông sinh {{birth date|1980|2|3}}. Ông làm việc tại đây.',config['extraction'])
    audit=template_audit([{'page_id':1,'revision_id':10,'raw_hash':'a','extraction':asdict(result)}],1)
    assert audit['supported_templates']['birth date']==1
    assert audit['excluded_nonprose_templates']['infobox person']==1
    assert audit['unresolved']==0 and audit['templates']==[]
