"""Pure discovery over a frozen seed/retained-lead inventory; no source/cache calls."""
from collections import Counter,defaultdict
from copy import deepcopy
import re
from vimfc.domain import GROUPS,Incompatible,canonical,digest,at_or_before,utc

VERSION='stratified-lead-1'
SNAPSHOT='2026-10-25T03:00:00Z'

def default_policy(quotas):
    if set(quotas)!=set(GROUPS) or any(type(v) is not int or v<=0 for v in quotas.values()) or sum(quotas.values())!=20000:
        raise Incompatible('Preserve eight positive soft quotas totaling20000')
    reporting={group:group for group in GROUPS}
    reporting['Landmark / Facility']='Location';reporting['Science / Technology']=None
    return {'version':VERSION,'snapshot':SNAPSHOT,'seed':'discovery-experiment-1','quotas':dict(quotas),
        'reporting_groups':reporting,'unmapped_reporting_groups':['Science / Technology'],
        'hybrid_seed_percent':70,'max_neighbors_per_seed':10,'frontier_scope':'retained_lead_prose'}

def verify_inventory(inventory):
    if inventory.get('format')!='vimfc-discovery-inventory-1' or inventory.get('inventory_id')!=digest({k:v for k,v in inventory.items() if k!='inventory_id'}):
        raise Incompatible('Discovery inventory checksum/format mismatch')
    if utc(inventory['snapshot'])!=inventory['snapshot']:raise Incompatible('Canonical snapshot required')

def seal_inventory(body):
    body=deepcopy(body);body.pop('inventory_id',None)
    for key in ('seeds','neighbors'):
        for row in body[key]:
            row['strata']=sorted(set(row.get('strata',[])))
            row['origins']=sorted({digest(o):o for o in row['origins']}.values(),key=canonical)
        body[key].sort(key=lambda r:(r.get('legacy_order',0),r['page_id'],digest(r)))
    return {**body,'inventory_id':digest(body)}

def _merge(rows,policy,*,neighbors=False):
    merged={}
    for row in rows:
        page=row.get('page_id');title=row.get('title');origins=row.get('origins',[])
        namespace_valid=row.get('namespace')==0 or (not neighbors and row.get('namespace') is None and row.get('candidate',{}).get('discovery_namespace_status')=='unverified')
        if (type(page) is not int or page<=0 or not isinstance(title,str) or not title.strip() or not namespace_valid
            or row.get('candidate',{}).get('page_id')!=page or row['candidate'].get('title')!=title or not origins
            or not set(row.get('strata',[]))<=set(policy['quotas'])):
            raise Incompatible('Unqualified discovery identity/namespace/stratum')
        if neighbors:
            for o in origins:
                target=o.get('target',{})
                if (o.get('kind')!='lead' or o.get('source_eligible') is not True or o.get('scope')!='retained_lead_prose'
                    or type(o.get('source_page_id')) is not int or type(o.get('source_revision_id')) is not int
                    or type(o.get('source_sent_id')) is not int or o['source_sent_id']<0
                    or not re.fullmatch('[a-f0-9]{64}',o.get('source_raw_hash',''))
                    or target.get('status')!='resolved' or target.get('page_id')!=page or target.get('title')!=title
                    or target.get('snapshot')!=policy['snapshot'] or type(target.get('revision_id')) is not int
                    or not re.fullmatch('[a-f0-9]{64}',target.get('raw_hash',''))
                    or not at_or_before(target['revision_timestamp'],policy['snapshot'])):
                    raise Incompatible('Frontier requires eligible historical retained-lead proof')
        if page in merged:
            old=merged[page]
            if old['title']!=title:raise Incompatible('Conflicting canonical identity in inventory')
            old['strata']=sorted(set(old['strata'])|set(row.get('strata',[])))
            old['origins']=sorted({digest(o):o for o in old['origins']+origins}.values(),key=canonical)
            old['legacy_order']=min(old.get('legacy_order',0),row.get('legacy_order',0))
            old['candidate_variants']=sorted({digest(c):c for c in old['candidate_variants']+[row['candidate']]}.values(),key=canonical)
            old['candidate']=deepcopy(min(old['candidate_variants'],key=lambda c:(c.get('lookup_title',title)!=title,canonical(c))))
        else:
            merged[page]=deepcopy(row);merged[page]['candidate_variants']=[deepcopy(row['candidate'])]
    return merged

def _seeds(rows,count,policy,exclude=()):
    remaining={p:r for p,r in rows.items() if p not in exclude};selected=[];served=Counter()
    while remaining and len(selected)<count:
        available={g for r in remaining.values() for g in r['strata']}
        if available:
            group=min(available,key=lambda g:(served[g]/policy['quotas'][g],g))
            row=min((r for r in remaining.values() if group in r['strata']),key=lambda r:(digest([policy['seed'],r['page_id']]),r['page_id']))
            served[group]+=1
        else:
            row=min(remaining.values(),key=lambda r:(digest([policy['seed'],r['page_id']]),r['page_id']))
        selected.append(row);del remaining[row['page_id']]
    return selected

