"""Snapshot hyperlinks for discovery only; never sentence or claim evidence."""
from collections import Counter
import json
import re

import mwparserfromhell as mw

from vimfc.domain import Incompatible, SourceUnavailable, digest
from vimfc.source.identities import normalize_title, title_at_snapshot
from .hyperlink_rank import graph_metrics

VERSION = 'wikigraph-1'
RANK_VERSION = 'rank-wikigraph-1'


def source_links(content):
    """Explicit wikitext links, including body; no remote template expansion."""
    redirect = re.match(r'^\s*#(?:redirect|đổi|đổi hướng)\s*\[\[([^\]]+)\]\]', content, re.I)
    return {'titles': [str(link.title) for link in mw.parse(content).filter_wikilinks()],
            'redirect': redirect[1] if redirect else None}


def canonical_title(title):
    return normalize_title(title.strip().removeprefix(':').partition('#')[0])


def build_graph(pages, snapshot):
    """Input pages carry checked historical title, namespace and raw identity."""
    pages = sorted(pages, key=lambda p: p['page_id'])
    by_title = {}
    for page in pages:
        if (page['snapshot'] != snapshot or not page.get('raw_hash')
                or not page.get('revision_id') or not page.get('source_validation', {}).get('manifest_id')):
            raise Incompatible('WikiGraph source lacks checked snapshot/raw identity')
        by_title.setdefault(normalize_title(page['title']), []).append(page)
    if len({p['page_id'] for p in pages}) != len(pages):
        raise Incompatible('Duplicate WikiGraph page identity')

    def resolve(title, source):
        title = canonical_title(title) or normalize_title(source['title'])
        seen, chain = set(), []
        while True:
            if title in seen:
                return None, 'redirect_cycle', chain
            seen.add(title)
            owners = by_title.get(title, [])
            if len(owners) != 1:
                reason = 'ambiguous_target' if owners else 'target_not_in_frozen_cache'
                # An absent prefixed title has no namespace-0 proof.
                if not owners and ':' in title:
                    reason = 'namespace_unverified'
                return None, reason, chain
            owner = owners[0]
            if owner['namespace'] != 0:
                return None, 'nonzero_namespace', chain
            redirect = owner['structural_source']['redirect']
            if redirect is None:
                return owner, None, chain
            chain.append(owner['page_id'])
            title = canonical_title(redirect) or normalize_title(owner['title'])

    edges, occurrences = set(), []
    canonical_pages = [p for p in pages if p['namespace'] == 0 and p['structural_source']['redirect'] is None]
    for source in canonical_pages:
        for title in source['structural_source']['titles']:
            target, reason, chain = resolve(title, source)
            if target and target['page_id'] == source['page_id']:
                reason = 'self_link'
            verified = target is not None and reason is None
            occurrence = {'source_page_id': source['page_id'], 'target_title': title,
                          'target_page_id': target['page_id'] if target else None,
                          'state': 'VERIFIED_STRUCTURAL_EDGE' if verified else 'UNRESOLVED',
                          'reason': reason, 'redirect_chain': chain}
            occurrences.append(occurrence)
            if verified:
                edges.add((source['page_id'], target['page_id']))
    graph = {'policy': VERSION, 'snapshot': snapshot,
             'vertices': [p['page_id'] for p in canonical_pages],
             'sources': [[p['page_id'], p['revision_id'], p['raw_hash'],
                          p['source_validation']['manifest_id']] for p in pages],
             'edges': [list(edge) for edge in sorted(edges)], 'occurrences': occurrences,
             'unresolved_reasons': dict(sorted(Counter(o['reason'] for o in occurrences if o['reason']).items())),
             'structural_edges_are_factual_evidence': False}
    graph['graph_id'] = digest(graph)
    return graph


