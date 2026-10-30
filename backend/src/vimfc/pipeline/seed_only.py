"""Scalable deterministic soft-quota sampling of frozen seed listings only."""
from collections import Counter, deque
from copy import deepcopy
from fractions import Fraction
from vimfc.domain import Incompatible, digest
from .discovery_policy import default_policy, verify_inventory

VERSION='seed-only-2'


def seed_policy(quotas):
    old=default_policy(quotas)
    return {k:old[k] for k in ('snapshot','quotas','reporting_groups','unmapped_reporting_groups')} | {
        'version':VERSION,'seed':'seed-only-wide-1','exploration_percent':20,'source':'frozen-seed-listings-only'}


def plan_seed_cohort(inventory,policy,budget):
    verify_inventory(inventory)
    if (policy!=seed_policy(policy['quotas']) or inventory['snapshot']!=policy['snapshot']
            or inventory['neighbors'] or type(budget) is not int or not 1<=budget<=100000):
        raise Incompatible('Invalid Seed-only policy, snapshot, budget or non-seed input')
    pools={g:[] for g in policy['quotas']}; unknown=[]; rows={}
    for row in inventory['seeds']:
        page=row.get('page_id')
        if (type(page) is not int or page<=0 or page in rows or row.get('namespace')!=0
            or not isinstance(row.get('title'),str) or not row['title'].strip()
            or row.get('candidate',{}).get('page_id')!=page or row['candidate'].get('title')!=row['title']
            or not row.get('origins') or not set(row.get('strata',[]))<=set(pools)):
            raise Incompatible('Invalid or duplicate frozen seed identity/origin/stratum')
        rows[page]=row
        if not row['strata']:unknown.append(page)
        for group in row['strata']:pools[group].append(page)
    keys={p:(digest([policy['seed'],p]),p) for p in rows}
    queues={g:deque(sorted(ids,key=keys.__getitem__)) for g,ids in pools.items()}
    exploration=deque(sorted(unknown,key=keys.__getitem__))
    fallback=deque(sorted(rows,key=keys.__getitem__)); selected=set(); served=Counter(); candidates=[]
    def take(queue):
        while queue and queue[0] in selected:queue.popleft()
        return queue.popleft() if queue else None
    for index in range(min(budget,len(rows))):
        for queue in queues.values():
            while queue and queue[0] in selected:queue.popleft()
        group=None; page=None
        if index%5==4:page=take(exploration)
        if page is None:
            available=[g for g,q in queues.items() if q]
            if available:
                group=min(available,key=lambda g:(Fraction(served[g],policy['quotas'][g]),g))
                page=take(queues[group]);served[group]+=1
            else:page=take(exploration)
        if page is None:page=take(fallback)
        selected.add(page);row=rows[page];candidate=deepcopy(row['candidate'])
        candidate.update(discovery_slot='seed',expected_strata=sorted(row['strata']),
            discovery_origins=deepcopy(row['origins']),discovery_version=VERSION,
            sampling_stratum=group or 'exploration')
        candidates.append(candidate)
    body={'format':'vimfc-discovery-cohort-1','policy':policy,'inventory_id':inventory['inventory_id'],
        'snapshot':inventory['snapshot'],'semantic_rules':inventory['semantic_rules'],'variant':'seed-only',
        'candidate_budget':budget,'candidates':candidates,'slot_counts':{'seed':len(candidates),'neighbor':0},
        'neighbor_shortfall':0,'seed_fallback':0,'shortfall':budget-len(candidates),'cohort_hash':digest(candidates)}
    return {**body,'manifest_id':digest(body)}
