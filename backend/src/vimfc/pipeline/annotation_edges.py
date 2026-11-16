"""Collection-only hyperlink labels; never promote candidates to evidence."""
from vimfc.domain import Incompatible, SourceUnavailable, digest
from vimfc.source.identities import normalize_title

VERSION = 'annotation-edge-1'
RANK_VERSION = 'rank-annotation-1'
EDGE_STATES = {'VERIFIED_EDGE', 'CANDIDATE_EDGE', 'BLOCKED_EDGE', 'UNRESOLVED'}


def classify_links(articles, archive, recovery=None):
    """Label checked Lead links from an already source-validated article pool.

    ``archive.resolve`` checks retained target revisions and redirect bytes.
    A supplied GraphRecoveryV2 may independently recertify a candidate.
    Legacy resolved targets alone only qualify as candidates.
    """
    if recovery is not None:
        from .graph_recovery_v2 import GraphRecoveryV2
        if not isinstance(recovery, GraphRecoveryV2) or recovery.context['snapshot'] != archive.context['snapshot']:
            raise Incompatible('Verified-edge authority must be scoped GraphRecoveryV2')
    records = [{**a, 'extraction': {**a['extraction'],
                'links': [dict(link) for link in a['extraction']['links']]}} for a in articles]
    pool_ids = {a['page_id'] for a in records}
    if len(pool_ids) != len(records):
        raise Incompatible('Duplicate candidate page identity')
    counts = {state: 0 for state in EDGE_STATES}
    for article in records:
        if article.get('snapshot') != archive.context['snapshot'] or not article.get('source_validation', {}).get('manifest_id'):
            raise Incompatible('Article lacks checked historical source/snapshot')
        if article.get('quality', {}).get('quality_version') != 'quality-relaxed-1':
            raise Incompatible('Annotation collection requires quality-relaxed-1')
        for link in article['extraction']['links']:
            state, target, reason = 'UNRESOLVED', {'status': 'unresolved'}, None
            title = normalize_title(link.get('target_title', ''))
            if link.get('scope') != 'retained_lead_prose' or not title:
                reason = 'outside_retained_lead'
            elif title.isdecimal():
                # Legacy numeric lookup keys can alias page IDs. Require V2.
                reason = 'numeric_title_requires_typed_selector'
                if recovery is not None:
                    decision = recovery.resolve(title, pool_ids, allow_live=False)
                    if decision.get('state') in {'RESOLVED_VALID', 'RESOLVED_OUTSIDE_COHORT'}:
                        target = decision['target']
                        state, reason = 'VERIFIED_EDGE', None
            else:
                try:
                    target = archive.resolve(title)
                    if target.get('status') == 'resolved':
                        if (target.get('snapshot') != archive.context['snapshot']
                                or type(target.get('page_id')) is not int or target['page_id'] <= 0):
                            raise Incompatible('Checked target identity/snapshot differs')
                        if not target.get('revision_id') or not target.get('raw_hash') or not target.get('proof'):
                            raise SourceUnavailable('Checked target evidence missing', code='graph_proof_missing')
                        state = 'CANDIDATE_EDGE'
                    else:
                        reason = target.get('reason', 'target_unresolved')
                except (Incompatible, ValueError, KeyError, TypeError) as error:
                    reason = str(error)
                    missing = reason in {'Missing historical move horizon', 'Pinned historical move index missing'}
                    state, target = ('UNRESOLVED' if missing else 'BLOCKED_EDGE'), {'status': 'unresolved'}
                    if missing:
                        reason = 'MISSING_PROOF: ' + reason
                except SourceUnavailable as error:
                    reason = error.code
                    if any(part in error.code for part in ('integrity', 'hash_mismatch',
                                                             'namespace', 'snapshot', 'cutoff')):
                        state = 'BLOCKED_EDGE'
                    target = {'status': 'unresolved'}
            if state == 'UNRESOLVED' and reason and ('proof' in reason.lower() or 'coverage' in reason.lower()):
                reason = reason if reason.startswith('MISSING_PROOF:') else 'MISSING_PROOF: ' + reason
            if state == 'CANDIDATE_EDGE' and recovery is not None:
                decision = recovery.resolve(title, pool_ids, allow_live=False)
                certified = decision.get('target') or {}
                if (decision.get('state') in {'RESOLVED_VALID', 'RESOLVED_OUTSIDE_COHORT'}
                        and certified.get('page_id') == target['page_id']
                        and certified.get('revision_id') == target['revision_id']
                        and certified.get('snapshot') == target['snapshot']
                        and certified.get('raw_hash') == target['raw_hash']
                        and certified.get('chain') == target.get('chain')
                        and certified.get('recovery_proof')):
                    state = 'VERIFIED_EDGE'
                elif decision.get('state') == 'SOURCE_INTEGRITY_BLOCKED':
                    state, target, reason = 'BLOCKED_EDGE', {'status': 'unresolved'}, decision.get('reason')
                elif decision.get('state') in {'RESOLVED_VALID', 'RESOLVED_OUTSIDE_COHORT', 'AMBIGUOUS'}:
                    state, target, reason = 'BLOCKED_EDGE', {'status': 'unresolved'}, 'contradictory_strict_target'
                else:
                    reason = 'MISSING_PROOF: historical_ownership'
            elif state == 'CANDIDATE_EDGE':
                reason = 'MISSING_PROOF: historical_ownership'
            link['target'], link['edge_status'] = target, state
            link['edge_policy'] = VERSION
            link['edge_reason'] = reason
            counts[state] += 1
    return records, counts


