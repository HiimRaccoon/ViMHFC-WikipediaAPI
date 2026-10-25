from dataclasses import asdict
from functools import lru_cache
from vimfc.config import load_config
from vimfc.pipeline.classify import classify
from vimfc.pipeline.quality import assess_quality


@lru_cache(maxsize=1)
def fixture_config():
    return load_config('configs/pipeline.json')


def article(page_id=1, group='Person', subtype='Q901', quality=None, links=()):
    paragraphs=['Nhà khoa học A sinh năm 1980 tại Hà Nội, là tác giả của nhiều công trình vật lý có giá trị được công bố trên các tạp chí khoa học quốc tế. Ông gia nhập viện nghiên cứu năm 2001 và tham gia giảng dạy tại trường đại học trong nhiều năm.']
    sentences=[{'sent_id':0,'paragraph_id':0,'text':'Nhà khoa học A sinh năm 1980 tại Hà Nội.','start':0,'end':39},
               {'sent_id':1,'paragraph_id':0,'text':'Ông gia nhập viện nghiên cứu năm 2001.','start':40,'end':77}]
    # Explicit offsets are recomputed by the real splitter, not used as test expectations.
    from vimfc.pipeline.sentences import split_sentences
    sentences=split_sentences(paragraphs,{'abbreviations':[]})
    occurrences=[]
    for target in links:
        occurrences.append({'target_title':f'Bài {target}','original_target':f'Bài {target}','fragment':'','link_text':'A','scope':'retained_lead_prose','sent_id':0,'paragraph_id':0,'start':13,'end':14,
            'target':{'status':'resolved','page_id':target,'title':f'Bài {target}','revision_id':target+1000,'raw_hash':'a'*64,'revision_timestamp':'2026-10-13T00:00:00Z','snapshot':'2026-10-14T00:00:00Z','chain':[],'proof':{'kind':'controlled_fixture'}}})
    root=subtype or {'Person':'Q5','Organization':'Q43229','Location':'Q6256','Event':'Q1656682','Creative Work':'Q17537576','Award / Competition':'Q618779','Landmark / Facility':'Q41176','Science / Technology':'Q11016'}[group]
    qid=f'Q{100000+page_id}'
    items={qid:{'revision_id':page_id+900000,'revision_timestamp':'2026-10-13T00:00:00Z','entity':{'id':qid,'sitelinks':{'viwiki':{'title':f'Bài {page_id}'}},'claims':{'P31':[{'mainsnak':{'snaktype':'value','datavalue':{'value':{'id':root}}}}]}}},
           root:{'revision_id':int(root[1:])+500,'revision_timestamp':'2026-10-13T00:00:00Z','entity':{'id':root,'claims':{}}}}
    classification=asdict(classify(f'Bài {page_id}',qid,items,fixture_config()['classification'],'2026-10-14T00:00:00Z'))
    classification['items']=items
    result={'page_id':page_id,'title':f'Bài {page_id}','revision_id':page_id+1000,'revision_timestamp':'2026-10-13T00:00:00Z','source_url':f'https://vi.wikipedia.org/w/index.php?oldid={page_id+1000}',
        'snapshot':'2026-10-14T00:00:00Z','raw_hash':'a'*64,'content_model':'wikitext',
        'extraction':{'outcome':'completed','extraction_version':fixture_config()['extraction']['version'],'full_lead':paragraphs[0],'paragraphs':paragraphs,'sentences':sentences,'links':occurrences,'diagnostics':[],
                      'stats':{'sentence_count':2,'paragraph_count':1,'lead_tokens':len(paragraphs[0].split()),'lead_characters':len(paragraphs[0]),'provisional':False}},
        'classification':classification}
    result['quality']=assess_quality(result['extraction'],classification,[],fixture_config()['extraction'])
    if quality is not None:
        result['quality']['score']=quality
    return result
