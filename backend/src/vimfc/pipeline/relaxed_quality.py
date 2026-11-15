"""Versioned article feasibility. Human annotators decide necessary hops later."""
from dataclasses import asdict, is_dataclass
import re
from .factual import detect_signals, UNCERTAIN

VERSION = 'quality-relaxed-1'
COMPLETE = re.compile(r'[.!?…]["”’)]*$')
# Literal descriptive class predicates only; not every occurrence of “là”.
DEFINITION = re.compile(
    r'\blà\s+(?:(?:một|những|các)\s+)?(?:'
    r'người|nhân vật|nhà (?:văn|thơ|khoa học|toán học)|cầu thủ|ca sĩ|diễn viên|'
    r'tổ chức|công ty|doanh nghiệp|trường|viện|thành phố|tỉnh|huyện|xã|phường|'
    r'quốc gia|địa danh|khu vực|làng|đảo|sông|núi|hồ|cảng|sân bay|'
    r'sự kiện|giải (?:đấu|thưởng)|cuộc (?:thi|chiến)|bộ phim|phim|tác phẩm|'
    r'bài hát|album|tiểu thuyết|tòa nhà|công trình|sân vận động|'
    r'thuật toán|phần mềm|thiết bị|loài|chi|họ|hành tinh|thiên hà)\b', re.I)


def assess_relaxed(extraction, classification, kinds, rules):
    ext = asdict(extraction) if is_dataclass(extraction) else extraction
    complete = [s for s in ext['sentences'] if s['text'].strip() and COMPLETE.search(s['text'])]
    complete_ids = {s['sent_id'] for s in complete}
    signals = detect_signals(ext, {**rules, 'quality_revision': 'quality-2.1'})
    signals = [s for s in signals if s['sent_id'] in complete_ids]
    for sentence in complete:
        text = sentence['text']
        match = DEFINITION.search(text)
        if (not match or not text[:match.start()].strip() or UNCERTAIN.search(text)
                or any(l['start'] - sentence['start'] <= match.start() < l['end'] - sentence['start']
                       for l in ext['links'] if l.get('sent_id') == sentence['sent_id'])):
            continue
        signals.append({'rule': 'definition', 'attribute_key': 'definition', 'sent_id': sentence['sent_id'],
                        'matched_text': match[0], 'match_start': match.start(), 'match_end': match.end(),
                        'value_kind': 'text', 'values': [match[0]], 'value_spans': [], 'date_spans': [],
                        'linked_targets': [l['target_title'] for l in ext['links'] if l.get('sent_id') == sentence['sent_id']],
                        'extraction_version': ext['extraction_version'], 'quality_version': VERSION})
    reasons = []
    if ext['outcome'] != 'completed': reasons.append('unresolved_extraction')
    if set(kinds) & {'redirect', 'disambiguation', 'list'}: reasons.append('excluded_article_kind')
    if not ext['full_lead'].strip(): reasons.append('empty_lead')
    if not complete: reasons.append('too_few_complete_sentences')
    if not signals: reasons.append('no_factual_signal')
    # Length only contributes to ranking; never an admission threshold.
    score = min(100, 20 + min(30, len(complete) * 5) + min(30, len({s['rule'] for s in signals}) * 10)
                + min(20, len(ext['full_lead'].split()) // 10))
    return {'eligible': not reasons, 'reasons': reasons, 'score': score, 'signals': signals,
            'quality_version': VERSION, 'quality_revision': VERSION,
            'classification_is_gate': False, 'factual_assessment': 'literal_heuristic_not_human_gold'}