def rankable_links(articles):
    """Keep only explicit, revision-backed edges for the new ranking policy."""
    records = [{**a, 'extraction': {**a['extraction'],
                'links': list(a['extraction']['links'])}} for a in articles]
    for article in records:
        if article.get('quality', {}).get('quality_version') != 'quality-relaxed-1':
            raise Incompatible('rank-annotation-1 requires checked relaxed collection records')
        if any(link.get('edge_policy') != VERSION or link.get('edge_status') not in EDGE_STATES
               for link in article['extraction']['links']):
            raise Incompatible('Annotation ranking requires explicit edge-policy labels; run collection adapter first')
        article['extraction']['links'] = [link for link in article['extraction']['links']
            if link.get('edge_status') in {'VERIFIED_EDGE', 'CANDIDATE_EDGE'}
            and link.get('edge_policy') == VERSION
            and link.get('scope') == 'retained_lead_prose'
            and link.get('target', {}).get('status') == 'resolved'
            and type(link['target'].get('page_id')) is int
            and link['target']['page_id'] > 0
            and link['target'].get('snapshot') == article.get('snapshot')
            and link['target'].get('revision_id')
            and link['target'].get('raw_hash')
            and link['target'].get('proof')]
    return records


def edge_report(articles):
    """Keep candidate counts distinct from independently verified metrics."""
    articles = list(articles)
    ids = {a['page_id'] for a in articles if a['quality']['eligible']}
    occurrences = {state: 0 for state in EDGE_STATES}
    internal = {'VERIFIED_EDGE': set(), 'CANDIDATE_EDGE': set()}
    for article in articles:
        for link in article['extraction']['links']:
            state = link.get('edge_status', 'UNRESOLVED')
            if state not in EDGE_STATES:
                raise Incompatible('Unknown annotation edge state')
            occurrences[state] += 1
            target = link.get('target', {})
            if (state in internal and article['page_id'] in ids
                    and target.get('page_id') in ids and target['page_id'] != article['page_id']):
                internal[state].add((article['page_id'], target['page_id']))
    internal['CANDIDATE_EDGE'] -= internal['VERIFIED_EDGE']
    return {'policy': VERSION, 'link_occurrences': occurrences,
            'internal_directed_edges': {state: len(edges) for state, edges in internal.items()},
            'candidate_is_verified_evidence': False}


def select_articles(articles, archive, quotas, *, target=20000, recovery=None, structural_graph=None):
    """Select any checked candidate pool; upstream checkpoints remain unchanged."""
    from .select import select_corpus
    if structural_graph is not None:
        from .wikigraph import select_structural
        return select_structural(articles, archive, quotas, target, structural_graph)
    records, _ = classify_links(articles, archive, recovery)
    selection = select_corpus(records, [], {'version': RANK_VERSION}, quotas, target)
    edges = edge_report(records)
    manifest = {'version': 'annotation-collection-2', 'snapshot': archive.context['snapshot'],
                'source_run_id': archive.context.get('run_id'),
                'source_config_hash': archive.context.get('config_hash'),
                'quality_policy': 'quality-relaxed-1', 'edge_policy': VERSION,
                'ranking_policy': RANK_VERSION, 'target': target,
                'selected_page_ids': selection.page_ids, 'quotas': quotas,
                'input_digest': digest([(a['page_id'], a['revision_id'], a['raw_hash'],
                    a['source_validation']['manifest_id'], a['classification'].get('primary_entity_type'),
                    a['classification'].get('subtypes', []),
                    a['classification'], a['quality']['eligible'], a['quality']['score'],
                    [(a['page_id'], a['revision_id'], l.get('target_title'), l.get('scope'),
                      l.get('sent_id'), l.get('paragraph_id'), l.get('start'), l.get('end'),
                      l['edge_status'], l.get('edge_reason'), l['target'])
                     for l in a['extraction']['links']])
                    for a in sorted(records, key=lambda a: a['page_id'])])}
    manifest['manifest_id'] = digest(manifest)
    return {'policy': VERSION, 'ranking_policy': RANK_VERSION,
            'articles': records, 'selection': selection, 'edges': edges, 'manifest': manifest}
