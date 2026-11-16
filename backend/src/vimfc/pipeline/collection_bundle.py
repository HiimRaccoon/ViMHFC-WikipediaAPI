"""Portable, checksummed copies of historical inputs; originals stay read-only."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3

from vimfc.domain import Incompatible, canonical, digest
from .relaxed_archive import CheckedArchive
from .relaxed_preservation import file_hash, snapshot_files


def inside(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative or PurePosixPath(relative).is_absolute():
        raise Incompatible('Bundle path must be portable and relative')
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise Incompatible('Bundle path escapes root')
    return path


def export_bundle(context, inventory_path, registry_path, output):
    """Preparation only: call after transfer approval, never during readiness audit."""
    output = Path(output).resolve()
    protected = [Path(context['database']).resolve().parent, Path(context['raw_cache']).resolve(),
                 Path(inventory_path).resolve().parent, Path(registry_path).resolve().parent]
    if output.exists() or any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
        raise Incompatible('Bundle output must be new and outside protected inputs')
    from .discovery_policy import verify_inventory
    inventory = json.loads(Path(inventory_path).read_text(encoding='utf-8'))
    verify_inventory(inventory)
    if inventory['snapshot'] != context['snapshot']:
        raise Incompatible('Bundle inventory snapshot differs')
    with CheckedArchive(context, registry_path) as archive:
        from .discovery_experiments import cache_baseline
        raw_baseline = cache_baseline(context['raw_cache'])
        output.mkdir(parents=True)
        for name, source in [('source.sqlite', archive.db), ('registry.sqlite', archive.registry.db)]:
            with sqlite3.connect(output / name) as destination:
                source.backup(destination)
        shutil.copytree(context['raw_cache'], output / 'raw-cache')
        if cache_baseline(output / 'raw-cache') != raw_baseline or cache_baseline(context['raw_cache']) != raw_baseline:
            raise Incompatible('Source raw cache changed during portable copy')
        shutil.copyfile(inventory_path, output / 'inventory.json')
        mapping = {}

        def retain(path, expected=None):
            path = Path(path).resolve(); body = path.read_bytes()
            sha = hashlib.sha256(body).hexdigest()
            if expected is not None and sha != expected:
                raise Incompatible('Registry transport artifact changed')
            relative = 'objects/' + sha
            target = output / relative; target.parent.mkdir(exist_ok=True)
            if not target.exists():
                target.write_bytes(body)
            mapping[str(path)] = relative
            return body

        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get('path'), str) and value.get('sha256'):
                    body = retain(value['path'], value['sha256'])
                    try:
                        approval = json.loads(body)
                    except (ValueError, UnicodeDecodeError):
                        approval = {}
                    records = approval.get('approvals', [approval]) if isinstance(approval, dict) else []
                    for record in records:
                        if not isinstance(record, dict):
                            continue
                        for entry in record.get('revisions', []):
                            if entry.get('evidence_path'):
                                retain(Path(value['path']).parent / entry['evidence_path'], entry['evidence_sha256'])
                        review = record.get('review', {})
                        if review.get('rca_path'):
                            retain(Path(value['path']).parent / review['rca_path'], review['rca_sha256'])
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        for row in archive.registry.db.execute('SELECT record_json FROM events ORDER BY sequence'):
            collect(json.loads(row[0]))
        (output / 'artifact-map.json').write_text(canonical(mapping), encoding='utf-8')
        portable = {k: context[k] for k in ('run_id', 'snapshot', 'config_hash', 'cohort_hash')}
        portable.update(database='source.sqlite', raw_cache='raw-cache')
        files = {Path(p).relative_to(output).as_posix(): info for p, info in snapshot_files([output]).items()}
        manifest = {'format': 'annotation-source-bundle-1', 'context': portable, 'files': files,
                    'inventory_id': inventory['inventory_id'], 'source_database_sha256': file_hash(context['database'])}
        manifest['bundle_id'] = digest(manifest)
        (output / 'bundle.json').write_text(canonical(manifest), encoding='utf-8')
    return read_bundle(output)[0]


def read_bundle(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'bundle.json').read_text(encoding='utf-8'))
    if (manifest.get('format') != 'annotation-source-bundle-1'
            or manifest.get('bundle_id') != digest({k: v for k, v in manifest.items() if k != 'bundle_id'})):
        raise Incompatible('Bundle manifest checksum differs')
    for relative, info in manifest['files'].items():
        path = inside(root, relative)
        if not path.is_file() or path.stat().st_size != info['bytes'] or file_hash(path) != info['sha256']:
            raise Incompatible('Bundle file checksum differs: ' + relative)
    context = {**manifest['context'], 'database': str(inside(root, manifest['context']['database'])),
               'raw_cache': str(inside(root, manifest['context']['raw_cache']))}
    mapping = json.loads((root / 'artifact-map.json').read_text(encoding='utf-8'))

    def resolve_artifact(original):
        relative = mapping.get(original)
        if relative not in manifest['files']:
            raise Incompatible('Unmapped registry artifact: ' + original)
        path = inside(root, relative)
        if file_hash(path) != manifest['files'][relative]['sha256']:
            raise Incompatible('Mapped registry artifact changed')
        return path
    return manifest, context, resolve_artifact
