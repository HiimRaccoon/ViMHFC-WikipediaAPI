"""Read-only archived extraction/quality comparison. Never acquires source data."""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
from vimfc.domain import Incompatible, digest, at_or_before
from vimfc.source.identities import normalize_title
from vimfc.storage.raw_cache import RawCache
from .extract import extract_lead
from .quality import assess_quality
from .pairs import build_pairs


def _metrics(records):
    return {'processed':len(records),'extraction_completed':sum(a['extraction']['outcome']=='completed' for a in records),
        'extraction_unresolved':sum(a['extraction']['outcome']!='completed' for a in records),
        'eligible':sum(a['quality']['eligible'] for a in records),
        'quality_reasons':dict(Counter(r for a in records for r in a['quality']['reasons']))}


def _plain(extraction):
    return {**extraction,'links':[{k:v for k,v in l.items() if k!='target'} for l in extraction['links']]}


def _verify_output(ext,quality):
    if [s['sent_id'] for s in ext['sentences']]!=list(range(len(ext['sentences']))):raise Incompatible('Noncontiguous sentences')
    for i,p in enumerate(ext['paragraphs']):
        cursor=0
        for s in (s for s in ext['sentences'] if s['paragraph_id']==i):
            if s['start']<cursor or p[cursor:s['start']].strip() or p[s['start']:s['end']]!=s['text']:raise Incompatible('Sentence prose/span mismatch')
            cursor=s['end']
        if p[cursor:].strip():raise Incompatible('Unrepresented prose')
    for l in ext['links']:
        s=ext['sentences'][l['sent_id']]
        if l['scope']!='retained_lead_prose' or l['paragraph_id']!=s['paragraph_id'] or not s['start']<=l['start']<l['end']<=s['end'] or ext['paragraphs'][s['paragraph_id']][l['start']:l['end']]!=l['link_text']:
            raise Incompatible('Retained link provenance mismatch')
    for signal in quality['signals']:
        text=ext['sentences'][signal['sent_id']]['text']
        if text[signal['match_start']:signal['match_end']]!=signal['matched_text']:raise Incompatible('Factual predicate span mismatch')
        for value in signal['value_spans']+signal['date_spans']:
            if text[value['start']:value['end']]!=value['text']:raise Incompatible('Factual value span mismatch')


