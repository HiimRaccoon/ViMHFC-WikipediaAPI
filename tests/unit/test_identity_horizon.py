from vimfc.source.identities import HistoricalIdentities
from vimfc.source.revisions import RevisionSource
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
import httpx
import hashlib


def test_title_lookup_and_move_proof_use_same_recorded_horizon(tmp_path):
    snapshot='2026-10-14T00:00:00Z'
    horizons=[]
    def serve(request):
        params=dict(request.url.params)
        if params.get('list')=='logevents':
            horizons.append(params.get('lestart'))
            # A previous index for a different horizon cannot prove this title.
            return httpx.Response(200,json={'query':{'logevents':[{'pageid':1,'title':'A','params':{'target_title':'B'},'timestamp':'2026-10-15T00:00:00Z'}]}})
        revision={'revid':10,'timestamp':'2026-10-13T00:00:00Z','sha1':hashlib.sha1(b'Text.').hexdigest(),'slots':{'main':{'contentmodel':'wikitext','content':'Text.'}}}
        return httpx.Response(200,json={'curtimestamp':'2026-10-16T00:00:00Z','query':{'pages':[{'pageid':1,'title':'B','revisions':[revision]}]}})
    with PipelineStore(tmp_path/'db') as store,MediaWikiClient('fixture@example.org',transport=httpx.MockTransport(serve)) as client:
        store.db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)',(snapshot,'__move_index__','[]'))
        identities=HistoricalIdentities(RevisionSource(client,store,RawCache(tmp_path/'raw')),snapshot)
        assert identities.raw_page(1).ref.title=='A'
        assert horizons==['2026-10-16T00:00:00Z']
