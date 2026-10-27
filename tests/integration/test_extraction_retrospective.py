from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import pytest
from vimfc.domain import canonical, digest, Incompatible
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.quality import assess_quality
from vimfc.storage.raw_cache import RawCache
from vimfc.storage.pipeline_store import PipelineStore


def archived(tmp_path,config):
    cfg=deepcopy(config)
    cfg['extraction']=json.loads(Path('configs/legacy/extraction-lead-3.json').read_text(encoding='utf-8'))
    path=tmp_path/'archive.sqlite';rawroot=tmp_path/'raw';raw='Album được phát hành ngày 4 tháng 9 năm 2006 bởi một hãng đĩa lớn. Đây là một tác phẩm âm nhạc có nhiều bài hát được trình diễn tại các chương trình ở nhiều quốc gia trên thế giới.'
    ext=asdict(extract_lead(raw,cfg['extraction']));cls={'outcome':'completed','primary_entity_type':'Creative Work','subtypes':[],'manifest_id':'pinned-fixture'}
    body={'content':raw,'local_sha256':hashlib.sha256(raw.encode()).hexdigest(),'source_sha1':hashlib.sha1(raw.encode()).hexdigest(),'content_model':'wikitext','page_id':1,'timestamp':'2026-10-20T00:00:00Z'}
    ref=RawCache(rawroot).put('viwiki',10,body)
    record={'page_id':1,'title':'Album','revision_id':10,'revision_timestamp':body['timestamp'],'snapshot':'2026-10-25T03:00:00Z','raw_hash':body['local_sha256'],'cache_ref':ref,'classification':cls,'extraction':ext,'article_kinds':[],'quality':assess_quality(ext,cls,[],cfg['extraction'])}
    with PipelineStore(path) as s:
        run=s.create(record['snapshot'],cfg)
        for i in [1,2]:s.db.execute('INSERT INTO candidates VALUES (?,?,?,?)',(run,i,i,canonical({'page_id':i,'title':str(i)})))
        s.complete(run,1,'process','pinned',record)
    return path,run,rawroot,record


def test_offline_retrospective_preserves_all_checkpoint_rows_and_missing_candidate(tmp_path,config):
    from vimfc.pipeline.retrospective import retrospective
    path,run,rawroot,_=archived(tmp_path,config)
    before=path.read_bytes()
    report=retrospective(path,run,config,rawroot,expected_candidates=2)
    assert report['before']['eligible']==0 and report['after']['eligible']==1
    assert report['not_processed']==[2] and report['cohort_count']==2
    assert report['classification_unchanged'] and report['old_extraction_replays']==1
    assert path.read_bytes()==before
    assert report['articles'][0]['manifest_id']=='pinned-fixture'


def test_retrospective_refuses_changed_classification_and_corrupt_raw(tmp_path,config):
    from vimfc.pipeline.retrospective import retrospective
    path,run,rawroot,record=archived(tmp_path,config)
    changed=deepcopy(config);changed['classification']['max_depth']=13
    with pytest.raises(Incompatible):retrospective(path,run,changed,rawroot,expected_candidates=2)
    (rawroot/record['cache_ref']['path']).write_bytes(b'bad artifact')
    with pytest.raises(ValueError):retrospective(path,run,config,rawroot,expected_candidates=2)
