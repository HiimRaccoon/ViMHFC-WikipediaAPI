"""Selected-set connectivity potential from verified retained-lead links.

Bundle proposals are sparse; utility always measures the full eligible graph.
The heap keeps decreasing diversity/coverage gains as optimistic bounds and
refreshes local proposals whenever selected graph or pair utility can increase.
Paths express annotation potential, never validated multi-hop claims.
"""
from collections import Counter, defaultdict
from fractions import Fraction
import heapq

from vimfc.domain import SelectionManifest
from .hyperlink_rank import graph_adjacency

VERSION = 'rank-connectivity-1'


def _specific(article):
    """Use verified instance evidence as a weak tie-break, not a classifier."""
    cls = article.get('classification', {})
    provenance = cls.get('primary_provenance') or []
    verified = (cls.get('outcome') == 'completed'
                and cls.get('primary_entity_type') is not None and bool(provenance))
    if not verified:
        return 0
    return 1 + int(any((p.get('properties') or [])[:1] == ['P31'] for p in provenance))


def _options(rules):
    rules = rules or {}
    return {key: rules.get(key, True)
            for key in ('connectivity', 'hub_normalization', 'diversity')}


def select_connected(candidates, pairs, rules, quotas, target=20000):
    """Deterministic greedy marginal utility per newly admitted article.

    bridge_cap bounds incoming/outgoing proposal partners and triple reuse per
    middle article, not graph metrics or marginal retained-edge/path accounting.
    Soft quotas never relabel articles or block admission. No article thresholds
    are introduced. Unknown primary labels receive no artificial quota credit.
    """
    articles = {a['page_id']: a for a in candidates if a['quality']['eligible']}
    out, inc, _, _ = graph_adjacency(articles.values())
    neighbors = {p: out[p] | inc[p] for p in articles}
    degree = {p: len(neighbors[p]) for p in articles}
    options = _options(rules)
    cap = max(1, int(rules.get('bridge_cap', 20)))
    bundles = {(p,) for p in articles}
    partners = {}
    for p in sorted(articles):
        # Structural degree and factual quality decide proposal order, with ID ties.
        key = lambda q: (degree[q] if options['hub_normalization'] else 0,
                         -articles[q]['quality']['score'], q)
        partners[p] = (sorted(inc[p], key=key)[:cap], sorted(out[p], key=key)[:cap])
        for q in partners[p][0] + partners[p][1]:
            bundles.add(tuple(sorted((p, q))))
        # At most cap distinct triples for each middle; never enumerate its
        # unbounded predecessor/successor product. Rotate partner reuse.
        left, right = partners[p]
        count = 0
        for offset in range(len(right)):
            for index, a in enumerate(left):
                c = right[(index + offset) % len(right)]
                if a != c:
                    bundles.add(tuple(sorted((a, p, c))))
                    count += 1
                if count >= cap:
                    break
            if count >= cap:
                break

    pair_utility = defaultdict(dict)
    accepted_pairs = defaultdict(dict)
    for pair in pairs:
        a, b = pair['endpoint_a'], pair['endpoint_b']
        # Historical bridge links are supplied solely by the verified graph.
        if (a not in articles or b not in articles or a == b
                or pair.get('kind') not in ('comparison', 'shared_entity', 'shared')
                or pair.get('score', 0) <= 0):
            continue
        value = min(100, pair['score'] // 8)
        accepted_pairs[a][b] = max(accepted_pairs[a].get(b, 0), value)
        accepted_pairs[b][a] = max(accepted_pairs[b].get(a, 0), value)
    # Bound both endpoint reuse, independent of input order.
    for a in sorted(accepted_pairs):
        for b, value in sorted(accepted_pairs[a].items(), key=lambda kv: (-kv[1], kv[0])):
            if b <= a or len(pair_utility[a]) >= cap or len(pair_utility[b]) >= cap:
                continue
            pair_utility[a][b] = pair_utility[b][a] = value
            bundles.add((a, b))

    by_article = defaultdict(set)
    for bundle in bundles:
        for p in bundle:
            by_article[p].add(bundle)
    selected, ordered, steps = set(), [], []
    groups, subtypes = Counter(), Counter()

    def gain(bundle):
        new = tuple(p for p in bundle if p not in selected)
        if not new or len(new) > target - len(selected):
            return None
        after = selected | set(new)
        features = {'quality': sum(articles[p]['quality']['score'] for p in new),
                    'internal_edges': 0, 'nonisolated_vertices': 0, 'two_hop_paths': 0,
                    'normalized_edges': 0, 'normalized_paths': 0,
                    'pair': 0, 'deficit': 0, 'novelty': 0}
        edges = {(a, b) for p in new for a, b in
                 ([(p, q) for q in out[p] & after] + [(q, p) for q in inc[p] & after])}
        features['internal_edges'] = len(edges)
        features['normalized_edges'] = sum(
            1000 * 2 // max(2, degree[a] + degree[b]) for a, b in edges)
        affected = set(new)
        for p in new:
            affected.update(neighbors[p])
        for p in affected:
            was = p in selected and bool(neighbors[p] & selected)
            now = p in after and bool(neighbors[p] & after)
            features['nonisolated_vertices'] += int(now) - int(was)
            def paths(pool):
                if p not in pool:
                    return 0
                incoming, outgoing = inc[p] & pool, out[p] & pool
                return len(incoming) * len(outgoing) - len(incoming & outgoing)
            delta = paths(after) - paths(selected)
            features['two_hop_paths'] += delta
            features['normalized_paths'] += delta * 1000 * 4 // max(4, degree[p] ** 2)
        retained_pairs = {tuple(sorted((p, q))) for p in new for q in pair_utility[p] if q in after}
        features['pair'] = sum(pair_utility[a][b] for a, b in retained_pairs)
        local_groups, local_types = groups.copy(), subtypes.copy()
        for p in new:
            cls = articles[p]['classification']
            group = cls.get('primary_entity_type')
            quota = quotas.get(group, 0) if group is not None else 0
            if quota > 0:
                features['deficit'] += max(0, 100 * (quota - local_groups[group]) // quota)
            local_groups[group] += 1
            types = sorted({s['subtype_id'] for s in cls.get('subtypes', [])})
            features['novelty'] += sum(100 // (1 + local_types[t]) for t in types) // max(1, len(types))
            local_types.update(types)
        score = features['quality'] + 2 * features['pair']
        if options['connectivity']:
            edge = features['normalized_edges'] if options['hub_normalization'] else 1000 * features['internal_edges']
            path = features['normalized_paths'] if options['hub_normalization'] else 1000 * features['two_hop_paths']
            score += edge * 6 // 10 + 400 * features['nonisolated_vertices'] + path
        if options['diversity']:
            score += features['deficit'] + features['novelty'] // 2
        return Fraction(score, len(new)), Fraction(sum(_specific(articles[p]) for p in new), len(new)), new, features

    heap = []
    def queue(bundle):
        value = gain(bundle)
        if value is not None:
            heapq.heappush(heap, (-value[0], -value[1], bundle))
    for bundle in sorted(bundles):
        queue(bundle)
    while heap and len(selected) < target:
        old_score, old_specific, bundle = heapq.heappop(heap)
        value = gain(bundle)
        if value is None:
            continue
        score, specific, new, features = value
        if (-score, -specific) != (old_score, old_specific):
            queue(bundle)
            continue
        selected.update(new)
        ordered.extend(new)
        steps.append({'step': len(steps), 'added_page_ids': list(new),
                      'gain_numerator': score.numerator, 'gain_denominator': score.denominator,
                      'features': features, 'selected_count': len(selected), 'ranking_version': VERSION})
        affected = set(new)
        for p in new:
            groups[articles[p]['classification'].get('primary_entity_type')] += 1
            subtypes.update({s['subtype_id'] for s in articles[p]['classification'].get('subtypes', [])})
            affected.update(neighbors[p])
            affected.update(pair_utility[p])
            # A newly selected endpoint may complete a path two hops away.
            for q in neighbors[p]:
                affected.update(neighbors[q])
        refresh = set()
        for p in affected:
            refresh.update(by_article[p])
        for proposal in sorted(refresh):
            queue(proposal)
    shortage = max(0, target - len(selected))
    return SelectionManifest(ordered, steps, 'insufficient_supply' if shortage else 'completed', shortage)


def article_scores(articles, rules=None):
    """Pool-level diagnostic scores, separate from selected-set marginal gains."""
    articles = list(articles)
    out, inc, _, _ = graph_adjacency(articles)
    options = _options(rules)
    degree = {p: len(out[p] | inc[p]) for p in out}
    rows = []
    for article in articles:
        p = article['page_id']
        starts = sum(len(out[q] - {p}) for q in out[p])
        edges = sum(2000 // max(2, degree[p] + degree[q]) for q in out[p] | inc[p])
        connectivity = edges if options['hub_normalization'] else 1000 * degree[p]
        score = article['quality']['score'] + (connectivity if options['connectivity'] else 0)
        rows.append({'page_id': p, 'title': article['title'], 'score': score,
                     'candidate_out_degree': len(out[p]), 'candidate_in_degree': len(inc[p]),
                     'potential_two_hop_starts': starts,
                     'primary_entity_type': article['classification'].get('primary_entity_type'),
                     'verified_primary_preference': _specific(article), 'ranking_version': VERSION})
    return sorted(rows, key=lambda row: (-row['score'], -row['verified_primary_preference'], row['page_id']))
