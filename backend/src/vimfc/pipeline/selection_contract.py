"""Strict schema for synthetic selection revalidation decisions."""
import re

from .generation_publication import pointer
from .trusted_control import fields, name, require

POLICIES = ('classification', 'extraction', 'ranking', 'quality', 'quotas', 'edge', 'selection_ranking')


def sha(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None, 'Invalid binding fingerprint')


def policy(value):
    fields(value, ('identity', 'hashes'))
    sha(value['identity'])
    fields(value['hashes'], POLICIES)
    for fingerprint in value['hashes'].values():
        sha(fingerprint)


def binding(value):
    fields(value, ('format', 'generation', 'checkpoint_sha256', 'selection_sha256',
                   'policy_hashes', 'policy_identity', 'activation'))
    require(value['format'] == 'synthetic-selection-epoch-1', 'Unsupported selection binding')
    pointer(value['generation'])
    sha(value['checkpoint_sha256'])
    sha(value['selection_sha256'])
    policy({'identity': value['policy_identity'], 'hashes': value['policy_hashes']})
    fields(value['activation'], ('decision_id', 'record_digest', 'epoch'))
    name(value['activation']['decision_id'])
    sha(value['activation']['record_digest'])
    require(type(value['activation']['epoch']) is int and value['activation']['epoch'] > 0,
            'Invalid activation epoch')

