import hashlib
import json
import shutil
import sqlite3
import pytest
from tests.helpers import article
from vimfc.domain import InvalidCorpus
from vimfc.config import load_config
from vimfc.pipeline.freeze import freeze
from vimfc.storage.corpus_store import verify_package


@pytest.fixture(scope='module')
def package(tmp_path_factory):
    root=tmp_path_factory.mktemp('normalized-package')
    run={'pipeline_run_id':'fixture','snapshot':'2026-10-14T00:00:00Z','config_hash':'b'*64,'config':load_config('configs/pipeline.json')}
    return freeze(run,[article(1,links=[2,99999])]+[article(i) for i in range(2,20001)],[],root)


@pytest.mark.parametrize('sql',[
    "UPDATE articles SET title='Wrong' WHERE page_id=1",
    "UPDATE paragraphs SET text='Wrong' WHERE page_id=1",
    'DELETE FROM article_subtypes WHERE page_id=1',
    "UPDATE lead_entity_links SET target_key='viwiki:99999' WHERE link_id=1",
    "UPDATE classification_provenance SET record_json='{}' WHERE page_id=1",
])
def test_verify_rejects_operational_tables_that_disagree_with_records(package,tmp_path,sql):
    copy=tmp_path/'copy'
    shutil.copytree(package,copy)
    db=sqlite3.connect(copy/'corpus.sqlite')
    db.execute(sql)
    db.commit()
    db.close()
    manifest=json.loads((copy/'manifest.json').read_text(encoding='utf-8'))
    manifest['database_sha256']=hashlib.sha256((copy/'corpus.sqlite').read_bytes()).hexdigest()
    (copy/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(InvalidCorpus):
        verify_package(copy)
