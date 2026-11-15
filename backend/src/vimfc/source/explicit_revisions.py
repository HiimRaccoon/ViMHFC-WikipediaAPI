"""Opt-in, disjoint title/page-ID selectors; legacy source semantics stay frozen."""
from dataclasses import asdict, replace
from copy import deepcopy
import json
import re

from vimfc.domain import SourceRevisionRef, SourceUnavailable, at_or_before, canonical, digest, utc
from .identities import normalize_title

FORMAT = 'explicit-selector-1'


def selector_value(kind, value):
    if kind == 'title':
        if not isinstance(value, str) or not normalize_title(value):
            raise ValueError('Explicit title must be a nonempty string')
        return normalize_title(value)
    if kind == 'page_id':
        if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
            raise ValueError('Explicit page ID must be a positive integer')
        return str(value)
    raise ValueError('Unknown explicit selector kind')


def selector_key(kind, value):
    """JSON punctuation cannot collide with a valid legacy MediaWiki title/ID."""
    return canonical([FORMAT, kind, selector_value(kind, value)])


def selector_ref_record(record):
    """Decode a typed cache envelope; never interpret it as a legacy ref."""
    value = json.loads(record) if isinstance(record, str) else record
    if (value.get('format') != FORMAT or value.get('ref_hash') != digest(value['ref'])
            or value.get('selector') != {'kind': value['selector']['kind'],
                                         'value': selector_value(value['selector']['kind'], value['selector']['value'])}):
        raise SourceUnavailable('Explicit selector receipt checksum/scope differs', code='invalid_historical_cache')
    return SourceRevisionRef(**value['ref'])


def _check(source, ref, site, kind, identity, snapshot):
    if (ref.site != site or ref.identity != identity or ref.snapshot != snapshot
            or type(ref.page_id) is not int or ref.page_id <= 0
            or type(ref.revision_id) is not int or ref.revision_id <= 0
            or not isinstance(ref.title, str) or not ref.title
            or not at_or_before(ref.revision_timestamp, snapshot)
            or not ref.lookup_timestamp or not at_or_before(snapshot, ref.lookup_timestamp)
            or kind == 'page_id' and ref.page_id != int(identity)):
        raise SourceUnavailable('Explicit selected revision identity/snapshot differs', code='invalid_historical_cache')
    if source.integrity_registry:
        source.integrity_registry.check(ref, source.reviewed_quarantine)
    if source.reviewed_quarantine:
        source.reviewed_quarantine.check(ref)


def _envelope(kind, identity, ref, provenance):
    return {'format': FORMAT, 'selector': {'kind': kind, 'value': identity},
            'ref': asdict(ref), 'ref_hash': digest(ref), 'provenance': provenance}


def _receipt(source, context, saved):
    verifier = getattr(source, 'selector_receipt_verifier', None)
    required = getattr(source, 'require_selector_receipts', False) is True
    if verifier is None:
        if required:
            raise SourceUnavailable('Selected revision lacks a verified response receipt', code='historical_selector_receipt_unverified')
        return saved
    try:
        receipt = deepcopy(verifier(deepcopy(context), deepcopy(saved)))
        expected = {'version': 'historical-selector-receipt-1', 'site': context['site'],
                    'kind': context['kind'], 'identity': context['identity'], 'snapshot': context['snapshot'],
                    'ref_hash': digest(context['ref']), 'query_hash': digest(context['query'])}
        for candidate in ([saved, receipt] if saved is not None else [receipt]):
            if (not isinstance(candidate, dict) or any(candidate.get(k) != v for k, v in expected.items())
                    or not isinstance(candidate.get('response_sha256'), str)
                    or not re.fullmatch('[0-9a-f]{64}', candidate['response_sha256'])
                    or candidate.get('manifest_id') != digest({k: v for k, v in candidate.items() if k != 'manifest_id'})):
                raise ValueError('Selected revision response receipt scope/hash differs')
        if saved is not None and saved['response_sha256'] != receipt['response_sha256']:
            raise ValueError('Selected revision response artifact changed')
        return receipt
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise SourceUnavailable('Selected revision response receipt is unverified', code='historical_selector_receipt_unverified') from error


def _check_provenance(source, site, kind, identity, snapshot, ref, provenance, visited=None):
    provenance = deepcopy(provenance)
    if provenance.get('kind') in ('validated_legacy_nonnumeric_title', 'validated_legacy_exact_page'):
        original = source.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                           (site, provenance.get('legacy_identity'), snapshot)).fetchone()
        if not original or digest(json.loads(original[0])) != provenance.get('legacy_record_hash') or json.loads(original[0]) != asdict(ref):
            raise SourceUnavailable('Migrated legacy selector provenance differs', code='invalid_historical_cache')
        return provenance
    if provenance.get('kind') == 'canonical_page_alias':
        key = provenance['source_selector_key']
        visited = set() if visited is None else set(visited)
        if key in visited:
            raise SourceUnavailable('Canonical selector alias cycle', code='invalid_historical_cache')
        visited.add(key)
        if provenance.get('source_ref') is not None:
            original = SourceRevisionRef(**provenance['source_ref'])
            original_provenance = provenance['source_provenance']
        else:
            row = source.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                          (site, key, snapshot)).fetchone()
            if not row:
                raise SourceUnavailable('Canonical selector source receipt missing', code='invalid_historical_cache')
            value = json.loads(row[0]); original = selector_ref_record(value)
            original_provenance = value['provenance']
        selector = json.loads(key)
        if (not isinstance(selector, list) or len(selector) != 3 or selector[0] != FORMAT
                or digest(original) != provenance['source_ref_hash']
                or ref != replace(original, identity=str(original.page_id))):
            raise SourceUnavailable('Canonical selector alias provenance differs', code='invalid_historical_cache')
        _check(source, original, site, selector[1], selector[2], snapshot)
        provenance['source_ref'] = asdict(original)
        provenance['source_provenance'] = _check_provenance(source, site, selector[1], selector[2], snapshot,
                                                           original, original_provenance, visited)
        return provenance
    if provenance.get('kind') != 'explicit_historical_selector':
        raise SourceUnavailable('Unknown explicit selector provenance', code='invalid_historical_cache')
    context = {'site': site, 'kind': kind, 'identity': identity, 'snapshot': snapshot,
               'ref': asdict(ref), 'query': provenance['query']}
    receipt = _receipt(source, context, provenance.get('response_receipt'))
    if receipt is not None:
        provenance['response_receipt'] = receipt
    return provenance


