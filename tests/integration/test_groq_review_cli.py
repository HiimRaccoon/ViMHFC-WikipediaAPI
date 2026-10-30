import importlib.util
import json
from pathlib import Path

import pytest

from tests.integration.test_groq_review_packet import make_packet


def cli_module():
    path = Path(__file__).resolve().parents[2] / 'scripts/groq_review.py'
    spec = importlib.util.spec_from_file_location('review_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_blank_export_import_evaluate_are_offline(tmp_path, capsys):
    packet = make_packet(tmp_path)
    main = cli_module().main
    args = ['--bundle', str(packet['root']), '--manifest-id', packet['manifest']['manifest_id'],
            '--workspace', str(tmp_path / 'workspace')]
    output = tmp_path / 'blank.jsonl'
    assert main(['export', *args, '--output', str(output)]) == 0
    assert main(['import', *args, '--labels', str(output)]) == 0
    assert main(['evaluate', *args, '--output', str(tmp_path / 'scores')]) == 0
    score = json.loads((tmp_path / 'scores/metrics.json').read_text(encoding='utf-8'))
    assert score['status'] == 'UNASSESSED' and score['reviewed_count'] == 0
    assert 'UNASSESSED' in (tmp_path / 'scores/report.md').read_text(encoding='utf-8')
    assert len(output.read_text(encoding='utf-8').splitlines()) == 90
    with pytest.raises((ValueError, FileExistsError)):
        main(['export', *args, '--output', str(packet['root'] / 'blind/labels.jsonl')])


def test_archive_evidence_status_does_not_verify_new_group():
    from vimfc.experiments.groq_classification.review_prepare import archive_evidence
    for outcome, reason, status in [('completed', None, 'COMPLETE'), ('unclassified', 'no_supported_rule', 'ABSENT_SUPPORTED_RULE'),
                                     ('unresolved', 'primary_ambiguity', 'AMBIGUOUS'),
                                     ('unresolved', 'primary_dependency_missing', 'DEPENDENCY_UNAVAILABLE')]:
        row = {'evaluation': {'baseline_outcome': outcome, 'baseline_primary': 'Person' if outcome == 'completed' else None,
                               'classification_reason': reason}, 'provenance': {'manifest_id': 'm'}}
        record = {'classification': {'outcome': outcome, 'primary_entity_type': row['evaluation']['baseline_primary'],
                                      'reason': reason, 'manifest_id': 'm', 'primary_provenance': [],
                                      'dependency_manifest': {'manifest_id': 'm', 'nodes': []}}}
        evidence = archive_evidence(row, record)
        assert evidence['availability'] == status
        assert evidence['verified_primary'] == row['evaluation']['baseline_primary']
        assert evidence['evidence_refs'] == ['manifest:m']
    with pytest.raises(ValueError):
        archive_evidence(row, {**record, 'classification': {**record['classification'], 'manifest_id': 'foreign'}})
