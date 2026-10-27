"""Archived punctuation/aliases and positive/negative factual evidence contracts."""
import json
from pathlib import Path
import pytest
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.quality import assess_quality


def signals(text, config):
    ext=extract_lead(text,config['extraction'])
    return ext,assess_quality(ext,{'outcome':'completed'},[],config['extraction'])


def test_linked_album_punctuation_preserves_sentence_and_exact_link_span(config):
    text='Bài hát thuộc album [[We Sing. We Dance. We Steal Things.]] (2008). Bài hát được phát hành năm 2008.'
    ext=extract_lead(text,config['extraction'])
    assert ext.outcome=='completed'
    assert [s['text'] for s in ext.sentences]==['Bài hát thuộc album We Sing. We Dance. We Steal Things. (2008).','Bài hát được phát hành năm 2008.']
    link=ext.links[0]
    assert link['sent_id']==0 and ext.paragraphs[0][link['start']:link['end']]==link['link_text']


def test_link_protection_does_not_hide_outside_sentence_boundaries(config):
    ext=extract_lead('Tác phẩm [[A. B! C?|Tên. Có! Dấu?]] được xuất bản. Câu thứ hai.',config['extraction'])
    assert ext.outcome=='completed' and len(ext.sentences)==2
    assert ext.links[0]['original_target']=='A. B! C?' and ext.links[0]['link_text']=='Tên. Có! Dấu?'


def test_terminal_link_punctuation_can_end_the_containing_sentence(config):
    ext=extract_lead('Ông thích [[Album.]] Bà thích tác phẩm khác.',config['extraction'])
    assert [s['text'] for s in ext.sentences]==['Ông thích Album.','Bà thích tác phẩm khác.']
    assert ext.links[0]['sent_id']==0


@pytest.mark.parametrize('name',['thông tin tiểu sử bóng đá','thông tin nghệ sĩ','thông tin nhạc sĩ','thông tin sự kiện','thông tin công trình xây dựng','thông tin hoa hậu','for','dablink','sfn','use dmy dates'])
def test_verified_nonprose_alias_excludes_nested_content_and_links(config,name):
    ext=extract_lead('{{'+name+'|name=[[Excluded]]|nested={{Unknown|fact}}}}\nVăn xuôi [[Đúng]] có nội dung. Câu tiếp theo.',config['extraction'])
    assert ext.outcome=='completed' and [l['target_title'] for l in ext.links]==['Đúng']
    assert 'Excluded' not in ext.full_lead and ext.template_usage['excluded_nonprose'][name]==1


@pytest.mark.parametrize('text,key,value',[
    ('Album được phát hành vào ngày 4 tháng 9 năm 2006 bởi hãng đĩa.', 'release','2006'),
    ('Giải đấu khởi tranh ngày 31 tháng 12 năm 2011.', 'event_start','2011'),
    ('Giải đấu kết thúc vào ngày 19 tháng 8 năm 2012.', 'event_end','2012'),
    ('Trường được thành lập ngày 25 tháng 11 năm 2010.', 'founded','2010'),
    ('Album được phát hành ngày 04/09/2006.', 'release','2006'),
    ('Tòa nhà có chiều cao 400 m và diện tích 2.500 m².', 'measurement','400'),
    ('Giải đấu có 14 câu lạc bộ tham dự.', 'quantity','14'),
])
def test_new_factual_attributes_have_exact_sentence_offsets(config,text,key,value):
    ext,q=signals(text,config)
    found=next(s for s in q['signals'] if s['attribute_key']==key)
    assert value in found['values'] and found['sent_id']==0
    s=ext.sentences[0]['text']
    assert s[found['match_start']:found['match_end']]==found['matched_text']
    assert all(s[v['start']:v['end']]==v['text'] for v in found['value_spans'])
    assert found['quality_version']=='quality-2'


@pytest.mark.parametrize('text',[
    'Album chưa được phát hành năm 2006.',
    'Album dự kiến phát hành vào năm 2030.',
    'Nếu được thành lập năm 2010, trường sẽ có nhiều sinh viên.',
    'Tòa nhà có thể cao 400 m.',
    'Mã sản phẩm là ABC-2006 và bài viết có số 14.',
    'Tên album là "Phát hành năm 2006".',
    'Ngày 31 tháng 2 năm 2010 là dữ liệu không hợp lệ.',
    'Album được phát hành ngày 31/02/2010.',
    'Album được phát hành bởi hãng "Năm 2006".',
    'Album được phát hành với mã ABC-2006.',
    'Tòa nhà cao 400 với 20 người tham dự.',
    'Ông sinh ngày 31 tháng 2 năm 2000.',
    'Album phát hành dự kiến vào năm 2030.',
    'Phòng có tiện nghi sinh hoạt và làm việc cho mọi người.',
    '[[Hãng 2006]] là tên riêng chứa từ phát hành.',
    'Album được phát hành với 2006 bản.',
])
def test_numeric_dates_negation_hypotheses_and_titles_do_not_invent_signals(config,text):
    _,q=signals(text,config)
    assert q['signals']==[]


def test_date_before_predicate_is_bound_without_stealing_another_clause(config):
    _,q=signals('Năm 1999, trường được thành lập. Ông sinh tại Hà Nội và thành lập Viện A năm 2001.',config)
    founded=[s for s in q['signals'] if s['rule']=='founded']
    assert [s['values'] for s in founded]==[['1999'],['2001']]
    assert not any(s['rule']=='birth' and s['value_kind']=='year' for s in q['signals'])


