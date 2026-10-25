"""Primary-First, Optional-Subtype: bounded evidence, never ambient cache state."""
from collections import deque
from copy import deepcopy
import re
from vimfc.domain import PrimaryFirstResult,SourceUnavailable,Incompatible,digest,utc,at_or_before
from vimfc.source.identities import normalize_title
from .classify import targets
from .dependencies import node_identity,policy_for,verify_manifest

FORMAT='vimfc-primary-first-1'
PROJECTION='classification-evidence-1'


def evidence_entity(entity):
    claims=entity.get('claims') or {}
    sitelinks=entity.get('sitelinks') or {}
    return {'id':entity['id'],'claims':{p:deepcopy(claims[p]) for p in ('P31','P279','P106') if p in claims},
        'sitelinks':{'viwiki':deepcopy(sitelinks['viwiki'])} if 'viwiki' in sitelinks else {}}


def node_ref(qid,item,snapshot):
    ref=node_identity(qid,item,snapshot,properties=())
    if item.get('evidence_projection'):
        if item['evidence_projection']!=PROJECTION or not isinstance(item.get('source_entity_hash'),str) or not re.fullmatch('[0-9a-f]{64}',item['source_entity_hash']):
            raise SourceUnavailable('Invalid compact evidence metadata',code='dependency_input_invalid',details={'qid':qid})
        if item['entity']!=evidence_entity(item['entity']):
            raise SourceUnavailable('Noncanonical evidence projection',code='dependency_input_invalid',details={'qid':qid})
        ref['entity_hash']=item['source_entity_hash']
    return {**ref,'evidence_hash':digest(evidence_entity(item['entity']))}


def project_item(item,snapshot):
    qid=item['entity']['id'];ref=node_ref(qid,item,snapshot)
    return {'entity':evidence_entity(item['entity']),'revision_id':ref['revision_id'],'revision_timestamp':ref['revision_timestamp'],
        'page_id':ref['page_id'],'content_model':ref['content_model'],'raw_hash':ref['content_hash'],
        'source_entity_hash':ref['entity_hash'],'evidence_projection':PROJECTION}


def _children(qid,item,depth,role,group,rules,snapshot):
    if depth>0 and qid in rules['roots'] and rules.get('stop_at_configured_roots'):return [],[]
    props=('P31',) if depth==0 else ('P279',)
    if depth==0 and role=='subtype' and group=='Person':props+=('P106',)
    children,failures=[],[]
    for prop in props:
        try:node_identity(qid,item,snapshot,properties=(prop,))
        except SourceUnavailable as error:
            if role=='primary':raise
            failures.append({'property':prop,'source_code':error.code});continue
        children.extend((prop,q) for q in targets(item['entity'],prop))
    return children,failures


def _walk(root,load,rules,snapshot,role,group,inventory,allocated):
    policy=policy_for(rules);queue=deque([(root,0,None,None)]);seen=set();nodes=[];edges=[];frontier=[];paths={};prepared_depths=set()
    while queue:
        q,depth,parent,prop=queue.popleft()
        if q in seen:continue
        seen.add(q);base={'qid':q,'depth':depth,'parent':parent,'property':prop,'role':role}
        if q not in allocated and len(allocated)>=policy['max_nodes']:
            frontier.append({**base,'reason':'hierarchy_node_budget_exhausted'});continue
        if hasattr(load,'prepare') and depth not in prepared_depths:
            batch=list(dict.fromkeys([q]+[c for c,d,_,_ in queue if d==depth and c not in seen]))
            new=[c for c in batch if c not in inventory]
            new=new[:max(0,policy['max_nodes']-len(allocated))]
            load.prepare(new,role);prepared_depths.add(depth)
        allocated.add(q)
        try:
            item=inventory.get(q) or load(q)
            node_ref(q,item,snapshot)
            children,property_failures=_children(q,item,depth,role,group,rules,snapshot)
        except KeyError as error:
            error=SourceUnavailable('Archived dependency input missing',code='dependency_input_missing')
            frontier.append({**base,'reason':'dependency_missing','source_code':error.code});continue
        except SourceUnavailable as error:
            if role=='primary' and (error.retryable or error.code not in ('dependency_input_missing','history_unavailable','historical_content_unavailable')):raise
            reason='history_unavailable' if error.code in ('history_unavailable','historical_content_unavailable') else 'dependency_missing'
            frontier.append({**base,'reason':reason,'source_code':error.code});continue
        inventory[q]=item;nodes.append(q)
        frontier.extend({**base,**failure,'reason':'dependency_missing'} for failure in property_failures)
        previous=paths.get(parent,{'path':[],'properties':[]})
        paths[q]={'path':previous['path']+[{'qid':q,'revision_id':item['revision_id'],'revision_timestamp':item['revision_timestamp']}],
            'properties':previous['properties']+([prop] if prop else [])}
        for p,child in children:
            edges.append({'from':q,'property':p,'to':child})
            if depth>=policy['max_depth']:
                frontier.append({'qid':child,'depth':depth+1,'parent':q,'property':p,'role':role,'reason':'dependency_policy_limit'})
            else:queue.append((child,depth+1,q,p))
    frontier=[f for f in frontier if f['reason']!='dependency_policy_limit' or f['qid'] not in seen]
    return {'node_qids':sorted(nodes),'edges':sorted(edges,key=lambda e:(e['from'],e['property'],e['to'])),
        'frontier':sorted(frontier,key=lambda f:(f['depth'],f['qid'],f['reason'],f['parent'] or '')),'complete':not frontier},paths


