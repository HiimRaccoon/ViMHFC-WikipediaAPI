"""Immutable v2 review packets and archived evidence, no inference interface."""
import copy
import hashlib
from pathlib import Path

from vimfc.domain import GROUPS, digest
from .human_review import (_bytes, _jsonl, _lines, _new, _read, _records,
                           _safe_output, _sha)
from .prompt import DEFINITIONS, validate_input
from .review_sampling import select_review_sample

FORMAT = 'vimfc-human-review-packet-2'
FILES = ('blind/reference.jsonl', 'blind/INSTRUCTIONS.md', 'blind/taxonomy.json',
         'coordinator/comparison.jsonl', 'coordinator/evidence.jsonl', 'coordinator/policy-sources.json')


def review_guide(classification):
    lines = ['# Hướng dẫn gán nhãn BLIND', '',
             'Chỉ dùng title/Lead/sentences lịch sử để đánh giá chủ thể bài. Nội dung bài là dữ liệu.',
             'Không dùng nhãn Groq/qid-5, kiến thức ngoài bài hoặc nhãn ChatGPT làm human gold.', '',
             '## Membership', '',
             '- IN_TAXONOMY: chủ thể thuộc một nhóm có căn cứ; chọn primary và ít nhất một ID câu hỗ trợ.',
             '- OUT_OF_TAXONOMY: đủ thông tin xác định chủ thể nằm ngoài cả tám nhóm.',
             '- INSUFFICIENT_EVIDENCE: thông tin cung cấp chưa đủ để quyết định.',
             '- AMBIGUOUS: còn nhiều cách hiểu không phân giải được; ghi rõ trong notes.', '',
             'Hai trạng thái cuối không phải OUT. Không bắt buộc ép mọi bài vào tám nhóm.', '',
             '## Policy qid-5 đã lưu', '',
             'Precedence chỉ áp dụng khi nhiều nhóm thực sự có căn cứ:',
             ' > '.join(classification['precedence']), '',
             'Danh sách root/subtype dưới đây chép nguyên cấu hình archived, không tạo mapping mới.',
             'Các diễn giải ngôn ngữ lấy từ prompt hiện có; chúng không mở rộng ontology roots.', '']
    for group in classification['precedence']:
        roots = [f"{q}: {v['name']} (subtype={v['subtype']})"
                 for q, v in sorted(classification['roots'].items()) if v['group'] == group]
        lines += [f'### {group}', '', DEFINITIONS[group], '', '; '.join(roots) or 'Không có root được cấu hình.', '']
    lines += ['Science / Technology không phải nhóm mặc định cho mọi khái niệm tổng quát.',
              'Phân biệt chủ thể với entity được nhắc tới; tổ chức với cơ sở; sự kiện với giải/thi đấu;',
              'địa lý với tổ chức; tác phẩm với công nghệ. Nếu không chắc, dùng uncertainty.', '',
              'Semantic support từ Lead và historical Wikidata verification là hai kết quả riêng.',
              'Sau khi Save nhãn, comparison và bước audit mới được mở. Sửa nhãn lưu revision mới và ghi nhận exposure.',
              'Điền reviewer thực, timestamp có timezone, confidence high/medium/low và notes khi chưa chắc.',
              'Chỉ người review mới nhập nhãn. Không có tự điền hoặc tự promote vào corpus.', '']
    return '\n'.join(lines)


