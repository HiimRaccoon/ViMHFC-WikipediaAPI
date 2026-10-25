import hashlib
import json
from pathlib import Path
import sqlite3
from vimfc.domain import CORPUS_SIZE, InvalidCorpus, digest


def verify_package(package):
    from vimfc.pipeline.freeze import validate_article
    package=Path(package)
    try:
        manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
        path=package/'corpus.sqlite'
        if any(package.glob('corpus.sqlite-*')):
            raise InvalidCorpus('Frozen package must be standalone without journal/WAL sidecars')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['database_sha256']:
            raise InvalidCorpus('Corpus database hash mismatch')
        with CorpusStore(package) as corpus:
            meta=corpus.meta
            if not meta['frozen'] or meta['article_count']!=CORPUS_SIZE or meta['schema_version']!=1:
                raise InvalidCorpus('Frozen schema/count invalid')
            if any(manifest.get(k)!=v for k,v in meta.items()):
                raise InvalidCorpus('Corpus manifest metadata mismatch')
            if corpus.db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or corpus.db.execute('PRAGMA foreign_key_check').fetchone():
                raise InvalidCorpus('SQLite integrity error')
            records=[json.loads(r[0]) for r in corpus.db.execute('SELECT record_json FROM articles ORDER BY page_id')]
            if len(records)!=CORPUS_SIZE or digest(records)!=meta['content_digest']:
                raise InvalidCorpus('Corpus count/content digest mismatch')
            ordinals=[r[0] for r in corpus.db.execute('SELECT ordinal FROM articles ORDER BY ordinal')]
            if ordinals!=list(range(CORPUS_SIZE)):
                raise InvalidCorpus('Invalid dense article ordinals')
            expected_subtypes={}
            expected_targets={}
            expected_links=[]
            expected_edges={}
            by_id={a['page_id']:a for a in records}
            for ordinal,a in enumerate(records):
                validate_article(a,meta['snapshot'],meta['extraction_version'],rules=meta['validation_rules'])
                row=corpus.db.execute('SELECT ordinal,title,revision_id,primary_entity_type FROM articles WHERE page_id=?',(a['page_id'],)).fetchone()
                if tuple(row)!=(ordinal,a['title'],a['revision_id'],a['classification']['primary_entity_type']):
                    raise InvalidCorpus('Normalized article metadata mismatch')
                paragraphs=[(r[0],r[1]) for r in corpus.db.execute('SELECT paragraph_id,text FROM paragraphs WHERE page_id=? ORDER BY paragraph_id',(a['page_id'],))]
                if paragraphs!=list(enumerate(a['extraction']['paragraphs'])):
                    raise InvalidCorpus('Normalized paragraph table mismatch')
                stored=[(r['sent_id'],r['revision_id'],r['paragraph_id'],r['text']) for r in corpus.db.execute('SELECT * FROM sentences WHERE page_id=? ORDER BY sent_id',(a['page_id'],))]
                expected=[(s['sent_id'],a['revision_id'],s['paragraph_id'],s['text']) for s in a['extraction']['sentences']]
                if stored!=expected:
                    raise InvalidCorpus('Normalized sentence table mismatch')
                types=[r[0] for r in corpus.db.execute('SELECT subtype_id FROM article_subtypes WHERE page_id=? ORDER BY subtype_id',(a['page_id'],))]
                if types!=sorted(s['subtype_id'] for s in a['classification']['subtypes']):
                    raise InvalidCorpus('Normalized subtype membership mismatch')
                for subtype in a['classification']['subtypes']:
                    if subtype['subtype_id'] in expected_subtypes and expected_subtypes[subtype['subtype_id']]!=subtype['name']:
                        raise InvalidCorpus('Conflicting subtype definitions')
                    expected_subtypes[subtype['subtype_id']]=subtype['name']
                cls=corpus.db.execute('SELECT record_json FROM classification_provenance WHERE page_id=?',(a['page_id'],)).fetchone()
                if cls is None or json.loads(cls[0])!=a['classification']:
                    raise InvalidCorpus('Normalized classification provenance mismatch')
                for link in a['extraction']['links']:
                    target=link.get('target',{})
                    key=None
                    if target.get('status')=='resolved':
                        key='viwiki:'+str(target['page_id'])
                        expected_targets.setdefault(key,(target['page_id'],target['title'],target))
                        if target['page_id'] in by_id:
                            other=by_id[target['page_id']]
                            if any(target[k]!=other[k] for k in ('title','revision_id','revision_timestamp','raw_hash')):
                                raise InvalidCorpus('Lead target revision disagrees with selected endpoint')
                    link_id=len(expected_links)+1
                    expected_links.append((link_id,a['page_id'],key,link['sent_id'],link))
                    if key and target['page_id'] in by_id:
                        expected_edges.setdefault((a['page_id'],target['page_id']),link_id)
            stored_types={r[0]:r[1] for r in corpus.db.execute('SELECT subtype_id,name FROM subtypes')}
            if stored_types!=expected_subtypes:
                raise InvalidCorpus('Normalized subtype definitions mismatch')
            stored_targets={r[0]:(r[1],r[2],json.loads(r[3])) for r in corpus.db.execute('SELECT target_key,page_id,title,record_json FROM entity_targets')}
            if stored_targets!=expected_targets:
                raise InvalidCorpus('Normalized entity targets/provenance mismatch')
            stored_links=[(r[0],r[1],r[2],r[3],json.loads(r[4])) for r in corpus.db.execute('SELECT link_id,source_page_id,target_key,sent_id,record_json FROM lead_entity_links ORDER BY link_id')]
            if stored_links!=expected_links:
                raise InvalidCorpus('Normalized lead link occurrences mismatch')
            edges={(r[0],r[1]):r[2] for r in corpus.db.execute('SELECT source_page_id,target_page_id,link_id FROM internal_edges')}
            if edges!=expected_edges:
                raise InvalidCorpus('Internal graph lacks exact retained occurrence provenance')
        return manifest
    except InvalidCorpus:
        raise
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
        raise InvalidCorpus(f'Invalid corpus package: {error}') from error


class CorpusStore:
    def __init__(self,package):
        self.package=Path(package)
        uri=(self.package/'corpus.sqlite').resolve().as_uri()+'?mode=ro'
        self.db=sqlite3.connect(uri,uri=True)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.meta=json.loads(self.db.execute('SELECT record_json FROM corpus_meta WHERE singleton=1').fetchone()[0])

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.db.close()

    def article(self,page_id):
        row=self.db.execute('SELECT record_json FROM articles WHERE page_id=?',(page_id,)).fetchone()
        if row is None:
            raise KeyError('Article not in frozen corpus')
        return json.loads(row[0])
