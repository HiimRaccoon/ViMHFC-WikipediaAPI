from pathlib import Path
import pytest
from vimfc.pipeline.extract import extract_lead


def test_full_lead_keeps_all_prose_and_only_actual_lead_links(config):
    raw = Path('tests/fixtures/lead.wiki').read_text(encoding='utf-8')
    result = extract_lead(raw,config['extraction'])
    assert result.outcome == 'completed'
    assert len(result.paragraphs) == 2 and len(result.sentences) == 4
    assert '3 tháng 2 năm 1980' in result.full_lead
    assert 'TP. Hà Nội.' in result.full_lead
    assert 'Không giữ' not in result.full_lead and 'Nội dung thân bài' not in result.full_lead
    assert [x['target_title'] for x in result.links] == ['Đại học B','Việt Nam']
    assert [s['sent_id'] for s in result.sentences] == [0,1,2,3]
    assert all(link['sent_id'] == 0 and link['scope'] == 'retained_lead_prose' for link in result.links)


@pytest.mark.parametrize('template', ['{{Tuổi|1980|1|1}}','{{birth date|1980|13|1}}','{{lang|en}}','{{nowrap|a|b}}'])
def test_unsupported_factual_forms_are_quarantined_not_silently_dropped(config,template):
    result = extract_lead('Ông sinh '+template+' tại [[Hà Nội]].',config['extraction'])
    assert result.outcome == 'unresolved_extraction' and result.diagnostics
    assert result.links == []
    assert result.diagnostics[0]['arguments']


def test_nested_formatter_preserves_link_provenance_and_quotes(config):
    result = extract_lead('Ông là {{nowrap|{{lang|en|[[A|Alpha]]}}}}. Ông sinh năm 1980.',config['extraction'])
    assert result.full_lead == 'Ông là Alpha. Ông sinh năm 1980.'
    assert result.links[0]['target_title'] == 'A'


def test_nonprose_tags_and_lists_cannot_leak_links(config):
    raw = '<div class="navbox">[[Sai]]</div>Văn xuôi [[Đúng]].\n\n* [[Danh sách]]\n\nĐoạn hai.\n==Heading==\n[[Thân]]'
    result = extract_lead(raw,config['extraction'])
    assert [x['target_title'] for x in result.links] == ['Đúng']
    assert 'Danh sách' not in result.full_lead
