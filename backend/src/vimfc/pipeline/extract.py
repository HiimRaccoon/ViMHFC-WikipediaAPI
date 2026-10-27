"""No remote expansion. Unknown factual templates quarantine the entire lead."""
from collections import Counter
import hashlib
import re
import unicodedata
import mwparserfromhell as mw
from mwparserfromhell import nodes
from vimfc.domain import LeadExtractionResult
from vimfc.source.identities import normalize_title
from .sentences import split_sentences
from .inline_templates import normalize_template_name,nonprose_kind,format_inline


def extract_lead(raw, rules):
    source=raw.content if hasattr(raw,'content') else raw
    raw_hash=hashlib.sha256(source.encode('utf-8')).hexdigest()
    diagnostics, occurrences=[],[]
    supported,nonprose=Counter(),Counter()

    def render(code,*,lists=True):
        output=[]
        dropping,line_start=False,True
        for node in code.nodes:
            if isinstance(node,nodes.Heading):
                break
            if isinstance(node,nodes.Tag) and node.wiki_markup in ('*','#',';',':') and line_start:
                if lists:
                    dropping=True
                else:
                    output.append(str(node))
                    line_start=False
                continue
            if isinstance(node,nodes.Text):
                for piece in str(node).splitlines(keepends=True):
                    if lists and line_start and re.match(r'^[ \t]*[*#;:]',piece):
                        dropping=True
                    if not dropping:
                        if '{{' in piece or '}}' in piece:
                            diagnostics.append({'template':'malformed_template','arguments':piece,'reason':'unparsed_template_delimiter_in_prose'})
                        output.append(piece)
                    if piece.endswith(('\n','\r')):
                        dropping,line_start=False,True
                    elif piece.strip():
                        line_start=False
                continue
            if dropping:
                continue
            if not isinstance(node,nodes.Comment):
                line_start=False
            if isinstance(node,nodes.Comment):
                continue
            elif isinstance(node,nodes.HTMLEntity):
                output.append(node.normalize())
            elif isinstance(node,nodes.Wikilink):
                target=str(node.title).strip()
                if re.match(r'^:?(?:file|image|tập tin|hình|category|thể loại):',target,re.I):
                    continue
                label=render(node.text if node.text is not None else node.title,lists=False)
                if not label.strip():
                    diagnostics.append({'template':'link_label','arguments':str(node),'reason':'empty_unresolved_link_label'})
                    continue
                title,_,fragment=target.partition('#')
                if not title or ':' in title:
                    output.append(label)
                    continue
                index=len(occurrences)
                occurrences.append({'target_title':normalize_title(title),'original_target':target,'fragment':fragment,'link_text':label,'scope':'retained_lead_prose'})
                output.append(f'\ue000{index}\ue001{label}\ue002')
            elif isinstance(node,nodes.ExternalLink):
                if node.title is not None:
                    output.append(render(node.title,lists=False))
            elif isinstance(node,nodes.Tag):
                name=str(node.tag).strip().casefold()
                attrs=' '.join(str(a) for a in node.attributes).casefold()
                if name in ('ref','references','table','gallery','imagemap','timeline','math','chem','score','syntaxhighlight','ul','ol','li'):
                    if name in ('math','chem'):
                        diagnostics.append({'template':'tag:'+name,'arguments':str(node.contents),'reason':'unsupported_factual_markup'})
                    continue
                if any(x in attrs for x in ('infobox','navbox','navigation','metadata','mw-editsection','thumb','hatnote','reference')):
                    continue
                if name=='br':
                    output.append(' ')
                elif name in ('p','div'):
                    output.append('\n\n'+render(node.contents,lists=lists)+'\n\n')
                elif name in ('b','i','em','strong','span','small','big','sup','sub','nowiki','s','u'):
                    output.append(render(node.contents,lists=False) if name!='nowiki' else str(node.contents))
                else:
                    diagnostics.append({'template':'tag:'+name,'arguments':str(node.contents),'reason':'unsupported_tag'})
            elif isinstance(node,nodes.Template):
                name=normalize_template_name(node.name)
                if nonprose_kind(name,rules):
                    nonprose[name]+=1
                    continue
                params={str(p.name).strip():p.value for p in node.params}
                try:
                    if len(params)!=len(node.params):
                        raise ValueError('duplicate arguments')
                    output.append(format_inline(name,params,rules,lambda value:render(value,lists=False),render_block=lambda value:render(value,lists=lists)))
                    supported[name]+=1
                except (ValueError,KeyError) as error:
                    diagnostics.append({'template':name,'arguments':{k:str(v) for k,v in params.items()},'reason':str(error)})
            else:
                diagnostics.append({'template':type(node).__name__,'arguments':str(node),'reason':'unsupported_ast_node'})
        return ''.join(output)

    # Parse the original AST before excluding lists. Removing source lines first
    # can delete a template's closing braces and leak infobox contents into prose.
    rendered=unicodedata.normalize('NFC',render(mw.parse(source)))
    paragraphs, links=[],[]
    for block in re.split(r'\n\s*\n',rendered):
        block=re.sub(r'\s+',' ',block).strip()
        if not block:
            continue
        text=[]
        cursor=0
        block_links=[]
        for match in re.finditer(r'\ue000(\d+)\ue001(.*?)\ue002',block):
            text.append(block[cursor:match.start()])
            left=sum(map(len,text))
            text.append(match[2])
            block_links.append({**occurrences[int(match[1])],'link_text':match[2],'paragraph_id':len(paragraphs),'start':left,'end':left+len(match[2])})
            cursor=match.end()
        text.append(block[cursor:])
        value=''.join(text)
        trim=len(value)-len(value.lstrip())
        value=value.strip()
        if value:
            for link in block_links:
                link['start']-=trim
                link['end']-=trim
                links.append(link)
            paragraphs.append(value)
    sentences=split_sentences(paragraphs,rules,links=links)
    for link in links:
        matches=[s for s in sentences if s['paragraph_id']==link['paragraph_id'] and s['start']<=link['start'] and link['end']<=s['end']]
        if len(matches)!=1:
            diagnostics.append({'template':'link_boundary','arguments':link,'reason':'link_crosses_sentence_boundary'})
        else:
            link['sent_id']=matches[0]['sent_id']
    text='\n\n'.join(paragraphs)
    for diagnostic in diagnostics:
        diagnostic['raw_hash']=raw_hash
    return LeadExtractionResult('unresolved_extraction' if diagnostics else 'completed',rules['version'],text,paragraphs,sentences,[] if diagnostics else links,diagnostics,
        {'sentence_count':len(sentences),'paragraph_count':len(paragraphs),'lead_characters':len(text),'lead_tokens':len(text.split()),'provisional':bool(diagnostics)},
        {'supported':dict(sorted(supported.items())),'excluded_nonprose':dict(sorted(nonprose.items()))})
