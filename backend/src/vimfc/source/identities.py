"""Historical title ownership and redirect proofs; unresolved is never an edge."""
from dataclasses import asdict, replace
import json
import re
import unicodedata
from vimfc.domain import canonical, digest, at_or_before, SourceUnavailable


def normalize_title(title):
    title = ' '.join(unicodedata.normalize('NFC',title.replace('_',' ')).strip().split())
    return title[:1].upper()+title[1:]


def title_at_snapshot(page_id, current_title, moves, snapshot):
    title = normalize_title(current_title)
    for event in sorted(moves,key=lambda e:e['timestamp'],reverse=True):
        if not at_or_before(event['timestamp'],snapshot) and event.get('pageid',event.get('logpage')) == page_id:
            if normalize_title(event.get('params',{}).get('target_title','')) != title:
                raise SourceUnavailable('Ambiguous historical move chain')
            title = normalize_title(event['title'])
    return title


def resolve_historical(title, lookup, max_redirects=10):
    title = normalize_title(title)
    chain, seen = [],set()
    for _ in range(max_redirects+1):
        if title in seen:
            return {'status':'unresolved','reason':'redirect_cycle','chain':chain}
        seen.add(title)
        try:
            owners = lookup(title)
        except SourceUnavailable as error:
            if error.retryable:
                raise
            return {'status':'unresolved','reason':str(error),'chain':chain}
        if len(owners) != 1:
            return {'status':'unresolved','reason':'ambiguous_or_missing_owner','chain':chain}
        owner = owners[0]
        redirect = re.match(r'^\s*#(?:redirect|đổi|đổi hướng)\s*\[\[([^\]]+)\]\]',owner['content'],re.I)
        if not redirect:
            return {**owner,'status':'resolved','chain':chain}
        target, _, fragment = redirect[1].partition('#')
        chain.append({'page_id':owner['page_id'],'revision_id':owner['revision_id'],'original_title':title,'target':target,'fragment':fragment})
        title = normalize_title(target)
    return {'status':'unresolved','reason':'redirect_depth','chain':chain}


class HistoricalIdentities:
    def __init__(self, source, snapshot):
        self.source, self.snapshot = source,snapshot
        self.moves = {}

    def _move_index(self,horizon):
        if not horizon:
            raise SourceUnavailable('Current title lookup has no recorded server-time horizon')
        if horizon not in self.moves:
            db = self.source.store.db
            key='__move_index__:'+horizon
            row = db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',(self.snapshot,key)).fetchone()
            if row:
                self.moves[horizon] = json.loads(row[0])
            else:
                latest=db.execute("SELECT lookup_title,record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title LIKE '__move_index__:%' ORDER BY lookup_title DESC LIMIT 1",(self.snapshot,)).fetchone()
                prior_horizon=latest[0].split(':',1)[1] if latest else self.snapshot
                moves=json.loads(latest[1]) if latest else []
                if at_or_before(horizon,prior_horizon):
                    moves=[m for m in moves if at_or_before(m['timestamp'],horizon)]
                else:
                    for data in self.source.client.continued('viwiki',{'action':'query','list':'logevents','letype':'move','lestart':horizon,'leend':prior_horizon,'ledir':'older','lelimit':'max','leprop':'ids|title|timestamp|details'}):
                        moves.extend(data.get('query',{}).get('logevents',[]))
                moves=sorted({digest(m):m for m in moves}.values(),key=lambda m:(m['timestamp'],m.get('logid',0)))
                self.moves[horizon]=moves
                db.execute('INSERT INTO identity_resolutions VALUES (?,?,?)',(self.snapshot,key,canonical(moves)))
        return self.moves[horizon]

    def raw_page(self, page_id):
        ref = self.source.select_revision('viwiki',str(page_id),self.snapshot)
        title = title_at_snapshot(page_id,ref.title,self._move_index(ref.lookup_timestamp),self.snapshot)
        return replace(self.source.fetch_revisions('viwiki',[ref])[0],ref=replace(ref,title=title))

    def resolve(self, title):
        title = normalize_title(title)
        db = self.source.store.db
        row = db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',(self.snapshot,title)).fetchone()
        if row:
            return json.loads(row[0])
        try:
            initial_ref=self.source.select_revision('viwiki',title,self.snapshot)
            horizon=initial_ref.lookup_timestamp
        except SourceUnavailable as error:
            if error.retryable:
                raise
            horizon=self.source.client.query('viwiki',{'action':'query','curtimestamp':1}).get('curtimestamp')
        moves = self._move_index(horizon)
        def lookup(name):
            hints = set()
            try:
                hints.add(self.source.select_revision('viwiki',name,self.snapshot).page_id)
            except SourceUnavailable as error:
                if error.retryable:
                    raise
            for move in moves:
                if normalize_title(move['title']) == name:
                    hint = move.get('pageid',move.get('logpage'))
                    if hint:
                        hints.add(hint)
            owners = []
            for page_id in sorted(hints):
                try:
                    raw = self.raw_page(page_id)
                except SourceUnavailable as error:
                    # A missing raw owner cannot prove that another surviving hint
                    # is the unique historical owner.
                    raise SourceUnavailable('Historical owner could not be verified: '+str(error),retryable=error.retryable) from error
                if normalize_title(raw.ref.title) == name:
                    owners.append({'page_id':page_id,'title':raw.ref.title,'revision_id':raw.ref.revision_id,'revision_timestamp':raw.ref.revision_timestamp,'raw_hash':raw.local_sha256,'content':raw.content})
            return owners
        result = resolve_historical(title,lookup)
        result['snapshot'] = self.snapshot
        result['proof'] = {'kind':'historical_revision_and_move_log','move_index_hash':digest(moves)}
        db.execute('INSERT OR IGNORE INTO identity_resolutions VALUES (?,?,?)',(self.snapshot,title,canonical(result)))
        return result
