import json
from pathlib import Path
from .domain import InvalidConfig, digest, GROUPS


def load_config(path):
    path = Path(path)
    cfg = json.loads(path.read_text(encoding='utf-8'))
    for key in ('classification', 'extraction', 'ranking'):
        rule_path = path.parent / cfg.pop(key + '_file')
        cfg[key] = json.loads(rule_path.read_text(encoding='utf-8'))
    if cfg['initial_target'] != 80000 or cfg['corpus_size'] != 20000:
        raise InvalidConfig('Initial target must be 80000 and frozen count exactly 20000')
    if set(cfg['quotas']) != set(GROUPS) or sum(cfg['quotas'].values()) != 20000:
        raise InvalidConfig('Eight soft quotas must total 20000')
    for name in ('classification', 'extraction', 'ranking'):
        if not cfg[name].get('version'):
            raise InvalidConfig(f'Missing {name} version')
    return cfg
