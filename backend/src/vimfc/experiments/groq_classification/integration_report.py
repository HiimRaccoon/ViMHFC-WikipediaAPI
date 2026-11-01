"""Descriptive shadow reports. No accuracy or verified-coverage inflation."""
from pathlib import Path
from .human_review import _bytes, _jsonl, _new, _safe_output


def write_shadow_report(report, output):
    output = _safe_output(output, report.get('source', {}).get('immutable_source_paths', []))
    data = {k: v for k, v in report.items() if k != 'outcomes'}
    _new(output / 'metrics.json', _bytes(data))
    _new(output / 'outcomes.jsonl', _jsonl(report['outcomes']))
    n = report['processed_denominator']; counts = report['counts']; coverage = report['coverage']
    lines = ['# Wikidata-first + Groq-assisted Classification Shadow', '',
             f"Policy `{report['policy_version']}`; source processed denominator **{n}**.", '',
             '**Descriptive experimental coverage only; human accuracy UNASSESSED.**',
             'LLM provisional primary is not Wikidata-verified, human gold or accepted corpus evidence.', '',
             '| State | Count |', '|---|---:|']
    lines += [f'| {name} | {value} |' for name, value in counts.items()]
    lines += ['', f"Baseline assigned {coverage['baseline_assigned_count']}/{n} ({coverage['baseline_assigned']:.2%}); "
              f"assisted assigned {coverage['assisted_assigned_count']}/{n} ({coverage['assisted_assigned']:.2%}).",
              f"Verified eligible remains **{report['verified_eligible']}**; separate provisional eligible candidates **{report['provisional_eligible_candidates']}**.",
              '', '| Group | Verified | Provisional | Verified Extraction pass | Provisional Extraction pass | Verified Quality pass | Provisional Quality pass | Verified eligible | Provisional candidates |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for group, v in report['groups'].items():
        lines.append('| ' + group + ' | ' + ' | '.join(str(v[k]) for k in (
            'verified', 'provisional', 'verified_extraction_pass', 'provisional_extraction_pass',
            'verified_nonclassification_quality_pass', 'provisional_nonclassification_quality_pass',
            'verified_eligible', 'provisional_eligible_candidates')) + ' |')
    lines += ['', 'Quality-pass columns apply every saved quality criterion other than the verified-classification prerequisite. '
              'No threshold is lowered; provisional candidates remain unaccepted.', '',
              f"Trusted retained leads {report['trusted_leads']}; legacy cache reused {report['legacy_cache_reused']}; "
              f"legacy records requiring new subject/scope validation {report['legacy_cache_needs_new_subject_or_scope_validation']}.",
              f"Pending new-schema calls {report['pending_live_count']}; invalid terminal outputs {report['invalid_terminal_outputs']}.", '',
              'OUT_OF_TAXONOMY is explicitly model-declared, not human or historical truth. Legacy abstention has unspecified membership.',
              'Subject-title/citation/technical-scope checks are conservative textual gates, not semantic entailment or a calibrated quality guarantee.', '',
              '## Operations', '', '```json', __import__('json').dumps(report.get('operations', {'status': 'OFFLINE_REPLAY', 'new_api_requests': 0}), indent=2), '```', '',
              '## Source integrity and next experiment', '',
              'The source cohort is1000:985processed and15outside this processed frame (13deferred,2reviewed quarantine in the saved handoff). '
              'The integration does not recover source failures or missing historical dependencies. Original qid-5 ambiguity/no-rule outcomes '
              'remain visible; model text judgments cannot repair historical Wikidata evidence.', '',
              'Proposed Pilot1000: freeze the same ordered1000identities/snapshot; paired arms A=originalqid-5 and '
              'B=Wikidata-first+this provisional wrapper. Use identical source, extraction, quality, inventory and quotas. '
              'Keep15source-blocked slots in both denominators. Report985processed separately; stratify paired coverage/quality/cost '
              'deltas by original outcome/group/reason. Freeze a finite request/token/runtime scope and shared account meter; '
              'preserve response/prompt/cache identities and an untouched quality evaluation subset. Do not treat provisional '
              'candidate gains as verified eligibility, accuracy or permission for production. This is a design, not an executed Pilot.', '']
    _new(output / 'report.md', ('\n'.join(lines).rstrip() + '\n').encode('utf-8'))
    return data
