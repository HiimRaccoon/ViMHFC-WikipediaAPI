from copy import deepcopy
import hashlib
import json
import shutil
import sqlite3
import pytest
from tests.helpers import article
from vimfc.domain import InvalidCorpus
from vimfc.pipeline.freeze import freeze
from vimfc.storage.corpus_store import CorpusStore, verify_package
from vimfc.config import load_config

RUN={'pipeline_run_id':'fixture-run','snapshot':'2026-10-14T00:00:00Z','config_hash':'b'*64,'config':load_config('configs/pipeline.json')}


@pytest.mark.parametrize('count',[19999,20001])
def test_exact_20000_publication_gate(tmp_path,count):
    with pytest.raises(InvalidCorpus,match='20000'):
        freeze(RUN,[article(i) for i in range(1,count+1)],[],tmp_path)
    assert not list(tmp_path.glob('*/corpus.sqlite'))


@pytest.mark.parametrize('mutation', ['future','gaps','body','ineligible','missing_provenance'])
def test_invalid_metadata_sentences_and_nonlead_edges_rejected_before_publication(tmp_path,mutation):
    first=article(1,links=[2])
    if mutation=='future': first['revision_timestamp']='2026-10-15T00:00:00Z'
    if mutation=='gaps': first['extraction']['sentences'][1]['sent_id']=9
    if mutation=='body': first['extraction']['links'][0]['scope']='full_article'
    if mutation=='ineligible': first['quality']['eligible']=False
    if mutation=='missing_provenance': first['classification']['provenance']=[]
    with pytest.raises(InvalidCorpus):
        freeze(RUN,[first]+[article(i) for i in range(2,20001)],[],tmp_path)


def test_standalone_20000_package_hash_graph_readonly_and_no_overwrite(tmp_path):
    articles=[article(1,links=[2,99999])]+[article(i) for i in range(2,20001)]
    package=freeze(RUN,articles,[],tmp_path/'output')
    assert verify_package(package)['article_count']==20000
    assert not list(package.glob('*-wal')) and not list(package.glob('*-shm'))
    before=hashlib.sha256((package/'corpus.sqlite').read_bytes()).hexdigest()
    with CorpusStore(package) as corpus:
        assert corpus.article(1)['extraction']['sentences'][0]['sent_id']==0
        assert corpus.db.execute('SELECT source_page_id,target_page_id FROM internal_edges').fetchall()[0][:]==(1,2)
        assert corpus.db.execute('SELECT count(*) FROM entity_targets').fetchone()[0]==2
        with pytest.raises(sqlite3.OperationalError):
            corpus.db.execute('DELETE FROM articles')
    assert hashlib.sha256((package/'corpus.sqlite').read_bytes()).hexdigest()==before
    with pytest.raises(InvalidCorpus,match='exists'):
        freeze(RUN,articles,[],tmp_path/'output')
    copy=tmp_path/'second-machine'
    shutil.copytree(package,copy)
    assert verify_package(copy)['corpus_version']==package.name
    with (copy/'corpus.sqlite').open('ab') as stream: stream.write(b'corruption')
    with pytest.raises(InvalidCorpus,match='hash'):
        verify_package(copy)
