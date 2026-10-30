"""Cache-only acceptance checks for a frozen Seed-only pilot (no source recovery)."""
from dataclasses import asdict
import json
from types import SimpleNamespace
from vimfc.domain import Incompatible,RawRevision,SourceRevisionRef,digest,canonical
from vimfc.source.dependencies import DependencySource
from .dependencies import classify_manifest
from .discovery_archive import Archive
from .extract import extract_lead
from .quality import assess_quality
from .pairs import build_pairs


def verify_seed_pilot(context,size):
    with Archive(context) as archive:
        row=archive.db.execute('SELECT report_json FROM pilot_reports WHERE run_id=? AND size=?',(context['run_id'],size)).fetchone()
        if not row:raise Incompatible('No completed invocation report for offline verification')
        report=json.loads(row[0]);prefix=archive.cohort[:size];ids={c['page_id'] for c in prefix}
        if (report['cohort_hash']!=digest(prefix) or report['config_hash']!=context['config_hash']
            or report['snapshot']!=context['snapshot']):raise Incompatible('Report identity mismatch')
        records=archive.records()
        if not set(records)<=ids:raise Incompatible('Process results outside requested prefix')
        source=SimpleNamespace(store=SimpleNamespace(db=archive.db),cache=archive.cache,reviewed_quarantine=None)
        deps=DependencySource(source,context['snapshot'],archive.config['classification'])
        manifests={}
        for page,record in records.items():
            m=record['classification']['dependency_manifest'];manifests[str(page)]=m['manifest_id']
            if any(f.get('source_code') not in (None,'history_unavailable','historical_content_unavailable') for f in m['frontier']):
                raise Incompatible('Unexpected source failure in frozen frontier; no progression')
            cls=asdict(classify_manifest(m,deps.materialize(m,allow_fetch=False),archive.config['classification']))
            if any(record['classification'].get(k)!=v for k,v in cls.items()):raise Incompatible('Classification replay mismatch')
            obj=archive.cache.get(record['cache_ref'])
            raw=RawRevision(SourceRevisionRef('viwiki',str(page),page,record['title'],record['revision_id'],record['revision_timestamp'],context['snapshot']),
                obj['content'],obj['content_model'],obj['source_sha1'],obj['local_sha256'],record['cache_ref'])
            extraction=asdict(extract_lead(raw,archive.config['extraction']))
            saved={**record['extraction'],'links':[{k:v for k,v in l.items() if k!='target'} for l in record['extraction']['links']]}
            if extraction!=saved or assess_quality(extraction,record['classification'],record['article_kinds'],archive.config['extraction'])!=record['quality']:
                raise Incompatible('Extraction/quality replay mismatch')
            for link in record['extraction']['links']:
                target=link.get('target',{})
                if target.get('status')!='resolved':continue
                ref=archive.db.execute("SELECT record_json FROM source_revisions WHERE site='viwiki' AND revision_id=?",(target['revision_id'],)).fetchone()
                if not ref:raise Incompatible('Missing resolved-link raw proof')
                archive.checked_raw(json.loads(ref[0])['cache_ref'],page_id=target['page_id'],revision_timestamp=target['revision_timestamp'],raw_hash=target['raw_hash'],model='wikitext')
        if len(records)!=report['work_counts']['processed']:raise Incompatible('Process count mismatch')
        if canonical(build_pairs(list(records.values()),archive.config['ranking']).pairs)!=canonical(archive.pairs()):
            raise Incompatible('Pair replay mismatch')
        return {'report_hash':digest(report),'validated_process_count':len(records),'errors':[],
                'manifest_ids':manifests,'status':'PASS','source_api_requests':0,'pair_replay':True,
                'cohort_hash':report['cohort_hash'],'snapshot':context['snapshot']}
