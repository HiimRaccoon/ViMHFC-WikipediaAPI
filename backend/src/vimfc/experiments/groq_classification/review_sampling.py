"""Versioned response-frame sampling; never fetches missing results."""
from collections import defaultdict

from .human_review import _rank, _records


def select_review_sample(rows, results, seed=20261031, evaluation_size=60,
                         diagnostic_size=30, required_ids=()):
    by_id, available = _records(rows, results)
    if any(type(v) is not int or v < 0 for v in (seed, evaluation_size, diagnostic_size)):
        raise ValueError('Sampling sizes and seed require nonnegative integers')
    if evaluation_size + diagnostic_size > len(available):
        raise ValueError('Insufficient saved responses; no inference fallback')
    if not set(required_ids) <= set(available):
        raise ValueError('Required case lacks a saved valid response')
    cells = defaultdict(list)
    for page, response in available.items():
        outcome = by_id[page]['evaluation']['baseline_outcome']
        group = '__abstain__' if response['prediction']['abstain'] else response['prediction']['predicted_group']
        cells[(outcome, group)].append(page)
    keys = sorted(cells)
    if evaluation_size < len(keys):
        raise ValueError('Evaluation sample must include every represented cell')
    counts = {key: 1 for key in keys}
    # Fixed allocation before any within-cell draw: balanced representation of
    # rare cells, then largest remaining population capacity, stable ties.
    while sum(counts.values()) < evaluation_size:
        key = min((k for k in keys if counts[k] < len(cells[k])),
                  key=lambda k: (-(len(cells[k]) - counts[k]), _rank(seed, 'allocate', k)))
        counts[key] += 1
    frames, members, selected = [], [], set()
    for key in keys:
        frame = sorted(cells[key])
        stratum = {'baseline_outcome': key[0], 'groq_group': key[1]}
        frames.append({'stratum': stratum, 'page_ids': frame, 'population_count': len(frame),
                       'sample_count': counts[key], 'inclusion_probability': counts[key] / len(frame)})
        for page in sorted(frame, key=lambda p: _rank(seed, 'evaluation-within-cell', p))[:counts[key]]:
            selected.add(page)
            members.append({'page_id': page, 'partition': 'evaluation_random', 'stratum': stratum,
                            'selection_reason': 'fixed_allocation_seeded_within_cell',
                            'inclusion_probability': counts[key] / len(frame)})
    disagreements = sorted(p for p, r in available.items()
                           if by_id[p]['evaluation']['baseline_outcome'] == 'completed'
                           and not r['prediction']['abstain']
                           and r['prediction']['predicted_group'] != by_id[p]['evaluation']['baseline_primary'])
    required = set(required_ids) | set(disagreements)
    needed = required - selected
    if len(needed) > diagnostic_size:
        raise ValueError('Diagnostic budget cannot cover required cases')
    diagnostics = []

    def add(page, reason):
        if page not in selected and len(diagnostics) < diagnostic_size:
            selected.add(page)
            diagnostics.append({'page_id': page, 'partition': 'diagnostic_targeted',
                                'selection_reason': reason, 'stratum': None, 'inclusion_probability': None})

    for page in sorted(needed, key=lambda p: _rank(seed, 'required', p)):
        add(page, 'required_previous_review_case' if page in required_ids else 'verified_reference_disagreement')
    # Round robin across risk strata avoids consuming the entire diagnostic
    # budget with Science while omitting abstentions or dependency reasons.
    risk_cells = defaultdict(list)
    for page, r in available.items():
        if page in selected:
            continue
        e, p = by_id[page]['evaluation'], r['prediction']
        if p['abstain'] or e['baseline_outcome'] != 'completed' or p['predicted_group'] == 'Science / Technology':
            risk_cells[(p['predicted_group'] or '__abstain__', e['classification_reason'] or '__verified__')].append(page)
    for key in risk_cells:
        risk_cells[key].sort(key=lambda p: _rank(seed, 'diagnostic-within-cell', p))
    risk_keys = sorted(risk_cells, key=lambda k: (k[0] != 'Science / Technology', _rank(seed, 'risk-cell', k)))
    while len(diagnostics) < diagnostic_size and any(risk_cells.values()):
        for key in risk_keys:
            if risk_cells[key]:
                add(risk_cells[key].pop(0), 'targeted_risk:' + '|'.join(key))
    for page in sorted(available, key=lambda p: _rank(seed, 'diagnostic-fill', p)):
        add(page, 'additional_diagnostic_control')
    members += diagnostics
    members.sort(key=lambda m: _rank(seed, 'review-ui-blind-order', m['page_id']))
    for i, member in enumerate(members, 1):
        member['review_id'] = f'R{i:03d}'
        member['input_fingerprint'] = by_id[member['page_id']]['input_fingerprint']
    return {'policy': 'human-review-evaluation60-diagnostic30-1', 'seed': seed,
            'available_count': len(available), 'available_page_ids': sorted(available),
            'evaluation_size': evaluation_size, 'diagnostic_size': diagnostic_size,
            'frames': frames, 'members': members, 'required_page_ids': sorted(required),
            'all_reference_disagreements': disagreements,
            'scope': 'Random response-frame sample and additional purposive diagnostics. '
                     'Separate reviewed-case metrics; no 300-slot or corpus accuracy estimate.'}
