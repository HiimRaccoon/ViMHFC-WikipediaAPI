from copy import deepcopy
from dataclasses import asdict
from vimfc.pipeline.classify import classify
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.quality import assess_quality
from vimfc.pipeline.pairs import build_pairs
from vimfc.pipeline.report import corpus_report
from tests.unit.test_classify import item,SNAP


def pair_articles(config):
    # Controlled authored sources, parsed/classified/filtered by production code.
    specs=[(1,'An','Q901',"{{Infobox person|caption=[[Cường]]}}\nAn sinh năm 1980 tại một thành phố ven biển và nghiên cứu vật lý hạt nhân trong nhiều năm tại trường đại học quốc gia. Anh gia nhập [[Viện B]] năm 2001 để phát triển các chương trình nghiên cứu năng lượng cho cộng đồng.\n==Thân bài==\n[[Cường]]"),
        (2,'Viện B','Q3918',"Viện B được thành lập năm 1950 để đào tạo các chuyên gia y tế và phát triển nhiều chương trình giáo dục dành cho người dân ở khu vực miền núi. Trụ sở viện tọa lạc tại một khu đô thị rộng lớn với bệnh viện và thư viện phục vụ sinh viên."),
        (3,'Cường','Q901',"Cường sinh năm 1990 tại một tỉnh đồng bằng và theo học ngành thiên văn với sự hướng dẫn của nhiều chuyên gia quốc tế nổi tiếng. Ông gia nhập đài quan sát năm 2010 để phân tích chuyển động của những ngôi sao xa và công bố kết quả trên tạp chí chuyên ngành.")]
    articles=[]
    for page,title,root,raw in specs:
        qid=f'Q{page+1000}'
        items={qid:item(qid,{'P31':[root]},title),root:item(root)}
        cls=asdict(classify(title,qid,items,config['classification'],SNAP));cls['items']=items
        if config['classification'].get('dependency_policy'):
            from vimfc.pipeline.dependencies import make_manifest,classify_manifest
            identity={'site':'viwiki','page_id':page,'title':title,'revision_id':page+20,
                'revision_timestamp':'2026-10-13T00:00:00Z','content_hash':'a'*64}
            dependencies=make_manifest(identity,qid,lambda q:items[q],config['classification'],SNAP)
            cls={**asdict(classify_manifest(dependencies,items,config['classification'])),'items':items,
                'manifest_id':dependencies['manifest_id'],'dependency_manifest':dependencies}
        ext=asdict(extract_lead(raw,config['extraction']))
        for link in ext['links']:
            link['target']={'status':'resolved','page_id':2,'title':'Viện B','revision_id':22,'revision_timestamp':'2026-10-13T00:00:00Z','snapshot':SNAP,'raw_hash':'a'*64,'proof':{'kind':'fixture'}}
        quality=assess_quality(ext,cls,[],config['extraction'])
        assert quality['eligible'],quality
        articles.append({'page_id':page,'title':title,'extraction':ext,'classification':cls,'quality':quality})
    return articles


def test_prose_link_bridge_and_compatible_comparison_flow_through_real_stages(config):
    articles=pair_articles(config)
    manifest=build_pairs(articles,config['ranking'])
    bridges=[p for p in manifest.pairs if p['kind']=='bridge']
    comparisons=[p for p in manifest.pairs if p['kind']=='comparison']
    assert [(p['endpoint_a'],p['endpoint_b']) for p in bridges]==[(1,2)]
    assert bridges[0]['score']>0 and bridges[0]['features']['relation_signal']==100
    assert bridges[0]['features']['complementary_information']==100
    assert bridges[0]['provenance'][0]['source_sent_id']==1
    assert [(p['endpoint_a'],p['endpoint_b']) for p in comparisons]==[(1,3)]
    assert comparisons[0]['score']>0 and comparisons[0]['features']['attribute_compatibility']==100
    assert comparisons[0]['provenance'][0]['block']==('Q901','birth','year')
    graph=corpus_report(articles,manifest.pairs,config['quotas'])
    assert graph['graph']['internal_edge_count']==1
    assert graph['positive_pairs']=={'bridge':1,'comparison':1}
    assert build_pairs(list(reversed(articles)),config['ranking'])==manifest


def test_manifest_classification_order_warmup_pairs_and_selection_are_identical(config):
    from vimfc.pipeline.dependencies import classify_manifest
    from vimfc.pipeline.select import select_corpus
    a=pair_articles(config);b=deepcopy(a)
    for record in reversed(b):
        cls=record['classification'];warm={**cls['items'],'Q999999':{'bad':'unrelated'}}
        replay=asdict(classify_manifest(cls['dependency_manifest'],warm,config['classification']))
        assert all(cls[k]==v for k,v in replay.items())
        record['quality']=assess_quality(record['extraction'],replay,[],config['extraction'])
    pa=build_pairs(a,config['ranking']);pb=build_pairs(list(reversed(b)),config['ranking'])
    assert pa==pb and {p['kind'] for p in pa.pairs}=={'bridge','comparison'}
    assert select_corpus(a,pa.pairs,config['ranking'],config['quotas'],target=3)==select_corpus(list(reversed(b)),pb.pairs,config['ranking'],config['quotas'],target=3)


def test_pairs_exclude_ineligible_endpoints_and_ambiguous_attribute_values(config):
    articles=pair_articles(config)
    articles[1]['quality']['eligible']=False
    for signal in articles[2]['quality']['signals']:
        signal['values']=['1990','1991']
    manifest=build_pairs(articles,config['ranking'])
    assert manifest.pairs==[]


def test_comparison_never_falls_back_from_missing_subtype_to_primary_group(config):
    articles=pair_articles(config)
    articles[0]['classification']['subtypes']=[]
    articles[2]['classification']['subtypes']=[]
    assert not [p for p in build_pairs(articles,config['ranking']).pairs if p['kind']=='comparison']


def test_primary_only_proven_article_can_bridge_but_never_primary_fallback_comparison(config):
    from vimfc.pipeline.dependencies import make_manifest,classify_manifest
    articles=pair_articles(config)
    for a in (articles[0],articles[2]):
        qid=f'Q{a["page_id"]+1000}'
        items={qid:item(qid,{'P31':['Q5']},a['title']),'Q5':item('Q5')}
        identity={'site':'viwiki','page_id':a['page_id'],'title':a['title'],'revision_id':a['page_id']+20,
            'revision_timestamp':'2026-10-13T00:00:00Z','content_hash':'a'*64}
        m=make_manifest(identity,qid,lambda q:items[q],config['classification'],SNAP)
        a['classification']={**asdict(classify_manifest(m,items,config['classification'])),'items':items,'dependency_manifest':m,'manifest_id':m['manifest_id']}
        a['quality']=assess_quality(a['extraction'],a['classification'],[],config['extraction'])
        assert a['quality']['eligible'] and a['classification']['subtypes']==[]
    pairs=build_pairs(articles,config['ranking'])
    assert [(p['endpoint_a'],p['endpoint_b']) for p in pairs.pairs if p['kind']=='bridge']==[(1,2)]
    assert not [p for p in pairs.pairs if p['kind']=='comparison']
