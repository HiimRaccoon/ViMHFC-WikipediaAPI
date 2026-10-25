import pytest
from vimfc.pipeline.extract import extract_lead


def test_archived_free_fire_label_keeps_literal_colon_and_nonempty_prose_span(config):
    result=extract_lead('Các môn thi đấu gồm [[Garena Free Fire|: Free Fire]], PUBG và Valorant. Giải diễn ra năm 2021.',config['extraction'])
    assert result.outcome=='completed'
    assert ': Free Fire' in result.full_lead
    link=result.links[0]
    assert link['link_text']==': Free Fire' and link['end']>link['start']


def test_inline_formatter_and_html_text_cannot_be_mistaken_for_list_lines(config):
    result=extract_lead('Tên {{nowrap|: Phần phụ}} và <small>: Ghi chú</small>. Nội dung tiếp theo.',config['extraction'])
    assert result.outcome=='completed'
    assert ': Phần phụ' in result.full_lead and ': Ghi chú' in result.full_lead


def test_empty_pipe_label_is_quarantined_instead_of_a_zero_width_graph_edge(config):
    result=extract_lead('Tên [[Đích|]] có nội dung. Câu tiếp theo.',config['extraction'])
    assert result.outcome=='unresolved_extraction' and not result.links
    assert any(d['reason']=='empty_unresolved_link_label' for d in result.diagnostics)


def test_document_lists_and_quotation_lists_still_exclude_their_links(config):
    raw='Văn xuôi [[Đúng]].\n* [[List]]\n{{cquote|Câu trích dẫn [[Quote]].\n* [[Quote list]]\nCâu kết.}}'
    result=extract_lead(raw,config['extraction'])
    assert result.outcome=='completed'
    assert [link['target_title'] for link in result.links]==['Đúng','Quote']


@pytest.mark.parametrize('label',["TV&nbsp;That's Incredible!",'A\u0301nh sáng','Tên   có khoảng trắng'])
def test_link_text_matches_the_normalized_retained_prose_exactly(config,label):
    result=extract_lead('Chương trình [[Đích|'+label+']] được giới thiệu. Câu tiếp theo.',config['extraction'])
    assert result.outcome=='completed'
    link=result.links[0]
    assert result.paragraphs[link['paragraph_id']][link['start']:link['end']]==link['link_text']
