"""Local review-only SQLite store with append-only revisions and atomic import."""
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3

from vimfc.domain import digest
from .evaluation import _csv_cell
from .human_review import ROOT, _bool, _bytes, _json, _new, _read
from .review_schema import SEMANTIC_FIELDS, validate_audit, validate_semantic

TRANSFER_FIELDS = ('schema_version', 'manifest_id', 'review_id', 'page_id', 'revision_id',
                   'input_fingerprint', 'review_status', 'exported_revision', 'comparison_exposed', *SEMANTIC_FIELDS,
                   'audit', 'exported_audit_revision')


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_review_path(path, packet):
    path = Path(path).resolve()
    protected = [ROOT / 'api', ROOT / '.git', ROOT / '.agents', ROOT / 'configs',
                 ROOT / 'backend', ROOT / 'frontend', ROOT / 'scripts', ROOT / 'tests', ROOT / 'docs',
                 packet['root'], *map(Path, packet['manifest']['source'].get('immutable_source_paths', []))]
    if any(path == p.resolve() or path.is_relative_to(p.resolve()) for p in protected):
        raise ValueError('Review writes overlap packet, source or code')
    if path.is_relative_to(ROOT / 'data'):
        base = ROOT / 'data/experiments'
        if not path.is_relative_to(base) or not path.relative_to(base).parts or not path.relative_to(base).parts[0].startswith('groq-review-'):
            raise ValueError('Review workspace requires a separate groq-review namespace')
    return path


def _csv_encode(value):
    if isinstance(value, str) and value.startswith("'"):
        return "'" + value
    return _csv_cell(value)


def _csv_decode(value):
    if value.startswith("'"):
        rest = value[1:]
        if rest.startswith("'") or _csv_cell(rest) != rest:
            return rest
    return value


