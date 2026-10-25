"""Schema ownership is separate for builder, corpus, and annotations."""
import sqlite3
from pathlib import Path
from vimfc.domain import Incompatible


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=5000')
    return db


def annotation_database(path, version):
    db = connect(path)
    try:
        current = db.execute('PRAGMA user_version').fetchone()[0]
        if current not in (0, 1):
            raise Incompatible('Unsupported annotations schema')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS annotation_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1),schema_version INTEGER NOT NULL,corpus_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY,corpus_version TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS samples (id TEXT PRIMARY KEY,submission_id TEXT UNIQUE NOT NULL,session_id TEXT NOT NULL REFERENCES sessions,corpus_version TEXT NOT NULL,claim TEXT NOT NULL CHECK(length(trim(claim))>0),label TEXT NOT NULL CHECK(label IN ('SUPPORTED','REFUTED','NEI')),hop_count INTEGER NOT NULL CHECK(hop_count IN (2,3) AND (label!='NEI' OR hop_count=2)),reasoning_chain TEXT CHECK(reasoning_chain IS NULL),created_at TEXT NOT NULL,document_coverage TEXT NOT NULL CHECK(document_coverage IN ('single_document','cross_document')),source_article_count INTEGER NOT NULL CHECK(source_article_count>0));
        CREATE TABLE IF NOT EXISTS sample_evidence (sample_id TEXT NOT NULL REFERENCES samples,evidence_role TEXT NOT NULL CHECK(evidence_role IN ('gold','partial')),page_id INTEGER NOT NULL,revision_id INTEGER NOT NULL,sent_id INTEGER NOT NULL,ordinal INTEGER NOT NULL,article_title TEXT NOT NULL,source_url TEXT NOT NULL,evidence_text TEXT NOT NULL,PRIMARY KEY(sample_id,evidence_role,page_id,revision_id,sent_id));
        CREATE TABLE IF NOT EXISTS submission_receipts (submission_id TEXT PRIMARY KEY,payload_digest TEXT NOT NULL,sample_id TEXT NOT NULL UNIQUE REFERENCES samples);
        CREATE INDEX IF NOT EXISTS samples_session_order ON samples(session_id,created_at,id);
        PRAGMA user_version=1;
        ''')
        row = db.execute('SELECT corpus_version FROM annotation_meta').fetchone()
        if row and row[0] != version:
            raise Incompatible('Annotation store is bound to another corpus version')
        db.execute('INSERT OR IGNORE INTO annotation_meta VALUES (1,1,?)', (version,))
        return db
    except BaseException:
        db.close()
        raise
