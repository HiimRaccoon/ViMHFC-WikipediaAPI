"""FAST-20K only: retain the first meaningful lead prose paragraph."""
from copy import deepcopy
import hashlib
import re

import mwparserfromhell as mw
from mwparserfromhell import nodes

from vimfc.domain import LeadExtractionResult
from .extract import extract_lead
from .sentences import split_sentences

VERSION = 'lead-first-paragraph-1'


def _prefix(source, rules):
    """Split only at parsed top-level boundaries, never inside an infobox/template."""
    parts = []
    def has_prose():
        return bool(extract_lead(''.join(parts), rules).paragraphs)
    for node in mw.parse(source).nodes:
        if isinstance(node, nodes.Heading):
            break
        if isinstance(node, nodes.Text):
            for piece in re.split(r'(\n[ \t\r]*\n)', str(node)):
                parts.append(piece)
                if re.fullmatch(r'\n[ \t\r]*\n', piece) and has_prose():
                    return ''.join(parts)
        elif isinstance(node, nodes.Tag) and str(node.tag).casefold() in ('p', 'div'):
            attrs = ' '.join(str(a) for a in node.attributes).casefold()
            if any(k in attrs for k in ('infobox', 'navbox', 'navigation', 'metadata', 'thumb', 'hatnote', 'reference')):
                parts.append(str(node))
                continue
            if has_prose():
                return ''.join(parts)
            clone = deepcopy(node)
            clone.contents = _prefix(str(node.contents), rules)
            parts.append(str(clone))
            if has_prose():
                return ''.join(parts)
        else:
            parts.append(str(node))
    return ''.join(parts)


def extract_first_paragraph(raw, rules):
    source = raw.content if hasattr(raw, 'content') else raw
    rules = {**rules, 'version': VERSION}
    result = extract_lead(_prefix(source, rules), rules)
    paragraphs = result.paragraphs[:1]
    links = [dict(link) for link in result.links if link['paragraph_id'] == 0]
    sentences = split_sentences(paragraphs, rules, links=links)
    for link in links:
        matches = [s for s in sentences if s['start'] <= link['start'] and link['end'] <= s['end']]
        if len(matches) != 1:
            raise ValueError('First-paragraph hyperlink sentence association differs')
        link['sent_id'] = matches[0]['sent_id']
    diagnostics = deepcopy(result.diagnostics)
    for diagnostic in diagnostics:
        diagnostic['raw_hash'] = hashlib.sha256(source.encode('utf-8')).hexdigest()
    text = paragraphs[0] if paragraphs else ''
    return LeadExtractionResult(result.outcome, VERSION, text, paragraphs, sentences, links, diagnostics,
        dict(sentence_count=len(sentences), paragraph_count=len(paragraphs), lead_characters=len(text),
             lead_tokens=len(text.split()), provisional=bool(diagnostics)), result.template_usage)
