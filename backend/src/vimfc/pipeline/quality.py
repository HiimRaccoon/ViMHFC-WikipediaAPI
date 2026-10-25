from dataclasses import asdict, is_dataclass
import re


def assess_quality(extraction, classification, kinds, rules):
    extraction=asdict(extraction) if is_dataclass(extraction) else extraction
    classification=asdict(classification) if is_dataclass(classification) else classification
    reasons=[]
    if extraction['outcome']!='completed':
        reasons.append('unresolved_extraction')
    if classification['outcome']!='completed':
        reasons.append('unsupported_classification')
    if set(kinds)&{'redirect','disambiguation','list'}:
        reasons.append('excluded_article_kind')
    complete=[s for s in extraction['sentences'] if re.search(r'[.!?…]["”’)]*$',s['text']) and len(s['text'].split())>=4]
    if len(complete)<rules['min_sentences']:
        reasons.append('too_few_complete_sentences')
    if len(extraction['full_lead'].split())<rules['min_tokens']:
        reasons.append('too_few_tokens')
    signals=[]
    for sentence in extraction['sentences']:
        for rule,pattern in sorted(rules['factual_rules'].items()):
            for match in re.finditer(pattern,sentence['text'],re.I):
                # Attribute values belong to their local clause. A year attached
                # to "founded" cannot become an invented birth-year attribute.
                tail=sentence['text'][match.end():]
                clause=re.split(r'[,;.!?]|\s+(?:và|nhưng|đồng thời|còn)\s+',tail,maxsplit=1,flags=re.I)[0]
                other_positions=[m.start() for other_rule,other_pattern in rules['factual_rules'].items() if other_rule!=rule for m in re.finditer(other_pattern,clause,re.I)]
                if other_positions:
                    clause=clause[:min(other_positions)]
                years=re.findall(r'\b(?:1\d{3}|20\d{2})\b',clause)
                linked=[link['target_title'] for link in extraction['links'] if link['sent_id']==sentence['sent_id']]
                signals.append({'rule':rule,'sent_id':sentence['sent_id'],'matched_text':match[0], 'linked_targets':linked,
                    'attribute_key':rule,'value_kind':'year' if years else 'entity','values':years if years else linked,'extraction_version':extraction['extraction_version']})
    if not signals:
        reasons.append('no_factual_signal')
    score=min(100,20+min(30,len(complete)*5)+min(30,len({s['rule'] for s in signals})*10)+min(20,len(extraction['full_lead'].split())//10))
    return {'eligible':not reasons,'reasons':reasons,'score':score,'signals':signals}
