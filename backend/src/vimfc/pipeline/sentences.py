"""Versioned NFC punctuation segmentation, with paragraph-relative spans."""
import re
import unicodedata


def split_sentences(paragraphs, rules):
    result=[]
    for paragraph_id, source in enumerate(paragraphs):
        text=unicodedata.normalize('NFC',source)
        protected=text
        for abbreviation in sorted(rules['abbreviations'],key=len,reverse=True):
            protected=re.sub(re.escape(abbreviation),lambda m:m[0].replace('.','\ue010'),protected)
        protected=re.sub(r'(?<=\d)\.(?=\d)', '\ue010',protected)
        protected=re.sub(r'\b[A-ZĐ]\.(?=\s)',lambda m:m[0].replace('.','\ue010'),protected)
        start=0
        ends=[m.end() for m in re.finditer(r'[.!?…]+["”’\)\]]*(?:\s+|$)',protected)]
        if not ends or ends[-1]!=len(text):
            ends.append(len(text))
        for end in ends:
            left=start
            while left<end and text[left].isspace():
                left+=1
            right=end
            while right>left and text[right-1].isspace():
                right-=1
            if left<right:
                result.append({'sent_id':len(result),'paragraph_id':paragraph_id,'text':text[left:right],'start':left,'end':right})
            start=end
    return result
