"""Freeze a manual review sample, never predict claims, labels or necessary hops."""
from collections import defaultdict
from copy import deepcopy
import csv,json
from pathlib import Path
from vimfc.domain import digest,Incompatible,canonical

ALLOCATION={('Q13406554','event_end','year'):22,('Q33999','birth','year'):8,('Q937857','birth','year'):6,
    ('Q3918','founded','year'):6,('Q13406554','event_start','year'):4,('Q13406554','event','year'):3,('Q11424','release','year'):1}

def sample_pairs(pairs,*,seed):
    groups=defaultdict(list);seen=set()
    for pair in pairs:
        key=(pair['kind'],pair['endpoint_a'],pair['endpoint_b'])
        if key in seen:raise Incompatible('Duplicate pair in review sampling frame')
        seen.add(key)
        block=tuple(pair['provenance'][0]['block']) if pair['kind']=='comparison' else ('bridge',)
        groups[block].append(pair)
    if len(groups[('bridge',)])!=29:raise Incompatible('This versioned review protocol requires29 Bridge pairs')
    if set(groups)-{('bridge',)}!=set(ALLOCATION):raise Incompatible('Review sampling strata changed; review protocol/version required')
    selected=[];strata=[]
    for block,count in [(('bridge',),29),*sorted(ALLOCATION.items())]:
        population=groups[block]
        if len(population)<count:raise Incompatible('Review sampling frame is smaller than the prescribed stratum')
        ranked=sorted(population,key=lambda p:(digest([seed,p['kind'],p['endpoint_a'],p['endpoint_b']]),p['endpoint_a'],p['endpoint_b']))
        strata.append({'block':list(block),'population':len(population),'sample':count,'inclusion_probability':count/len(population)})
        for p in ranked[:count]:
            key=[p['kind'],p['endpoint_a'],p['endpoint_b']]
            selected.append({'pair_id':digest(key),'kind':p['kind'],'endpoint_a':p['endpoint_a'],'endpoint_b':p['endpoint_b'],
                'block':list(block),'inclusion_probability':count/len(population),'sampling_weight':len(population)/count})
    body={'protocol_version':'manual-pair-review-1','seed':seed,'frame_hash':digest(sorted(pairs,key=canonical)),
        'strata':strata,'pairs':selected,'human_validated':False,'valid_two_hop_yield':None}
    return {**body,'sample_id':digest(body)}

def build_review_pack(records,pairs,*,seed):
    sample=sample_pairs(pairs,seed=seed);lookup={(p['kind'],p['endpoint_a'],p['endpoint_b']):p for p in pairs};pack=[]
    for selected in sample['pairs']:
        pair=lookup[(selected['kind'],selected['endpoint_a'],selected['endpoint_b'])]
        a,b=[records[selected[k]] for k in ('endpoint_a','endpoint_b')]
        if not all(r['quality']['eligible'] and r['extraction']['outcome']=='completed' for r in (a,b)) or a['snapshot']!=b['snapshot']:
            raise Incompatible('Review endpoints require eligible completed leads at one snapshot')
        if pair['kind']=='bridge':
            links=[l for l in a['extraction']['links'] if l.get('scope')=='retained_lead_prose' and l.get('target',{}).get('status')=='resolved' and l['target']['page_id']==b['page_id']]
            if not links:raise Incompatible('Bridge lacks a retained-lead witness')
            witness=deepcopy(min(links,key=canonical));sentence=a['extraction']['sentences'][witness['sent_id']]
            if (a['extraction']['paragraphs'][witness['paragraph_id']][witness['start']:witness['end']]!=witness['link_text']
                or sentence['paragraph_id']!=witness['paragraph_id'] or not sentence['start']<=witness['start']<witness['end']<=sentence['end']
                or any(witness['target'].get(k)!=b[k] for k in ('page_id','revision_id','revision_timestamp','snapshot','raw_hash'))):
                raise Incompatible('Bridge witness identity/span mismatch')
        else:
            subtype,attribute,kind=selected['block'];evidence={}
            for r in (a,b):
                supported={s['subtype_id'] if isinstance(s,dict) else s for s in r['classification']['subtypes']}
                if subtype not in supported:raise Incompatible('Comparison must not fall back to primary group')
                signals=[s for s in r['quality']['signals'] if s.get('attribute_key')==attribute and s.get('value_kind')==kind and s.get('values')]
                if not signals:raise Incompatible('Comparison attribute evidence is missing')
                for s in signals:
                    sentence=r['extraction']['sentences'][s['sent_id']]['text']
                    if not s.get('value_spans') or any(sentence[v['start']:v['end']]!=v['text'] for v in s['value_spans']):
                        raise Incompatible('Factual value provenance does not slice the stored sentence')
                evidence[str(r['page_id'])]=deepcopy(signals)
            witness={'block':selected['block'],'endpoint_signals':evidence}
        documents=[]
        for r in (a,b):
            documents.append({k:deepcopy(r[k]) for k in ('page_id','title','revision_id','revision_timestamp','snapshot','raw_hash','source_url','extraction','quality')})
            documents[-1]['classification']={k:deepcopy(r['classification'].get(k)) for k in ('primary_entity_type','primary_provenance','subtypes','subtype_status','subtype_provenance','manifest_id')}
        risk={'heuristic_features':pair['features'],'necessary_hops':'not_assessed_without_authored_claim',
            'title_only_or_single_fact_shortcut':'review_required','subject_date_edition_scope':'review_required',
            'human_verdict':None}
        pack.append({**selected,'documents':documents,'witness':witness,'provenance':deepcopy(pair['provenance']),
            'risks':risk,'human_verdict':None,'authored_claim':None})
    return {'format':'vimfc-manual-review-pack-1','sample':sample,'pairs':pack,'human_validation':{'status':'unassessed','valid_two_hop_yield':None,
        'pair_conversion_denominator':'pairs actually attempted for claim authoring','claim_validation_denominator':'authored claims independently reviewed',
        'annotators_required':2,'zero_denominator':'N/A','single_document_genuine_multihop_allowed':True}}

