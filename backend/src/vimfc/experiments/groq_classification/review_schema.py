"""Explicit human decisions; no defaults that could manufacture gold."""
from copy import deepcopy
from datetime import datetime

from vimfc.domain import GROUPS

MEMBERSHIPS = ('IN_TAXONOMY', 'OUT_OF_TAXONOMY', 'INSUFFICIENT_EVIDENCE', 'AMBIGUOUS')
SEMANTIC_FIELDS = ('membership', 'primary_group', 'evidence_sentence_ids', 'confidence',
                   'notes', 'reviewer', 'review_timestamp')
AUDIT_FIELDS = ('verification', 'verified_group', 'evidence_refs', 'error_tags',
                'notes', 'reviewer', 'review_timestamp')
VERIFICATIONS = ('VERIFIED', 'CONFLICTING_EVIDENCE', 'NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE')
ERROR_TAGS = ('subject_confusion', 'organization_facility', 'event_award', 'location_organization',
              'creative_science', 'precedence', 'outside_taxonomy', 'science_catch_all', 'other')


def attribution(payload):
    if not isinstance(payload.get('reviewer'), str) or not payload['reviewer'].strip():
        raise ValueError('Actual reviewer is required')
    stamp = payload.get('review_timestamp')
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Review timestamp must be ISO 8601 with timezone') from None
    if not isinstance(payload.get('notes'), str) or len(payload['notes']) > 20000:
        raise ValueError('Notes must be text of at most 20000 characters')


def validate_semantic(payload, reference):
    if not isinstance(payload, dict) or set(payload) != set(SEMANTIC_FIELDS):
        raise ValueError('Unexpected or missing human label fields')
    attribution(payload)
    if payload['membership'] not in MEMBERSHIPS:
        raise ValueError('Choose one of four taxonomy memberships')
    if payload['confidence'] not in ('high', 'medium', 'low'):
        raise ValueError('Choose high, medium or low certainty')
    ids = payload['evidence_sentence_ids']
    known = {s['id'] for s in reference['sentences']}
    if not isinstance(ids, list) or any(type(i) is not int or i not in known for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Evidence IDs must be distinct integer IDs from historical sentences')
    if payload['membership'] == 'IN_TAXONOMY':
        if payload['primary_group'] not in GROUPS or not ids:
            raise ValueError('IN requires an exact primary group and supporting sentences')
    elif payload['primary_group'] is not None:
        raise ValueError('Only IN can have a primary group')
    if payload['membership'] in ('INSUFFICIENT_EVIDENCE', 'AMBIGUOUS') and not payload['notes'].strip():
        raise ValueError('Explain insufficient evidence or ambiguity in notes')
    return deepcopy(payload)


def validate_audit(payload, evidence):
    if not isinstance(payload, dict) or set(payload) != set(AUDIT_FIELDS):
        raise ValueError('Unexpected or missing audit fields')
    attribution(payload)
    if payload['verification'] not in VERIFICATIONS:
        raise ValueError('Unknown historical verification decision')
    refs, tags = payload['evidence_refs'], payload['error_tags']
    if not isinstance(refs, list) or any(not isinstance(r, str) or r not in evidence['evidence_refs'] for r in refs) or len(set(refs)) != len(refs):
        raise ValueError('Cite only supplied historical evidence references')
    if not isinstance(tags, list) or any(t not in ERROR_TAGS for t in tags) or len(set(tags)) != len(tags):
        raise ValueError('Unknown or duplicate error tag')
    if payload['verification'] == 'VERIFIED':
        if (evidence['availability'] != 'COMPLETE' or not refs or payload['verified_group'] not in GROUPS
                or payload['verified_group'] != evidence.get('verified_primary')):
            raise ValueError('VERIFIED requires exact archived policy group and cited historical evidence')
    elif payload['verified_group'] is not None:
        raise ValueError('Unverified audit cannot assign a verified group')
    if not payload['notes'].strip():
        raise ValueError('Explain the historical audit decision')
    return deepcopy(payload)
