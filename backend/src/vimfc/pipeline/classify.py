from collections import deque
from vimfc.domain import ClassificationResult, at_or_before
from vimfc.source.identities import normalize_title


def targets(entity, prop):
    result=[]
    for statement in (entity.get('claims') or {}).get(prop,[]):
        if statement.get('rank')=='deprecated':
            continue
        snak=statement.get('mainsnak',{})
        value=snak.get('datavalue',{}).get('value',{})
        if snak.get('snaktype')=='value' and isinstance(value,dict) and value.get('id'):
            result.append(value['id'])
    return sorted(set(result))


def classification_children(identity,entity,depth,rules):
    # A recognized class closes only its own ancestor branch. Other instance /
    # occupation branches still run, so an unresolved sibling cannot be hidden.
    if depth>0 and identity in rules['roots'] and rules.get('stop_at_configured_roots',False):
        return []
    props=('P31','P106') if depth==0 else ('P279',)
    return [(prop,child) for prop in props for child in targets(entity,prop)]


def classify(article_title, qid, items, rules, snapshot):
    unresolved='unresolved' if rules.get('terminal_unresolved') else 'pending'
    root=items.get(qid)
    if not root or normalize_title((root['entity'].get('sitelinks') or {}).get('viwiki',{}).get('title',''))!=normalize_title(article_title):
        return ClassificationResult(unresolved,reason='historical_sitelink_mismatch')
    queue=deque([(qid,[],[],0)])
    visited, matches, provenance=set(),[],[]
    cutoffs,missing=[],[]
    while queue:
        identity,path,properties,depth=queue.popleft()
        if identity in visited:
            continue
        if rules.get('cached_closure_completion') and len(visited)>=rules['max_cached_nodes']:
            return ClassificationResult(unresolved,provenance=provenance,reason='hierarchy_node_budget_exhausted',diagnostics=[{'qid':identity,'depth':depth,'visited_count':len(visited),'node_budget':rules['max_cached_nodes'],'required_dependency':True}])
        visited.add(identity)
        item=items.get(identity)
        if not item or not at_or_before(item['revision_timestamp'],snapshot) or item['entity'].get('id')!=identity:
            missing.append({'qid':identity,'depth':depth,'properties':properties,'path':path,'required_dependency':True})
            continue
        path=path+[{'qid':identity,'revision_id':item['revision_id'],'revision_timestamp':item['revision_timestamp']}]
        mapping=rules['roots'].get(identity)
        if mapping:
            matches.append((identity,mapping))
            provenance.append({'path':path,'properties':properties,'root':identity})
        children=[(prop,child) for prop,child in classification_children(identity,item['entity'],depth,rules) if child not in visited]
        if children and depth>=rules['max_depth'] and not rules.get('cached_closure_completion'):
            cutoffs.append({'qid':identity,'depth':depth,'properties':properties,'path':path,'required_children':children})
        else:
            queue.extend((child,path,properties+[prop],depth+1) for prop,child in children)
    if missing:
        return ClassificationResult(unresolved,provenance=provenance,reason='missing_or_invalid_historical_dependency',diagnostics=missing)
    for cutoff in cutoffs:
        cutoff['required_children']=[(prop,child) for prop,child in cutoff['required_children'] if child not in visited]
    cutoffs=[cutoff for cutoff in cutoffs if cutoff['required_children']]
    if cutoffs:
        return ClassificationResult(unresolved,provenance=provenance,reason='truncated_hierarchy',diagnostics=cutoffs)
    if not matches:
        return ClassificationResult('unclassified',reason='no_supported_rule')
    groups={mapping['group'] for _,mapping in matches}
    group=next(g for g in rules['precedence'] if g in groups)
    subtypes=[{'subtype_id':identity,'name':mapping['name']} for identity,mapping in sorted(matches) if mapping['group']==group and mapping.get('subtype')]
    return ClassificationResult('completed',group,subtypes,provenance)