def plan_cohort(inventory,policy,variant,budget):
    verify_inventory(inventory)
    if policy.get('version')!=VERSION or policy['snapshot']!=inventory['snapshot'] or policy!=default_policy(policy['quotas']):
        raise Incompatible('Unsupported discovery policy/snapshot; version changed semantics')
    if variant not in ('legacy','seed-only','hybrid') or type(budget) is not int or not 1<=budget<=80000:
        raise Incompatible('Unknown variant or invalid candidate budget')
    seeds=_merge(inventory['seeds'],policy);neighbors=_merge(inventory['neighbors'],policy,neighbors=True)
    origins={};strata={};candidate_variants={}
    for pool in (seeds,neighbors):
        for page,row in pool.items():
            if page in seeds and page in neighbors and seeds[page]['title']!=neighbors[page]['title']:
                raise Incompatible('Seed/frontier disagree on canonical historical title')
            origins[page]=sorted({digest(o):o for o in origins.get(page,[])+row['origins']}.values(),key=canonical)
            strata[page]=sorted(set(strata.get(page,[]))|set(row['strata']))
            candidate_variants[page]=sorted({digest(c):c for c in candidate_variants.get(page,[])+row['candidate_variants']}.values(),key=canonical)
    desired_neighbors=budget-budget*70//100 if variant=='hybrid' else 0
    desired_seeds=budget-desired_neighbors
    chosen=sorted(seeds.values(),key=lambda r:(r['legacy_order'],r['page_id']))[:budget] if variant=='legacy' else _seeds(seeds,desired_seeds,policy)
    seed_ids={r['page_id'] for r in chosen};selected=set(seed_ids);neighbor_rows=[];served=Counter()
    while len(neighbor_rows)<desired_neighbors:
        options=[]
        for page,row in neighbors.items():
            if page in selected:continue
            sources={o['source_page_id'] for o in row['origins']} & seed_ids
            sources={s for s in sources if served[s]<policy['max_neighbors_per_seed']}
            if not sources:continue
            source=min(sources,key=lambda s:(served[s],digest([policy['seed'],s]),s))
            options.append(((served[source],len(row['origins']),digest([policy['seed'],page]),page),source,row))
        if not options:break
        _,source,row=min(options,key=lambda x:x[0]);served[source]+=1;neighbor_rows.append(row);selected.add(row['page_id'])
    fallback=[]
    if variant=='hybrid' and len(chosen)+len(neighbor_rows)<budget:
        fallback=_seeds(seeds,budget-len(chosen)-len(neighbor_rows),policy,selected);chosen+=fallback
    candidates=[]
    for slot,rows in (('seed',chosen),('neighbor',neighbor_rows)):
        for row in rows:
            # Keep the candidate itself independent of the eventual run ID or
            # machine cache; origin hints never become a primary classification.
            candidate=deepcopy(row['candidate']);candidate.update(discovery_slot=slot,expected_strata=strata[row['page_id']],
                discovery_origins=origins[row['page_id']],discovery_candidate_variants=candidate_variants[row['page_id']],discovery_version=VERSION)
            candidates.append(candidate)
    if variant=='hybrid':
        seed_queue=candidates[:len(chosen)];neighbor_queue=candidates[len(chosen):];candidates=[]
        si=ni=0
        for position in range(len(seed_queue)+len(neighbor_queue)):
            use_neighbor=(position%10>=7 and ni<len(neighbor_queue)) or si==len(seed_queue)
            candidates.append(neighbor_queue[ni] if use_neighbor else seed_queue[si])
            if use_neighbor:ni+=1
            else:si+=1
    body={'format':'vimfc-discovery-cohort-1','policy':policy,'inventory_id':inventory['inventory_id'],'snapshot':inventory['snapshot'],
        'semantic_rules':inventory['semantic_rules'],'variant':variant,'candidate_budget':budget,'candidates':candidates,
        'slot_counts':{'seed':len(chosen),'neighbor':len(neighbor_rows)},'neighbor_shortfall':desired_neighbors-len(neighbor_rows),
        'seed_fallback':len(fallback),'shortfall':budget-len(candidates),'cohort_hash':digest(candidates)}
    return {**body,'manifest_id':digest(body)}

def verify_cohort(manifest,inventory):
    expected=plan_cohort(inventory,manifest['policy'],manifest['variant'],manifest['candidate_budget'])
    if manifest!=expected:raise Incompatible('Discovery manifest differs from deterministic plan')
