import re


def score_pair(kind, features, rules):
    return sum(weight*features.get(key,0) for key,weight in rules[kind+'_weights'].items())


def features_for(kind, a, b, rules, relation=None):
    stop=set(rules['stopwords'])
    def tokens(article):
        return set(re.findall(r'\w+',article['extraction']['full_lead'].casefold()))-stop
    ta,tb=tokens(a),tokens(b)
    redundancy=100*len(ta&tb)//max(1,len(ta|tb))
    sa,sb=a['quality']['signals'],b['quality']['signals']
    keys_a={s['attribute_key'] for s in sa}
    keys_b={s['attribute_key'] for s in sb}
    complementary=100*len(keys_a^keys_b)//max(1,len(keys_a|keys_b))
    direct=any(len({s['rule'] for s in sa if s['sent_id']==i})>=2 and relation and relation['sent_id']==i for i in {s['sent_id'] for s in sa})
    return {'relation_signal':100 if relation and any(s['sent_id']==relation['sent_id'] and relation['target_title'] in s['linked_targets'] for s in sa) else 0,
        'complementary_information':complementary,'endpoint_quality':min(a['quality']['score'],b['quality']['score']),
        'subtype_match':100 if kind=='comparison' else 0,'attribute_compatibility':100 if kind=='comparison' else 0,
        'redundancy':redundancy,'shortcut_risk':100 if direct else redundancy}
