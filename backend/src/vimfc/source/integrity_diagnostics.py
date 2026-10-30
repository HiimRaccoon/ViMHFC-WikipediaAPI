"""Pure offline observations; this module never grants historical source trust."""
from dataclasses import asdict
import hashlib
import json
import re

from vimfc.domain import utc,at_or_before

FORMAT='vimfc-integrity-diagnostic-1'


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Ambiguous duplicate JSON key: '+key)
        result[key]=value
    return result


def inspect_revision_response(response,expected,*,expected_response_sha256):
    """Bind exact HTTP body/ref and report hash comparisons, never accept raw.

    expected_response_sha256 must come from a separately reviewed receipt.
    Matching hashes here are diagnostic observations, not a substitute for
    RevisionSource validation, acquisition provenance, or review authorization.
    """
    if (not isinstance(response,bytes) or not isinstance(expected_response_sha256,str)
        or not re.fullmatch('[0-9a-f]{64}',expected_response_sha256)
        or hashlib.sha256(response).hexdigest()!=expected_response_sha256):
        raise ValueError('Captured response artifact checksum mismatch')
    try:
        if (expected.site!='wikidata' or not re.fullmatch(r'Q[1-9][0-9]*',expected.identity)
            or expected.title!=expected.identity or type(expected.page_id) is not int or expected.page_id<=0
            or type(expected.revision_id) is not int or expected.revision_id<=0
            or not at_or_before(expected.revision_timestamp,expected.snapshot)):
            raise ValueError('Expected historical reference is invalid')
        payload=json.loads(response.decode('utf-8'),object_pairs_hook=_unique_object)
        if 'error' in payload:raise ValueError('Captured API error is not revision evidence')
        page,=payload['query']['pages'];revision,=page['revisions']
        if (page['pageid']!=expected.page_id or page['title']!=expected.identity or page.get('ns')!=0
            or revision['revid']!=expected.revision_id or utc(revision['timestamp'])!=utc(expected.revision_timestamp)
            or page.get('missing') or set(revision['slots'])!={'main'}):
            raise ValueError('Captured revision does not match exact historical reference')
        slot=revision['slots']['main'];content=slot['content']
        if slot['contentmodel']!='wikibase-item' or slot['contentformat']!='application/json' or not isinstance(content,str):
            raise ValueError('Unexpected content model/format or hidden content')
        entity=json.loads(content,object_pairs_hook=_unique_object)
        if entity['type']!='item' or entity['id']!=expected.identity:raise ValueError('Content entity identity differs')
        revision_sha1=revision['sha1'];slot_sha1=slot['sha1']
        if any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{1,40}',v) for v in (revision_sha1,slot_sha1)):
            raise ValueError('Missing or malformed source hash metadata')
        encoded=content.encode('utf-8');computed=hashlib.sha1(encoded).hexdigest()
        return {'format':FORMAT,'source_ref':asdict(expected),'response_sha256':expected_response_sha256,
            'status':'metadata_hashes_match' if computed==revision_sha1==slot_sha1 else 'source_representation_mismatch',
            'content_sha1':computed,'content_sha256':hashlib.sha256(encoded).hexdigest(),'utf8_length':len(encoded),
            'reported_revision_sha1':revision_sha1,'reported_slot_sha1':slot_sha1,
            'revision_sha1_matches_content':computed==revision_sha1,'slot_sha1_matches_content':computed==slot_sha1,
            'reported_revision_size':revision.get('size'),'reported_slot_size':slot.get('size'),
            'size_interpretation':'Metadata size is not proof of original serialized blob byte length; Wikibase historical getSize used PHP-serialized native data.',
            'content_model':slot['contentmodel'],'content_format':slot['contentformat'],
            'trusted_historical_evidence':False,'source_validation_semantics_changed':False,'decision_authority':'diagnostic only'}
    except (KeyError,TypeError,AttributeError,UnicodeError) as error:
        raise ValueError('Malformed captured response/reference: '+str(error)) from error