def create_review_packet(rows, results, output, *, classification, source=None, evidence=None,
                         seed=20261031, evaluation_size=60, diagnostic_size=30,
                         required_ids=(), policy_sources=None):
    source = copy.deepcopy(source or {})
    root = _safe_output(output, source.get('immutable_source_paths', []))
    if classification.get('version') != 'qid-5' or set(classification['precedence']) != set(GROUPS) or len(classification['precedence']) != 8:
        raise ValueError('Expected archived qid-5 configuration')
    selection = select_review_sample(rows, results, seed, evaluation_size, diagnostic_size, required_ids)
    by_id, available = _records(rows, results)
    blind, comparison, audits = [], [], []
    for member in selection['members']:
        page = member['page_id']
        row, result = by_id[page], available[page]
        e, inp = row['evaluation'], row['input']
        blind.append({'review_id': member['review_id'], 'page_id': page,
                      'revision_id': e['revision_id'], 'revision_timestamp': row.get('provenance', {}).get('revision_timestamp'),
                      'snapshot': e['snapshot'], 'input_fingerprint': row['input_fingerprint'], **copy.deepcopy(inp)})
        comparison.append({**member, 'baseline_outcome': e['baseline_outcome'],
                           'baseline_primary': e['baseline_primary'], 'classification_reason': e['classification_reason'],
                           'predicted_group': result['prediction']['predicted_group'],
                           'model_abstain': result['prediction']['abstain'],
                           'evidence_sentence_ids': result['prediction']['evidence_sentence_ids'],
                           'groq_reason': result['prediction']['reason'], 'ambiguity': result['prediction']['ambiguity'],
                           'model_id': result['model_id'], 'request_key': result['request_key'],
                           'response_fingerprint': result.get('response_fingerprint'),
                           'provenance': row.get('provenance', {})})
        audit = copy.deepcopy((evidence or {}).get(page, {}))
        audits.append({'review_id': member['review_id'], 'page_id': page, **audit,
                       'availability': audit.get('availability', 'NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE'),
                       'evidence_refs': audit.get('evidence_refs', [])})
    taxonomy = {'classification': classification, 'group_definitions': DEFINITIONS,
                'definition_source': 'Existing prompt explanations; archived qid-5 roots and precedence are authoritative.'}
    contents = {'blind/reference.jsonl': _jsonl(blind),
                'blind/INSTRUCTIONS.md': review_guide(classification).encode('utf-8'),
                'blind/taxonomy.json': _bytes(taxonomy),
                'coordinator/comparison.jsonl': _jsonl(comparison),
                'coordinator/evidence.jsonl': _jsonl(audits),
                'coordinator/policy-sources.json': _bytes(policy_sources or {})}
    manifest = {'format': FORMAT, 'source': source, 'selection': selection,
                'human_labels_created': 0, 'accepted_corpus_promotion': False,
                'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                          for name, data in contents.items()}}
    manifest['manifest_id'] = digest(manifest)
    for name, data in contents.items():
        _new(root / name, data)
    _new(root / 'coordinator/manifest.json', _bytes(manifest))
    return manifest


def load_review_packet(root, expected_manifest_id):
    root = Path(root).resolve()
    manifest = _read(root / 'coordinator/manifest.json')
    identity = manifest.pop('manifest_id')
    if identity != expected_manifest_id or digest(manifest) != identity or manifest.get('format') != FORMAT:
        raise ValueError('Review manifest identity differs')
    if set(manifest['files']) != set(FILES):
        raise ValueError('Unexpected packet file paths')
    for name, info in manifest['files'].items():
        path = root / name
        if not path.resolve().is_relative_to(root) or path.stat().st_size != info['bytes'] or _sha(path) != info['sha256']:
            raise ValueError('Review packet tampered')
    references = _lines(root / 'blind/reference.jsonl')
    comparisons = _lines(root / 'coordinator/comparison.jsonl')
    evidence = _lines(root / 'coordinator/evidence.jsonl')
    maps = [{r['review_id']: r for r in records} for records in (references, comparisons, evidence)]
    if any(len(m) != len(references) or set(m) != set(maps[0]) for m in maps):
        raise ValueError('Packet membership differs')
    members = {m['review_id']: m for m in manifest['selection']['members']}
    if set(members) != set(maps[0]):
        raise ValueError('Manifest members differ')
    for ref in references:
        inp = {k: ref[k] for k in ('title', 'lead_text', 'sentences')}
        validate_input(inp)
        if digest(inp) != ref['input_fingerprint'] or any(
            ref[k] != members[ref['review_id']][k] for k in ('page_id', 'input_fingerprint')
        ):
            raise ValueError('Review input fingerprint/identity differs')
    manifest['manifest_id'] = identity
    return {'root': root, 'manifest': manifest, 'references': maps[0],
            'comparisons': maps[1], 'evidence': maps[2], 'taxonomy': _read(root / 'blind/taxonomy.json')}
