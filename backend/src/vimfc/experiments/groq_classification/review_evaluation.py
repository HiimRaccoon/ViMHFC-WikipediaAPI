"""Human-only RQ1/RQ2/RQ3 metrics, kept separate by sampling partition."""
from collections import Counter

from vimfc.domain import GROUPS
from .human_review import _views, classification_metrics
from .review_schema import MEMBERSHIPS, validate_audit, validate_semantic

VIEWS = ('qid5_baseline', 'groq_only', 'wikidata_first_suggestions')


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _membership(rows, comparisons, view):
    behavior = {}
    for membership in MEMBERSHIPS:
        values = [_views(comparisons[rid])[view] for rid, label in rows.items() if label['membership'] == membership]
        n = len(values)
        assigned = sum(v in GROUPS for v in values)
        abstained = values.count('__abstain__')
        behavior[membership] = {'denominator': n, 'assigned_count': assigned,
                                'assigned_rate': ratio(assigned, n), 'abstention_count': abstained,
                                'abstention_rate': ratio(abstained, n), 'unresolved_count': values.count(None)}
    inside, outside = behavior['IN_TAXONOMY'], behavior['OUT_OF_TAXONOMY']
    tp, fp = inside['assigned_count'], outside['assigned_count']
    return {'by_membership': behavior, 'false_inclusion_count': fp,
            'false_inclusion_rate': ratio(fp, outside['denominator']),
            'decided_denominator': inside['denominator'] + outside['denominator'],
            'assigned_decided_denominator': tp + fp,
            'membership_precision': ratio(tp, tp + fp), 'membership_recall': ratio(tp, inside['denominator']),
            'confusion_counts': {'human_in_assigned': tp, 'human_out_assigned': fp,
                                 'human_in_declined_or_unresolved': inside['denominator'] - tp,
                                 'human_out_declined_or_unresolved': outside['denominator'] - fp},
            'explicit_out_detection_supported': False,
            'interpretation': 'Operational assigned-versus-declined proxy on human IN/OUT only. '
                              'Model abstention does not distinguish OUT, insufficient evidence or ambiguity.'}


def _suggestions(members, labels, audits, comparisons):
    candidates = [rid for rid in members if comparisons[rid]['baseline_outcome'] in ('unclassified', 'unresolved')
                  and not comparisons[rid]['model_abstain'] and comparisons[rid]['predicted_group'] in GROUPS]
    reviewed = [rid for rid in candidates if rid in labels]
    correct = [rid for rid in reviewed if labels[rid]['membership'] == 'IN_TAXONOMY'
               and labels[rid]['primary_group'] == comparisons[rid]['predicted_group']]
    uncertain = [rid for rid in reviewed if labels[rid]['membership'] in ('INSUFFICIENT_EVIDENCE', 'AMBIGUOUS')]
    verified = [rid for rid in reviewed if rid in audits and audits[rid]['verification'] == 'VERIFIED'
                and audits[rid]['verified_group'] == comparisons[rid]['predicted_group']]
    gaps = [rid for rid in labels if comparisons[rid]['baseline_outcome'] in ('unclassified', 'unresolved')]
    n = len(labels)
    return {'selected_suggestions': len(candidates), 'reviewed_suggestions': len(reviewed),
            'pending_suggestions': len(candidates) - len(reviewed),
            'semantic_correct': len(correct), 'semantic_wrong': len(reviewed) - len(correct) - len(uncertain),
            'undecidable': len(uncertain), 'historically_audited_suggestions': sum(rid in audits for rid in reviewed),
            'historically_verified_suggestions': len(verified),
            'semantic_correct_without_sufficient_historical_verification': len(set(correct) - set(verified)),
            'semantic_correct_awaiting_audit': sum(rid not in audits for rid in correct),
            'semantic_correct_audited_unverified': sum(rid in audits and rid not in verified for rid in correct),
            'correct_additional_coverage': {'numerator': len(correct), 'reviewed_article_denominator': n,
                                           'reviewed_baseline_gap_denominator': len(gaps),
                                           'rate_over_reviewed_articles': ratio(len(correct), n),
                                           'rate_over_reviewed_baseline_gaps': ratio(len(correct), len(gaps)),
                                           'historically_verified_correct_numerator': len(set(correct) & set(verified)),
                                           'note': 'Semantic-correct suggestions do not change verified coverage or eligibility.'}}


