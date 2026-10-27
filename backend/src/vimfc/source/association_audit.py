"""Read-only historical association diagnostics; never pin QIDs or infer absence."""
import hashlib
import json
import re
from vimfc.domain import at_or_before
from .identities import normalize_title


def audit_associations(db,cache,snapshot,articles):
    by_title={};invalid=[];missing=[];examined=0
    rows=db.execute("SELECT identity,record_json FROM snapshot_revision_refs WHERE site='wikidata' AND snapshot=? ORDER BY identity",(snapshot,))
    for qid,encoded in rows:
        if not re.fullmatch(r'Q[1-9][0-9]*',qid):continue
        ref=json.loads(encoded)
        raw_row=db.execute("SELECT record_json FROM source_revisions WHERE site='wikidata' AND revision_id=?",(ref['revision_id'],)).fetchone()
        if not raw_row:
            missing.append({'qid':qid,'revision_id':ref['revision_id']});continue
        try:
            raw=cache.get(json.loads(raw_row[0])['cache_ref']);content=raw['content'].encode('utf-8')
            entity=json.loads(raw['content'])
            if (ref['site']!='wikidata' or ref['identity']!=qid or ref['title']!=qid or ref['snapshot']!=snapshot
                or not at_or_before(ref['revision_timestamp'],snapshot) or raw['timestamp']!=ref['revision_timestamp']
                or raw['page_id']!=ref['page_id'] or raw['content_model']!='wikibase-item' or entity.get('id')!=qid
                or hashlib.sha1(content).hexdigest()!=raw['source_sha1'] or hashlib.sha256(content).hexdigest()!=raw['local_sha256']):
                raise ValueError('Historical identity/model/hash/cutoff mismatch')
            examined+=1
            sitelink=(entity.get('sitelinks') or {}).get('viwiki',{}).get('title')
            if sitelink:
                by_title.setdefault(normalize_title(sitelink),[]).append({'qid':qid,'page_id':ref['page_id'],
                    'revision_id':ref['revision_id'],'revision_timestamp':ref['revision_timestamp'],'raw_hash':raw['local_sha256'],
                    'snapshot':snapshot,'historical_sitelink_title':sitelink})
        except (ValueError,KeyError,TypeError,OSError,AttributeError) as error:
            invalid.append({'qid':qid,'revision_id':ref['revision_id'],'error':str(error)})
    results=[]
    for article in sorted(articles,key=lambda a:a['page_id']):
        matches=by_title.get(normalize_title(article['title']),[])
        results.append({'article':article,'status':'historical_match_needs_review' if len(matches)==1 else 'ambiguous_historical_matches' if matches else 'not_found_in_archived_inventory',
            'matches':matches,'safe_to_pin_without_review':False,'absence_proven':False,
            'next_action':'Review exact historical article identity and association proof; do not repeat pageprops indefinitely.'})
    return {'format':'vimfc-historical-association-audit-1','snapshot':snapshot,'coverage':'selected_archived_inventory_only',
        'physical_api_requests':0,'examined_qids':examined,'missing_raw_entries':missing,'invalid_entries':invalid,'articles':results}