def archived_graph(archive, records):
    """Use all available pinned raw pages, before eligibility/selection."""
    pages = [{**a, 'namespace': a['source_validation']['namespace_scope']['namespace']}
             for a in records]
    known = {p['page_id'] for p in pages}
    unavailable = {}
    rows = archive.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND snapshot=? ORDER BY identity',
                              ('viwiki', archive.context['snapshot']))
    for row in rows:
        ref = json.loads(row[0]); page = ref['page_id']
        if page in known:
            continue
        try:
            title = title_at_snapshot(page, ref['title'], archive._moves(ref.get('lookup_timestamp')), archive.context['snapshot'])
            if ':' in title:
                # Reuse the exact namespace checkpoint for legitimate ns0 colon titles.
                acquired, validation = archive.article_source(page)
                raw = {'content': acquired['content'], 'local_sha256': acquired['local_sha256']}
                namespace = validation['namespace_scope']['namespace']
            else:
                raw, _, _ = archive._identity_raw(page, title, revision_id=ref['revision_id'],
                                                 timestamp=ref['revision_timestamp'])
                validation = {'snapshot': archive.context['snapshot'], 'ref': ref,
                              'raw_hash': raw['local_sha256'], 'policy': VERSION}
                namespace = 0
            pages.append({'page_id': page, 'title': title, 'snapshot': archive.context['snapshot'],
                          'revision_id': ref['revision_id'], 'raw_hash': raw['local_sha256'], 'namespace': namespace,
                          'source_validation': {'manifest_id': digest(validation)},
                          'structural_source': source_links(raw['content'])})
            known.add(page)
        except (Incompatible, SourceUnavailable, ValueError, KeyError, TypeError, OSError) as error:
            unavailable[str(page)] = getattr(error, 'code', str(error))
    graph = build_graph(pages, archive.context['snapshot'])
    graph['unavailable_sources'] = unavailable
    graph['graph_id'] = digest({k: v for k, v in graph.items() if k != 'graph_id'})
    return graph


def graph_records(articles, graph):
    """Private adapter to existing connectivity math; never persisted as evidence."""
    outgoing = {}
    for a, b in graph['edges']:
        outgoing.setdefault(a, []).append(b)
    return [{**a, 'extraction': {'links': [
        {'scope': 'retained_lead_prose', 'target': {'status': 'resolved', 'page_id': b}}
        for b in outgoing.get(a['page_id'], [])]}} for a in articles]


def structural_metrics(graph, ids):
    return graph_metrics(graph_records([{'page_id': p} for p in sorted(ids)], graph))


def select_structural(articles, archive, quotas, target, graph):
    from .connectivity_rank import select_connected
    articles = sorted(articles, key=lambda a: a['page_id'])
    if (graph['snapshot'] != archive.context['snapshot'] or graph.get('policy') != VERSION
            or graph['graph_id'] != digest({k: v for k, v in graph.items() if k != 'graph_id'})):
        raise Incompatible('WikiGraph snapshot/policy/fingerprint differs')
    if any(a['snapshot'] != graph['snapshot'] for a in articles):
        raise Incompatible('WikiGraph article snapshot differs')
    selection = select_connected(graph_records(articles, graph), [], {'version': RANK_VERSION}, quotas, target)
    manifest = {'version': 'annotation-collection-wikigraph-1', 'snapshot': archive.context['snapshot'],
                'source_run_id': archive.context.get('run_id'), 'source_config_hash': archive.context.get('config_hash'),
                'quality_policy': 'quality-relaxed-1', 'edge_policy': VERSION, 'ranking_policy': RANK_VERSION,
                'target': target, 'quotas': quotas, 'selected_page_ids': selection.page_ids,
                'graph_id': graph['graph_id'], 'input_digest': digest(articles)}
    manifest['manifest_id'] = digest(manifest)
    return {'policy': VERSION, 'ranking_policy': RANK_VERSION, 'articles': articles,
            'selection': selection, 'manifest': manifest, 'structural_graph': graph}
