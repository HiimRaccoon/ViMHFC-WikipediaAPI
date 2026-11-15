"""Streaming durable-file preservation; excludes credentials and volatile SHM."""
import hashlib
from pathlib import Path


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def snapshot_files(roots, *, exclude=(), progress=None):
    excluded = [Path(p).resolve() for p in exclude]
    paths = set()
    for root in roots:
        root = Path(root)
        for path in ([root] if root.is_file() else root.rglob('*')):
            if (not path.is_file() or path.name.endswith('-shm') or path.name == '.env'
                    or any(path.resolve().is_relative_to(e) for e in excluded)):
                continue
            paths.add(path.resolve())
    result = {}
    for index, path in enumerate(sorted(paths), 1):
        stat = path.stat()
        result[str(path)] = {'bytes': stat.st_size, 'sha256': file_hash(path)}
        if (path.stat().st_size, path.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise ValueError('Protected input changed while hashing: ' + str(path))
        if progress and index % 5000 == 0: progress({'event': 'preservation_hash', 'files': index, 'total': len(paths)})
    return result


def compare_files(before, after):
    changed = sorted(p for p in before if p not in after or before[p] != after[p])
    added = sorted(set(after) - set(before))
    return {'passed': not changed and not added, 'checked_files': len(before),
            'checked_bytes': sum(r['bytes'] for r in before.values()), 'changed_or_missing_files': changed,
            'unexpected_new_protected_files': added, 'excluded': ['volatile SQLite *-shm', 'credentials .env', 'new owned output directory']}
