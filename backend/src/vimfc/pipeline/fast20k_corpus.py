"""Separate one-paragraph annotation publication; legacy freeze invariants are untouched."""
from collections import Counter
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata
import uuid

from vimfc.domain import Incompatible, canonical, digest, at_or_before
from vimfc.domain import GROUPS
from vimfc.storage.raw_cache import RawCache
from vimfc.source.identities import normalize_title
from .annotation_edges import select_articles, classify_links, rankable_links, edge_report, EDGE_STATES
from .hyperlink_rank import graph_metrics
from .incremental_collection import CollectionArchive, _publish_json
from .first_paragraph import extract_first_paragraph, VERSION
from .quality import assess_quality
from .relaxed_pilot import relaxed_rules
from .discovery_policy import SNAPSHOT


class FastArchive(CollectionArchive):
    def resolve(self,title):
        target=super().resolve(title)
        # Target raw was checked by the archive; do not duplicate whole revisions per link.
        return {k:v for k,v in target.items() if k!='content'}


def pool_report(workspace,header):
    context={**header['context'],'database':str(Path(workspace)/'pipeline.sqlite'),
             'raw_cache':str(Path(workspace)/'raw-cache')}
    with FastArchive(context) as archive:
        records=[];rejected=Counter();extractions=Counter()
        query="SELECT record_json,record_hash FROM fast_work WHERE status='processed'"
        if 'selection' in header['profile']:
            query="SELECT w.record_json,w.record_hash FROM fast_work w JOIN btt_members m USING(page_id) WHERE w.status='processed'"
        for body,checksum in archive.db.execute(query):
            a=json.loads(body)
            if digest(a)!=checksum:raise Incompatible('FAST-20K pool checksum differs')
            extractions[a['extraction']['outcome']]+=1
            if a['quality']['eligible']:records.append(a)
            else:rejected.update(a['quality']['reasons'])
        archive.collection_targets={}
        for a in records:archive.collection_targets.setdefault(normalize_title(a['title']),[]).append(a)
        labeled,_=classify_links(records,archive)
        return dict(eligible_articles=len(records),extraction_states=dict(extractions),
            scope='selected_forest' if 'selection' in header['profile'] else 'eligible_pool',
            rejection_reasons=dict(rejected),edges=edge_report(labeled),graph=graph_metrics(rankable_links(labeled)),
            classification=dict(Counter(a['classification'].get('primary_entity_type') or 'Unknown' for a in records)),
            graph_is_semantically_verified=False)


def validate_article(record, raw, config):
    ext=record['extraction'];proof=record['source_validation'];article=proof['article']
    if (record['snapshot']!=SNAPSHOT or not at_or_before(record['revision_timestamp'],SNAPSHOT)
            or proof['manifest_id']!=digest({k:v for k,v in proof.items() if k!='manifest_id'})
            or any(record[k]!=article[k] for k in ('page_id','title','revision_id','revision_timestamp'))
            or record['raw_hash']!=article['content_hash'] or not record['quality']['eligible']
            or ext['extraction_version']!=VERSION or ext['outcome']!='completed'
            or len(ext['paragraphs'])!=1 or ext['paragraphs'][0]!=ext['full_lead']
            or not unicodedata.is_normalized('NFC',ext['full_lead'])):
        raise Incompatible('FAST-20K article identity/first-paragraph/quality contract differs')
    text=raw['content'].encode('utf-8')
    if (hashlib.sha256(text).hexdigest()!=record['raw_hash'] or hashlib.sha1(text).hexdigest()!=proof['source_sha1']
            or raw['page_id']!=record['page_id'] or raw['timestamp']!=record['revision_timestamp']
            or raw['content_model']!='wikitext'):
        raise Incompatible('FAST-20K raw source identity/hash differs')
    replay=asdict(extract_first_paragraph(raw['content'],config['extraction']))
    if any(replay[k]!=ext[k] for k in ('outcome','extraction_version','full_lead','paragraphs','sentences','diagnostics','stats')):
        raise Incompatible('FAST-20K extraction replay differs')
    keys=('target_title','original_target','fragment','link_text','scope','paragraph_id','start','end','sent_id')
    if [{k:l[k] for k in keys} for l in replay['links']]!=[{k:l[k] for k in keys} for l in ext['links']]:
        raise Incompatible('FAST-20K retained hyperlink replay differs')
    if [s['sent_id'] for s in ext['sentences']]!=list(range(len(ext['sentences']))):
        raise Incompatible('FAST-20K sentence IDs are not contiguous')
    for s in ext['sentences']:
        if s['paragraph_id']!=0 or ext['full_lead'][s['start']:s['end']]!=s['text']:
            raise Incompatible('FAST-20K sentence span differs')
    if assess_quality(replay,record['classification'],record['article_kinds'],relaxed_rules(config))!=record['quality']:
        raise Incompatible('FAST-20K quality replay differs')
    for link in ext['links']:
        if link.get('edge_policy')!='annotation-edge-1' or link.get('edge_status') not in EDGE_STATES:
            raise Incompatible('FAST-20K link state/policy missing')
    return True