def test_units_and_birth_dates_do_not_cross_comparison_attribute_blocks(config):
    _,q=signals('Tòa nhà cao 400 m. Album được phát hành năm 2006.',config)
    measurement=next(s for s in q['signals'] if s['rule']=='measurement')
    assert measurement['value_kind']=='quantity' and measurement['values']==['400']
    assert not any(s['rule']=='birth' for s in q['signals'])


def test_new_dates_preserve_full_literal_and_calendar_value(config):
    ext,q=signals('Album được phát hành ngày 04/09/2006.',config)
    s=next(s for s in q['signals'] if s['rule']=='release')
    d=s['date_spans'][0]
    assert d['iso_date']=='2006-09-04'
    assert ext.sentences[0]['text'][d['start']:d['end']]==d['text']=='04/09/2006'


def test_publisher_name_year_is_not_a_release_year(config):
    _,q=signals('Album được phát hành bởi [[Hãng 2006]].',config)
    assert all(s['value_kind']!='year' for s in q['signals'])


@pytest.mark.parametrize('title',['[[1989 (album)|1989]]','1989'])
def test_numeric_work_title_is_not_a_temporal_comparison_attribute(config,title):
    _,q=signals('Bài hát được phát hành trong album '+title+'.',config)
    assert not any(s['value_kind']=='year' for s in q['signals'])


def test_inverse_linked_author_is_not_confused_with_unrelated_leading_link(config):
    _,q=signals('Bài hát do [[Nhạc sĩ A]] sáng tác.',config)
    assert next(s for s in q['signals'] if s['rule']=='authorship')['values']==['Nhạc sĩ A']


def test_decimal_comma_measurement_is_not_split_or_normalized(config):
    _,q=signals('Tòa nhà cao 2,5 m.',config)
    assert [(s['rule'],s['values'],s['value_spans'][0]['unit']) for s in q['signals']]==[('measurement',['2,5'],'m')]


def test_birth_date_and_event_start_end_have_unambiguous_local_provenance(config):
    _,q=signals('Ông sinh ngày 4 tháng 9 năm 2000. Giải khởi tranh năm 2011 và kết thúc năm 2012.',config)
    assert next(s for s in q['signals'] if s['rule']=='birth')['date_spans'][0]['iso_date']=='2000-09-04'
    assert [(s['attribute_key'],s['values']) for s in q['signals'] if s['rule'].startswith('event_')]==[('event_start',['2011']),('event_end',['2012'])]


def test_full_date_is_not_copied_to_a_later_predicate(config):
    _,q=signals('Ông sinh ngày 4 tháng 9 năm 2000 sau đó thành lập trường.',config)
    assert [s['rule'] for s in q['signals']]==['birth']


def test_explicit_year_link_is_a_date_value_not_an_unrelated_named_title(config):
    _,q=signals('Ông sinh ngày [[25 tháng 8]] năm [[1966]].',config)
    s=next(s for s in q['signals'] if s['rule']=='birth')
    assert s['value_kind']=='year' and s['values']==['1966']


def test_explicit_unlinked_named_attribute_retains_literal_provenance(config):
    ext,q=signals('Bài hát được sáng tác bởi Dub Allbritten. Ông làm việc tại Hà Nội.',config)
    assert [(s['rule'],s['value_kind'],s['values']) for s in q['signals']]==[('authorship','text',['Dub Allbritten']),('employment','text',['Hà Nội'])]
    for s in q['signals']:
        v=s['value_spans'][0]
        assert ext.sentences[s['sent_id']]['text'][v['start']:v['end']]==v['text']


def test_consumed_place_preposition_and_inverse_plain_author_keep_literal_values(config):
    _,q=signals('Ông sinh ra tại Queens, New York năm 1975. Phim do Jon M. Chu đạo diễn.',config)
    assert [(s['rule'],s['value_kind'],s['values']) for s in q['signals']]==[('birth','text',['Queens']),('authorship','text',['Jon M. Chu'])]


def test_quoted_date_is_not_attribute_date_provenance(config):
    _,q=signals('Album phát hành năm 2006 với tên "Ngày 4 tháng 9 năm 2008".',config)
    s=next(s for s in q['signals'] if s['rule']=='release')
    assert s['values']==['2006'] and s['date_spans']==[]


def test_ambiguous_factual_templates_stay_quarantined(config):
    for template in ['{{Unknown|fact}}','{{langx|ru|X|lit=nghĩa}}','{{convert|26|t|lb}}']:
        ext=extract_lead('Nội dung '+template+' có [[A]] và số 2006.',config['extraction'])
        assert ext.outcome=='unresolved_extraction' and ext.links==[]


def test_lead3_replay_keeps_original_sentence_and_quality_semantics():
    rules=json.loads(Path('configs/legacy/extraction-lead-3.json').read_text(encoding='utf-8'))
    ext=extract_lead('Bài hát thuộc [[We Sing. We Dance. We Steal Things.]] (2008).',rules)
    assert ext.outcome=='unresolved_extraction'
    q=assess_quality(extract_lead('Album được phát hành năm 2006.',rules),{'outcome':'completed'},[],rules)
    assert q['signals']==[] and 'quality_version' not in q