def select_explicit(source, site, kind, value, snapshot):
    identity, snapshot = selector_value(kind, value), utc(snapshot)
    key = selector_key(kind, identity)
    source.cache_lookups += 1
    row = source.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                  (site, key, snapshot)).fetchone()
    if row:
        envelope = json.loads(row[0])
        if envelope.get('selector') != {'kind': kind, 'value': identity}:
            raise SourceUnavailable('Explicit cache selector differs', code='invalid_historical_cache')
        ref = selector_ref_record(envelope)
        _check(source, ref, site, kind, identity, snapshot)
        provenance = _check_provenance(source, site, kind, identity, snapshot, ref, envelope['provenance'])
        if provenance != envelope['provenance']:
            envelope['provenance'] = provenance
            source.store.db.execute('UPDATE snapshot_revision_refs SET record_json=? WHERE site=? AND identity=? AND snapshot=?',
                                     (canonical(envelope), site, key, snapshot))
        source.cache_hits += 1
        return ref
    # Legacy nonnumeric keys were always title selectors. Numeric keys can be
    # either aliases or page-ID lookups and are deliberately never migrated.
    legacy = None
    if kind == 'page_id' or kind == 'title' and not identity.isdecimal():
        legacy = source.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                         (site, identity, snapshot)).fetchone()
        if legacy and kind == 'page_id' and json.loads(legacy[0]).get('page_id') != int(identity):
            legacy = None
    if legacy:
        ref = SourceRevisionRef(**json.loads(legacy[0]))
        _check(source, ref, site, kind, identity, snapshot)
        provenance = {'kind': 'validated_legacy_exact_page' if kind == 'page_id' else 'validated_legacy_nonnumeric_title', 'legacy_identity': identity,
                      'legacy_record_hash': digest(json.loads(legacy[0]))}
        source.cache_hits += 1
    else:
        if (getattr(source, 'require_selector_receipts', False) is True
                and getattr(source, 'selector_receipt_verifier', None) is None):
            raise SourceUnavailable('Selected revision lacks a response verifier', code='historical_selector_receipt_unverified')
        source._cached_failure(site, key, snapshot, 'select')
        params = {'action': 'query', 'prop': 'revisions',
                  'titles' if kind == 'title' else 'pageids': identity,
                  'rvstart': snapshot, 'rvdir': 'older', 'rvlimit': 1,
                  'rvprop': 'ids|timestamp', 'rvslots': 'main', 'curtimestamp': 1}
        try:
            payload = source.client.query(site, params)
        except SourceUnavailable as error:
            source._remember_failure(site, key, snapshot, 'select', error)
            raise
        pages = payload.get('query', {}).get('pages', [])
        if len(pages) != 1 or pages[0].get('missing') or not pages[0].get('revisions'):
            error = SourceUnavailable('No historical revision for explicit selector', code='history_unavailable',
                                      details={'site': site, 'selector_kind': kind, 'identity': identity, 'snapshot': snapshot})
            source._remember_failure(site, key, snapshot, 'select', error)
            raise error
        page, revision = pages[0], pages[0]['revisions'][0]
        ref = SourceRevisionRef(site, identity, page['pageid'], page['title'], revision['revid'],
                                utc(revision['timestamp']), snapshot, payload.get('curtimestamp'))
        _check(source, ref, site, kind, identity, snapshot)
        provenance = {'kind': 'explicit_historical_selector', 'query': params}
        provenance = _check_provenance(source, site, kind, identity, snapshot, ref, provenance)
    record = _envelope(kind, identity, ref, provenance)
    alias = replace(ref, identity=str(ref.page_id))
    alias_key = selector_key('page_id', ref.page_id)
    existing = source.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                       (site, alias_key, snapshot)).fetchone()
    if existing:
        other = selector_ref_record(existing[0])
        _check(source, other, site, 'page_id', str(ref.page_id), snapshot)
        if any(getattr(other, field) != getattr(ref, field) for field in ('page_id', 'revision_id', 'revision_timestamp')):
            raise SourceUnavailable('Explicit canonical page alias conflicts', code='invalid_historical_cache')
    with source.store.transaction():
        source.store.db.execute('INSERT INTO snapshot_revision_refs VALUES (?,?,?,?)', (site, key, snapshot, canonical(record)))
        source.store.db.execute('INSERT OR IGNORE INTO snapshot_revision_refs VALUES (?,?,?,?)',
                                (site, alias_key, snapshot, canonical(_envelope('page_id', str(ref.page_id), alias,
                                    {'kind': 'canonical_page_alias', 'source_selector_key': key, 'source_ref_hash': digest(ref)}))))
        source.store.db.execute('DELETE FROM source_failures WHERE site=? AND identity=? AND snapshot=? AND operation=?',
                                (site, key, snapshot, 'select'))
    return ref
