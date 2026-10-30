"""Recoverable immutable publication; acquisition and sealing share a host lease."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
from vimfc.domain import Incompatible,canonical


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def stage(path,value):
    encoded=(canonical(value)+'\n').encode('utf-8')
    with path.open('xb') as stream:stream.write(encoded);stream.flush();os.fsync(stream.fileno())
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(encoded).hexdigest()}


def publish_alias(source,destination):
    source=Path(source);destination=Path(destination)
    expected=file_hash(source)
    if destination.exists():
        if file_hash(destination)!=expected:raise Incompatible('Existing inventory publication conflicts; no overwrite')
        return
    temp=destination.with_name('.inventory-publication-'+str(uuid.uuid4())+'.tmp')
    try:
        shutil.copyfile(source,temp)
        if file_hash(temp)!=expected:raise Incompatible('Inventory publication copy checksum mismatch')
        try:os.link(temp,destination)
        except FileExistsError:
            if file_hash(destination)!=expected:raise Incompatible('Concurrent inventory publication conflicts')
    finally:
        if temp.exists():temp.unlink()


def _complete(inventory,config,output,publication):
    root=output/'acquisition-attempts'
    for item in publication['files'].values():
        path=Path(item['path']).resolve()
        if not path.is_relative_to(root.resolve()) or file_hash(path)!=item['sha256']:
            raise Incompatible('Staged inventory publication missing/corrupt/outside inventory root')
    frozen=json.loads(Path(publication['files']['inventory']['path']).read_text(encoding='utf-8'))
    if inventory.freeze(config,commit=False)!=frozen or publication['inventory_id']!=frozen['inventory_id']:
        raise Incompatible('Staged inventory differs from verified checkpoint')
    publish_alias(publication['files']['inventory']['path'],output/'inventory.json')
    publish_alias(publication['files']['summary']['path'],output/'inventory-summary.json')
    return frozen


def recover_publication(inventory,config,output):
    output=Path(output).resolve();inventory.claim_host()
    try:
        row=inventory.db.execute("SELECT value FROM metadata WHERE key='publication'").fetchone()
        if not row:return None
        return _complete(inventory,config,output,json.loads(row[0]))
    finally:inventory.release_host()


def publish_inventory(inventory,config,output,folder):
    output=Path(output).resolve();folder=Path(folder).resolve()
    if not folder.is_relative_to(output/'acquisition-attempts'):
        raise Incompatible('Publication staging requires an inventory attempt directory')
    inventory.claim_host()
    try:
        existing=inventory.db.execute("SELECT value FROM metadata WHERE key='publication'").fetchone()
        if existing:return _complete(inventory,config,output,json.loads(existing[0]))
        frozen=inventory.freeze(config,commit=False)
        folder.mkdir(parents=True,exist_ok=True)
        publication={'inventory_id':frozen['inventory_id'],'files':{
            'inventory':stage(folder/'validated-inventory.json',frozen),
            'summary':stage(folder/'validated-inventory-summary.json',{k:v for k,v in frozen.items() if k not in ('seeds','neighbors')})}}
        with inventory.db:
            inventory.db.execute("INSERT INTO metadata VALUES ('publication',?)",(canonical(publication),))
            inventory.db.execute("INSERT OR IGNORE INTO metadata VALUES ('frozen',?)",(frozen['inventory_id'],))
        return _complete(inventory,config,output,publication)
    finally:inventory.release_host()
