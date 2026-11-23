from dataclasses import asdict
import unicodedata

import pytest

from vimfc.pipeline.first_paragraph import extract_first_paragraph, VERSION
from vimfc.pipeline.extract import extract_lead


def test_infobox_multiple_paragraphs_heading_and_real_links(config):
    text = '{{Infobox person\n|name=X\n}}\n\nA là một nhà khoa học tại [[Hà Nội]]. A sinh năm 1980.\n\nĐoạn hai có [[Huế]].\n== Thân bài ==\n[[Đà Nẵng]]'
    result = extract_first_paragraph(text, config['extraction'])
    assert result.outcome == 'completed' and result.extraction_version == VERSION
    assert len(result.paragraphs) == 1 and 'Đoạn hai' not in result.full_lead
    assert [l['target_title'] for l in result.links] == ['Hà Nội']
    assert [s['sent_id'] for s in result.sentences] == list(range(len(result.sentences)))
    for link in result.links:
        sentence = result.sentences[link['sent_id']]
        assert result.full_lead[link['start']:link['end']] == link['link_text']
        assert sentence['start'] <= link['start'] < link['end'] <= sentence['end']
    assert asdict(result) == asdict(extract_first_paragraph(text, config['extraction']))
    assert len(extract_lead(text, config['extraction']).paragraphs) == 2


@pytest.mark.parametrize('first,second,outcome', [
    ('A là một thành phố.', '{{Unknown|sự kiện}}', 'completed'),
    ('A là {{Unknown|một thành phố}}.', 'Đoạn khác.', 'unresolved_extraction'),
    ('{{unclosed|A là một thành phố.', 'Đoạn khác.', 'unresolved_extraction'),
    ('A là một thành phố.', '{{unclosed|later', 'completed'),
])
def test_template_scope_and_malformed_boundaries(config, first, second, outcome):
    result = extract_first_paragraph(first + '\n\n' + second, config['extraction'])
    assert result.outcome == outcome
    assert len(result.paragraphs) <= 1


@pytest.mark.parametrize('raw', [
    'A là một thành phố.\n== Lịch sử ==\nB là {{Unknown}}.',
    '<p>A là một thành phố.</p><p>B là {{Unknown}}.</p>',
    '<div><p>A là một thành phố.</p><p>B là {{Unknown}}.</p></div>',
    '{| class="wikitable"\n| bỏ bảng\n|}\n\nA là một thành phố.\n\nB là {{Unknown}}.',
    '* [[Mục danh sách]]\n\nA là một thành phố.\n\nB là {{Unknown}}.',
])
def test_reliably_discarded_content_does_not_poison_first_paragraph(config, raw):
    result = extract_first_paragraph(raw, config['extraction'])
    assert result.outcome == 'completed'
    assert result.paragraphs == ['A là một thành phố.'] and not result.links


def test_short_first_paragraph_is_not_extended(config):
    result = extract_first_paragraph('Ngắn.\n\nB là một nhà khoa học sinh năm 1980.', config['extraction'])
    assert result.paragraphs == ['Ngắn.']


def test_nfc_vietnamese_abbreviations_and_punctuation(config):
    raw = unicodedata.normalize('NFD', 'TS. A là một nhà khoa học tại TP. [[Hồ Chí Minh]]. Ông sinh năm 1980!')
    result = extract_first_paragraph(raw, config['extraction'])
    assert result.outcome == 'completed' and unicodedata.is_normalized('NFC', result.full_lead)
    assert len(result.sentences) == 2
    assert [s['sent_id'] for s in result.sentences] == [0, 1]
    assert result.links[0]['sent_id'] == 0
