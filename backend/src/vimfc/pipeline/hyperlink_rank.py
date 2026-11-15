"""Sparse lead-only graph features; reuse deterministic soft-quota bundle selection."""
from collections import defaultdict
from copy import deepcopy

VERSION = 'rank-hyperlink-1'
SELECTION_WEIGHTS = {'quality': 1, 'pair': 8, 'connectivity': 6, 'deficit': 1,
                     'novelty': 1, 'hub': 5, 'overrepresentation': 2}


def graph_adjacency(articles):
    by_id = {a['page_id']: a for a in articles}
    outgoing = {p: set() for p in by_id}
    incoming = {p: set() for p in by_id}
    external = unresolved = 0
    for page, article in by_id.items():
        for link in article['extraction']['links']:
            if link.get('scope') != 'retained_lead_prose': continue
            target = link.get('target', {})
            if target.get('status') != 'resolved':
                unresolved += 1
                continue
            other = target['page_id']
            if other not in by_id:
                external += 1
            elif other != page:
                outgoing[page].add(other)
                incoming[other].add(page)
    return outgoing, incoming, external, unresolved


def graph_metrics(articles):
    articles = list(articles)
    out, inc, external, unresolved = graph_adjacency(articles)
    edges = sorted((a, b) for a in out for b in out[a])
    # Distinct vertices A→B→C; reciprocal A→B→A is not a two-hop chain.
    count = sum(len(inc[b]) * len(out[b]) - len(inc[b] & out[b]) for b in out)
    examples = []
    for b in sorted(out):
        for a in sorted(inc[b]):
            for c in sorted(out[b]):
                if a != c and len(examples) < 100: examples.append([a, b, c])
                if len(examples) >= 100: break
            if len(examples) >= 100: break
        if len(examples) >= 100: break
    remaining = set(out); components = []
    while remaining:
        frontier = [min(remaining)]; remaining.remove(frontier[0]); size = 0
        while frontier:
            p = frontier.pop(); size += 1
            new = (out[p] | inc[p]) & remaining
            remaining.difference_update(new); frontier.extend(sorted(new))
        components.append(size)
    return {'vertices': len(out), 'directed_nonself_edges': len(edges),
            'nonisolated_vertices': sum(bool(out[p] or inc[p]) for p in out),
            'isolated_vertices': sum(not out[p] and not inc[p] for p in out),
            'weak_component_sizes': sorted(components, reverse=True),
            'largest_weak_component': max(components, default=0),
            'potential_two_hop_paths': count, 'two_hop_examples': examples,
            'outside_pool_link_occurrences': external, 'unresolved_link_occurrences': unresolved,
            'edges': [list(e) for e in edges], 'paths_are_claims': False}


def article_ranking(articles):
    articles = list(articles)
    out, inc, _, _ = graph_adjacency(articles)
    rows = []
    for a in articles:
        p = a['page_id']; degree = len(out[p] | inc[p])
        paths = sum(len(out[b] - {p}) for b in out[p])
        score = (12 * min(20, len(out[p])) + 8 * min(20, len(inc[p])) + 10 * min(20, paths)
                 + a['quality']['score'] // 5 - 10 * max(0, degree - 20).bit_length())
        rows.append({'page_id': p, 'title': a['title'], 'score': score,
                     'candidate_out_degree': len(out[p]), 'candidate_in_degree': len(inc[p]),
                     'potential_two_hop_starts': paths,
                     'primary_entity_type': a['classification'].get('primary_entity_type')})
    return sorted(rows, key=lambda r: (-r['score'], r['page_id']))


def select_hyperlink(candidates, pairs, rules, quotas, target):
    from .select import select_corpus
    articles = [deepcopy(a) for a in candidates if a['quality']['eligible']]
    out, inc, _, _ = graph_adjacency(articles)
    graph_pairs = []
    for a in sorted(out):
        for b in sorted(out[a]):
            # Positive utilities come only from real eligible lead links. Pair
            # metadata remains separate from the operational directed graph.
            chain = bool((out[b] - {a}) or (inc[a] - {b}))
            degree = max(len(out[a] | inc[a]), len(out[b] | inc[b]))
            graph_pairs.append({'endpoint_a': a, 'endpoint_b': b, 'kind': 'bridge',
                                'score': max(8, (640 + 160 * chain) * min(20, degree) // max(1, degree))})
    for a in articles:
        a['extraction']['links'] = [l for l in a['extraction']['links']
                                     if l.get('scope') == 'retained_lead_prose'
                                     and l.get('target', {}).get('status') == 'resolved']
        # Internal accounting sentinel only; never persisted as a primary label.
        if a['classification'].get('primary_entity_type') is None:
            a['classification']['primary_entity_type'] = 'Unknown'
        a['quality']['score'] //= 5
    selection_rules = {**rules, 'version': 'rank-1', 'selection_weights': SELECTION_WEIGHTS}
    # Comparison remains supported-subtype-only. Preserve its fallback utility.
    return select_corpus(articles, graph_pairs + [p for p in pairs if p.get('kind') == 'comparison'],
                         selection_rules, {**quotas, 'Unknown': max(1, target)}, target)
