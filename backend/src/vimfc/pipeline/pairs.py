"""Sparse directed bridges and capped cyclic comparison neighbors."""
from collections import defaultdict
from vimfc.domain import PairManifest, digest
from .rank import features_for, score_pair


def comparison_blocks(articles):
    blocks=defaultdict(set)
    for identity,a in sorted(articles.items()):
        for subtype in a['classification']['subtypes']:
            for signal in a['quality']['signals']:
                if signal['value_kind']=='year' and len(signal['values'])==1 and str(signal['values'][0]).isdigit():
                    blocks[(subtype['subtype_id'],signal['attribute_key'],'year')].add(identity)
    return blocks


def build_pairs(candidates, rules):
    articles={a['page_id']:a for a in candidates if a['quality']['eligible']}
    pairs,omitted=[],{'bridge':0,'comparison':0}
    for page_id,a in sorted(articles.items()):
        bridges={}
        for link in a['extraction']['links']:
            target=link.get('target',{})
            other=target.get('page_id')
            if link['scope']!='retained_lead_prose' or target.get('status')!='resolved' or other not in articles or other==page_id:
                continue
            features=features_for('bridge',a,articles[other],rules,link)
            pair={'kind':'bridge','endpoint_a':page_id,'endpoint_b':other,'features':features,'score':score_pair('bridge',features,rules),
                  'provenance':[{'source_sent_id':link['sent_id'],'original_target':link['original_target'],'target_resolution':target,'rules':rules['version']}]}
            if other not in bridges or pair['score']>bridges[other]['score']:
                bridges[other]=pair
        ordered=sorted(bridges.values(),key=lambda p:(-p['score'],p['endpoint_b']))
        pairs.extend(ordered[:rules['bridge_cap']])
        omitted['bridge']+=max(0,len(ordered)-rules['bridge_cap'])
    blocks=comparison_blocks(articles)
    used,counts=set(),defaultdict(int)
    for key,identities in sorted(blocks.items()):
        identities=sorted(identities,key=lambda p:(digest(p),p))
        n=len(identities)
        available=n*(n-1)//2
        generated=0
        for offset in range(1,min(n,rules['comparison_cap']+1)):
            for i,left in enumerate(identities):
                right=identities[(i+offset)%n]
                a_id,b_id=sorted((left,right))
                if a_id==b_id or (a_id,b_id) in used or counts[a_id]>=rules['comparison_cap'] or counts[b_id]>=rules['comparison_cap']:
                    continue
                used.add((a_id,b_id))
                counts[a_id]+=1
                counts[b_id]+=1
                features=features_for('comparison',articles[a_id],articles[b_id],rules)
                pairs.append({'kind':'comparison','endpoint_a':a_id,'endpoint_b':b_id,'features':features,'score':score_pair('comparison',features,rules),
                    'provenance':[{'block':key,'rules':rules['version'],'endpoint_signals':{str(p):articles[p]['quality']['signals'] for p in (a_id,b_id)}}]})
                generated+=1
        omitted['comparison']+=max(0,available-generated)
    return PairManifest(sorted(pairs,key=lambda p:(p['kind'],p['endpoint_a'],p['endpoint_b'])),omitted,rules['version'])
