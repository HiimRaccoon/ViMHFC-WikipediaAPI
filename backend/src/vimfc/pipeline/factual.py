"""Conservative local factual signals, exact source spans, no inferred dates/units.

All predicates require a literal value or a local explicit linked entity.
These are potential signals, never semantic claim validation.
"""
from datetime import date
import re

YEAR=r'(?<![\w-])(?:1\d{3}|20\d{2})(?![\w-])'
DATE=re.compile(r'\bngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(1\d{3}|20\d{2})\b',re.I)
NUMERIC_DATE=re.compile(r'\b(\d{1,2})[/-](\d{1,2})[/-](1\d{3}|20\d{2})\b')
NUMBER=r'\d+(?:[.,]\d+)*'
UNITS=r'(?:km²|m²|km|cm|mm|m|kg|tấn|triệu\s+người|người|tầng|đội(?:\s+bóng)?|câu\s+lạc\s+bộ|quốc\s+gia|cửa\s+hàng|nhân\s+viên|sinh\s+viên|bài\s+dự\s+thi|album|bản|USD|đồng|%)'
QUANTITY=re.compile(r'(?P<value>'+NUMBER+r')\s*(?P<unit>'+UNITS+r')(?!\w)',re.I)
UNCERTAIN=re.compile(r'\b(?:không|chưa|dự\s+kiến|dự\s+định|có\s+thể|nếu|sẽ)\b',re.I)
BOUNDARY=re.compile(r'[;!?…]|(?<!\d)[.,]|[.,](?!\d)|\s+(?:và|nhưng|đồng\s+thời|còn)\s+',re.I)


def _quoted(text,start,end):
    return any(m.start()<=start<end<=m.end() for m in re.finditer(r'"[^"\n]*"|“[^”\n]*”',text))


def _named_value(text,start,end,*,direct=False):
    pattern=r'([^.;!?]+)' if direct else r'\b(?:bởi|tại|ở|của|với|cho|là)\s+([^.;!?]+)'
    m=re.search(pattern,text[start:end],re.I)
    if not m:return None
    value=m[1].strip()
    if not value[:1].isupper() or not all(w[:1].isupper() for w in value.split()):return None
    return {'start':start+m.start(1),'end':start+m.start(1)+len(value),'text':value}


def _parenthetical_founding(text,match,span):
    prefix=text[match.end():span['start']]
    return (bool(re.fullmatch(r'\s*(?:(?:đầu tiên(?:\s+trên cả nước)?|chính thức)\s*)?\(\s*',prefix,re.I))
        and bool(re.match(r'\s*\)',text[span['end']:])))


def _coordinated_founding(text,match,links,sentence):
    shared=re.match(r'\s+và\s+(?:là\s+)?(?:hiệu trưởng|giám đốc|chủ tịch)(?:\s+đầu tiên)?\s+của\s+',text[match.end():],re.I)
    if not shared:return None
    start=match.end()+shared.end()
    for link in links:
        left,right=link['start']-sentence['start'],link['end']-sentence['start']
        if left==start and not UNCERTAIN.search(text[match.start():right]):
            return link,{'start':left,'end':right,'text':text[left:right]}
    return None


