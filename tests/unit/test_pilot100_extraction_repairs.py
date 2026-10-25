import pytest
from vimfc.pipeline.extract import extract_lead


@pytest.mark.parametrize('name',['Thông tin khu dân cư','Thông tin máy bay','Thông tin phim','Thông tin trường học','Bảng phân loại','Ethnic group','Viên chức'])
def test_reviewed_infobox_alias_does_not_quarantine_authored_prose(config,name):
    raw='{{'+name+'|name=X|caption=[[Excluded]]|nested={{Unknown|factual}}}}\nÔng sinh năm 1980 tại [[Hà Nội]]. Ông gia nhập viện nghiên cứu năm 2001.'
    result=extract_lead(raw,config['extraction'])
    assert result.outcome=='completed'
    assert [link['target_title'] for link in result.links]==['Hà Nội']
    assert 'Excluded' not in result.full_lead and '1980' in result.full_lead


@pytest.mark.parametrize('name',['Bài cùng tên','Phân biệt','Về','Other','Redirect','Xem thêm','Mồ côi','Thiếu nguồn gốc','BLP sources'])
def test_reviewed_hatnote_or_maintenance_is_not_factual_prose(config,name):
    result=extract_lead('{{'+name+'|[[Not evidence]]}}\nVăn xuôi [[Đúng]]. Đoạn tiếp theo.',config['extraction'])
    assert result.outcome=='completed'
    assert [link['target_title'] for link in result.links]==['Đúng']


@pytest.mark.parametrize('template,expected',[
    ('{{langx|en|[[A|Alpha]]}}','tiếng Anh: Alpha'),
    ('{{langx|ru|Р-9}}','tiếng Nga: Р-9'),
    ('{{IPA|nl|kaːveː}}','IPA (nl): kaːveː'),
    ('{{nihongo|Company|会社|Kaisha}}','Company (tiếng Nhật: 会社; chuyển tự: Kaisha)'),
    ('{{zh|c=苗|p=Miáo}}','chữ Hán: 苗; bính âm: Miáo'),
    ('{{airport codes|BNE|YBBN}}','IATA: BNE; ICAO: YBBN'),
])
def test_safe_formatter_preserves_authored_arguments_without_invented_links(config,template,expected):
    result=extract_lead('Tên ('+template+'). Câu tiếp theo.',config['extraction'])
    assert result.outcome=='completed'
    assert expected in result.full_lead
    assert all(link['target_title']=='A' for link in result.links)


@pytest.mark.parametrize('template',['{{langx|xx|X}}','{{langx|en|X|unknown=fact}}','{{langx|en}}','{{nihongo|X|Y|Z|extra fact}}','{{zh|c=X|mystery=Y}}','{{sclass|Type VII|tàu ngầm|4}}','{{cvt|26|t|lb}}','{{USA}}'])
def test_unreviewed_or_ambiguous_factual_forms_stay_unresolved(config,template):
    result=extract_lead('Nội dung '+template+' tại [[A]].',config['extraction'])
    assert result.outcome=='unresolved_extraction' and not result.links


def test_quotation_prose_and_links_are_preserved_but_footnote_is_excluded(config):
    result=extract_lead('Tuyên bố:\n{{cquote|Đoạn [[A]] đầu tiên.\n\nĐoạn thứ hai.}}\nCâu cuối{{efn|[[Footnote]]}}.',config['extraction'])
    assert result.outcome=='completed'
    assert 'Đoạn thứ hai.' in result.full_lead
    assert [link['target_title'] for link in result.links]==['A']


def test_company_abbreviation_does_not_split_a_retained_link(config):
    result=extract_lead('Phim do [[Warner Bros.|Warner Bros. Television]] sản xuất. Phim ra mắt năm 2010.',config['extraction'])
    assert result.outcome=='completed'
    assert len(result.sentences)==2 and result.links[0]['sent_id']==0
@pytest.mark.parametrize('text',['Before {{birth date|999999999999999999999|1|1}} after.','Before {{birth date|1980|999999999999999999999|1}} after.','Before {{Unknown|fact','Before unknown }} factual text.'])
def test_malformed_factual_templates_are_quarantined_without_aborting(text,config):
    result=extract_lead(text,config['extraction'])
    assert result.outcome=='unresolved_extraction'
    assert result.diagnostics and result.links==[]


def test_list_lines_inside_an_excluded_infobox_do_not_break_its_ast(config):
    raw='{{Infobox organization|members=\n* [[Box link]] {{USA}} }}\nÔng sinh năm 1980 tại [[Hà Nội]]. Ông gia nhập viện năm 2001.'
    result=extract_lead(raw,config['extraction'])
    assert result.outcome=='completed'
    assert [link['target_title'] for link in result.links]==['Hà Nội']
    assert 'members=' not in result.full_lead


def test_a_multiline_template_on_a_list_line_is_skipped_as_a_whole(config):
    result=extract_lead('* {{Unknown|nested=\n[[List target]]}}\nÔng sinh năm 1980 tại [[Hà Nội]]. Ông gia nhập viện năm 2001.',config['extraction'])
    assert result.outcome=='completed'
    assert [link['target_title'] for link in result.links]==['Hà Nội']