class ReviewStore:
    def __init__(self, packet, workspace):
        self.packet = packet
        self.root = safe_review_path(workspace, packet)
        marker = self.root / 'review-workspace.json'
        identity = {'format': 'vimfc-review-workspace-1', 'manifest_id': packet['manifest']['manifest_id']}
        if self.root.exists():
            if not marker.is_file() or _read(marker) != identity:
                raise ValueError('Refuse existing directory without matching review workspace identity')
        else:
            _new(marker, _bytes(identity))
        self.database = self.root / 'reviews.sqlite'
        if self.database.exists() and (self.database.is_symlink() or not self.database.resolve().is_relative_to(self.root)):
            raise ValueError('Review database is not owned by workspace')
        with self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS revisions (
                    review_id TEXT NOT NULL, kind TEXT NOT NULL, revision INTEGER NOT NULL,
                    record_json TEXT NOT NULL, PRIMARY KEY(review_id, kind, revision));
                CREATE TABLE IF NOT EXISTS exposure (
                    review_id TEXT PRIMARY KEY, exposed_at TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON revisions
                    BEGIN SELECT RAISE(ABORT, 'Review history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON revisions
                    BEGIN SELECT RAISE(ABORT, 'Review history is append-only'); END;
            ''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _reference(self, rid):
        if not isinstance(rid, str) or rid not in self.packet['references']:
            raise ValueError('Unknown review identity')
        return self.packet['references'][rid]

    @staticmethod
    def _current(db, rid, kind='semantic'):
        row = db.execute('SELECT record_json FROM revisions WHERE review_id=? AND kind=? ORDER BY revision DESC LIMIT 1', (rid, kind)).fetchone()
        return _json(row[0]) if row else None

    def current(self, rid, kind='semantic'):
        self._reference(rid)
        with self._connection() as db:
            return self._current(db, rid, kind)

    def history(self, rid):
        self._reference(rid)
        with self._connection() as db:
            return [_json(r[0]) for r in db.execute('SELECT record_json FROM revisions WHERE review_id=? ORDER BY kind, revision', (rid,))]

    def _append(self, db, rid, payload, kind, expected_revision, source):
        current = self._current(db, rid, kind)
        revision = current['revision'] if current else 0
        if type(expected_revision) is not int or expected_revision != revision:
            raise ValueError('Stale review revision; reload before saving')
        semantic = self._current(db, rid)
        if kind == 'audit' and not semantic:
            raise ValueError('Submit blind semantic review before auditing evidence')
        ref = self._reference(rid)
        record = {'review_id': rid, 'kind': kind, 'revision': revision + 1,
                  'manifest_id': self.packet['manifest']['manifest_id'],
                  'input_fingerprint': ref['input_fingerprint'], 'payload': payload,
                  'received_at': now(), 'source': source,
                  'comparison_exposed': bool(db.execute('SELECT 1 FROM exposure WHERE review_id=?', (rid,)).fetchone()),
                  'semantic_revision': semantic['revision'] if kind == 'audit' else revision + 1,
                  'previous_hash': current['record_hash'] if current else None}
        record['record_hash'] = digest(record)
        db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (rid, kind, revision + 1, json.dumps(record, ensure_ascii=False)))
        return record

    def save(self, rid, payload, *, expected_revision):
        value = validate_semantic(payload, self._reference(rid))
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._append(db, rid, value, 'semantic', expected_revision, 'manual-local-ui')

    def save_audit(self, rid, payload, *, expected_revision, expected_semantic_revision):
        self._reference(rid)
        value = validate_audit(payload, self.packet['evidence'][rid])
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            semantic = self._current(db, rid)
            if not semantic:
                raise ValueError('Submit blind semantic review before auditing evidence')
            if type(expected_semantic_revision) is not int or semantic['revision'] != expected_semantic_revision:
                raise ValueError('Stale semantic revision; reload before auditing')
            result = self._append(db, rid, value, 'audit', expected_revision, 'manual-local-audit')
            db.execute('INSERT OR IGNORE INTO exposure VALUES(?,?)', (rid, now()))
            return result

    def was_exposed(self, rid):
        self._reference(rid)
        with self._connection() as db:
            return bool(db.execute('SELECT 1 FROM exposure WHERE review_id=?', (rid,)).fetchone())

    def mark_exposed(self, rid):
        self._reference(rid)
        with self._connection() as db:
            if not self._current(db, rid):
                raise ValueError('Comparison is locked until semantic review is saved')
            db.execute('INSERT OR IGNORE INTO exposure VALUES(?,?)', (rid, now()))

    def snapshot(self):
        with self._connection() as db:
            db.execute('BEGIN')
            labels, audits = {}, {}
            for rid in self.packet['references']:
                label, audit = self._current(db, rid), self._current(db, rid, 'audit')
                if label:
                    labels[rid] = label
                if audit and label and audit['semantic_revision'] == label['revision']:
                    audits[rid] = audit
            return labels, audits

    def progress(self):
        labels, audits = self.snapshot()
        return {'total': len(self.packet['references']), 'reviewed': len(labels), 'audited': len(audits),
                'reviewed_ids': list(labels), 'next_pending': next((rid for rid in self.packet['references'] if rid not in labels), None)}

    def export(self, fmt='jsonl'):
        labels, audits = self.snapshot()
        records = []
        for rid, ref in self.packet['references'].items():
            label, audit = labels.get(rid), audits.get(rid)
            records.append({'schema_version': 2, 'manifest_id': self.packet['manifest']['manifest_id'],
                            'review_id': rid, 'page_id': ref['page_id'], 'revision_id': ref['revision_id'],
                            'input_fingerprint': ref['input_fingerprint'],
                            'review_status': 'reviewed' if label else 'pending',
                            'exported_revision': label['revision'] if label else 0,
                            'comparison_exposed': self.was_exposed(rid),
                            **(label['payload'] if label else {k: None for k in SEMANTIC_FIELDS}),
                            'audit': audit['payload'] if audit else None,
                            'exported_audit_revision': audit['revision'] if audit else 0})
        if fmt == 'jsonl':
            return ''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in records).encode('utf-8')
        if fmt != 'csv':
            raise ValueError('Choose csv or jsonl')
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=TRANSFER_FIELDS)
        writer.writeheader()
        writer.writerows({k: _csv_encode(v) for k, v in r.items()} for r in records)
        return stream.getvalue().encode('utf-8-sig')

    def import_labels(self, data, fmt):
        text = data.decode('utf-8-sig')
        if fmt == 'jsonl':
            rows = [_json(line) for line in text.splitlines() if line.strip()]
        elif fmt == 'csv':
            reader = csv.DictReader(io.StringIO(text, newline=''))
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError('Duplicate or missing CSV headers')
            rows = list(reader)
            for row in rows:
                if set(row) != set(TRANSFER_FIELDS) or any(v is None for v in row.values()):
                    raise ValueError('Unexpected import columns')
                row.update({k: _csv_decode(v) for k, v in row.items()})
                for key in ('schema_version', 'page_id', 'revision_id', 'exported_revision', 'exported_audit_revision'):
                    row[key] = int(row[key])
                row['comparison_exposed'] = _bool(row['comparison_exposed'])
                for key in ('evidence_sentence_ids', 'audit'):
                    row[key] = _json(row[key]) if row[key] else None
                for key in SEMANTIC_FIELDS:
                    if row[key] == '':
                        row[key] = None
                if row['review_status'] == 'reviewed' and row['notes'] is None:
                    row['notes'] = ''
        else:
            raise ValueError('Choose csv or jsonl')
        validated, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(TRANSFER_FIELDS):
                raise ValueError('Unexpected import schema; comparison is not human gold')
            rid = row['review_id']
            ref = self._reference(rid)
            if rid in seen:
                raise ValueError('Duplicate review identity')
            seen.add(rid)
            if type(row['schema_version']) is not int or row['schema_version'] != 2 or row['manifest_id'] != self.packet['manifest']['manifest_id']:
                raise ValueError('Foreign review schema/manifest')
            if any(digest(row[k]) != digest(ref[k]) for k in ('page_id', 'revision_id', 'input_fingerprint')):
                raise ValueError('Historical identity was changed')
            if any(type(row[k]) is not int or row[k] < 0 for k in ('exported_revision', 'exported_audit_revision')):
                raise ValueError('Invalid exported revision')
            if type(row['comparison_exposed']) is not bool:
                raise ValueError('Explicit comparison exposure flag required')
            if row['review_status'] == 'pending':
                if any(row[k] is not None for k in SEMANTIC_FIELDS) or row['audit'] is not None or row['comparison_exposed']:
                    raise ValueError('Pending records cannot contain gold or audit decisions')
                continue
            if row['review_status'] != 'reviewed':
                raise ValueError('Unknown review status')
            value = validate_semantic({k: row[k] for k in SEMANTIC_FIELDS}, ref)
            audit = validate_audit(row['audit'], self.packet['evidence'][rid]) if row['audit'] is not None else None
            validated.append((rid, value, audit, row))
        # All validation precedes the write transaction; rollback also protects
        # against any insertion failure after an earlier row has been inserted.
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for rid, value, audit, row in validated:
                current = self._current(db, rid)
                if current and current['revision'] != row['exported_revision']:
                    raise ValueError('Stale import revision; export current labels before editing')
                current_audit = self._current(db, rid, 'audit')
                if audit and current_audit and current_audit['revision'] != row['exported_audit_revision']:
                    raise ValueError('Stale imported audit revision')
                if row['comparison_exposed'] or audit:
                    db.execute('INSERT OR IGNORE INTO exposure VALUES(?,?)', (rid, now()))
                self._append(db, rid, value, 'semantic', current['revision'] if current else 0, 'import:' + fmt)
                if audit:
                    self._append(db, rid, audit, 'audit', current_audit['revision'] if current_audit else 0, 'import:' + fmt)
                    db.execute('INSERT OR IGNORE INTO exposure VALUES(?,?)', (rid, now()))
        return len(validated)
