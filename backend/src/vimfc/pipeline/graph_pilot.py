"""Frozen-admission Pilot for checked historical hyperlink target completion.

Resolution checkpoints belong to GraphRecovery; report generations are additive.
Titles may order work but never establish an edge or expand the frozen cohort.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

from vimfc.domain import Incompatible, canonical, digest
from vimfc.source.identities import normalize_title
from .connectivity_rank import VERSION, article_scores, select_connected
from .hyperlink_rank import graph_metrics
from .pairs import build_pairs
from .relaxed_pilot import evaluate_archive
from .relaxed_preservation import file_hash

RESOLVED = {'RESOLVED_VALID', 'RESOLVED_OUTSIDE_COHORT'}
STATES = RESOLVED | {'UNRESOLVED', 'AMBIGUOUS', 'SOURCE_INTEGRITY_BLOCKED'}


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _verified_manifest(root, name):
    manifest = _read(root / name)
    if digest({k: v for k, v in manifest.items() if k != 'manifest_id'}) != manifest['manifest_id']:
        raise Incompatible('Prior report manifest checksum differs')
    for relative, expected in manifest['files'].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or file_hash(path) != expected:
            raise Incompatible('Prior report file checksum differs: ' + relative)
    return manifest


def _admission(records):
    return {r['page_id']: {'quality': r['quality'], 'classification': r['classification'],
                           'source_manifest_id': r['source_validation']['manifest_id']}
            for r in records}


def _target_coverage(records, cohort_ids):
    states = Counter(); titles = set(); resolved = set(); unresolved = set(); outside = set()
    for article in records:
        if article['extraction']['outcome'] != 'completed': continue
        for link in article['extraction']['links']:
            if link.get('scope') != 'retained_lead_prose': continue
            title = normalize_title(link['target_title']); titles.add(title)
            target = link.get('target', {})
            if target.get('status') == 'resolved':
                resolved.add(title); states['resolved_occurrences'] += 1
                if target['page_id'] not in cohort_ids:
                    outside.add(title); states['outside_cohort_occurrences'] += 1
                else: states['inside_cohort_occurrences'] += 1
            else:
                unresolved.add(title); states['unresolved_occurrences'] += 1
    return {**{key: states[key] for key in ('resolved_occurrences', 'outside_cohort_occurrences',
                                           'inside_cohort_occurrences', 'unresolved_occurrences')},
            'link_occurrences': sum(states[k] for k in ('resolved_occurrences', 'unresolved_occurrences')),
            'unique_titles': len(titles), 'resolved_unique_titles': len(resolved),
            'unresolved_unique_titles': len(unresolved), 'outside_cohort_unique_titles': len(outside),
            'mixed_status_unique_titles': len(resolved & unresolved),
            'warning': 'Unique resolved/unresolved title counts overlap only for mixed-status titles; do not sum them.'}


def _clusters(articles):
    by_id = {a['page_id']: a for a in articles}
    adjacency = {p: set() for p in by_id}
    for a, b in graph_metrics(articles)['edges']:
        adjacency[a].add(b); adjacency[b].add(a)
    remaining = set(by_id); groups = []
    while remaining:
        first = min(remaining); remaining.remove(first); queue = [first]; members = []
        while queue:
            page = queue.pop(); members.append(page)
            new = adjacency[page] & remaining
            remaining.difference_update(new); queue.extend(sorted(new))
        if len(members) > 1:
            ids = sorted(members)
            groups.append({'page_ids': ids, 'size': len(ids),
                           'members': [{'page_id': p, 'title': by_id[p]['title'],
                                        'primary_entity_type': by_id[p]['classification'].get('primary_entity_type')}
                                       for p in ids]})
    return sorted(groups, key=lambda row: (-row['size'], row['page_ids']))


def read_prior_release(path, current):
    """Verify a prior release before using its measurements as a comparison."""
    root = Path(path).resolve()
    manifest = _verified_manifest(root, 'manifest.json')
    if not {'metrics.json', 'articles.json', 'cohort.json', 'edge-witnesses.json'} <= set(manifest['files']):
        raise Incompatible('Prior release manifest omits required evidence files')
    if (root / 'audit-manifest.json').exists():
        _verified_manifest(root, 'audit-manifest.json')
    metrics = _read(root / 'metrics.json')
    records = _read(root / 'articles.json')
    if any(metrics[k] != current[k] for k in ('source_run_id', 'snapshot', 'cohort_hash', 'source_config_hash')):
        raise Incompatible('Prior relaxed release uses a different source/cohort/snapshot')
    if _read(root / 'cohort.json') != current['cohort'] or _admission(records) != _admission(current['records']):
        raise Incompatible('Historical admission differs from the frozen relaxed release')
    if metrics['quality_policy'] != current['quality_policy']:
        raise Incompatible('Frozen relaxed feasibility policy differs')
    return {'metrics': metrics['metrics'], 'baseline_metrics': metrics['baseline_metrics'],
            'graph': metrics.get('graph', {}),
            'target_coverage': _target_coverage(records, set(current['cohort_page_ids'])),
            'edge_witnesses': _read(root / 'edge-witnesses.json'),
            'manifest_id': manifest['manifest_id'], 'path': str(root)}


def _selection_report(articles, pairs, rules, quotas, target):
    selection = select_connected(articles, pairs, rules, quotas, target=target)
    selected = set(selection.page_ids)
    groups = Counter(a['classification'].get('primary_entity_type') or 'Unknown'
                     for a in articles if a['page_id'] in selected)
    return {'selection': asdict(selection), 'graph': graph_metrics(a for a in articles if a['page_id'] in selected),
            'group_distribution': dict(sorted(groups.items()))}


def build_graph_report(relaxed, resolutions, *, prior_relaxed=None, diagnostic_target=100, recovery=None):
    """Rebuild only graph/ranking metadata; never change admission or labels."""
    records = deepcopy(relaxed['records'])
    cohort_ids = set(relaxed['cohort_page_ids'])
    decisions = deepcopy(resolutions)
    for title, decision in decisions.items():
        if decision.get('state') not in STATES:
            raise Incompatible('Unknown historical target decision state: ' + title)
        if decision['state'] in RESOLVED:
            target = decision.get('target') or {}
            if target.get('status') != 'resolved' or target.get('snapshot') != relaxed['snapshot']:
                raise Incompatible('Resolved target lacks matching historical snapshot/status: ' + title)
            expected = 'RESOLVED_VALID' if target['page_id'] in cohort_ids else 'RESOLVED_OUTSIDE_COHORT'
            if decision['state'] != expected:
                raise Incompatible('Target cohort-scope decision differs: ' + title)
    witnesses = []; occurrences = Counter(); unknown = []
    for article in records:
        links = []
        for old_link in article['extraction']['links']:
            link = deepcopy(old_link)
            if (link.get('scope') != 'retained_lead_prose'
                    or article['extraction']['outcome'] != 'completed'):
                # Unretained material is never supplied to an operational graph.
                link['target'] = {'status': 'unresolved', 'reason': 'outside_verified_retained_lead_prose'}
                links.append(link)
                continue
            title = normalize_title(link['target_title'])
            decision = decisions.get(title, {'state': 'UNRESOLVED', 'target': None, 'reason': 'not_evaluated'})
            occurrences[decision['state']] += 1
            if decision['state'] in RESOLVED:
                link['target'] = deepcopy(decision['target'])
                witnesses.append({'source_page_id': article['page_id'], 'source_revision_id': article['revision_id'],
                                  'source_raw_hash': article['raw_hash'],
                                  'source_manifest_id': article['source_validation']['manifest_id'],
                                  'target_decision_hash': digest(decision), **link})
            else:
                link['target'] = {'status': 'unresolved', 'reason': decision.get('reason', decision['state'])}
                unknown.append({'source_page_id': article['page_id'], 'target_title': title,
                                'sent_id': link.get('sent_id'), 'state': decision['state'],
                                'reason': decision.get('reason')})
            links.append(link)
        article['extraction']['links'] = links
    if _admission(records) != _admission(relaxed['records']):
        raise Incompatible('Graph completion changed frozen article admission')
    trusted = [a for a in records if a['extraction']['outcome'] == 'completed' and a['extraction']['full_lead'].strip()]
    eligible = [a for a in records if a['quality']['eligible']]
    rules = {'version': VERSION, 'bridge_cap': relaxed['ranking_policy'].get('bridge_cap', 20), 'connectivity': True,
             'hub_normalization': True, 'diversity': True}
    pair_rules = {key: value for key, value in relaxed['ranking_policy'].items()
                  if key in ('bridge_cap', 'comparison_cap', 'bridge_weights', 'comparison_weights', 'stopwords')}
    pair_rules['version'] = 'rank-1'
    pairs = build_pairs(eligible, pair_rules).pairs
    production = _selection_report(eligible, pairs, rules, relaxed['quotas'], 20000)
    diagnostic_target = min(max(0, int(diagnostic_target)), len(eligible))
    variants = {'full': {}, 'without_connectivity': {'connectivity': False},
                'without_hub_normalization': {'hub_normalization': False},
                'without_diversity': {'diversity': False}}
    ablations = {name: _selection_report(eligible, pairs, {**rules, **options}, relaxed['quotas'], diagnostic_target)
                 for name, options in variants.items()}
    eligible_graph = graph_metrics(eligible)
    before_eligible_graph = (prior_relaxed or {}).get('graph', {}).get('both_endpoints_relaxed_eligible')
    if not before_eligible_graph:
        before_eligible_graph = graph_metrics(a for a in relaxed['records'] if a['quality']['eligible'])
    before_edges = {tuple(edge) for edge in before_eligible_graph.get('edges', [])}
    after_edges = {tuple(edge) for edge in eligible_graph['edges']}
    new_edges = after_edges - before_edges; lost_edges = before_edges - after_edges
    metrics = {**relaxed['metrics'], 'eligible_edges': eligible_graph['directed_nonself_edges'],
               'nonisolated_eligible_vertices': eligible_graph['nonisolated_vertices'],
               'potential_two_hop_paths': eligible_graph['potential_two_hop_paths']}
    prior_metrics = prior_relaxed['metrics'] if prior_relaxed else relaxed['metrics']
    original_metrics = prior_relaxed['baseline_metrics'] if prior_relaxed else relaxed['baseline_metrics']
    def delta(key):
        old = prior_metrics.get(key)
        return metrics[key] - old if isinstance(old, (int, float)) else 'NOT_AVAILABLE'
    partial = (bool(relaxed.get('blockers')) or any(d['state'] not in RESOLVED for d in decisions.values())
               or bool(unknown) or bool(lost_edges))
    return {'format': 'vimfc-graph-pilot-1', 'status': 'PARTIAL' if partial else 'COMPLETE',
            'completed': True, 'pilot_accepted': False, 'eligibility_unchanged': True,
            **{k: relaxed[k] for k in ('source_run_id', 'source_config_hash', 'snapshot', 'cohort_hash', 'cohort_page_ids', 'cohort', 'quality_policy', 'quotas')},
            'ranking_policy': rules, 'pair_scoring_policy': pair_rules,
            'metrics': metrics, 'source_blockers': relaxed.get('blockers', []),
            'comparisons': {'original_pilot': original_metrics, 'relaxed_pilot': prior_metrics,
                            'graph_completed_pilot': metrics,
                            'relaxed_comparison_source': 'verified_release' if prior_relaxed else 'fresh_archive_replay',
                            'prior_release': prior_relaxed,
                            'graph_deltas_from_relaxed': {k: delta(k) for k in ('eligible_edges', 'nonisolated_eligible_vertices', 'potential_two_hop_paths')}},
            'graph': {'candidate_pool_trusted_leads': graph_metrics(trusted),
                      'both_endpoints_relaxed_eligible': eligible_graph,
                      'selected_corpus': production['graph'],
                      'connected_clusters': _clusters(eligible),
                      'scope': 'Verified historical retained Lead links; cohort membership is fixed; paths are annotation potential only.'},
            'graph_change': {'new_directed_edges': [list(e) for e in sorted(new_edges)],
                             'lost_directed_edges': [list(e) for e in sorted(lost_edges)],
                             'retained_directed_edges': [list(e) for e in sorted(before_edges & after_edges)],
                             'previous_verified_edges_preserved': not lost_edges,
                             'new_edge_witnesses': [w for w in witnesses if
                                 (w['source_page_id'], w['target']['page_id']) in new_edges],
                             'previous_edge_witnesses': (prior_relaxed or {}).get('edge_witnesses', []),
                             'warning': 'Earlier weaker target proofs are never restored when current validation is unresolved or blocked.'},
            'target_coverage': {'before': (prior_relaxed or {}).get('target_coverage') or
                                         _target_coverage(relaxed['records'], cohort_ids),
                                'after': _target_coverage(records, cohort_ids)},
            'selection': production['selection'], 'group_distribution': production['group_distribution'],
            'ranking': article_scores(eligible, rules), 'target_decisions': decisions,
            'target_states': dict(sorted(Counter(d['state'] for d in decisions.values()).items())),
            'target_occurrence_states': dict(sorted(occurrences.items())),
            'unique_target_titles': len(decisions), 'target_link_occurrences': sum(occurrences.values()),
            'unknown_targets': unknown, 'edge_witnesses': witnesses, 'records': records,
            'ablations': {'diagnostic_target': diagnostic_target, 'reduced_capacity': ablations,
                          'warning': 'Reduced-capacity diagnostics are separate from the unchanged target-20000 selection. Retaining every eligible article cannot demonstrate a selection gain.'},
            'recovery': recovery or {}, 'api_requests': {'wikimedia': (recovery or {}).get('physical_attempts', 0),
                                                       'groq': 0, 'other_llm': 0},
            'human_quality': 'UNASSESSED', 'paths_are_claims': False}


def evaluate_graph_archive(context, output, *, registry_path=None, expected_count=1000,
                           allow_live=False, limits=None, prior_relaxed=None, progress=None,
                           diagnostic_target=100, transport=None, sleep=None,
                           recovery_version='historical-target-resolution-1'):
    """Replay original admission, complete all target titles cache-first, then rank."""
    from .graph_recovery import GraphRecovery
    if recovery_version == 'historical-target-resolution-2':
        from .graph_recovery_v2 import GraphRecoveryV2
        GraphRecovery = GraphRecoveryV2
    elif recovery_version != 'historical-target-resolution-1':
        raise Incompatible('Unknown historical target recovery version')
    relaxed = evaluate_archive(context, expected_count=expected_count, registry_path=registry_path, progress=progress)
    prior = read_prior_release(prior_relaxed, relaxed) if prior_relaxed is not None else None
    root = Path(output)
    binding = {'format': 'vimfc-graph-pilot-checkpoint-1',
               **{k: relaxed[k] for k in ('source_run_id', 'source_config_hash', 'snapshot', 'cohort_hash')},
               'quality_policy_hash': digest(relaxed['quality_policy']),
               'admission_hash': digest(_admission(relaxed['records'])),
               'prior_release_manifest_id': prior['manifest_id'] if prior else None}
    if recovery_version != 'historical-target-resolution-1':
        binding['recovery_version'] = recovery_version
    root.mkdir(parents=True, exist_ok=True)
    checkpoint = root / 'pilot-checkpoint.json'
    if checkpoint.exists():
        if _read(checkpoint) != binding: raise Incompatible('Graph Pilot resume binding differs')
    else:
        with checkpoint.open('x', encoding='utf-8') as stream: stream.write(canonical(binding) + '\n')
    titles = Counter(normalize_title(l['target_title']) for a in relaxed['records']
                     if a['extraction']['outcome'] == 'completed'
                     for l in a['extraction']['links'] if l.get('scope') == 'retained_lead_prose')
    hints = {normalize_title(c['title']) for c in relaxed['cohort']}
    ordered = sorted(titles, key=lambda title: (title not in hints, -titles[title], title))
    kwargs = {'registry_path': registry_path}
    if limits is not None: kwargs['limits'] = limits
    if transport is not None: kwargs['transport'] = transport
    if sleep is not None: kwargs['sleep'] = sleep
    cohort_ids = set(relaxed['cohort_page_ids']); decisions = {}
    with GraphRecovery(context, root / 'recovery', **kwargs) as recovery:
        for index, title in enumerate(ordered):
            decisions[title] = recovery.resolve(title, cohort_ids, allow_live=False)
            if progress and (index + 1) % 100 == 0:
                progress({'event': 'graph_cache_replay', 'visited_targets': index + 1, 'unique_targets': len(ordered)})
        if allow_live:
            for index, title in enumerate(ordered):
                if recovery.summary().get('online_stopped'): break
                if decisions[title]['state'] == 'UNRESOLVED':
                    decisions[title] = recovery.resolve(title, cohort_ids, allow_live=True)
                if progress and (index + 1) % 10 == 0:
                    progress({'event': 'graph_historical_completion', 'visited_targets': index + 1,
                              'unique_targets': len(ordered), 'budget': recovery.summary()})
            # Other target acquisitions may have supplied cache needed by a
            # previously negative title. This pass is strictly cache-only.
            for title in ordered:
                if decisions[title]['state'] not in RESOLVED:
                    decisions[title] = recovery.resolve(title, cohort_ids, allow_live=False)
        summary = recovery.summary()
    return build_graph_report(relaxed, decisions, prior_relaxed=prior,
                              diagnostic_target=diagnostic_target, recovery=summary)


def write_graph_report(result, output):
    root = Path(output); root.mkdir(parents=True, exist_ok=False)
    omitted = {'records', 'cohort', 'edge_witnesses', 'target_decisions'}
    parts = {'metrics.json': {k: v for k, v in result.items() if k not in omitted},
             'cohort.json': result['cohort'], 'articles.json': result['records'],
             'target-decisions.json': result['target_decisions'], 'edge-witnesses.json': result['edge_witnesses']}
    for name, value in parts.items(): (root / name).write_text(canonical(value) + '\n', encoding='utf-8')
    keys = ('cohort_size', 'processed', 'trusted_historical_leads', 'eligible', 'eligible_edges',
            'nonisolated_eligible_vertices', 'potential_two_hop_paths', 'source_integrity_blockers')
    lines = ['# Historical graph completion Pilot', '', f"Status: {result['status']}. Eligibility unchanged: true.", '',
             '| Metric | Original Pilot | Relaxed Pilot | Graph completed |', '|---|---:|---:|---:|']
    for key in keys:
        values = [result['comparisons'][name].get(key, 'NOT_AVAILABLE') for name in
                  ('original_pilot', 'relaxed_pilot', 'graph_completed_pilot')]
        lines.append('| ' + key + ' | ' + ' | '.join(str(v) for v in values) + ' |')
    lines += ['', 'Only verified historical retained Lead links count. Outside-cohort targets never expand the frozen cohort.',
              'Potential paths are not validated multi-hop claims. Reduced-capacity ranking ablations are diagnostic.',
              'Unknown target titles and occurrences are reported separately. No LLM labels or human gold are assigned.', '']
    (root / 'comparison.md').write_text('\n'.join(lines), encoding='utf-8')
    manifest = {'format': result['format'], 'source_run_id': result['source_run_id'],
                'snapshot': result['snapshot'], 'cohort_hash': result['cohort_hash'],
                'quality_policy_hash': digest(result['quality_policy']), 'ranking_policy_hash': digest(result['ranking_policy']),
                'files': {p.name: file_hash(p) for p in sorted(root.iterdir()) if p.is_file()}}
    manifest['manifest_id'] = digest(manifest)
    (root / 'manifest.json').write_text(canonical(manifest) + '\n', encoding='utf-8')
    return manifest