def evaluate_review(packet, submissions, audit_records):
    comparisons = packet['comparisons']
    labels, audits = {}, {}
    for rid, record in submissions.items():
        if rid not in packet['references'] or record.get('manifest_id') != packet['manifest']['manifest_id'] or record.get('input_fingerprint') != packet['references'][rid]['input_fingerprint']:
            raise ValueError('Foreign human review identity')
        labels[rid] = validate_semantic(record['payload'], packet['references'][rid])
    for rid, record in audit_records.items():
        if rid not in submissions or record['semantic_revision'] != submissions[rid]['revision']:
            continue
        audits[rid] = validate_audit(record['payload'], packet['evidence'][rid])
    partitions = {}
    for partition in ('evaluation_random', 'diagnostic_targeted'):
        members = [rid for rid, c in comparisons.items() if c['partition'] == partition]
        gold = {rid: labels[rid] for rid in members if rid in labels}
        scoped_audits = {rid: audits[rid] for rid in gold if rid in audits}
        inside = {rid: label for rid, label in gold.items() if label['membership'] == 'IN_TAXONOMY'}
        primary = {view: classification_metrics([v['primary_group'] for v in inside.values()],
                                                [_views(comparisons[rid])[view] for rid in inside], labels=list(GROUPS))
                   for view in VIEWS} if inside else None
        rq3 = _suggestions(members, gold, scoped_audits, comparisons) if gold else None
        if rq3 is not None:
            rq3['by_reason'] = {}
            for reason in ('no_supported_rule', 'primary_ambiguity', 'primary_dependency_missing'):
                subset = [rid for rid in members if comparisons[rid]['classification_reason'] == reason]
                subset_gold = {rid: gold[rid] for rid in subset if rid in gold}
                rq3['by_reason'][reason] = _suggestions(subset, subset_gold, scoped_audits, comparisons)
        support = Counter(v['primary_group'] for v in inside.values())
        partitions[partition] = {
            'selected_count': len(members), 'reviewed_count': len(gold), 'pending_count': len(members) - len(gold),
            'audited_count': len(scoped_audits), 'membership_counts': dict(Counter(v['membership'] for v in gold.values())),
            'rq1': {view: _membership(gold, comparisons, view) for view in VIEWS} if gold else None,
            'rq2': primary, 'rq3': rq3,
            'groq_error_tags': dict(Counter(t for audit in scoped_audits.values() for t in audit['error_tags'])),
            'science_technology_probe': {
                'reviewed_science_suggestions': sum(comparisons[rid]['predicted_group'] == 'Science / Technology' for rid in gold),
                'human_out_assigned_science': sum(comparisons[rid]['predicted_group'] == 'Science / Technology' and
                                                 label['membership'] == 'OUT_OF_TAXONOMY' for rid, label in gold.items()),
                'human_other_group_assigned_science': sum(comparisons[rid]['predicted_group'] == 'Science / Technology' and
                                                         label['membership'] == 'IN_TAXONOMY' and label['primary_group'] != 'Science / Technology'
                                                         for rid, label in gold.items())},
            'approach_coverage': {
                view: {'selected_assigned': sum(_views(comparisons[rid])[view] in GROUPS for rid in members),
                       'selected_denominator': len(members),
                       'reviewed_assigned': sum(_views(comparisons[rid])[view] in GROUPS for rid in gold),
                       'reviewed_denominator': len(gold)} for view in VIEWS},
            'per_group_human_support': {g: support[g] for g in GROUPS},
            'small_sample_groups': [g for g in GROUPS if support[g] < 5],
            'warning': 'Group support <5 is too small for a confirmed quality claim. '
                       'Metrics describe reviewed cases in this partition only; no pooled headline or population estimate.'}
    return {'format': 'vimfc-review-quality-evaluation-1', 'manifest_id': packet['manifest']['manifest_id'],
            'status': 'ASSESSED_REVIEWED_CASES_ONLY' if labels else 'UNASSESSED',
            'selected_count': len(packet['references']), 'reviewed_count': len(labels), 'partitions': partitions,
            'population_accuracy_claim': False, 'accepted_corpus_promotion': False,
            'metric_policy': 'RQ1 excludes INSUFFICIENT/AMBIGUOUS from decided IN/OUT. RQ2 uses human IN only, '
                             'with model abstention/unresolved counted wrong; Macro-F1 includes all 8 groups, '
                             'zero division=0, supports disclosed. RQ3 counts only saved non-abstaining suggestions '
                             'for original qid-5 gaps. Historical verification is separate and audit-bound to semantic revision. '
                             'All rates have explicit denominators; pending human records are not gold.'}