def retrospective(database,run_id,config,raw_root,*,expected_candidates=1000):
    database=Path(database).resolve();cache=RawCache(raw_root)
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    try:
        db.execute('BEGIN')
        row=db.execute('SELECT snapshot,config_json,config_hash FROM pipeline_runs WHERE pipeline_run_id=?',(run_id,)).fetchone()
        if not row:raise Incompatible('Unknown archived run')
        snapshot,encoded,config_hash=row;old_config=json.loads(encoded)
        if digest(old_config)!=config_hash or old_config['classification']!=config['classification']:
            raise Incompatible('Archived fingerprint/classification rules differ')
        if any(old_config['extraction'][k]!=config['extraction'][k] for k in ('min_tokens','min_sentences')):
            raise Incompatible('Retrospective cannot relax quality thresholds')
        candidates=[r[0] for r in db.execute('SELECT canonical_page_id FROM candidates WHERE run_id=? ORDER BY discovery_order',(run_id,))]
        if len(candidates)!=expected_candidates:raise Incompatible('Archived cohort size differs')
        archived=db.execute('SELECT report_json FROM pilot_reports WHERE run_id=? AND size=?',(run_id,expected_candidates)).fetchone()
        archived=json.loads(archived[0]) if archived else None
        if archived and archived.get('cohort_page_ids')!=candidates:raise Incompatible('Archived pilot cohort order differs')
        old_records=[];new_records=[];audits=[];checksums=[];plain_same=sentence_same=0
        extraction_only=quality_only=0
        rows=db.execute("SELECT identity,result_json,result_hash FROM stage_results WHERE run_id=? AND stage='process' AND status='completed' ORDER BY identity",(run_id,))
        for identity,encoded,checksum in rows:
            a=json.loads(encoded)
            if digest(a)!=checksum or a['page_id'] not in candidates:raise Incompatible('Archived process checksum/cohort mismatch')
            if any(x['page_id']==a['page_id'] for x in old_records):raise Incompatible('Multiple completed process results')
            raw=cache.get(a['cache_ref']);content=raw['content'];data=content.encode('utf-8')
            if (hashlib.sha256(data).hexdigest()!=a['raw_hash'] or raw['local_sha256']!=a['raw_hash']
                or hashlib.sha1(data).hexdigest()!=raw['source_sha1'] or raw['page_id']!=a['page_id']
                or raw['timestamp']!=a['revision_timestamp'] or raw['content_model']!='wikitext'
                or a['snapshot']!=snapshot or not at_or_before(a['revision_timestamp'],snapshot)):
                raise Incompatible('Archived source hash/identity/cutoff mismatch')
            old_ext=asdict(extract_lead(content,old_config['extraction']))
            if old_ext!=_plain(a['extraction']):raise Incompatible('Legacy extraction no longer replays')
            old_q=assess_quality(a['extraction'],a['classification'],a['article_kinds'],old_config['extraction'])
            if old_q!=a['quality']:raise Incompatible('Legacy quality no longer replays')
            ext=asdict(extract_lead(content,config['extraction']))
            cls={k:a['classification'].get(k) for k in ('outcome','primary_entity_type','subtypes','manifest_id')}
            old={'page_id':a['page_id'],'title':a['title'],'classification':cls,'extraction':a['extraction'],'quality':old_q,
                'revision_id':a['revision_id'],'raw_hash':a['raw_hash']}
            new={**old,'extraction':ext,'quality':assess_quality(ext,cls,a['article_kinds'],config['extraction'])}
            _verify_output(ext,new['quality'])
            extraction_only+=assess_quality(ext,cls,a['article_kinds'],old_config['extraction'])['eligible']
            quality_only+=assess_quality(old_ext,cls,a['article_kinds'],config['extraction'])['eligible']
            old_records.append(old);new_records.append(new);checksums.append([identity,checksum])
            same=ext['full_lead']==old_ext['full_lead'];plain_same+=same;sentence_same+=ext['sentences']==old_ext['sentences']
            old_templates=set(d['template'] for d in old_ext['diagnostics']);new_templates=set(d['template'] for d in ext['diagnostics'])
            audits.append({'page_id':a['page_id'],'title':a['title'],'manifest_id':cls['manifest_id'],
                'raw_hash':a['raw_hash'],'revision_id':a['revision_id'],'old_extraction':old_ext['outcome'],'new_extraction':ext['outcome'],
                'old_eligible':old_q['eligible'],'new_eligible':new['quality']['eligible'],
                'old_reasons':old_q['reasons'],'new_reasons':new['quality']['reasons'],'full_lead_text_unchanged':same,
                'old_templates':sorted(old_templates),'new_templates':sorted(new_templates),
                'signals':new['quality']['signals'],'sentences':ext['sentences'] if new['quality']['eligible'] and not old_q['eligible'] else []})
        # Only previously proven identity resolutions are usable offline. Missing
        # resolutions remain unknown, never inferred from equal-looking titles.
        known={a['page_id']:a for a in new_records};unverified=Counter()
        for a in new_records:
            for l in a['extraction']['links']:
                r=db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',(snapshot,normalize_title(l['target_title']))).fetchone()
                target=json.loads(r[0]) if r else {}
                other=known.get(target.get('page_id'))
                valid=(target.get('status')=='resolved' and target.get('proof') and target.get('snapshot')==snapshot
                    and target.get('revision_timestamp') and at_or_before(target['revision_timestamp'],snapshot))
                if valid and other:
                    valid=target.get('revision_id')==other['revision_id'] and target.get('raw_hash')==other['raw_hash']
                l['target']={k:v for k,v in target.items() if k!='content'} if valid else {'status':'not_verified_offline'}
                if a['quality']['eligible'] and not valid:unverified['eligible_link_occurrences']+=1
        before_pairs=build_pairs(old_records,config['ranking']);after_pairs=build_pairs(new_records,config['ranking'])
        def counts(p):return dict(Counter(x['kind'] for x in p.pairs))
        return {'scope':'offline_retrospective_not_pilot','run_id':run_id,'snapshot':snapshot,'cohort_count':len(candidates),
            'cohort_page_ids_hash':digest(candidates),'archived_cohort_hash':archived.get('cohort_hash') if archived else None,
            'old_config_hash':config_hash,'new_config_hash':digest(config),
            'classification_unchanged':True,'classification_version':config['classification']['version'],
            'old_extraction_version':old_config['extraction']['version'],'new_extraction_version':config['extraction']['version'],
            'quality_version':config['extraction'].get('quality_version','quality-1'),
            'old_extraction_replays':len(old_records),'old_quality_replays':len(old_records),'input_checksums_hash':digest(checksums),
            'output_sentence_link_signal_provenance_checks':len(new_records),
            'not_processed':sorted(set(candidates)-set(known)),'full_lead_text_unchanged':plain_same,'sentence_arrays_unchanged':sentence_same,
            'before':_metrics(old_records),'after':_metrics(new_records),
            'ablation_eligible':{'new_extraction_old_quality':extraction_only,'old_extraction_new_quality':quality_only},
            'before_pairs':counts(before_pairs),'after_pairs_known_resolutions_only':counts(after_pairs),
            'unverified_offline_links':dict(unverified),'human_pair_yield':'unassessed','articles':audits,
            'new_pairs_known_resolutions_only':after_pairs.pairs}
    finally:
        db.rollback();db.close()