def publish(workspace,output,*,fixture=False):
    workspace,output=Path(workspace).resolve(),Path(output).resolve()
    header=json.loads((workspace/'workspace.json').read_text(encoding='utf-8'))
    if header['binding_id']!=digest({k:v for k,v in header.items() if k!='binding_id'}):
        raise Incompatible('FAST-20K publication header checksum differs')
    target=header['profile']['target']
    if (not fixture and (target!=20000 or header.get('fixture'))) or output.exists() or output.is_relative_to(workspace):
        raise Incompatible('FAST-20K publication needs exactly 20000 and a new destination')
    context={**header['context'],'database':str(workspace/'pipeline.sqlite'),'raw_cache':str(workspace/'raw-cache')}
    # Writer must already be stopped. Publication opens only read-only workspace adapters.
    with closing(sqlite3.connect(Path(context['database']).as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT value FROM fast_control WHERE key='binding'").fetchone()[0]!=canonical(header):
            raise Incompatible('FAST-20K publication database binding differs')
        records=[];qwen_rows={row[0]:tuple(row) for row in db.execute('SELECT page_id,status,record_json,raw_json FROM fast_qwen')}
        forest=None
        query='SELECT record_json,record_hash FROM fast_work WHERE eligible=1 ORDER BY ordinal'
        if 'selection' in header['profile']:
            from .btt import validate_forest
            forest=validate_forest(db,target)
            forest_decisions=dict(roots=[dict(page_id=p,status=s,entity_group=g) for p,s,g in db.execute('SELECT * FROM btt_roots ORDER BY page_id')],
                targets=[dict(parent_id=p,title=t,status=s,target_id=q,degree=d,raw_hash=h) for p,t,s,q,d,h in db.execute("SELECT l.parent_id,l.title_key,l.status,l.target_id,p.degree,json_extract(w.record_json,'$.raw_hash') FROM btt_links l LEFT JOIN btt_pool p ON p.page_id=l.target_id LEFT JOIN fast_work w ON w.page_id=l.target_id ORDER BY l.parent_id,l.position")])
            query='SELECT w.record_json,w.record_hash FROM fast_work w JOIN btt_members m USING(page_id) WHERE w.eligible=1 ORDER BY m.ordinal'
        for body,checksum in db.execute(query):
            record=json.loads(body)
            if digest(record)!=checksum:raise Incompatible('FAST-20K eligible record checksum differs')
            suggestion=db.execute('SELECT record_json,status FROM fast_qwen WHERE page_id=?',(record['page_id'],)).fetchone()
            if suggestion:
                record['classification_suggestion']=json.loads(suggestion[0]) if suggestion[0] else dict(
                    suggested_primary_entity_type=None,classification_source='qwen_suggestion',classification_status='pending',
                    model_id=header['profile']['qwen']['model_id'],model_revision=header['profile']['qwen']['model_revision'],
                    uncertainty=True)
                item=record['classification_suggestion']
                if (item.get('suggested_primary_entity_type') not in (*GROUPS,None)
                        or item['model_id']!=header['profile']['qwen']['model_id']
                        or item['model_revision']!=header['profile']['qwen']['model_revision']):
                    raise Incompatible('FAST-20K Qwen suggestion identity/taxonomy differs')
                if suggestion[0]:
                    ref=item['raw_response_reference'];raw_body=json.loads(qwen_rows[record['page_id']][3])
                    if ref!=dict(table='fast_qwen',page_id=record['page_id'],sha256=digest(raw_body)):
                        raise Incompatible('FAST-20K Qwen raw response reference differs')
            records.append(record)
    if len(records)<target:raise Incompatible('FAST-20K insufficient eligible supply; no partial corpus publication')
    with FastArchive(context) as archive:
        archive.collection_targets={}
        for a in records:archive.collection_targets.setdefault(normalize_title(a['title']),[]).append(a)
        ranking=deepcopy(records)
        for a in ranking:
            suggestion=a.get('classification_suggestion',{})
            if a['classification'].get('primary_entity_type') is None and suggestion.get('suggested_primary_entity_type'):
                a['classification']=dict(outcome='suggested',primary_entity_type=suggestion['suggested_primary_entity_type'],
                    subtypes=[],classification_source='qwen_ranking_hint_only')
        result=select_articles(ranking,archive,archive.config['quotas'],target=target)
        selection=result['selection']
        if selection.shortage or len(set(selection.page_ids))!=target:
            raise Incompatible('FAST-20K exact-target graph-aware selection failed')
        originals={a['page_id']:a for a in records};annotated={a['page_id']:a for a in result['articles']}
        if forest is not None:
            expected={(n['parent_id'],n['page_id']) for n in forest if n['parent_id'] is not None}
            actual={(a['page_id'],l.get('target',{}).get('page_id')) for a in result['articles'] for l in a['extraction']['links'] if l['edge_status'] in ('CANDIDATE_EDGE','VERIFIED_EDGE')}
            if not expected<=actual:raise Incompatible('BTT selected parent link lacks checked target evidence')
        final=[]
        for page in selection.page_ids:
            record=deepcopy(originals[page]);record['extraction']=annotated[page]['extraction']
            acquired,proof=archive.article_source(page)
            if proof!=record['source_validation']:raise Incompatible('FAST-20K source admission replay differs')
            validate_article(record,archive.cache.get(proof['cache_ref']),archive.config)
            final.append(record)
        metrics=graph_metrics(rankable_links(final));report=edge_report(final)
        version=digest(dict(binding_id=header['binding_id'],selection=asdict(selection),records=final))
        output.parent.mkdir(parents=True,exist_ok=True)
        stage=output.parent/('.fast20k-pending-'+uuid.uuid4().hex);stage.mkdir()
        if forest is not None:_publish_json(stage/'forest.json',dict(policy=header['profile']['selection'],nodes=forest,decisions=forest_decisions))
        ids=set(selection.page_ids)
        with closing(sqlite3.connect(stage/'corpus.sqlite')) as corpus, corpus:
            corpus.execute('PRAGMA foreign_keys=ON')
            corpus.executescript('''
              CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE articles(page_id INTEGER PRIMARY KEY,ordinal INTEGER UNIQUE NOT NULL,record_json TEXT NOT NULL);
              CREATE TABLE source_objects(raw_hash TEXT PRIMARY KEY,raw_json TEXT NOT NULL);
              CREATE TABLE sentences(page_id INTEGER REFERENCES articles,sent_id INTEGER,paragraph_id INTEGER CHECK(paragraph_id=0),text TEXT NOT NULL,PRIMARY KEY(page_id,sent_id));
              CREATE TABLE lead_links(page_id INTEGER REFERENCES articles,occurrence INTEGER,record_json TEXT NOT NULL,PRIMARY KEY(page_id,occurrence));
              CREATE TABLE internal_edges(source_page_id INTEGER REFERENCES articles,target_page_id INTEGER REFERENCES articles,edge_state TEXT NOT NULL,PRIMARY KEY(source_page_id,target_page_id));
              CREATE TABLE fast_qwen(page_id INTEGER PRIMARY KEY REFERENCES articles,status TEXT NOT NULL,record_json TEXT,raw_json TEXT);
            ''')
            corpus.executemany('INSERT INTO articles VALUES (?,?,?)',[(a['page_id'],i,canonical(a)) for i,a in enumerate(final)])
            corpus.executemany('INSERT INTO fast_qwen VALUES (?,?,?,?)',[qwen_rows[a['page_id']] for a in final if a['page_id'] in qwen_rows])
            with (stage/'corpus.jsonl').open('x',encoding='utf-8',newline='\n') as articles_out,(stage/'graph.jsonl').open('x',encoding='utf-8',newline='\n') as graph_out:
                for a in final:
                    articles_out.write(canonical(dict(corpus_version=version,**a))+'\n')
                    raw=archive.cache.get(a['source_validation']['cache_ref'])
                    corpus.execute('INSERT OR IGNORE INTO source_objects VALUES (?,?)',(a['raw_hash'],canonical(raw)))
                    corpus.executemany('INSERT INTO sentences VALUES (?,?,?,?)',[(a['page_id'],s['sent_id'],0,s['text']) for s in a['extraction']['sentences']])
                    for i,l in enumerate(a['extraction']['links']):
                        corpus.execute('INSERT INTO lead_links VALUES (?,?,?)',(a['page_id'],i,canonical(l)))
                        other=l.get('target',{}).get('page_id')
                        if l['edge_status'] in ('VERIFIED_EDGE','CANDIDATE_EDGE') and other in ids and other!=a['page_id']:
                            edge=dict(corpus_version=version,source_page_id=a['page_id'],target_page_id=other,
                                edge_status=l['edge_status'],source_sent_id=l['sent_id'],link=l)
                            graph_out.write(canonical(edge)+'\n')
                            corpus.execute('INSERT OR REPLACE INTO internal_edges VALUES (?,?,?)',(a['page_id'],other,l['edge_status']))
            corpus.execute('INSERT INTO metadata VALUES (?,?)',('corpus_version',version))
            corpus.execute('INSERT INTO metadata VALUES (?,?)',('schema','fast20k-annotation-corpus-1'))
            corpus.execute('INSERT INTO metadata VALUES (?,?)',('configuration',canonical(archive.config)))
            corpus.execute('INSERT INTO metadata VALUES (?,?)',('workspace_binding',canonical(header)))
            if forest is not None:corpus.execute('INSERT INTO metadata VALUES (?,?)',('btt_forest',canonical(forest)))
            if corpus.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or corpus.execute('PRAGMA foreign_key_check').fetchone():
                raise Incompatible('FAST-20K corpus SQLite validation failed')
        files={}
        for path in stage.iterdir():
            with path.open('rb') as stream: checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            files[path.name]=dict(bytes=path.stat().st_size,sha256=checksum)
        manifest=dict(format='fast20k-annotation-corpus-1',corpus_version=version,snapshot=SNAPSHOT,articles=len(final),
            binding_id=header['binding_id'],
            fixture=fixture,extraction_policy=VERSION,quality_policy='quality-relaxed-1',edge_policy='annotation-edge-1',
            ranking_policy='rank-annotation-1',config_hash=context['config_hash'],run_id=context['run_id'],
            source_bundle_id=header['bundle_id'],inventory_id=header['inventory_id'],
            selection=asdict(selection),ranking_input_digest=result['manifest']['input_digest'],
            classification= dict(Counter(a['classification'].get('primary_entity_type') or 'Unknown' for a in final)),
            suggested_classification=dict(Counter(a.get('classification_suggestion',{}).get('suggested_primary_entity_type') or 'Unknown' for a in final)),
            quota_targets=archive.config['quotas'],
            quota_deviations={g:sum((a['classification'].get('primary_entity_type') or a.get('classification_suggestion',{}).get('suggested_primary_entity_type'))==g for a in final)-n for g,n in archive.config['quotas'].items()},
            qwen_suggestion_states=dict(Counter(a.get('classification_suggestion',{}).get('classification_status','not_required') for a in final)),
            graph=metrics,edge_report=report,files=files,claims_generated=0,labels_generated=0,hops_assigned=0)
        if forest is not None:manifest.update(selection_policy=header['profile']['selection'],forest_sha256=digest(forest))
        _publish_json(stage/'manifest.json',manifest)
        stage.rename(output)
        return manifest
