"""Validate first; publish a new standalone package by same-volume rename."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import re
from dataclasses import asdict
from vimfc.domain import CORPUS_SIZE, GROUPS, InvalidCorpus, canonical, digest, at_or_before
from .report import corpus_report
from .classify import classify
from .quality import assess_quality


def validate_article(article,snapshot,extraction_version,*,rules=None):
    def require(value,message):
        if not value:
            raise InvalidCorpus(f'Article {article.get("page_id")}: {message}')
    require(type(article['page_id']) is int and article['page_id']>0,'invalid page identity')
    require(article.get('revision_id',0)>0 and article.get('title') and article.get('source_url'),'missing source metadata')
    require(article.get('snapshot')==snapshot and at_or_before(article['revision_timestamp'],snapshot),'revision beyond snapshot')
    require(article.get('content_model')=='wikitext' and len(article.get('raw_hash',''))==64,'missing raw provenance')
    ext,cls=article['extraction'],article['classification']
    require(ext['outcome']=='completed' and ext['extraction_version']==extraction_version and not ext.get('diagnostics'),'unsupported extraction version/result')
    require(article['quality']['eligible'] and not article['quality']['reasons'],'ineligible article')
    require(cls['outcome']=='completed' and cls['primary_entity_type'] in GROUPS and cls.get('provenance') and cls.get('items'),'unsupported classification')
    for item in cls['items'].values():
        require(item.get('revision_id') and at_or_before(item['revision_timestamp'],snapshot),'invalid classification dependency')
    require(all(item['entity']['id']==qid for qid,item in cls['items'].items()),'classification entity identity mismatch')
    for proof in cls['provenance']:
        require(proof.get('path'),'missing classification path')
        for ref in proof['path']:
            require(ref['qid'] in cls['items'] and cls['items'][ref['qid']]['revision_id']==ref['revision_id'] and at_or_before(ref['revision_timestamp'],snapshot),'unbacked classification path')
        require(proof['root']==proof['path'][-1]['qid'],'classification root does not end its proven path')
    require(rules is not None,'full archived validation rules are required')
    if rules['classification'].get('dependency_policy'):
        from .dependencies import verify_manifest,classify_manifest
        from vimfc.domain import Incompatible,SourceUnavailable
        manifest=cls.get('dependency_manifest',{})
        identity={'site':'viwiki','page_id':article['page_id'],'title':article['title'],'revision_id':article['revision_id'],
            'revision_timestamp':article['revision_timestamp'],'content_hash':article['raw_hash']}
        try:
            verify_manifest(manifest,rules['classification'],identity)
            require(manifest['snapshot']==snapshot and cls.get('manifest_id')==manifest['manifest_id'],'manifest snapshot/identity mismatch')
            expected_classification=asdict(classify_manifest(manifest,cls['items'],rules['classification']))
        except (Incompatible,SourceUnavailable,KeyError,ValueError) as error:
            raise InvalidCorpus(f'Article {article["page_id"]}: dependency manifest does not replay: {error}') from error
    else:
        expected_classification=asdict(classify(article['title'],cls['provenance'][0]['path'][0]['qid'],cls['items'],rules['classification'],snapshot))
    require(all(cls.get(k,[] if k=='diagnostics' else None)==v for k,v in expected_classification.items()),'classification does not replay from archived metadata')
    expected_quality=assess_quality(ext,cls,article.get('article_kinds',[]),rules['extraction'])
    require(expected_quality['eligible'] and article['quality']==expected_quality,'quality rules/signals do not replay')
    require(ext['full_lead']=='\n\n'.join(ext['paragraphs']) and ext['full_lead'].strip(),'lead/paragraph mismatch')
    sentences=ext['sentences']
    require([s['sent_id'] for s in sentences]==list(range(len(sentences))) and len(sentences)>=2,'sentence IDs are not contiguous')
    require(ext['stats']['sentence_count']==len(sentences) and ext['stats']['paragraph_count']==len(ext['paragraphs']),'summary stats mismatch')
    for paragraph_id,paragraph in enumerate(ext['paragraphs']):
        cursor=0
        for sentence in (s for s in sentences if s['paragraph_id']==paragraph_id):
            require(paragraph[sentence['start']:sentence['end']]==sentence['text'] and sentence['text'].strip(),'sentence is not exact retained prose')
            require(not paragraph[cursor:sentence['start']].strip() and sentence['start']>=cursor,'omitted/overlapping factual prose')
            cursor=sentence['end']
        require(not paragraph[cursor:].strip(),'unrepresented lead prose')
    require(all(0<=s['paragraph_id']<len(ext['paragraphs']) for s in sentences),'invalid sentence paragraph')
    for link in ext['links']:
        require(link['scope']=='retained_lead_prose','body/nonprose link cannot enter corpus graph')
        require(0<=link['sent_id']<len(sentences),'invalid lead link sentence')
        sent=sentences[link['sent_id']]
        require(link['paragraph_id']==sent['paragraph_id'] and sent['start']<=link['start']<link['end']<=sent['end'],'link outside retained prose')
        require(ext['paragraphs'][sent['paragraph_id']][link['start']:link['end']]==link['link_text'],'link text/span mismatch')
        target=link.get('target',{})
        if target.get('status')=='resolved':
            require(target.get('page_id',0)>0 and target.get('revision_id',0)>0 and re.fullmatch('[0-9a-f]{64}',target.get('raw_hash','')) and target.get('proof') and target.get('snapshot')==snapshot and at_or_before(target['revision_timestamp'],snapshot),'target historical identity unproven')


def freeze(run,articles,pairs,output):
    if len(articles)!=CORPUS_SIZE or len({a['page_id'] for a in articles})!=CORPUS_SIZE:
        raise InvalidCorpus('Freeze requires exactly 20000 unique eligible articles')
    articles=sorted(articles,key=lambda a:a['page_id'])
    extraction_version=run['config']['extraction']['version']
    by_id={a['page_id']:a for a in articles}
    for article in articles:
        validate_article(article,run['snapshot'],extraction_version,rules=run['config'])
        for link in article['extraction']['links']:
            target=link.get('target',{})
            if target.get('status')=='resolved' and target['page_id'] in by_id:
                other=by_id[target['page_id']]
                if any(target[k]!=other[k] for k in ('title','revision_id','revision_timestamp','raw_hash')):
                    raise InvalidCorpus('Lead target disagrees with selected historical article')
    content_digest=digest(articles)
    version=digest({'snapshot':run['snapshot'],'config':run['config_hash'],'content':content_digest,'pairs':pairs})
    root=Path(output)
    root.mkdir(parents=True,exist_ok=True)
    target=root/version
    if target.exists():
        raise InvalidCorpus('Corpus version already exists; never overwrite frozen packages')
    # TemporaryDirectory owns cleanup of only its own unpublished directory.
    with tempfile.TemporaryDirectory(prefix='.freeze-',dir=root) as scratch:
        folder=Path(scratch)/version
        folder.mkdir()
        db=sqlite3.connect(folder/'corpus.sqlite')
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA journal_mode=DELETE')
            db.executescript('''
            CREATE TABLE corpus_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1),record_json TEXT NOT NULL);
            CREATE TABLE articles(page_id INTEGER PRIMARY KEY,ordinal INTEGER NOT NULL UNIQUE,title TEXT NOT NULL,revision_id INTEGER NOT NULL,primary_entity_type TEXT NOT NULL,record_json TEXT NOT NULL);
            CREATE TABLE paragraphs(page_id INTEGER NOT NULL REFERENCES articles,paragraph_id INTEGER NOT NULL,text TEXT NOT NULL,PRIMARY KEY(page_id,paragraph_id));
            CREATE TABLE sentences(page_id INTEGER NOT NULL REFERENCES articles,sent_id INTEGER NOT NULL,revision_id INTEGER NOT NULL,paragraph_id INTEGER NOT NULL,text TEXT NOT NULL,PRIMARY KEY(page_id,sent_id),FOREIGN KEY(page_id,paragraph_id) REFERENCES paragraphs);
            CREATE TABLE subtypes(subtype_id TEXT PRIMARY KEY,name TEXT NOT NULL);
            CREATE TABLE article_subtypes(page_id INTEGER NOT NULL REFERENCES articles,subtype_id TEXT NOT NULL REFERENCES subtypes,PRIMARY KEY(page_id,subtype_id));
            CREATE TABLE entity_targets(target_key TEXT PRIMARY KEY,page_id INTEGER NOT NULL,title TEXT NOT NULL,record_json TEXT NOT NULL);
            CREATE TABLE lead_entity_links(link_id INTEGER PRIMARY KEY,source_page_id INTEGER NOT NULL REFERENCES articles,target_key TEXT REFERENCES entity_targets,sent_id INTEGER NOT NULL,record_json TEXT NOT NULL,FOREIGN KEY(source_page_id,sent_id) REFERENCES sentences(page_id,sent_id));
            CREATE TABLE internal_edges(source_page_id INTEGER NOT NULL REFERENCES articles,target_page_id INTEGER NOT NULL REFERENCES articles,link_id INTEGER NOT NULL REFERENCES lead_entity_links,PRIMARY KEY(source_page_id,target_page_id));
            CREATE TABLE classification_provenance(page_id INTEGER PRIMARY KEY REFERENCES articles,record_json TEXT NOT NULL);
            CREATE TABLE quality_report(record_json TEXT NOT NULL);
            CREATE INDEX article_group ON articles(primary_entity_type);
            CREATE INDEX subtype_pages ON article_subtypes(subtype_id,page_id);
            CREATE INDEX link_target ON lead_entity_links(target_key,source_page_id);
            CREATE INDEX link_source ON lead_entity_links(source_page_id);
            CREATE INDEX edge_target ON internal_edges(target_page_id);
            PRAGMA user_version=1;
            ''')
            ids={a['page_id'] for a in articles}
            for ordinal,a in enumerate(articles):
                db.execute('INSERT INTO articles VALUES (?,?,?,?,?,?)',(a['page_id'],ordinal,a['title'],a['revision_id'],a['classification']['primary_entity_type'],canonical(a)))
                db.executemany('INSERT INTO paragraphs VALUES (?,?,?)',[(a['page_id'],i,p) for i,p in enumerate(a['extraction']['paragraphs'])])
                db.executemany('INSERT INTO sentences VALUES (?,?,?,?,?)',[(a['page_id'],s['sent_id'],a['revision_id'],s['paragraph_id'],s['text']) for s in a['extraction']['sentences']])
                db.execute('INSERT INTO classification_provenance VALUES (?,?)',(a['page_id'],canonical(a['classification'])))
                for subtype in a['classification']['subtypes']:
                    db.execute('INSERT OR IGNORE INTO subtypes VALUES (?,?)',(subtype['subtype_id'],subtype['name']))
                    db.execute('INSERT INTO article_subtypes VALUES (?,?)',(a['page_id'],subtype['subtype_id']))
            for a in articles:
                for link in a['extraction']['links']:
                    resolution=link.get('target',{})
                    key=None
                    if resolution.get('status')=='resolved':
                        key='viwiki:'+str(resolution['page_id'])
                        db.execute('INSERT OR IGNORE INTO entity_targets VALUES (?,?,?,?)',(key,resolution['page_id'],resolution['title'],canonical(resolution)))
                    link_id=db.execute('INSERT INTO lead_entity_links(source_page_id,target_key,sent_id,record_json) VALUES (?,?,?,?)',(a['page_id'],key,link['sent_id'],canonical(link))).lastrowid
                    if key and resolution['page_id'] in ids:
                        db.execute('INSERT OR IGNORE INTO internal_edges VALUES (?,?,?)',(a['page_id'],resolution['page_id'],link_id))
            report=corpus_report(articles,pairs,run['config'].get('quotas',{}))
            meta={'corpus_version':version,'schema_version':1,'snapshot':run['snapshot'],'run_id':run['pipeline_run_id'],'config_hash':run['config_hash'],'extraction_version':extraction_version,'frozen':True,'article_count':CORPUS_SIZE,'content_digest':content_digest,'selection_digest':digest([a['page_id'] for a in articles]),'validation_rules':{key:run['config'][key] for key in ('extraction','classification')},'rule_versions':{k:v.get('version') for k,v in run['config'].items() if isinstance(v,dict) and 'version' in v}}
            db.execute('INSERT INTO corpus_meta VALUES (1,?)',(canonical(meta),))
            db.execute('INSERT INTO quality_report VALUES (?)',(canonical(report),))
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                raise InvalidCorpus('Corpus database integrity failed')
        finally:
            db.close()
        manifest={**meta,'database_sha256':hashlib.sha256((folder/'corpus.sqlite').read_bytes()).hexdigest(),'group_counts':report['group_counts']}
        (folder/'manifest.json').write_text(canonical(manifest)+'\n',encoding='utf-8')
        folder.rename(target)
    return target
