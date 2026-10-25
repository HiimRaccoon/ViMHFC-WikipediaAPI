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
