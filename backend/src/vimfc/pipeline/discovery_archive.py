"""WAL-aware read-only adapter for offline experiments and review evidence."""
from copy import deepcopy
import hashlib,json,sqlite3
from pathlib import Path
from types import SimpleNamespace
from vimfc.domain import Incompatible,digest,canonical,at_or_before
from vimfc.storage.raw_cache import RawCache
from vimfc.source.dependencies import DependencySource
from .dependencies import verify_manifest
from .discovery_policy import seal_inventory

STREAM_STRATA={'people':'Person','organizations':'Organization','locations':'Location','events':'Event',
    'works':'Creative Work','awards':'Award / Competition','facilities':'Landmark / Facility','science':'Science / Technology'}

class Archive:
    def __init__(self,context):
        self.context=context
        self.db=sqlite3.connect(Path(context['database']).resolve().as_uri()+'?mode=ro',uri=True)
        self.db.row_factory=sqlite3.Row;self.db.execute('BEGIN')
        row=self.db.execute('SELECT * FROM pipeline_runs WHERE pipeline_run_id=?',(context['run_id'],)).fetchone()
        if not row:self.db.close();raise Incompatible('Unknown archived source run')
        self.run=dict(row);self.config=json.loads(row['config_json']);self.cache=RawCache(context['raw_cache'])
        self.cohort=[json.loads(r[0]) for r in self.db.execute('SELECT record_json FROM candidates WHERE run_id=? ORDER BY discovery_order',(context['run_id'],))]
        if (digest(self.config)!=context['config_hash'] or row['config_hash']!=context['config_hash']
            or row['snapshot']!=context['snapshot'] or digest(self.cohort)!=context['cohort_hash']):
            self.db.close();raise Incompatible('Archived config/snapshot/cohort mismatch')

    def __enter__(self):return self
    def __exit__(self,*args):self.db.close()

    def checked_raw(self,cache_ref,*,page_id,revision_timestamp,raw_hash,model):
        raw=self.cache.get(cache_ref);encoded=raw['content'].encode('utf-8')
        if (raw['page_id']!=page_id or raw['timestamp']!=revision_timestamp or raw['content_model']!=model
            or raw['local_sha256']!=raw_hash or hashlib.sha256(encoded).hexdigest()!=raw_hash
            or hashlib.sha1(encoded).hexdigest()!=raw['source_sha1'] or not at_or_before(raw['timestamp'],self.context['snapshot'])):
            raise Incompatible('Archived raw identity/model/hash/cutoff mismatch')
        return raw

    def records(self):
        output={};source=SimpleNamespace(store=SimpleNamespace(db=self.db),cache=self.cache,reviewed_quarantine=None)
        dependencies=DependencySource(source,self.context['snapshot'],self.config['classification'])
        for row in self.db.execute("SELECT identity,result_json,result_hash FROM stage_results WHERE run_id=? AND stage='process' AND status='completed' ORDER BY identity",(self.context['run_id'],)):
            try:
                record=json.loads(row['result_json']);classification=record['classification'];manifest=classification['dependency_manifest']
                if digest(record)!=row['result_hash'] or str(record['page_id'])!=row['identity']:raise Incompatible('Process checkpoint checksum/identity mismatch')
                verify_manifest(manifest,self.config['classification'])
                binding=self.db.execute('SELECT m.record_json FROM article_manifests b JOIN dependency_manifests m USING(manifest_id) WHERE b.run_id=? AND b.page_id=?',(self.context['run_id'],record['page_id'])).fetchone()
                if not binding or json.loads(binding[0])!=manifest:raise Incompatible('Archived manifest binding mismatch')
                article={'site':'viwiki','page_id':record['page_id'],'title':record['title'],'revision_id':record['revision_id'],'revision_timestamp':record['revision_timestamp'],'content_hash':record['raw_hash']}
                if manifest['article']!=article or manifest['snapshot']!=self.context['snapshot'] or record['snapshot']!=self.context['snapshot']:
                    raise Incompatible('Archived article differs from manifest')
                if self.config.get('discovery'):
                    scope=record.get('discovery_source_scope')
                    if not scope or scope.get('namespace')!=0 or scope.get('article')!=article:raise Incompatible('Missing discovery scope evidence')
                    if ':' in article['title']:
                        deps={'article':article,'snapshot':self.context['snapshot'],'policy':'historical-title-namespace-1'}
                        row=self.db.execute("SELECT result_json,result_hash FROM stage_results WHERE run_id=? AND identity=? AND stage='verify_namespace' AND dependency_hash=? AND status='completed'",(self.context['run_id'],str(record['page_id']),digest(deps))).fetchone()
                        if not row or json.loads(row[0])!=scope or digest(scope)!=row[1]:raise Incompatible('Discovery namespace checkpoint is missing or differs from process input')
                self.checked_raw(record['cache_ref'],page_id=record['page_id'],revision_timestamp=record['revision_timestamp'],raw_hash=record['raw_hash'],model=record['content_model'])
                dependencies.materialize(manifest,allow_fetch=False)
                # Full checksum was checked before dropping repeated projected
                # item payloads; the immutable manifest is retained as evidence.
                classification.pop('items',None)
                output[record['page_id']]=record
            except (ValueError,KeyError,TypeError,OSError,AttributeError) as error:
                raise Incompatible('Invalid archived process/raw input; no API recovery') from error
        return output

    def pairs(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT record_json FROM pair_candidates WHERE run_id=? ORDER BY pair_kind,endpoint_a,endpoint_b',(self.context['run_id'],))]

    def hint_articles(self):
        articles=[]
        for row in self.db.execute("SELECT page_id FROM candidate_work WHERE run_id=? AND json_extract(record_json,'$.error_code') IN ('missing_item_hint','association_review_required') ORDER BY page_id",(self.context['run_id'],)):
            stage=self.db.execute("SELECT result_json,result_hash FROM stage_results WHERE run_id=? AND identity=? AND stage='acquire' AND status='completed'",(self.context['run_id'],str(row[0]))).fetchone()
            if not stage:raise Incompatible('Missing historical Wikipedia input for association review')
            saved=json.loads(stage[0]);ref=saved['ref']
            if digest(saved)!=stage[1] or ref['snapshot']!=self.context['snapshot']:raise Incompatible('Association article checkpoint mismatch')
            self.checked_raw(saved['cache_ref'],page_id=ref['page_id'],revision_timestamp=ref['revision_timestamp'],raw_hash=saved['local_sha256'],model=saved['content_model'])
            articles.append({**ref,'raw_hash':saved['local_sha256']})
        return articles

    def inventory(self,records):
        seeds=[];neighbors={};omitted=[]
        for order,candidate in enumerate(self.cohort):
            origin=candidate.get('discovery_origin','unassigned');stratum=STREAM_STRATA.get(origin)
            checked=records.get(candidate['page_id']);proof=candidate.get('identity_proof',{})
            canonical_checked=bool(checked and checked['title']==candidate['title'] and checked['snapshot']==self.context['snapshot'])
            if not canonical_checked and proof.get('status')=='resolved' and all(proof.get(k)==v for k,v in {'page_id':candidate['page_id'],'title':candidate['title'],'snapshot':self.context['snapshot']}.items()):
                row=self.db.execute("SELECT record_json FROM source_revisions WHERE site='viwiki' AND revision_id=?",(proof['revision_id'],)).fetchone()
                if row:
                    self.checked_raw(json.loads(row[0])['cache_ref'],page_id=proof['page_id'],revision_timestamp=proof['revision_timestamp'],raw_hash=proof['raw_hash'],model='wikitext')
                    canonical_checked=True
            verified=canonical_checked and ':' not in candidate['title']
            seed=deepcopy(candidate);seed['discovery_namespace_status']='verified' if verified else 'unverified'
            seeds.append({'page_id':candidate['page_id'],'title':candidate['title'],'namespace':0 if verified else None,
                'namespace_basis':'checked_historical_canonical_title_no_separator' if verified else 'exact_revision_namespace_check_required',
                'candidate':seed,'strata':[stratum] if stratum else [],'legacy_order':order,'origins':[{'kind':'seed','stream':origin}]})
        for record in records.values():
            if not record['quality']['eligible'] or record['extraction']['outcome']!='completed':continue
            for link in record['extraction']['links']:
                target=link.get('target',{});page=target.get('page_id');title=target.get('title','')
                if link.get('scope')!='retained_lead_prose' or target.get('status')!='resolved' or page==record['page_id']:continue
                # Every non-main namespace title contains a namespace separator.
                # Conservatively exclude ALL colon titles, including legitimate
                # main-namespace colon titles, until an explicit scope proof exists.
                if ':' in title:
                    omitted.append({'page_id':page,'reason':'colon_title_needs_explicit_namespace_proof'});continue
                if target.get('snapshot')!=self.context['snapshot']:raise Incompatible('Frontier identity snapshot mismatch')
                row=self.db.execute("SELECT record_json FROM source_revisions WHERE site='viwiki' AND revision_id=?",(target['revision_id'],)).fetchone()
                if not row:
                    omitted.append({'page_id':page,'reason':'target_raw_not_registered'});continue
                self.checked_raw(json.loads(row[0])['cache_ref'],page_id=page,revision_timestamp=target['revision_timestamp'],raw_hash=target['raw_hash'],model='wikitext')
                paragraph=record['extraction']['paragraphs'][link['paragraph_id']];sentence=record['extraction']['sentences'][link['sent_id']]
                if paragraph[link['start']:link['end']]!=link['link_text'] or sentence['paragraph_id']!=link['paragraph_id'] or not sentence['start']<=link['start']<link['end']<=sentence['end']:
                    raise Incompatible('Frontier link span does not belong to retained lead sentence')
                origin={'kind':'lead','scope':'retained_lead_prose','source_eligible':True,'source_page_id':record['page_id'],
                    'source_revision_id':record['revision_id'],'source_raw_hash':record['raw_hash'],'source_manifest_id':record['classification']['manifest_id'],
                    'source_sent_id':link['sent_id'],'link_span':{k:link[k] for k in ('paragraph_id','start','end','link_text','original_target')},'target':deepcopy(target)}
                if page in neighbors and neighbors[page]['title']!=title:raise Incompatible('Conflicting historical frontier titles')
                neighbor=neighbors.setdefault(page,{'page_id':page,'title':title,'namespace':0,'namespace_basis':'historical_canonical_title_without_colon',
                    'strata':[],'candidate':{'page_id':page,'title':title,'lookup_title':title,'discovery_origin':'lead-neighbor','identity_proof':deepcopy(target)},'origins':[]})
                neighbor['origins'].append(origin)
        body={'format':'vimfc-discovery-inventory-1','snapshot':self.context['snapshot'],
            'semantic_rules':{k:digest(self.config[k]) for k in ('classification','extraction','ranking')},
            'source':{'run_id':self.context['run_id'],'config_hash':self.context['config_hash'],'cohort_hash':self.context['cohort_hash'],
                'processed':len(records),'eligible_seed_manifest_ids':sorted({r['classification']['manifest_id'] for r in records.values() if r['quality']['eligible']})},
            'seeds':seeds,'neighbors':list(neighbors.values()),'omitted_frontier':sorted(omitted,key=canonical)}
        return seal_inventory(body)
