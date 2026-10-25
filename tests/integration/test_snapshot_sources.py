from dataclasses import asdict
import hashlib
import httpx
import pytest
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from vimfc.domain import SourceUnavailable

SNAP = '2026-10-14T00:00:00Z'


def test_cutoff_single_page_batch_and_cached_resume(tmp_path):
    calls = []
    content = 'Nội dung lịch sử.'
    def serve(request):
        p = dict(request.url.params)
        calls.append(p)
        if 'revids' not in p:
            assert p['rvstart'] == SNAP and p['rvlimit'] == '1' and p['rvdir'] == 'older'
            assert p['titles'] == 'Bài A' and 'redirects' not in p
        revision = {'revid': 101, 'timestamp': '2026-10-13T01:00:00Z',
                    'sha1': hashlib.sha1(content.encode()).hexdigest(),
                    'slots': {'main': {'contentmodel': 'wikitext', 'content': content}}}
        return httpx.Response(200, json={'query': {'pages': [{'pageid': 1, 'ns': 0, 'title': 'Bài A', 'revisions': [revision]}]}})
    with PipelineStore(tmp_path / 'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(serve)) as client:
        source = RevisionSource(client, store, RawCache(tmp_path / 'raw'))
        ref = source.select_revision('viwiki', 'Bài A', SNAP)
        raw = source.fetch_revisions('viwiki', [ref])[0]
        assert raw.content == content and raw.ref.revision_id == 101
        assert source.select_revision('viwiki', 'Bài A', SNAP) == ref
        assert source.fetch_revisions('viwiki', [ref])[0].content == content
        assert len(calls) == 2
        assert calls[1]['revids'] == '101' and 'rvlimit' not in calls[1]


@pytest.mark.parametrize('page', [
    {'pageid': 1, 'title': 'A', 'revisions': []},
    {'pageid': 1, 'title': 'A', 'revisions': [{'revid': 1, 'timestamp': '2026-10-15T00:00:00Z'}]},
    {'title': 'A', 'missing': True},
])
def test_no_history_or_future_revision_never_falls_back(tmp_path, page):
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('test@example.org', transport=httpx.MockTransport(lambda r: httpx.Response(200,json={'query':{'pages':[page]}}))) as client:
        source = RevisionSource(client, store, RawCache(tmp_path/'raw'))
        with pytest.raises(SourceUnavailable):
            source.select_revision('viwiki', 'A', SNAP)


def test_http_200_maxlag_retry_after_and_attempt_limit():
    attempts, waits = [], []
    def serve(request):
        attempts.append(dict(request.url.params))
        return httpx.Response(200,json={'error':{'code':'maxlag','info':'busy'}},headers={'Retry-After':'7'})
    with MediaWikiClient('test@example.org', transport=httpx.MockTransport(serve), sleep=waits.append) as client:
        with pytest.raises(SourceUnavailable) as exc:
            client.query('viwiki', {'action':'query'})
        assert exc.value.retryable
        assert len(attempts) == 5 and len(waits) == 4 and min(waits) >= 7
        assert all(p['maxlag'] == '5' for p in attempts)


def test_continuation_empty_batch_is_not_exhaustion():
    calls = []
    def serve(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200,json=({'continue':{'continue':'-||','apcontinue':'B'},'query':{'allpages':[]}}
            if len(calls) == 1 else {'query':{'allpages':[{'pageid':2,'title':'B'}]}}))
    with MediaWikiClient('test@example.org', transport=httpx.MockTransport(serve)) as client:
        pages = list(client.continued('viwiki', {'action':'query','list':'allpages'}))
    assert len(pages) == 2 and calls[1]['continue'] == '-||' and calls[1]['apcontinue'] == 'B'


def test_orphaned_durable_raw_blob_is_reused_without_network(tmp_path):
    from vimfc.domain import SourceRevisionRef
    cache=RawCache(tmp_path/'raw')
    content='Nội dung lịch sử.'
    cache.put('viwiki',101,{'page_id':1,'timestamp':'2026-10-13T01:00:00Z','content':content,'content_model':'wikitext',
        'source_sha1':hashlib.sha1(content.encode()).hexdigest(),'local_sha256':hashlib.sha256(content.encode()).hexdigest()})
    def forbidden(request): raise AssertionError('Already durable raw blob must not be downloaded again')
    with PipelineStore(tmp_path/'db') as store, MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(forbidden)) as client:
        ref=SourceRevisionRef('viwiki','A',1,'A',101,'2026-10-13T01:00:00Z',SNAP)
        assert RevisionSource(client,store,cache).fetch_revisions('viwiki',[ref])[0].content==content
        assert store.db.execute('SELECT count(*) FROM source_revisions').fetchone()[0]==1


def test_known_revision_batches_shrink_on_payload_limit_and_keep_input_order(tmp_path):
    from vimfc.domain import SourceRevisionRef
    batches=[]
    def serve(request):
        ids=[int(i) for i in request.url.params['revids'].split('|')]
        batches.append(len(ids))
        if len(ids)>10: return httpx.Response(413)
        pages=[]
        for revision_id in reversed(ids):
            content=f'Historical text {revision_id}.'
            pages.append({'pageid':revision_id,'title':str(revision_id),'revisions':[{'revid':revision_id,'timestamp':'2026-10-13T00:00:00Z','sha1':hashlib.sha1(content.encode()).hexdigest(),
                'slots':{'main':{'contentmodel':'wikitext','content':content}}}]})
        return httpx.Response(200,json={'query':{'pages':pages}})
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve)) as client:
        refs=[SourceRevisionRef('viwiki',str(i),i,str(i),i,'2026-10-13T00:00:00Z',SNAP) for i in range(1,22)]
        result=RevisionSource(client,store,RawCache(tmp_path/'raw')).fetch_revisions('viwiki',refs)
        assert batches==[20,10,10,1]
        assert [r.ref.revision_id for r in result]==list(range(1,22))
