"""Explicit offline Pilot: same frozen identities, separate decisions and reports."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import time

from vimfc.domain import GROUPS, Incompatible, SourceUnavailable, canonical, digest
from .quality import assess_quality
from .relaxed_archive import CheckedArchive
from .hyperlink_rank import graph_metrics, article_ranking, SELECTION_WEIGHTS
from .pairs import build_pairs
from .select import select_corpus

SNAPSHOT = '2026-10-25T03:00:00Z'


def safe_output(output, protected):
    path = Path(output).resolve()
    for item in protected:
        other = Path(item).resolve()
        if path == other or path.is_relative_to(other) or other.is_relative_to(path):
            raise ValueError('Output overlaps protected source')
    if path.exists(): raise FileExistsError('Pilot output must be new')
    return path


def relaxed_rules(config):
    return {**deepcopy(config['extraction']), 'quality_version': 'quality-relaxed-1',
            'quality_revision': 'quality-relaxed-1', 'min_sentences': 1, 'min_tokens': 0}


def evaluate_archive(context, *, expected_count=1000, registry_path=None, progress=None):
    started = time.perf_counter()
    records = []; blockers = []; link_unknowns = []; baseline = []; baseline_graph_blockers = []
    with CheckedArchive(context, registry_path) as archive:
        if len(archive.cohort) != expected_count: raise Incompatible('Frozen cohort cardinality differs')
        config = deepcopy(archive.config); rules = relaxed_rules(config)
        rank_rules = {**deepcopy(config['ranking']), 'version': 'rank-hyperlink-1', 'selection_version': 'hyperlink-greedy-1',
                      'selection_weights': SELECTION_WEIGHTS,
                      'graph_scoring': {'edge_base': 640, 'chain_bonus': 160, 'degree_cap': 20,
                                        'hub_edge_normalization': 'min(20,degree)/degree', 'quality_divisor': 5}}
        cohort = deepcopy(archive.cohort)
        queue = {r['page_id']: json.loads(r['record_json']) for r in archive.db.execute(
            'SELECT page_id,record_json FROM candidate_work WHERE run_id=?', (context['run_id'],))}
        processed = {int(r[0]) for r in archive.db.execute(
            "SELECT identity FROM stage_results WHERE run_id=? AND stage='process' AND status='completed'", (context['run_id'],))}
        registry_audit = {'event_count': 0, 'states': {}}
        if archive.registry:
            registry_audit = {'event_count': archive.registry.db.execute('SELECT count(*) FROM events').fetchone()[0],
                              'states': dict(Counter(o['state'] for o in archive.registry.observations.values()))}
        for index, candidate in enumerate(cohort):
            page = candidate['page_id']; work = queue.get(page, {})
            if work.get('status') == 'quarantined_integrity':
                blockers.append({'page_id': page, 'title': candidate['title'], 'stage': 'reviewed_integrity_quarantine',
                                 'reason': work.get('error_code'), 'original_work': work})
                continue
            try:
                if page in processed:
                    record = archive.processed(page)
                    baseline.append(deepcopy(record))
                elif work.get('error_code') in ('missing_item_hint', 'association_review_required'):
                    # No missing Wikidata association can bypass Wikipedia intake.
                    record = archive.source_only(page)
                else:
                    blockers.append({'page_id': page, 'title': candidate['title'], 'stage': 'source_not_complete', 'original_work': work})
                    continue
            except (Incompatible, SourceUnavailable, ValueError, KeyError, TypeError, OSError) as error:
                blockers.append({'page_id': page, 'title': candidate['title'], 'stage': 'source_or_dependency_validation',
                                 'reason': str(error), 'error_type': type(error).__name__})
                continue
            old = record['quality']; record['baseline_quality'] = old
            record['quality'] = assess_quality(record['extraction'], record['classification'], record['article_kinds'], rules)
            if record['extraction']['outcome'] == 'completed':
                links = []
                try:
                    for link in record['extraction']['links']:
                        target = archive.resolve(link['target_title'])
                        if target['status'] != 'resolved':
                            link_unknowns.append({'source_page_id': page, 'target_title': link['target_title'],
                                                  'sent_id': link['sent_id'], 'reason': target.get('reason')})
                        links.append({**link, 'target': target})
                except (Incompatible, SourceUnavailable, ValueError, KeyError, TypeError, OSError) as error:
                    # A missing proof is unknown; a corrupt proof blocks this
                    # source's graph, not unrelated independently checked leads.
                    link_unknowns.append({'source_page_id': page, 'reason': str(error), 'integrity_failure': True})
                    links = [{**l, 'target': {'status': 'unresolved', 'reason': 'graph_validation_failed'}} for l in record['extraction']['links']]
                    record['graph_integrity_blocked'] = True
                record['extraction']['links'] = links
            records.append(record)
            if progress and ((index + 1) % 100 == 0 or index + 1 == len(cohort)):
                progress({'event': 'archived_replay', 'visited': index + 1, 'processed': len(records), 'blockers': len(blockers)})
        # Save old graph exactly as it was, validating every admitted target
        # through the same archive resolver before comparing metrics.
        baseline_eligible_ids = {r['page_id'] for r in baseline if r['quality']['eligible']}
        for record in baseline:
            original = record.pop('original_graph_links')
            try:
                for link in original:
                    target = link.get('target', {})
                    if target.get('status') == 'resolved':
                        if not {'page_id', 'title', 'revision_id', 'revision_timestamp', 'raw_hash', 'snapshot', 'proof'} <= set(target):
                            raise Incompatible('Malformed saved baseline target proof')
                        if record['page_id'] not in baseline_eligible_ids or target['page_id'] not in baseline_eligible_ids:
                            # Only internal eligible edges enter the published
                            # baseline metric. External namespace uncertainty
                            # cannot invalidate a separately proven internal edge.
                            link['target'] = {'status': 'unresolved', 'reason': 'outside_baseline_graph_scope'}
                            continue
                        checked = archive.resolve(link['target_title'])
                        if checked != target: raise Incompatible('Original baseline target proof differs')
            except (Incompatible, SourceUnavailable, ValueError, KeyError, TypeError, OSError) as error:
                baseline_graph_blockers.append({'page_id': record['page_id'], 'reason': str(error)})
                original = []
            record['extraction']['links'] = original
        source_work = dict(Counter(r.get('status', 'unknown') for r in queue.values()))

    trusted = [r for r in records if r['extraction']['outcome'] == 'completed' and r['extraction']['full_lead'].strip()]
    eligible = [r for r in records if r['quality']['eligible']]
    old_eligible = [r for r in baseline if r['quality']['eligible']]
    old_ids = {r['page_id'] for r in old_eligible}; new_ids = {r['page_id'] for r in eligible}
    pairs = build_pairs(eligible, config['ranking'])
    selection = select_corpus(eligible, pairs.pairs, rank_rules, config['quotas'])
    selected_ids = set(selection.page_ids)
    selected = [r for r in eligible if r['page_id'] in selected_ids]
    ablations = {}; independent_gains = {}; necessary = Counter(); gained_examples = []
    for name in ('sentence_threshold', 'token_threshold', 'classification_gate'):
        ids = []
        for r in baseline:
            changed = deepcopy(config['extraction']); cls = deepcopy(r['classification'])
            if name == 'sentence_threshold': changed['min_sentences'] = 1
            elif name == 'token_threshold': changed['min_tokens'] = 0
            else: cls['outcome'] = 'completed'
            if not r['quality']['eligible'] and assess_quality(r['extraction'], cls, r['article_kinds'], changed)['eligible']:
                ids.append(r['page_id'])
        independent_gains[name] = {'count': len(ids), 'page_ids': ids}
    for r in eligible:
        reasons = r['baseline_quality']['reasons']
        flags = [n for n, reason in (('sentence_threshold', 'too_few_complete_sentences'), ('token_threshold', 'too_few_tokens'),
                                     ('classification_gate', 'unsupported_classification'), ('factual_signal', 'no_factual_signal')) if reason in reasons]
        if r['page_id'] not in old_ids:
            necessary.update(flags)
            gained_examples.append({'page_id': r['page_id'], 'title': r['title'], 'baseline_reasons': reasons,
                                    'relaxations': flags, 'primary_entity_type': r['classification'].get('primary_entity_type'),
                                    'sentences': r['extraction']['sentences'], 'source_manifest_id': r['source_validation']['manifest_id']})
    ablations = {'single_change_from_original_baseline': independent_gains,
                 'overlapping_baseline_reasons_among_newly_eligible': dict(necessary),
                 'warning': 'Single-change gains hold all other old criteria fixed. Overlapping reason counts MUST NOT be summed.',
                 'unique_gained': len(new_ids - old_ids), 'unique_lost': len(old_ids - new_ids)}
    groups = {g: 0 for g in (*GROUPS, 'Unknown')}
    for r in eligible: groups[r['classification'].get('primary_entity_type') or 'Unknown'] += 1
    old_graph = graph_metrics(old_eligible); eligible_graph = graph_metrics(eligible)
    graph = {'candidate_pool_trusted_leads': graph_metrics(trusted), 'both_endpoints_relaxed_eligible': eligible_graph,
             'selected_corpus_diagnostic': graph_metrics(selected), 'baseline_eligible': old_graph,
             'baseline_graph_blockers': baseline_graph_blockers,
             'scope': 'Verified retained historical Lead links only; category/dump/body metadata never becomes evidence.',
             'unknown_targets': link_unknowns}
    edge_witnesses = []
    for r in trusted:
        for link in r['extraction']['links']:
            if link.get('target', {}).get('status') == 'resolved':
                edge_witnesses.append({'source_page_id': r['page_id'], 'source_revision_id': r['revision_id'],
                                       'source_raw_hash': r['raw_hash'], 'source_manifest_id': r['source_validation']['manifest_id'], **link})
    metrics = {'cohort_size': len(cohort), 'processed': len(records), 'trusted_historical_leads': len(trusted), 'eligible': len(eligible),
               'eligible_without_verified_primary': sum(r['classification']['outcome'] != 'completed' for r in eligible),
               'factual_leads_without_verified_primary': sum(bool(r['quality']['signals']) and r['classification']['outcome'] != 'completed' for r in trusted),
               'source_integrity_blockers': len(blockers), 'source_only_hint_records': len(records) - len(baseline),
               'eligible_edges': eligible_graph['directed_nonself_edges'], 'nonisolated_eligible_vertices': eligible_graph['nonisolated_vertices'],
               'potential_two_hop_paths': eligible_graph['potential_two_hop_paths']}
    baseline_metrics = {'cohort_size': len(cohort), 'processed': len(baseline),
                        'trusted_historical_leads': sum(r['extraction']['outcome'] == 'completed' and bool(r['extraction']['full_lead'].strip()) for r in baseline),
                        'eligible': len(old_eligible), 'eligible_without_verified_primary': 0,
                        'eligible_edges': old_graph['directed_nonself_edges'], 'nonisolated_eligible_vertices': old_graph['nonisolated_vertices'],
                        'potential_two_hop_paths': old_graph['potential_two_hop_paths'],
                        'source_integrity_blockers': len(cohort) - len(baseline)}
    if baseline_graph_blockers:
        for key in ('eligible_edges', 'nonisolated_eligible_vertices', 'potential_two_hop_paths'):
            baseline_metrics[key] = 'NOT_AVAILABLE'
        graph['baseline_eligible'] = {'status': 'NOT_AVAILABLE', 'blockers': baseline_graph_blockers}
    verified_baseline_subset = deepcopy(baseline_metrics)
    if len(baseline) != len(processed):
        for key in baseline_metrics:
            if key != 'cohort_size': baseline_metrics[key] = 'NOT_AVAILABLE'
        graph['baseline_eligible'] = {'status': 'NOT_AVAILABLE', 'reason': 'archived_process_inputs_failed_validation'}
    return {'format': 'vimfc-relaxed-pilot-1', 'status': 'PARTIAL' if blockers or baseline_graph_blockers or any(r.get('graph_integrity_blocked') for r in records) else 'COMPLETE',
            'completed': True, 'data_complete': not blockers, 'integrity_complete': not blockers and not baseline_graph_blockers and not any(r.get('graph_integrity_blocked') for r in records),
            'pilot_accepted': False, 'source_run_id': context['run_id'], 'snapshot': context['snapshot'],
            'cohort_hash': digest(cohort), 'cohort_page_ids': [c['page_id'] for c in cohort], 'cohort': cohort,
            'quality_policy': rules, 'ranking_policy': rank_rules, 'quotas': config['quotas'],
            'source_config_hash': context['config_hash'], 'baseline_metrics': baseline_metrics, 'metrics': metrics,
            'verified_baseline_subset_metrics': verified_baseline_subset,
            'baseline_work_status_counts': source_work, 'registry': registry_audit, 'blockers': blockers,
            'group_distribution': groups, 'ablations': ablations, 'graph': graph, 'edge_witnesses': edge_witnesses,
            'ranking': article_ranking(eligible), 'selection': asdict(selection),
            'gained_examples': gained_examples, 'rejected_examples': [{'page_id': r['page_id'], 'title': r['title'], 'reasons': r['quality']['reasons'],
                'classification_reason': r['classification'].get('reason')} for r in records if not r['quality']['eligible']],
            'quality_rejections_overlapping': dict(Counter(reason for r in records for reason in r['quality']['reasons'])),
            'classification_outcomes': dict(Counter(r['classification']['outcome'] for r in records)),
            'classification_reasons': dict(Counter(r['classification'].get('reason') or 'none' for r in records)),
            'records': records, 'api_requests': {'wikimedia': 0, 'groq': 0, 'other_llm': 0},
            'human_quality': 'UNASSESSED', 'elapsed_seconds': time.perf_counter() - started}


def write_report(result, output):
    root = Path(output); root.mkdir(parents=True, exist_ok=False)
    parts = {'metrics.json': {k: v for k, v in result.items() if k not in ('records', 'cohort', 'edge_witnesses', 'gained_examples', 'rejected_examples')},
             'cohort.json': result['cohort'], 'articles.json': result['records'], 'edge-witnesses.json': result['edge_witnesses'],
             'gained-examples.json': result['gained_examples'], 'rejected-examples.json': result['rejected_examples']}
    for name, value in parts.items(): (root / name).write_text(canonical(value) + '\n', encoding='utf-8')
    import hashlib
    manifest = {'format': result['format'], 'source_run_id': result['source_run_id'], 'snapshot': result['snapshot'],
                'cohort_hash': result['cohort_hash'], 'quality_policy_hash': digest(result['quality_policy']),
                'ranking_policy_hash': digest(result['ranking_policy']),
                'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in parts}}
    manifest['manifest_id'] = digest(manifest)
    (root / 'manifest.json').write_text(canonical(manifest) + '\n', encoding='utf-8')
    return manifest


def seal_audit(output):
    """Bind operational receipts after publication without a self-referential hash."""
    from .relaxed_preservation import file_hash
    root = Path(output)
    if (root / 'manifest.json').exists():
        report = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        if digest({k: v for k, v in report.items() if k != 'manifest_id'}) != report['manifest_id']:
            raise Incompatible('Result manifest checksum differs')
        for name, expected in report['files'].items():
            if file_hash(root / name) != expected: raise Incompatible('Result package changed before audit seal')
    seal = {'format': 'vimfc-relaxed-audit-1',
            'files': {p.name: file_hash(p) for p in sorted(root.iterdir()) if p.is_file() and p.name != 'audit-manifest.json'}}
    seal['manifest_id'] = digest(seal)
    with (root / 'audit-manifest.json').open('x', encoding='utf-8') as stream: stream.write(canonical(seal) + '\n')
    return seal