def _decision(root,article,inventory,graph,paths,rules):
    item=inventory.get(root)
    if not item:return {'status':'unresolved','group':None,'reason':'primary_dependency_missing','candidates':[],'provenance':[]}
    title=((item['entity'].get('sitelinks') or {}).get('viwiki') or {}).get('title','')
    if normalize_title(title)!=normalize_title(article['title']):
        return {'status':'unresolved','group':None,'reason':'historical_sitelink_mismatch','candidates':[],'provenance':[]}
    candidates=[];proofs=[]
    for q in graph['node_qids']:
        if q not in rules['roots']:continue
        mapping=rules['roots'][q];candidates.append({'qid':q,'group':mapping['group']})
        proofs.append({**paths[q],'root':q})
    groups={c['group'] for c in candidates};group=next((g for g in rules['precedence'] if g in groups),None)
    if graph['frontier'] and (not group or group!=rules['precedence'][0]):
        return {'status':'unresolved','group':None,'reason':'primary_ambiguity' if group else 'primary_dependency_missing',
            'candidates':candidates,'provenance':proofs}
    return {'status':'classified' if group else 'unclassified','group':group,
        'reason':('precedence_dominates_open_branches' if graph['frontier'] else 'closed_primary_graph') if group else 'no_supported_rule',
        'candidates':candidates,'provenance':proofs}


def make_manifest(article,root,load,rules,snapshot):
    policy=policy_for(rules);snapshot=utc(snapshot)
    if policy['version']!='primary-first-1' or article.get('site')!='viwiki' or not at_or_before(article['revision_timestamp'],snapshot):
        raise Incompatible('Primary-first article/policy/snapshot mismatch')
    inventory={};allocated=set()
    primary,paths=_walk(root,load,rules,snapshot,'primary',None,inventory,allocated)
    decision=_decision(root,article,inventory,primary,paths,rules)
    subtype={'node_qids':[],'edges':[],'frontier':[],'complete':False,'attempted':False};subpaths={}
    if decision['status']=='classified':
        subtype,subpaths=_walk(root,load,rules,snapshot,'subtype',decision['group'],inventory,allocated)
        subtype['attempted']=True
    nodes=[node_ref(q,inventory[q],snapshot) for q in sorted(inventory)]
    frontier=primary['frontier']+subtype['frontier']
    body={'format':FORMAT,'snapshot':snapshot,'article':article,'root_qid':root,'policy':policy,'nodes':nodes,
        'primary':primary,'subtype':subtype,'primary_decision':decision,'budget_used':len(allocated),
        'subtype_provenance':[{**subpaths[q],'root':q} for q in subtype['node_qids'] if rules['roots'].get(q,{}).get('subtype') and rules['roots'][q]['group']==decision['group']],
        'frontier':frontier,'complete':not frontier,'edges':[{**e,'role':role} for role,g in (('primary',primary),('subtype',subtype)) for e in g['edges']]}
    return {**body,'manifest_id':digest(body)}


def classify_manifest(manifest,items,rules):
    verify_manifest(manifest,rules)
    if manifest['format']!=FORMAT:raise Incompatible('Expected primary-first manifest')
    declared={}
    for ref in manifest['nodes']:
        q=ref['qid'];role='primary' if q in manifest['primary']['node_qids'] else 'subtype'
        if q not in items:raise SourceUnavailable('Declared manifest artifact missing',code='dependency_input_missing',details={'qid':q,'role':role})
        if node_ref(q,items[q],manifest['snapshot'])!=ref:raise SourceUnavailable('Manifest evidence/hash mismatch',code='dependency_input_invalid',details={'qid':q,'role':role})
        declared[q]=items[q]
    errors={f['qid']:f for f in manifest['frontier'] if f.get('source_code')}
    def load(q):
        if q in declared:return declared[q]
        if q in errors:raise SourceUnavailable('Pinned dependency acquisition evidence',code=errors[q]['source_code'])
        raise SourceUnavailable('Manifest omitted required evidence',code='dependency_input_missing')
    expected=make_manifest(manifest['article'],manifest['root_qid'],load,rules,manifest['snapshot'])
    if expected!=manifest:raise Incompatible('Primary/subtype inventories do not replay')
    d=manifest['primary_decision'];group=d['group'];primary=d['status'];subtypes=[];subproofs=[]
    if primary=='classified':
        for q in manifest['subtype']['node_qids']:
            mapping=rules['roots'].get(q,{})
            if mapping.get('group')==group and mapping.get('subtype'):
                subtypes.append({'subtype_id':q,'name':mapping['name']})
        subproofs=manifest['subtype_provenance']
    sg=manifest['subtype'];missing=any(f.get('source_code') for f in sg['frontier'])
    search='not_run' if not sg['attempted'] else 'dependency_missing' if missing else 'complete' if sg['complete'] else 'policy_limited'
    status='supported' if subtypes else 'not_attempted' if not sg['attempted'] else 'incomplete' if missing else 'no_configured_subtype_found' if sg['complete'] else 'undetermined_in_policy'
    outcome={'classified':'completed','unclassified':'unclassified','unresolved':'unresolved'}[primary]
    return PrimaryFirstResult(outcome,group,subtypes,d['provenance']+subproofs,
        None if outcome=='completed' else d['reason'],manifest['frontier'],primary,d['provenance'],d['candidates'],d['reason'],status,search,subproofs)
