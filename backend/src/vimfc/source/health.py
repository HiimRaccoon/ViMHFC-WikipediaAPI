"""Operational Wikidata circuit state. Never classification evidence."""
from datetime import datetime, timedelta, timezone
import uuid

from vimfc.domain import SourceUnavailable


def timestamp(value):
    return value.isoformat().replace('+00:00','Z')


def parsed(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')) if value else None


class WikidataHealth:
    site='wikidata'

    def __init__(self,store,client):
        self.store,self.client=store,client

    def snapshot(self,probe_token=None):
        row=self.store.db.execute('SELECT * FROM host_circuits WHERE site=?',(self.site,)).fetchone()
        state=dict(row) if row else {'state':'unknown','consecutive_failures':0,'next_retry_at':None,'last_code':None,'last_message':None}
        deadlines=[];reason=state.get('last_code') or 'not_observed'
        if state['state']=='open' and state.get('next_retry_at'):deadlines.append(parsed(state['next_retry_at']))
        if state['state']=='half_open' and state.get('probe_token')!=probe_token and state.get('probe_until'):
            deadlines.append(parsed(state['probe_until']));reason='health_probe_in_flight'
        row=self.store.db.execute('SELECT retry_at FROM api_cooldowns WHERE site=?',(self.site,)).fetchone()
        if row:deadlines.append(parsed(row[0]))
        local=getattr(self.client,'cooldowns',{}).get(self.site)
        if local:deadlines.append(local)
        deadline=max(deadlines) if deadlines else None
        cooling=bool(deadline and deadline>datetime.now(timezone.utc))
        requires_probe=state['state']=='open' or state['state']=='half_open' and state.get('probe_token')!=probe_token
        blocked=cooling or requires_probe
        if blocked and state['state'] not in ('open','half_open'):reason='api_cooldown'
        return {'site':self.site,'state':state['state'],'blocked':blocked,
            'policy_version':'wikidata-health-1',
            'cooldown_active':cooling,'requires_probe':requires_probe,
            'retry_at':timestamp(deadline) if deadline else None,'reason':reason,
            'consecutive_failures':state['consecutive_failures'],'last_code':state.get('last_code'),
            'last_message':state.get('last_message'),'stability_confirmed':False}

    def owns_probe(self,token):
        row=self.store.db.execute('SELECT state,probe_token FROM host_circuits WHERE site=?',(self.site,)).fetchone()
        return bool(row and row[0]=='half_open' and row[1]==token)

    def trip(self,error,probe_token=None):
        with self.store.transaction():
            if probe_token and not self.owns_probe(probe_token):
                error.details['stale_probe']=True
                return self.snapshot()
            row=self.store.db.execute('SELECT consecutive_failures FROM host_circuits WHERE site=?',(self.site,)).fetchone()
            failures=(row[0] if row else 0)+1
            # Long enough to stop short retry-pass storms. Retry-After can only
            # extend this exponential hold, never be shortened by its cap.
            hold=min(1800,120*2**min(failures-1,4))
            deadline=datetime.now(timezone.utc)+timedelta(seconds=hold)
            for value in (error.retry_at,self.snapshot().get('retry_at')):
                if value:deadline=max(deadline,parsed(value))
            retry=timestamp(deadline);stamp=timestamp(datetime.now(timezone.utc))
            self.store.db.execute('''INSERT INTO host_circuits VALUES (?,'open',?,?,?,?,?,NULL,NULL)
                ON CONFLICT(site) DO UPDATE SET state='open',consecutive_failures=excluded.consecutive_failures,
                next_retry_at=excluded.next_retry_at,last_observed_at=excluded.last_observed_at,
                last_code=excluded.last_code,last_message=excluded.last_message,probe_token=NULL,probe_until=NULL''',
                (self.site,failures,retry,stamp,error.code,str(error)))
            self.store.db.execute('''INSERT INTO api_cooldowns VALUES (?,?) ON CONFLICT(site) DO UPDATE
                SET retry_at=CASE WHEN julianday(excluded.retry_at)>julianday(retry_at) THEN excluded.retry_at ELSE retry_at END''',(self.site,retry))
        self.client.cooldowns[self.site]=deadline
        error.retry_at=retry
        error.details.update(site=self.site,circuit_recorded=True,circuit_state='open',circuit_failures=failures)
        return self.snapshot()

    def preflight(self):
        # Claim the single half-open probe under a short transaction; never hold
        # the SQLite writer lock while doing HTTP. Other clients see the lease.
        with self.store.transaction():
            current=self.snapshot()
            if current['cooldown_active']:return {**current,'allowed':False,'probe_performed':False}
            token=str(uuid.uuid4());until=timestamp(datetime.now(timezone.utc)+timedelta(seconds=90))
            self.store.db.execute('''INSERT INTO host_circuits(site,state,probe_token,probe_until) VALUES (?,'half_open',?,?)
                ON CONFLICT(site) DO UPDATE SET state='half_open',probe_token=excluded.probe_token,probe_until=excluded.probe_until''',(self.site,token,until))
        previous=self.client.stage;self.client.stage='health_preflight'
        before=sum(v for k,v in self.client.metrics.items() if k.startswith(self.site+':health_preflight:') and k.endswith(':requests'))
        def sent():
            return sum(v for k,v in self.client.metrics.items() if k.startswith(self.site+':health_preflight:') and k.endswith(':requests'))>before
        try:
            payload=self.client.query(self.site,{'action':'query','meta':'siteinfo','siprop':'general'},attempts=1,probe_token=token)
            if not isinstance(payload,dict) or not isinstance(payload.get('query',{}).get('general'),dict):
                raise SourceUnavailable('Invalid health probe response',retryable=True,code='health_probe_invalid')
            self.store.db.execute('''UPDATE host_circuits SET state='closed',consecutive_failures=0,next_retry_at=NULL,
                last_observed_at=?,last_code='probe_passed_advisory',last_message='Single probe passed; source stability is not established',
                probe_token=NULL,probe_until=NULL WHERE site=? AND probe_token=? AND state='half_open' ''',
                (timestamp(datetime.now(timezone.utc)),self.site,token))
            result=self.snapshot()
            return {**result,'allowed':not result['blocked'],'probe_performed':sent()}
        except SourceUnavailable as error:
            performed=sent()
            if performed and not error.details.get('circuit_recorded') and self.owns_probe(token):
                self.trip(error,probe_token=token)
            elif not performed and self.owns_probe(token):
                # Local rejection/offline mode is no evidence of a source
                # failure. Restore only our own lease, never newer host state.
                state=current['state'] if current['state']!='half_open' else 'open'
                self.store.db.execute('''UPDATE host_circuits SET state=?,next_retry_at=?,probe_token=NULL,probe_until=NULL
                    WHERE site=? AND probe_token=? AND state='half_open' ''',(state,current.get('retry_at'),self.site,token))
            return {**self.snapshot(),'allowed':False,'probe_performed':performed,'error_code':error.code,'error':str(error)}
        finally:
            self.client.stage=previous
