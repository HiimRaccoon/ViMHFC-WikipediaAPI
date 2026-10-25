from vimfc.pipeline.sentences import split_sentences


def test_abbreviations_initials_decimals_quotes_and_nfc(config):
    parts = split_sentences(['TS. N. A. nói: “Giá trị là 3.14.” Ông ở TP. Hà Nội.','Đoạn khác... Kết thúc!'],config['extraction'])
    assert [s['text'] for s in parts] == ['TS. N. A. nói: “Giá trị là 3.14.”','Ông ở TP. Hà Nội.','Đoạn khác...','Kết thúc!']
    assert [s['sent_id'] for s in parts] == [0,1,2,3]
    assert [s['paragraph_id'] for s in parts] == [0,0,1,1]
