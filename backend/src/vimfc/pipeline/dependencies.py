"""Immutable logical inputs for qid-4; no cache or network access here."""
from collections import deque
from dataclasses import asdict
import re
from vimfc.domain import ClassificationResult,Incompatible,SourceUnavailable,at_or_before,digest,utc
from .classify import classify,classification_children

FORMAT='vimfc-dependencies-1'


def article_identity(raw):
    ref=raw.ref
    return {'site':ref.site,'page_id':ref.page_id,'title':ref.title,'revision_id':ref.revision_id,
        'revision_timestamp':utc(ref.revision_timestamp),'content_hash':raw.local_sha256}


def policy_for(rules):
    p=rules.get('dependency_policy',{})
    if p.get('version') not in ('bounded-bfs-1','primary-first-1') or not 0<=p.get('max_depth',-1)<=min(12,rules['max_depth']) or not 1<=p.get('max_nodes',0)<=rules['max_cached_nodes']:
        raise Incompatible('Unsupported dependency policy or acquisition bound')
    policy={**p,'rules_hash':digest(rules),'root_stopping':rules.get('stop_at_configured_roots',False),'properties':['P31','P106','P279']}
    if p['version']=='primary-first-1':
        policy.update(primary_properties=['P31','P279'],person_subtype_properties=['P31','P106','P279'],
            primary_open_branch_rule='global_precedence_dominance',budget_priority='primary_then_optional')
    return policy


def node_identity(qid,item,snapshot,*,properties=('P31','P106','P279')):
    try:
        entity=item['entity']
        if not isinstance(entity,dict) or not isinstance(qid,str) or not re.fullmatch(r'Q[1-9][0-9]*',qid):
            raise ValueError('Invalid historical entity identity')
        # MediaWiki can serialize an empty PHP map as []; normalize ONLY that
        # explicit empty container, never arbitrary falsy/scalar values.
        claims=entity.get('claims')
        if type(claims) is list and claims==[]:claims={}
        if not isinstance(claims,dict):raise ValueError('Invalid claims container')
        for prop in properties:
            statements=claims.get(prop,[])
            if not isinstance(statements,list):raise ValueError('Invalid claims statements')
            for statement in statements:
                if not isinstance(statement,dict):raise ValueError('Invalid claim statement')
                snak=statement.get('mainsnak',{})
                if not isinstance(snak,dict) or snak.get('snaktype') not in ('value','novalue','somevalue'):raise ValueError('Invalid mainsnak')
                value=snak.get('datavalue',{})
                if not isinstance(value,dict):raise ValueError('Invalid datavalue')
                target=value.get('value',{})
                if snak['snaktype']=='value' and (not isinstance(target,dict) or 'id' not in target):
                    raise ValueError('Classification claim lacks an entity value')
                if isinstance(target,dict) and 'id' in target and (not isinstance(target['id'],str) or not re.fullmatch(r'Q[1-9][0-9]*',target['id'])):
                    raise ValueError('Invalid classification target QID')
        sitelinks=entity.get('sitelinks',{})
        if type(sitelinks) is list and sitelinks==[]:sitelinks={}
        if not isinstance(sitelinks,dict):raise ValueError('Invalid sitelinks container')
        if 'viwiki' in sitelinks and (not isinstance(sitelinks['viwiki'],dict) or not isinstance(sitelinks['viwiki'].get('title'),str)):
            raise ValueError('Invalid historical viwiki sitelink')
        content_hash=item.get('raw_hash') or digest(entity)
        valid=(entity['id']==qid and isinstance(item['revision_timestamp'],str) and isinstance(snapshot,str)
            and type(item['revision_id']) is int and item['revision_id']>0
            and at_or_before(item['revision_timestamp'],snapshot)
            and bool(re.fullmatch('[0-9a-f]{64}',content_hash))
            and item.get('content_model','wikibase-item')=='wikibase-item')
        if not valid: raise ValueError('Historical node identity/hash/cutoff mismatch')
        return {'site':'wikidata','qid':qid,'page_id':item.get('page_id'),'revision_id':item['revision_id'],
            'revision_timestamp':utc(item['revision_timestamp']),'snapshot':utc(snapshot),'content_model':'wikibase-item',
            'content_hash':content_hash,'entity_hash':digest(entity)}
    except (KeyError,TypeError,ValueError) as error:
        raise SourceUnavailable('Invalid dependency artifact',code='dependency_input_invalid',details={'qid':qid,'cause':str(error)}) from error