def detect_signals(extraction,rules):
    patterns={**rules['factual_rules'],**rules.get('factual_rules_v2',{})}
    signals=[]
    for sentence in extraction['sentences']:
        text=sentence['text']
        matches=sorted([(m.start(),name,m) for name,pattern in sorted(patterns.items()) for m in re.finditer(pattern,text,re.I)])
        for _,name,match in matches:
            refined=rules.get('quality_revision')=='quality-2.1'
            binding_reason=None
            # Restrict values and qualifiers to the predicate's own clause.
            left=max((m.end() for m in BOUNDARY.finditer(text[:match.start()])),default=0)
            right=next((m.start()+match.end() for m in BOUNDARY.finditer(text[match.end():])),len(text))
            right=min([right]+[m.start() for _,other,m in matches if other!=name and m.start()>=match.end()])
            if UNCERTAIN.search(text[left:right]) or _quoted(text,match.start(),match.end()):
                continue
            own_links=[l for l in extraction['links'] if l.get('sent_id')==sentence['sent_id']]
            if any(l['start']-sentence['start']<=match.start()<match.end()<=l['end']-sentence['start'] for l in own_links):
                continue
            clause=text[match.end():right];offset=match.end()
            # A leading dated adverbial may bind to the immediately following
            # predicate, but not to another sentence or a later coordinated one.
            if not re.search(YEAR,clause) and left and re.fullmatch(r'\s*(?:Năm\s+(?:1\d{3}|20\d{2})|ngày\s+\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s+(?:1\d{3}|20\d{2}))\s*,\s*',text[:left],re.I):
                clause=text[:left]+clause;offset=None
            dated=[m for pattern in (DATE,NUMERIC_DATE) for m in pattern.finditer(text,0 if offset is None else match.start(),right)
                if not _quoted(text,m.start(),m.end()) and not any(l['start']-sentence['start']<=m.start()<m.end()<=l['end']-sentence['start'] for l in own_links)]
            try:
                for m in dated:date(int(m[3]),int(m[2]),int(m[1]))
            except ValueError:
                continue
            spans=[];kind='entity';values=[]
            if name in ('measurement','quantity'):
                m=QUANTITY.match(text,match.end(),right)
                if m and name=='measurement':
                    unit=m['unit'].casefold()
                    if ('cao' in match[0] or 'chiều' in match[0]) and unit not in ('m','km','cm','mm'):m=None
                    elif 'diện tích' in match[0] and unit not in ('m²','km²'):m=None
                if m:
                    spans.append({'start':m.start('value'),'end':m.end('value'),'text':m['value'],'unit':m['unit']})
                values=[s['text'] for s in spans];kind='quantity'
            else:
                if offset is None:
                    candidates=list(re.finditer(YEAR,text[:left]))+list(re.finditer(YEAR,text[match.end():right]))
                    for m in candidates:
                        # Leading prefix owns the first group; tail offsets
                        # remain sentence-relative when no prefix year exists.
                        start=m.start() if re.search(YEAR,text[:left]) else m.start()+match.end()
                        spans.append({'start':start,'end':start+len(m[0]),'text':m[0]})
                else:
                    spans=[{'start':offset+m.start(),'end':offset+m.end(),'text':m[0]} for m in re.finditer(YEAR,clause)]
                for m in dated:
                    if not any(s['start']==m.start(3) for s in spans):
                        spans.append({'start':m.start(3),'end':m.end(3),'text':m[3]})
                spans.sort(key=lambda s:s['start'])
                if spans:kind='year';values=[s['text'] for s in spans]
                else:
                    local=[]
                    for l in own_links:
                        start,end=l['start']-sentence['start'],l['end']-sentence['start']
                        forward=match.end()<=start<right
                        inverse=(name in ('authorship','release','event') and left<=start<end<=match.start()
                            and not text[end:match.start()].strip()
                            and re.search(r'\b(?:do|bởi|được)\s*$',text[left:start],re.I))
                        if forward or inverse:local.append(l['target_title'])
                    values=local
            spans=[s for s in spans if not _quoted(text,s['start'],s['end']) and not any(l['start']-sentence['start']<=s['start']<s['end']<=l['end']-sentence['start'] and l['link_text']!=s['text'] for l in own_links)]
            if kind=='year':
                spans=[s for s in spans if not re.match(r'\s*(?:'+UNITS+r')(?!\w)',text[s['end']:],re.I)]
                parenthetical=refined and name=='founded' and any(_parenthetical_founding(text,match,s) for s in spans)
                spans=[s for s in spans if any(m.start(3)==s['start'] for m in dated)
                    or re.search(r'\b(?:năm|vào|từ|đến)\s*$',text[max(0,s['start']-20):s['start']],re.I)
                    or (s['start']>=match.end() and not text[match.end():s['start']].strip(' (:'))
                    or (refined and name=='founded' and _parenthetical_founding(text,match,s))]
                if refined and re.search(r'\(\s*(?:1\d{3}|20\d{2})\s*[-–—]\s*(?:1\d{3}|20\d{2})\s*\)',text[match.end():right]):
                    spans=[]
                if parenthetical:binding_reason='founding_parenthetical_year'
            if kind in ('year','quantity'):
                values=[s['text'] for s in spans]
                if not values:kind='entity'
            if refined and name=='founded' and not values:
                coordinated=_coordinated_founding(text,match,own_links,sentence)
                if coordinated:
                    link,span=coordinated;values=[link['target_title']];spans=[span]
                    binding_reason='coordinated_founding_object'
            if not values and kind=='entity':
                direct=bool(re.search(r'\b(?:tại|ở|của|với|bởi|là)$',match[0],re.I))
                literal=_named_value(text,match.end(),right,direct=direct)
                if literal and direct:
                    # Direct values may begin with whitespace after a predicate
                    # that consumed its preposition.
                    literal['start']+=len(text[literal['start']:literal['end']])-len(text[literal['start']:literal['end']].lstrip())
                    literal['end']=literal['start']+len(literal['text'])
                if not literal and name in ('authorship','release','event'):
                    inv=re.search(r'\b(?:do|bởi)\s+([^,;!?]+?)\s*$',text[:match.start()],re.I)
                    if inv and all(w[:1].isupper() for w in inv[1].split()):
                        literal={'start':inv.start(1),'end':inv.end(1),'text':inv[1]}
                if literal and not _quoted(text,literal['start'],literal['end']):
                    kind='text';values=[literal['text']];spans=[literal]
            if not values:
                continue
            signals.append({'rule':name,'attribute_key':name,'sent_id':sentence['sent_id'],'matched_text':match[0],
                'linked_targets':[l['target_title'] for l in own_links], 'value_kind':kind,'values':values,
                'extraction_version':extraction['extraction_version'],'quality_version':'quality-2',
                'match_start':match.start(),'match_end':match.end(),'value_spans':spans,
                'date_spans':[{'start':m.start(),'end':m.end(),'text':m[0],'iso_date':date(int(m[3]),int(m[2]),int(m[1])).isoformat()} for m in dated]})
            if refined:
                signals[-1]['quality_revision']='quality-2.1'
                if binding_reason:signals[-1]['binding_reason']=binding_reason
    return signals
