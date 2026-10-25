from vimfc.pipeline.rank import score_pair


def test_fixed_point_positive_and_negative_scores_keep_features(config):
    features={'relation_signal':100,'complementary_information':100,'endpoint_quality':80,'redundancy':0,'shortcut_risk':0}
    assert score_pair('bridge',features,config['ranking'])==760
    features.update(relation_signal=0,complementary_information=0,redundancy=100,shortcut_risk=100)
    assert score_pair('bridge',features,config['ranking'])==-340
