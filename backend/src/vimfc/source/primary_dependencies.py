"""Bounded, batched role acquisition; failures stay attached to their QID."""
from vimfc.domain import SourceUnavailable


class RoleLoader:
    def __init__(self,dependency_source):
        self.dependency_source=dependency_source
        self.source=dependency_source.source
        self.items,self.errors={},{}

    def _error(self,qid,error):
        if not isinstance(error,SourceUnavailable):
            error=SourceUnavailable('Invalid selected historical dependency',code='invalid_historical_cache',details={'qid':qid,'cause':str(error)})
        elif error.code=='source_unavailable' and not error.retryable:
            error=SourceUnavailable('Historical dependency validation failed',code='invalid_historical_cache',details={'qid':qid,'cause':str(error)})
        self.errors[qid]=error

    def _fetch(self,refs):
        try:
            raws=self.source.fetch_revisions('wikidata',refs,recover_missing=True)
        except (SourceUnavailable,ValueError,TypeError,KeyError,OSError,AttributeError) as error:
            if isinstance(error,SourceUnavailable) and error.code=='source_hash_mismatch' and error.details.get('qid') in {r.identity for r in refs}:
                # Isolate the known bad member without refetching that rejected
                # revision or discarding validated siblings already cached.
                bad=error.details['qid'];self._error(bad,error)
                remaining=[r for r in refs if r.identity!=bad]
                if remaining:self._fetch(remaining)
                return
            if len(refs)>1 and (not isinstance(error,SourceUnavailable) or error.code=='historical_content_unavailable' or error.code=='invalid_historical_cache' and error.details.get('qid')):
                # Successful parts of a partial response are already cached;
                # isolate a corrupt/missing member instead of invalidating peers.
                for ref in refs:self._fetch([ref])
            else:
                for ref in refs:self._error(ref.identity,error)
            return
        for raw in raws:
            try:self.items[raw.ref.identity]=self.dependency_source._item(raw.ref.identity,raw)
            except SourceUnavailable as error:self._error(raw.ref.identity,error)

    def prepare(self,qids,role):
        refs=[]
        for q in qids:
            if q in self.items or q in self.errors:continue
            try:refs.append(self.source.select_revision('wikidata',q,self.dependency_source.snapshot))
            except (SourceUnavailable,ValueError,TypeError,KeyError,OSError,AttributeError) as error:self._error(q,error)
        size=self.source.batch_size
        for offset in range(0,len(refs),size):self._fetch(refs[offset:offset+size])

    def __call__(self,qid):
        if qid not in self.items and qid not in self.errors:self.prepare([qid],None)
        if qid in self.errors:raise self.errors[qid]
        return self.items[qid]
