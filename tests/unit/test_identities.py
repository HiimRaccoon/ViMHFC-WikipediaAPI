from vimfc.source.identities import resolve_historical, title_at_snapshot
from pathlib import Path
import json


def test_moved_title_rewinds_to_snapshot_owner():
    moves = json.loads(Path('tests/fixtures/move_redirects.json').read_text(encoding='utf-8'))['moves']
    assert title_at_snapshot(1,'Tên mới',moves,'2026-10-14T00:00:00Z') == 'Tên cũ'


def test_redirect_chain_retains_fragment_and_rejects_cycles_ambiguity():
    records = {'A':{'page_id':1,'title':'A','revision_id':10,'content':'#ĐỔI [[B#Mục]]'},
               'B':{'page_id':2,'title':'B','revision_id':20,'content':'Văn xuôi.'}}
    result = resolve_historical('A', lambda title: [records[title]])
    assert result['page_id'] == 2 and result['chain'][0]['fragment'] == 'Mục'
    records['B']['content'] = '#REDIRECT [[A]]'
    assert resolve_historical('A', lambda title:[records[title]])['status'] == 'unresolved'
    assert resolve_historical('A', lambda title:[records['A'],records['B']])['status'] == 'unresolved'


def test_redirect_depth_is_bounded():
    def lookup(title):
        i = int(title)
        return [{'page_id':i+1,'title':title,'revision_id':i+10,'content':f'#REDIRECT [[{i+1}]]'}]
    assert resolve_historical('0',lookup)['reason'] == 'redirect_depth'
