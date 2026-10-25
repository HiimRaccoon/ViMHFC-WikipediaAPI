"""Lazy deterministic greedy: optimistic stale gains plus neighbor refreshes."""
from collections import Counter, defaultdict
from fractions import Fraction
import heapq
from vimfc.domain import CORPUS_SIZE, SelectionManifest


def select_corpus(candidates, pairs, rules, quotas, target=CORPUS_SIZE):
    articles={a['page_id']:a for a in candidates if a['quality']['eligible']}
    adjacency=defaultdict(dict)
    links=defaultdict(set)
    bundles=set((i,) for i in articles)
    for pair in pairs:
        a,b=pair['endpoint_a'],pair['endpoint_b']
        if a in articles and b in articles and pair['score']>0:
            adjacency[a][b]=max(adjacency[a].get(b,0),min(100,pair['score']//8))
            adjacency[b][a]=adjacency[a][b]
            bundles.add(tuple(sorted((a,b))))
    for i,a in articles.items():
        for occurrence in a['extraction']['links']:
            other=occurrence.get('target',{}).get('page_id')
            if occurrence['scope']=='retained_lead_prose' and other in articles and other!=i:
                links[i].add(other)
                links[other].add(i)
    by_article=defaultdict(set)
    for bundle in bundles:
        for i in bundle:
            by_article[i].add(bundle)
    selected,ordered,steps=set(),[],[]
    groups,subtypes=Counter(),Counter()
    weights=rules['selection_weights']

    def gain(bundle):
        new=tuple(i for i in bundle if i not in selected)
        if not new or len(new)>target-len(selected):
            return None
        features=Counter()
        retained=set()
        new_set=set(new)
        for i in new:
            a=articles[i]
            group=a['classification']['primary_entity_type']
            types=[s['subtype_id'] for s in a['classification']['subtypes']]
            quota=max(1,quotas[group])
            features['quality']+=a['quality']['score']
            features['deficit']+=max(0,100*(quota-groups[group])//quota)
            features['novelty']+=sum(100//(1+subtypes[t]) for t in types)//max(1,len(types))
            features['overrepresentation']+=min(100,100*groups[group]//quota)+sum(min(100,subtypes[t]) for t in types)//max(1,len(types))
            features['hub']+=max(0,len(links[i])-20).bit_length()*5
            features['connectivity']+=min(20,len(links[i]&(selected|new_set)))*5
            for partner,utility in adjacency[i].items():
                key=tuple(sorted((i,partner)))
                if partner in selected|new_set and key not in retained:
                    features['pair']+=utility
                    retained.add(key)
        score=sum(features[key]*weight*(-1 if key in ('hub','overrepresentation') else 1) for key,weight in weights.items())
        return Fraction(score,len(new)),new,dict(features)

    heap=[]
    def queue(bundle):
        value=gain(bundle)
        if value:
            heapq.heappush(heap,(-value[0],bundle))
    for bundle in sorted(bundles):
        queue(bundle)
    while heap and len(selected)<target:
        old,bundle=heapq.heappop(heap)
        value=gain(bundle)
        if value is None:
            continue
        score,new,features=value
        if -old!=score:
            queue(bundle)
            continue
        selected.update(new)
        ordered.extend(new)
        steps.append({'step':len(steps),'added_page_ids':list(new),'gain_numerator':score.numerator,'gain_denominator':score.denominator,'features':features,'selected_count':len(selected)})
        affected=set()
        for i in new:
            a=articles[i]
            groups[a['classification']['primary_entity_type']]+=1
            subtypes.update(s['subtype_id'] for s in a['classification']['subtypes'])
            affected.update(links[i])
            affected.update(adjacency[i])
        for i in sorted(affected-selected):
            for candidate_bundle in sorted(by_article[i]):
                queue(candidate_bundle)
    shortage=max(0,target-len(selected))
    return SelectionManifest(ordered,steps,'insufficient_supply' if shortage else 'completed',shortage)