def make_manifest(article,qid,load_item,rules,snapshot):
    if rules.get('dependency_policy',{}).get('version')=='primary-first-1':
        from .primary import make_manifest as make_primary_manifest
        return make_primary_manifest(article,qid,load_item,rules,snapshot)
    policy=policy_for(rules);snapshot=utc(snapshot)
    if article.get('site')!='viwiki' or not at_or_before(article['revision_timestamp'],snapshot):
        raise Incompatible('Article is incompatible with dependency snapshot')
    queue=deque([(qid,0,None,None)])
    seen,limited=set(),set()
    nodes,edges,frontier={ },[],[]
    prefetched_depths=set()
    while queue:
        identity,depth,parent,prop=queue.popleft()
        if identity in seen or identity in limited: continue
        base={'qid':identity,'depth':depth,'parent':parent,'property':prop}
        if len(seen)>=policy['max_nodes']:
            limited.add(identity);frontier.append({**base,'reason':'hierarchy_node_budget_exhausted'})
            continue
        if hasattr(load_item,'prefetch') and depth not in prefetched_depths:
            identities=list(dict.fromkeys([identity]+[q for q,d,_,_ in queue if d==depth and q not in seen and q not in limited]))
            load_item.prefetch(identities[:policy['max_nodes']-len(seen)])
            prefetched_depths.add(depth)
        seen.add(identity)
        try:
            item=load_item(identity)
        except KeyError as error:
            raise SourceUnavailable('Required dependency has not been materialized',code='dependency_input_missing',details=base) from error
        except SourceUnavailable as error:
            if error.code not in ('history_unavailable','historical_content_unavailable'):
                raise
            frontier.append({**base,'reason':'history_unavailable','source_code':error.code})
            continue
        nodes[identity]=node_identity(identity,item,snapshot)
        for property_id,child in classification_children(identity,item['entity'],depth,rules):
            edges.append({'from':identity,'property':property_id,'to':child})
            if depth>=policy['max_depth']:
                frontier.append({'qid':child,'depth':depth+1,'parent':identity,'property':property_id,'reason':'dependency_policy_limit'})
            else:
                queue.append((child,depth+1,identity,property_id))
    # A child already processed through a shorter/sibling path is not an open
    # depth frontier. History-unavailable evidence remains explicit separately.
    frontier=[f for f in frontier if f['reason']!='dependency_policy_limit' or f['qid'] not in seen]
    body={'format':FORMAT,'snapshot':snapshot,'article':article,'root_qid':qid,'policy':policy,
        'nodes':[nodes[q] for q in sorted(nodes)],'edges':sorted(edges,key=lambda e:(e['from'],e['property'],e['to'])),
        'frontier':sorted(frontier,key=lambda f:(f['depth'],f['qid'],f['reason'],f['parent'] or '')),'complete':not frontier}
    return {**body,'manifest_id':digest(body)}


def verify_manifest(manifest,rules=None,article=None):
    if not isinstance(manifest,dict):raise Incompatible('Dependency manifest must be an object')
    body={k:v for k,v in manifest.items() if k!='manifest_id'}
    if body.get('format') not in (FORMAT,'vimfc-primary-first-1') or digest(body)!=manifest.get('manifest_id'):
        raise Incompatible('Dependency manifest checksum/format mismatch')
    if rules is not None and body['policy']!=policy_for(rules):
        raise Incompatible('Dependency manifest rules/policy mismatch; create a new run')
    if rules is not None:
        expected='vimfc-primary-first-1' if rules.get('dependency_policy',{}).get('version')=='primary-first-1' else FORMAT
        if body['format']!=expected:raise Incompatible('Dependency manifest format/policy mismatch')
    if article is not None and body['article']!=article:
        raise Incompatible('Dependency manifest article mismatch; create a new run')
    return manifest


def classify_manifest(manifest,items,rules):
    if rules.get('dependency_policy',{}).get('version')=='primary-first-1':
        from .primary import classify_manifest as classify_primary_manifest
        return classify_primary_manifest(manifest,items,rules)
    verify_manifest(manifest,rules)
    declared={}
    for ref in manifest['nodes']:
        qid=ref['qid']
        if qid not in items:
            raise SourceUnavailable('Manifest artifact is missing; classification was not run',code='dependency_input_missing',details={'qid':qid,'manifest_id':manifest['manifest_id']})
        if node_identity(qid,items[qid],manifest['snapshot'])!=ref:
            raise SourceUnavailable('Manifest artifact identity/hash differs',code='dependency_input_invalid',details={'qid':qid,'manifest_id':manifest['manifest_id']})
        declared[qid]=items[qid]
    historical={f['qid']:f for f in manifest['frontier'] if f['reason']=='history_unavailable'}
    def load(qid):
        if qid in declared: return declared[qid]
        if qid in historical: raise SourceUnavailable('Pinned negative lookup evidence',code=historical[qid]['source_code'])
        raise SourceUnavailable('Manifest omits a required dependency',code='dependency_input_missing',details={'qid':qid})
    expected=make_manifest(manifest['article'],manifest['root_qid'],load,rules,manifest['snapshot'])
    if expected!=manifest:
        raise Incompatible('Manifest graph/frontier does not replay from declared artifacts')
    base=classify(manifest['article']['title'],manifest['root_qid'],declared,rules,manifest['snapshot'])
    if manifest['frontier']:
        reasons={f['reason'] for f in manifest['frontier']}
        reason=next(r for r in ('history_unavailable','hierarchy_node_budget_exhausted','dependency_policy_limit') if r in reasons)
        return ClassificationResult('unresolved',provenance=base.provenance,reason=reason,diagnostics=manifest['frontier'])
    return base
