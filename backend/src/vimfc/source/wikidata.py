"""Snapshot-only, root-bounded BFS with shared cache and content batches."""
from collections import Counter
import hashlib
import json
from vimfc.domain import SourceRevisionRef,SourceUnavailable,at_or_before
from vimfc.pipeline.classify import classification_children
from vimfc.source.identities import normalize_title


class WikidataSource:
    def __init__(self, source, snapshot, rules):
        self.source,self.snapshot,self.rules=source,snapshot,rules
        self.items={}
        self.metrics=Counter()
        self._titles=None
        self._cached_refs={}
        self.last_traversal={}

    def _load(self,qids):
        unknown=[]
        for qid in qids:
            if qid in self.items:
                self.metrics['hierarchy_memory_hits']+=1
            else:
                unknown.append(qid)
        refs=[self.source.select_revision('wikidata',qid,self.snapshot) for qid in unknown]
        for raw in self.source.fetch_revisions('wikidata',refs):
            qid=raw.ref.identity
            if raw.content_model!='wikibase-item':
                raise SourceUnavailable('Unexpected Wikidata content model')
            entity=json.loads(raw.content)
            if entity.get('id')!=qid:
                raise SourceUnavailable('Wikidata entity identity mismatch')
            self.items[qid]={'entity':entity,'revision_id':raw.ref.revision_id,'revision_timestamp':raw.ref.revision_timestamp,'raw_hash':raw.local_sha256,'cache_ref':raw.cache_ref}
            self.metrics['unique_historical_items_loaded']+=1
            title=(entity.get('sitelinks') or {}).get('viwiki',{}).get('title')
            if title and self._titles is not None:
                self._titles.setdefault(normalize_title(title),set()).add(qid)

    def item(self,qid):
        self._load([qid])
        return self.items[qid]

    def _read_cached(self,qid,ref,cache_ref):
        try:
            raw=self.source.cache.get(cache_ref)
            entity=json.loads(raw['content'])
            valid=(ref.site=='wikidata' and ref.identity==qid and raw['content_model']=='wikibase-item' and raw['page_id']==ref.page_id and ref.snapshot==self.snapshot
                and raw['timestamp']==ref.revision_timestamp and at_or_before(raw['timestamp'],self.snapshot)
                and hashlib.sha256(raw['content'].encode('utf-8')).hexdigest()==raw['local_sha256']
                and isinstance(entity,dict) and entity.get('id')==qid
                and isinstance(entity.get('sitelinks') or {},dict) and isinstance(entity.get('claims') or {},dict)
                and isinstance(((entity.get('sitelinks') or {}).get('viwiki') or {}),dict)
                and isinstance((((entity.get('sitelinks') or {}).get('viwiki') or {}).get('title') or ''),str))
            if not valid:
                raise ValueError('Historical cache identity/hash/cutoff/model mismatch')
            return raw,entity
        except (OSError,ValueError,TypeError,KeyError) as error:
            raise SourceUnavailable('Invalid historical association cache',code='invalid_historical_cache',details={'qid':qid,'revision_id':ref.revision_id,'snapshot':self.snapshot,'cause':str(error)}) from error

    def _index_snapshot(self):
        if self._titles is None:
            titles,cached_refs={},{}
            rows=self.source.store.db.execute("SELECT s.identity,s.record_json,r.record_json FROM snapshot_revision_refs s JOIN source_revisions r ON r.site=s.site AND r.revision_id=json_extract(s.record_json,'$.revision_id') WHERE s.site='wikidata' AND s.snapshot=? AND s.identity GLOB 'Q[0-9]*'",(self.snapshot,)).fetchall()
            for qid,ref_json,raw_json in rows:
                try:
                    ref=SourceRevisionRef(**json.loads(ref_json))
                    cache_ref=json.loads(raw_json)['cache_ref']
                except (ValueError,TypeError,KeyError) as error:
                    raise SourceUnavailable('Invalid historical association cache metadata',code='invalid_historical_cache',details={'qid':qid,'snapshot':self.snapshot,'cause':str(error)}) from error
                raw,entity=self._read_cached(qid,ref,cache_ref)
                cached_refs[qid]=(ref,cache_ref)
                target=((entity.get('sitelinks') or {}).get('viwiki') or {}).get('title')
                if target:
                    titles.setdefault(normalize_title(target),set()).add(qid)
            self._titles,self._cached_refs=titles,cached_refs

    def _load_cached(self,qids):
        # Direct blob reads only: this path cannot issue a source request, even
        # if the required node lies beyond the network acquisition depth.
        for qid in qids:
            if qid in self.items or qid not in self._cached_refs:
                continue
            ref,cache_ref=self._cached_refs[qid]
            raw,entity=self._read_cached(qid,ref,cache_ref)
            self.items[qid]={'entity':entity,'revision_id':ref.revision_id,'revision_timestamp':ref.revision_timestamp,'raw_hash':raw['local_sha256'],'cache_ref':cache_ref}
            self.metrics['cached_completion_items_loaded']+=1

    def cached_qid(self,title):
        self.metrics['association_lookups']+=1
        self._index_snapshot()
        identities=self._titles.get(normalize_title(title),set())
        if len(identities)>1:
            raise SourceUnavailable('Ambiguous historical sitelink association',code='ambiguous_sitelink',details={'qids':sorted(identities)})
        if identities:
            self.metrics['association_hits']+=1
            return next(iter(identities))
        return None

    def closure(self,qid):
        frontier=[(qid,0)]
        visited={}
        cached_completion=self.rules.get('cached_closure_completion',False)
        skipped=set()
        self.last_traversal={'network_max_depth':self.rules['max_depth'],'max_depth':0,'visited_count':0,'uncached_frontier':[],'node_budget_exhausted':False}
        if cached_completion:
            self._index_snapshot()
        try:
            while frontier:
                unique={}
                for identity,depth in frontier:
                    if identity not in visited and identity not in skipped:
                        unique.setdefault(identity,depth)
                if cached_completion:
                    remaining=self.rules['max_cached_nodes']-len(visited)
                    if len(unique)>remaining:
                        self.last_traversal['node_budget_exhausted']=True
                        unique=dict(list(unique.items())[:max(0,remaining)])
                    if not unique:
                        break
                    self._load_cached(unique)
                network=[identity for identity,depth in unique.items() if depth<=self.rules['max_depth']]
                self._load(network)
                following=[]
                for identity,depth in unique.items():
                    if identity not in self.items:
                        skipped.add(identity)
                        continue
                    item=self.items[identity]
                    visited[identity]=item
                    self.last_traversal['max_depth']=max(depth,self.last_traversal['max_depth'])
                    children=classification_children(identity,item['entity'],depth,self.rules)
                    if cached_completion or depth<self.rules['max_depth']:
                        following.extend((child,depth+1) for _,child in children)
                    if depth>0 and identity in self.rules['roots'] and self.rules.get('stop_at_configured_roots'):
                        self.metrics['terminal_root_stops']+=1
                frontier=following
        except SourceUnavailable as error:
            error.partial_items=dict(visited)
            raise
        finally:
            self.last_traversal['visited_count']=len(visited)
            self.last_traversal['uncached_frontier']=sorted(skipped)
        return visited