def write_review_pack(folder,pack):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    (folder/'review-pack.json').write_text(json.dumps(pack,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'pairs').mkdir()
    index=['# Frozen79-pair sample — human review pending','', '| Pair | Kind / block | Inclusion probability |','|---|---|---|']
    for pair in pack['pairs']:
        key=pair['pair_id'];index.append(f"| [{key[:12]}](pairs/{key}.md) | {pair['kind']} / {' / '.join(pair['block'])} | {pair['inclusion_probability']:.6f} |")
        lines=[f'# Pair {key}', '', f"{pair['kind']}; sampling weight={pair['sampling_weight']}; human verdict: UNASSESSED.",'',
            'Review subject/date/series scope, evidence sufficiency, title/single-fact shortcuts and necessity of factual steps. Scores are heuristics.','']
        for doc in pair['documents']:
            lines.extend([f"## {doc['title']} (page {doc['page_id']})",'',f"Revision: {doc['revision_id']} at {doc['revision_timestamp']}; snapshot: {doc['snapshot']}.",
                f"Source: {doc['source_url']}",f"Raw SHA-256: `{doc['raw_hash']}`; manifest: `{doc['classification'].get('manifest_id')}`.",''])
            lines.extend(f"- sentence {s['sent_id']}: {s['text']}" for s in doc['extraction']['sentences'])
            lines.append('')
        lines.extend(['## Exact witness / risks','','```json',json.dumps({'witness':pair['witness'],'risks':pair['risks']},ensure_ascii=False,indent=2),'```',''])
        (folder/'pairs'/f'{key}.md').write_text('\n'.join(lines),encoding='utf-8')
    (folder/'index.md').write_text('\n'.join(index)+'\n',encoding='utf-8')
    fields=['pair_id','authored_claim_id','authored_claim','gold_sentence_ids','factual_steps','necessary_hops','evidence_sufficient','title_shortcut','subject_date_scope_ok','verdict','notes']
    for name in ('annotator-a.csv','annotator-b.csv'):
        with (folder/name).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            writer.writerows({'pair_id':p['pair_id']} for p in pack['pairs'])
    (folder/'README.md').write_text('''# Manual pair review — NOT human validated

Open review-pack.json and inspect both full leads, exact sentences, revision/hash/manifest references, pair witness and risk fields. Existing heuristic scores are not gold validity or hop labels. No claims have been generated by this pack.

Two independent external annotators use separate CSV sheets. Author claims manually, record exact evidence sentence IDs and factual steps, and judge evidence sufficiency, subject/date/series/edition scope and whether at least two steps are necessary. A title or single asserted fact answering the claim is a shortcut; two links/documents alone do not establish two hops. Genuine single-document multi-hop remains allowed. Resolve disagreements through the agreed external process, retaining both original judgments. Do not manufacture NEI examples from source gaps.

Report pair-conversion yield separately from authored-claim validation yield. Include unsupported authoring attempts in the former denominator; denominator zero is N/A. Weight sampled Comparison estimates by population/sample size; Bridge is a census. Report per-stratum counts, shared-endpoint/hub dependence and uncertainty rather than an unqualified 79-pair population precision. Final necessary-hop and label judgments remain human decisions.
''',encoding='utf-8')
