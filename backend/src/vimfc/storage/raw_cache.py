import gzip
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from vimfc.domain import canonical


class RawCache:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, site, revision_id, value):
        if site not in ('viwiki', 'wikidata') or type(revision_id) is not int or revision_id <= 0:
            raise ValueError('Invalid raw cache identity')
        raw = canonical(value).encode('utf-8')
        sha = hashlib.sha256(raw).hexdigest()
        rel = Path(site) / str(revision_id) / (sha + '.json.gz')
        target = self.root / rel
        ref = {'path': rel.as_posix(), 'sha256': sha}
        if target.exists():
            self.get(ref)
            return ref
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(gzip.compress(raw, mtime=0))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        return ref

    def get(self, ref):
        path = (self.root / ref['path']).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError('Invalid raw cache path')
        try:
            raw = gzip.decompress(path.read_bytes())
            if hashlib.sha256(raw).hexdigest() != ref['sha256']:
                raise ValueError('Raw object hash mismatch')
            return json.loads(raw)
        except (OSError, EOFError, json.JSONDecodeError) as error:
            raise ValueError('Raw cache missing or corrupt') from error

    def find(self,site,revision_id):
        if site not in ('viwiki','wikidata') or type(revision_id) is not int or revision_id<=0:
            raise ValueError('Invalid raw cache identity')
        found=[]
        for path in sorted((self.root/site/str(revision_id)).glob('*.json.gz')):
            ref={'path':path.relative_to(self.root).as_posix(),'sha256':path.name.removesuffix('.json.gz')}
            found.append((ref,self.get(ref)))
        return found
