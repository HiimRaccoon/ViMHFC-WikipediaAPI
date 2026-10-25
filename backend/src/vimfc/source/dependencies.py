"""Scoped historical acquisition and pinned-artifact materialization for qid-4."""
import hashlib,json
from vimfc.domain import SourceRevisionRef,SourceUnavailable,Incompatible,at_or_before
from vimfc.pipeline.dependencies import make_manifest,verify_manifest,node_identity


class DependencySource:
    def __init__(self,source,snapshot,rules):
        self.source,self.snapshot,self.rules=source,snapshot,rules

    def _item(self,qid,raw):
        try:
            ref=raw.ref;entity=json.loads(raw.content)
            if (ref.site!='wikidata' or ref.identity!=qid or ref.snapshot!=self.snapshot
                or not at_or_before(ref.revision_timestamp,self.snapshot) or raw.content_model!='wikibase-item'
                or hashlib.sha256(raw.content.encode('utf-8')).hexdigest()!=raw.local_sha256
                or hashlib.sha1(raw.content.encode('utf-8')).hexdigest()!=raw.source_sha1):
                raise ValueError('Historical identity/model/hash/cutoff mismatch')
            result={'entity':entity,'revision_id':ref.revision_id,'revision_timestamp':ref.revision_timestamp,
                'page_id':ref.page_id,'content_model':raw.content_model,'raw_hash':raw.local_sha256}
            if self.rules.get('dependency_policy',{}).get('version')=='primary-first-1':
                from vimfc.pipeline.primary import project_item
                return project_item(result,self.snapshot)
            node_identity(qid,result,self.snapshot)
            return result
        except (ValueError,TypeError,KeyError,AttributeError,SourceUnavailable) as error:
            raise SourceUnavailable('Invalid required historical cache/metadata',code='invalid_historical_cache',details={'qid':qid,'cause':str(error)}) from error

    def acquire(self,article,qid):
        if self.rules.get('dependency_policy',{}).get('version')=='primary-first-1':
            from .primary_dependencies import RoleLoader
            return make_manifest(article,qid,RoleLoader(self),self.rules,self.snapshot)
        items,errors={},{}
        def load(identity):
            if identity in errors:raise errors[identity]
            if identity in items:return items[identity]
            try:
                ref=self.source.select_revision('wikidata',identity,self.snapshot)
                raw=self.source.fetch_revisions('wikidata',[ref],recover_missing=True)[0]
                return self._item(identity,raw)
            except (ValueError,TypeError,KeyError,OSError) as error:
                raise SourceUnavailable('Invalid required historical cache/metadata',code='invalid_historical_cache',details={'qid':identity,'cause':str(error)}) from error
        def prefetch(identities):
            refs=[]
            for identity in identities:
                try:refs.append(self.source.select_revision('wikidata',identity,self.snapshot))
                except SourceUnavailable as error:
                    if error.code!='history_unavailable':raise
                    errors[identity]=error
            if not refs:return
            try:
                raws=self.source.fetch_revisions('wikidata',refs,recover_missing=True)
                items.update((raw.ref.identity,self._item(raw.ref.identity,raw)) for raw in raws)
            except SourceUnavailable as error:
                if error.code!='historical_content_unavailable':raise
                # Successful members of a partially unavailable batch have been
                # cached. Keep negative evidence only for the identified IDs.
                for ref in refs:
                    if ref.revision_id in error.details.get('unavailable_revision_ids',[]):errors[ref.identity]=error
            except (ValueError,TypeError,KeyError,OSError) as error:
                raise SourceUnavailable('Invalid required historical cache/metadata',code='invalid_historical_cache',details={'cause':str(error)}) from error
        load.prefetch=prefetch
        try:
            return make_manifest(article,qid,load,self.rules,self.snapshot)
        except SourceUnavailable as error:
            if error.code=='source_unavailable' and not error.retryable:
                raise SourceUnavailable('Required historical source identity/hash validation failed',code='invalid_historical_cache',details={'source_code':error.code,'cause':str(error)}) from error
            raise
        except (ValueError,TypeError,KeyError,OSError,AttributeError) as error:
            raise SourceUnavailable('Invalid selected historical reference metadata',code='invalid_historical_cache',details={'qid':qid,'cause':str(error)}) from error

    def materialize(self,manifest,*,allow_fetch=True):
        verify_manifest(manifest,self.rules)
        if manifest['snapshot']!=self.snapshot: raise Incompatible('Manifest snapshot mismatch')
        outputs={}
        primary_first=manifest['format']=='vimfc-primary-first-1'
        def role(qid):return 'primary' if primary_first and qid in manifest['primary']['node_qids'] else 'subtype' if primary_first else 'classification'
        refs=[]
        for node in manifest['nodes']:
            if node['page_id'] is None:
                raise SourceUnavailable('Manifest lacks retrieval metadata',code='dependency_input_missing',details={'qid':node['qid'],'role':role(node['qid'])})
            ref=SourceRevisionRef('wikidata',node['qid'],node['page_id'],node['qid'],node['revision_id'],node['revision_timestamp'],self.snapshot)
            refs.append(ref)
        if not allow_fetch:
            for ref in refs:
                row=self.source.store.db.execute('SELECT record_json FROM source_revisions WHERE site=? AND revision_id=?',('wikidata',ref.revision_id)).fetchone()
                try:
                    if not row: raise FileNotFoundError('Manifest artifact is not registered')
                    cached=self.source.cache.get(json.loads(row[0])['cache_ref'])
                    if cached['page_id']!=ref.page_id or cached['timestamp']!=ref.revision_timestamp:
                        raise ValueError('Pinned raw metadata mismatch')
                    from vimfc.domain import RawRevision
                    raw=RawRevision(ref,cached['content'],cached['content_model'],cached['source_sha1'],cached['local_sha256'])
                    outputs[ref.identity]=self._item(ref.identity,raw)
                except (ValueError,KeyError,TypeError,OSError,AttributeError,SourceUnavailable) as error:
                    missing=isinstance(error,FileNotFoundError) or isinstance(error.__cause__,FileNotFoundError)
                    raise SourceUnavailable('Manifest artifact missing' if missing else 'Manifest artifact invalid',code='dependency_input_missing' if missing else 'dependency_input_invalid',details={'qid':ref.identity,'role':role(ref.identity),'manifest_id':manifest['manifest_id'],'cause':str(error)}) from error
        else:
            try:
                raws=self.source.fetch_revisions('wikidata',refs,recover_missing=True)
                outputs={raw.ref.identity:self._item(raw.ref.identity,raw) for raw in raws}
            except SourceUnavailable as error:
                error.details.update({'manifest_id':manifest['manifest_id'],'phase':'materialize'})
                bad_ids=set(error.details.get('unavailable_revision_ids',error.details.get('requested_revision_ids',[])))
                qid=error.details.get('qid') or error.details.get('identity')
                affected=[{'qid':r.identity,'revision_id':r.revision_id,'role':role(r.identity)} for r in refs if r.revision_id in bad_ids or r.identity==qid]
                roles=sorted({a['role'] for a in affected})
                details={**error.details,'artifacts':affected,'role':roles[0] if len(roles)==1 else 'mixed' if roles else 'unknown','cause':str(error),'source_code':error.code}
                if len(affected)==1:details['qid']=affected[0]['qid']
                if error.retryable:
                    error.details.update(details)
                    raise
                invalid=error.code in ('invalid_historical_cache','dependency_input_invalid','source_unavailable')
                raise SourceUnavailable('Declared manifest artifacts could not be materialized',code='dependency_input_invalid' if invalid else 'dependency_input_missing',details=details) from error
            except (ValueError,TypeError,KeyError,OSError,AttributeError) as error:
                raise SourceUnavailable('Required manifest artifact is invalid',code='dependency_input_invalid',details={'manifest_id':manifest['manifest_id'],'cause':str(error)}) from error
        for node in manifest['nodes']:
            identity=node_identity
            if primary_first:
                from vimfc.pipeline.primary import node_ref
                identity=node_ref
            if identity(node['qid'],outputs[node['qid']],self.snapshot)!=node:
                raise SourceUnavailable('Materialized artifact differs from manifest',code='dependency_input_invalid',details={'qid':node['qid'],'role':role(node['qid']),'manifest_id':manifest['manifest_id']})
        return outputs
