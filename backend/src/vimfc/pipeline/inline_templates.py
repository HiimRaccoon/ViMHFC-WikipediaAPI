"""Strict, versioned argument-only formatters; no implicit links or expansion."""
from datetime import date
import re


def normalize_template_name(value):
    name=' '.join(str(value).replace('_',' ').strip().casefold().split())
    return re.sub(r'^(?:template|bản mẫu):\s*','',name)


def nonprose_kind(name,rules):
    if name in rules.get('nonprose_templates',{}):
        return rules['nonprose_templates'][name]
    if any(name.startswith(p) for p in rules.get('excluded_magic_prefixes',[])):
        return 'page_metadata'
    if any(name==p or name.startswith(p+' ') for p in rules['excluded_template_prefixes']):
        return 'configured_nonprose'
    return None


def format_inline(name,params,rules,render,*,render_block=None):
    formatter=rules['formatters'].get(name)
    keys=set(params)
    if formatter=='lang' and keys=={'1','2'}:
        return render(params['2'])
    if formatter=='text' and keys=={'1'}:
        return render(params['1'])
    if formatter=='date' and keys <= {'1','2','3','df','mf'} and {'1','2','3'} <= keys:
        try:
            value=date(*(int(str(params[k]).strip()) for k in ('1','2','3')))
        except (ValueError,OverflowError) as error:
            raise ValueError('invalid or out-of-range date arguments') from error
        return f'{value.day} tháng {value.month} năm {value.year}'
    if formatter=='language_label' and keys=={'1','2'}:
        language=rules['language_names'].get(str(params['1']).strip().casefold())
        if language and str(params['2']).strip():
            return language+': '+render(params['2'])
    if formatter=='ipa' and keys=={'1','2'} and re.fullmatch(r'[a-z]{2,3}(?:-[a-z0-9]+)*',str(params['1']).strip()) and str(params['2']).strip():
        return 'IPA ('+str(params['1']).strip()+'): '+render(params['2'])
    if formatter=='japanese' and {'1','2'} <= keys <= {'1','2','3'} and all(str(params[k]).strip() for k in keys):
        text=render(params['1'])+' (tiếng Nhật: '+render(params['2'])
        if '3' in params:
            text+='; chuyển tự: '+render(params['3'])
        return text+')'
    if formatter=='chinese' and 'c' in keys and keys <= {'c','p'} and all(str(params[k]).strip() for k in keys):
        text='chữ Hán: '+render(params['c'])
        return text+('; bính âm: '+render(params['p']) if 'p' in keys else '')
    if formatter=='airport_codes' and keys=={'1','2'} and re.fullmatch('[A-Z0-9]{3}',str(params['1']).strip()) and re.fullmatch('[A-Z0-9]{4}',str(params['2']).strip()):
        return 'IATA: '+str(params['1']).strip()+'; ICAO: '+str(params['2']).strip()
    if formatter=='quotation' and keys=={'1'} and str(params['1']).strip():
        return '\n\n“'+(render_block or render)(params['1'])+'”\n\n'
    raise ValueError('unsupported template or ambiguous/missing arguments')
