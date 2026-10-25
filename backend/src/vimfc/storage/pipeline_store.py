from contextlib import contextmanager
import json
import uuid
from vimfc.domain import Incompatible, canonical, digest, utc, now
from .migrations import connect


class PipelineStore:
    def __init__(self, path):
        self.db = connect(path)
        if self.db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
            raise Incompatible('Unsupported pipeline schema')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS pipeline_runs (pipeline_run_id TEXT PRIMARY KEY,snapshot TEXT NOT NULL,config_json TEXT NOT NULL,config_hash TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reuse_run TEXT REFERENCES pipeline_runs,production_report TEXT);
        CREATE TABLE IF NOT EXISTS discovery_streams (run_id TEXT NOT NULL REFERENCES pipeline_runs,stream_id TEXT NOT NULL,state_json TEXT NOT NULL,PRIMARY KEY(run_id,stream_id));
        CREATE TABLE IF NOT EXISTS candidates (run_id TEXT NOT NULL REFERENCES pipeline_runs,canonical_page_id INTEGER NOT NULL,discovery_order INTEGER NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(run_id,canonical_page_id));
        CREATE INDEX IF NOT EXISTS candidate_order ON candidates(run_id,discovery_order);
        CREATE TABLE IF NOT EXISTS candidate_lookups (run_id TEXT NOT NULL,wiki TEXT NOT NULL,normalized_title TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(run_id,wiki,normalized_title));
        CREATE TABLE IF NOT EXISTS source_revisions (site TEXT NOT NULL,revision_id INTEGER NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(site,revision_id));
        CREATE TABLE IF NOT EXISTS snapshot_revision_refs (site TEXT NOT NULL,identity TEXT NOT NULL,snapshot TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(site,identity,snapshot));
        CREATE TABLE IF NOT EXISTS identity_resolutions (snapshot TEXT NOT NULL,lookup_title TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(snapshot,lookup_title));
        CREATE TABLE IF NOT EXISTS stage_results (run_id TEXT NOT NULL REFERENCES pipeline_runs,identity TEXT NOT NULL,stage TEXT NOT NULL,dependency_hash TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT,result_hash TEXT,lease_until TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,last_error TEXT,next_retry_at TEXT,PRIMARY KEY(run_id,identity,stage,dependency_hash));
        CREATE INDEX IF NOT EXISTS stage_queue ON stage_results(run_id,stage,status,next_retry_at);
        CREATE TABLE IF NOT EXISTS classification_results (run_id TEXT NOT NULL,page_id INTEGER NOT NULL,fingerprint TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(run_id,page_id,fingerprint));
        CREATE TABLE IF NOT EXISTS lead_results (run_id TEXT NOT NULL,page_id INTEGER NOT NULL,fingerprint TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(run_id,page_id,fingerprint));
        CREATE TABLE IF NOT EXISTS pair_candidates (run_id TEXT NOT NULL,pair_kind TEXT NOT NULL,endpoint_a INTEGER NOT NULL,endpoint_b INTEGER NOT NULL,ranking_version TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(run_id,pair_kind,endpoint_a,endpoint_b,ranking_version));
        CREATE INDEX IF NOT EXISTS pairs_a ON pair_candidates(run_id,endpoint_a);
        CREATE INDEX IF NOT EXISTS pairs_b ON pair_candidates(run_id,endpoint_b);
        CREATE TABLE IF NOT EXISTS selection_steps (run_id TEXT NOT NULL,selection_version TEXT NOT NULL,step INTEGER NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(run_id,selection_version,step));
        CREATE TABLE IF NOT EXISTS run_events (event_id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,stage TEXT,created_at TEXT NOT NULL,record_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pilot_reports (run_id TEXT NOT NULL,size INTEGER NOT NULL,report_json TEXT NOT NULL,PRIMARY KEY(run_id,size));
        CREATE TABLE IF NOT EXISTS source_failures (site TEXT NOT NULL,identity TEXT NOT NULL,snapshot TEXT NOT NULL,operation TEXT NOT NULL,error_json TEXT NOT NULL,retry_at TEXT,retryable INTEGER NOT NULL,recorded_at TEXT NOT NULL,PRIMARY KEY(site,identity,snapshot,operation));
        CREATE TABLE IF NOT EXISTS api_cooldowns (site TEXT PRIMARY KEY,retry_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dependency_manifests (manifest_id TEXT PRIMARY KEY,record_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS article_manifests (run_id TEXT NOT NULL REFERENCES pipeline_runs,page_id INTEGER NOT NULL,request_hash TEXT NOT NULL,manifest_id TEXT NOT NULL REFERENCES dependency_manifests,PRIMARY KEY(run_id,page_id));
        CREATE TABLE IF NOT EXISTS historical_item_hints (run_id TEXT NOT NULL REFERENCES pipeline_runs,page_id INTEGER NOT NULL,article_hash TEXT NOT NULL,qid TEXT NOT NULL,PRIMARY KEY(run_id,page_id));
        PRAGMA user_version=1;
        ''')
        if 'lease_token' not in {r['name'] for r in self.db.execute('PRAGMA table_info(stage_results)')}:
            self.db.execute('ALTER TABLE stage_results ADD COLUMN lease_token TEXT')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.db.close()

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
            return
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def create(self, snapshot, config, reuse_run=None):
        previous=None
        if reuse_run:
            previous=self.run(reuse_run)
        run_id = str(uuid.uuid4())
        stamp = now()
        with self.transaction():
            self.db.execute('INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?,?,NULL)',
                            (run_id, utc(snapshot), canonical(config), digest(config), 'collecting', stamp, stamp, reuse_run))
            # A new extraction/ranking run replays the archived cohort, rather
            # than depending on today's live discovery results.
            if previous and previous['snapshot']==utc(snapshot) and all(previous['config'].get(k)==config.get(k) for k in ('seed','streams','namespace_prefixes')):
                self.db.execute('INSERT INTO candidates SELECT ?,canonical_page_id,discovery_order,record_json FROM candidates WHERE run_id=?',(run_id,reuse_run))
                self.db.execute('INSERT INTO discovery_streams SELECT ?,stream_id,state_json FROM discovery_streams WHERE run_id=?',(run_id,reuse_run))
                self.db.execute('INSERT INTO candidate_lookups SELECT ?,wiki,normalized_title,record_json FROM candidate_lookups WHERE run_id=?',(run_id,reuse_run))
        return run_id

    def run(self, run_id):
        row = self.db.execute('SELECT * FROM pipeline_runs WHERE pipeline_run_id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown pipeline run')
        result = dict(row)
        result['config'] = json.loads(result.pop('config_json'))
        if digest(result['config']) != result['config_hash']:
            raise Incompatible('Corrupt run fingerprint; preserve run and create a new run')
        return result

    def require_config(self, run_id, cfg):
        if self.run(run_id)['config_hash'] != digest(cfg):
            raise Incompatible('Incompatible configuration. Create a new run with --reuse-run; prior results are preserved.')

    def result(self, run_id, identity, stage, dep):
        row = self.db.execute("SELECT result_json,result_hash FROM stage_results WHERE run_id=? AND identity=? AND stage=? AND dependency_hash=? AND status='completed'", (run_id, str(identity), stage, dep)).fetchone()
        if row:
            value = json.loads(row[0])
            if digest(value) != row[1]:
                raise Incompatible('Stage result checksum mismatch')
            return value
        return None

    def complete(self, run_id, identity, stage, dep, result):
        self.db.execute('''INSERT INTO stage_results (run_id,identity,stage,dependency_hash,status,result_json,result_hash) VALUES (?,?,?,?,'completed',?,?)
        ON CONFLICT(run_id,identity,stage,dependency_hash) DO UPDATE SET status='completed',result_json=excluded.result_json,result_hash=excluded.result_hash,lease_until=NULL,last_error=NULL,next_retry_at=NULL''', (run_id,str(identity),stage,dep,canonical(result),digest(result)))

    def candidates(self, run_id, limit=None):
        rows = self.db.execute('SELECT record_json FROM candidates WHERE run_id=? ORDER BY discovery_order,canonical_page_id LIMIT ?', (run_id,limit if limit is not None else -1))
        return [json.loads(r[0]) for r in rows]

    def event(self, run_id, stage, value):
        self.db.execute('INSERT INTO run_events(run_id,stage,created_at,record_json) VALUES (?,?,?,?)', (run_id,stage,now(),canonical(value)))

    def manifest_for(self,run_id,page_id,request_hash):
        from vimfc.pipeline.dependencies import verify_manifest
        row=self.db.execute('SELECT a.request_hash,m.record_json FROM article_manifests a JOIN dependency_manifests m ON m.manifest_id=a.manifest_id WHERE a.run_id=? AND a.page_id=?',(run_id,page_id)).fetchone()
        if not row: return None
        if row[0]!=request_hash: raise Incompatible('Frozen dependency input changed; create a new run')
        try:manifest=json.loads(row[1])
        except (ValueError,TypeError) as error:raise Incompatible('Dependency manifest JSON is corrupt') from error
        verify_manifest(manifest)
        bound=self.db.execute('SELECT manifest_id FROM article_manifests WHERE run_id=? AND page_id=?',(run_id,page_id)).fetchone()[0]
        if manifest['manifest_id']!=bound: raise Incompatible('Manifest binding checksum mismatch')
        return manifest

    def publish_manifest(self,run_id,page_id,request_hash,manifest):
        from vimfc.pipeline.dependencies import verify_manifest
        verify_manifest(manifest)
        with self.transaction():
            previous=self.manifest_for(run_id,page_id,request_hash)
            if previous is not None and previous!=manifest:
                raise Incompatible('Cannot replace frozen dependency manifest; create a new run')
            row=self.db.execute('SELECT record_json FROM dependency_manifests WHERE manifest_id=?',(manifest['manifest_id'],)).fetchone()
            if row and row[0]!=canonical(manifest): raise Incompatible('Conflicting content-addressed manifest')
            self.db.execute('INSERT OR IGNORE INTO dependency_manifests VALUES (?,?,?)',(manifest['manifest_id'],canonical(manifest),now()))
            self.db.execute('INSERT OR IGNORE INTO article_manifests VALUES (?,?,?,?)',(run_id,page_id,request_hash,manifest['manifest_id']))

    def item_hint(self,run_id,page_id,article):
        row=self.db.execute('SELECT article_hash,qid FROM historical_item_hints WHERE run_id=? AND page_id=?',(run_id,page_id)).fetchone()
        if row and row[0]!=digest(article): raise Incompatible('Pinned article association input changed; create a new run')
        return row[1] if row else None

    def pin_item_hint(self,run_id,page_id,article,qid):
        with self.transaction():
            previous=self.item_hint(run_id,page_id,article)
            if previous and previous!=qid: raise Incompatible('Pinned item hint changed; create a new run')
            self.db.execute('INSERT OR IGNORE INTO historical_item_hints VALUES (?,?,?,?)',(run_id,page_id,digest(article),qid))
