"""Serial per-host requests; only this module touches the source network."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import threading
import time
import os
import httpx
from vimfc.domain import SourceUnavailable

ENDPOINTS = {'viwiki':'https://vi.wikipedia.org/w/api.php', 'wikidata':'https://www.wikidata.org/w/api.php'}


class MediaWikiClient:
    _locks = {site:threading.Lock() for site in ENDPOINTS}

    def __init__(self, contact, *, user_agent='ViMFC/0.1', transport=None, sleep=time.sleep, attempts=5):
        if not contact or ('@' not in contact and not contact.startswith('https://')):
            raise ValueError('Configure a real contact email/URL before acquisition')
        if not 1 <= attempts <= 5:
            raise ValueError('At most five attempts')
        self.client = httpx.Client(headers={'User-Agent':f'{user_agent} ({contact})'},timeout=60,transport=transport,follow_redirects=False)
        self.sleep, self.attempts = sleep, attempts
        self.metrics = Counter()
        self.stage = 'acquisition'
        self.mock_transport=transport is not None
        self.cooldowns={}
        self.cooldown_store=None
        self.wikidata_health=None

    def enable_wikidata_health(self,store):
        from .health import WikidataHealth
        self.cooldown_store=store
        if self.wikidata_health is None:self.wikidata_health=WikidataHealth(store,self)
        return self.wikidata_health

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.client.close()

    def query(self, site, params, *, attempts=None,probe_token=None):
        if not self.mock_transport and os.environ.get('VIMFC_OFFLINE','').casefold() in ('1','true','yes'):
            raise SourceUnavailable('Source network disabled in offline mode',code='offline')
        query = {**params,'format':'json','formatversion':2,'maxlag':5}
        self.metrics[f'{site}:{self.stage}:logical_requests'] += 1
        limit=self.attempts if attempts is None else attempts
        if not 1<=limit<=self.attempts:raise ValueError('Request attempts cannot exceed the configured budget')
        with self._locks[site]:
            if site=='wikidata' and self.wikidata_health:
                host=self.wikidata_health.snapshot(probe_token)
                if host['blocked']:
                    self.metrics[f'{site}:{self.stage}:circuit_deferrals']+=1
                    raise SourceUnavailable('Wikidata circuit is paused',retryable=True,code='host_circuit_open',
                        retry_at=host['retry_at'],details=host)
            deadline=self.cooldowns.get(site)
            if self.cooldown_store:
                row=self.cooldown_store.db.execute('SELECT retry_at FROM api_cooldowns WHERE site=?',(site,)).fetchone()
                if row:
                    persisted=datetime.fromisoformat(row[0].replace('Z','+00:00'))
                    deadline=max(deadline,persisted) if deadline else persisted
            if deadline and deadline>datetime.now(timezone.utc):
                self.metrics[f'{site}:{self.stage}:cooldown_deferrals']+=1
                raise SourceUnavailable('Host is in Retry-After cooldown',retryable=True,code='host_cooldown',retry_at=deadline.isoformat().replace('+00:00','Z'),details={'site':site,'stage':self.stage,**{k:params[k] for k in ('titles','pageids','revids','rvstart') if k in params}})
            slept=0
            for attempt in range(limit):
                wait = min(60,2 ** attempt)
                retryable = False
                self.metrics[f'{site}:{self.stage}:{params.get("list",params.get("prop","other"))}:requests'] += 1
                try:
                    started=time.perf_counter()
                    response = self.client.get(ENDPOINTS[site],params=query)
                    self.metrics[f'{site}:{self.stage}:request_seconds'] += time.perf_counter()-started
                    retry = response.headers.get('Retry-After')
                    if retry:
                        try:
                            wait = max(wait,float(retry))
                        except ValueError:
                            try:
                                wait = max(wait,(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds())
                            except (ValueError,TypeError):
                                pass
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if response.status_code >= 400:
                        raise SourceUnavailable(f'HTTP {response.status_code}',retryable=retryable,code='http_'+str(response.status_code))
                    payload = response.json()
                    if 'error' in payload:
                        error = payload['error']
                        code = error.get('code')
                        retryable = code in ('maxlag','ratelimited','readonly','internal_api_error_DBConnectionError')
                        if code == 'maxlag':
                            wait = max(5,wait)
                        raise SourceUnavailable(f'{code}: {error.get("info", "API error")}',retryable=retryable,code=code)
                    return payload
                except (httpx.TimeoutException,httpx.NetworkError) as error:
                    failure = SourceUnavailable(str(error),retryable=True,code='network_timeout')
                except (ValueError, httpx.DecodingError) as error:
                    failure = SourceUnavailable(f'Invalid API JSON: {error}',retryable=False)
                except SourceUnavailable as error:
                    failure = error
                self.metrics[f'{site}:{self.stage}:errors'] += 1
                self.metrics[f'{site}:{self.stage}:error_code:{failure.code}'] += 1
                failure.details.update({'site':site,'stage':self.stage,'attempts':attempt+1,'endpoint':params.get('list',params.get('prop','other')),
                    **{k:params[k] for k in ('titles','pageids','revids','rvstart') if k in params}})
                if site=='wikidata' and self.wikidata_health and failure.code=='maxlag':
                    failure.retry_at=(datetime.now(timezone.utc)+timedelta(seconds=wait)).isoformat().replace('+00:00','Z')
                    self.wikidata_health.trip(failure,probe_token=probe_token)
                    if failure.details.get('circuit_recorded'):self.metrics[f'{site}:{self.stage}:circuit_trips']+=1
                    raise failure
                # Long Retry-After belongs to the durable scheduler. Do not
                # block one HTTP call indefinitely or retry before its deadline.
                if not failure.retryable or attempt+1 == limit or slept+wait>60:
                    if failure.retryable:
                        deadline=datetime.now(timezone.utc)+timedelta(seconds=wait)
                        self.cooldowns[site]=deadline
                        failure.retry_at=deadline.isoformat().replace('+00:00','Z')
                        if self.cooldown_store:
                            self.cooldown_store.db.execute('INSERT INTO api_cooldowns VALUES (?,?) ON CONFLICT(site) DO UPDATE SET retry_at=max(retry_at,excluded.retry_at)',(site,failure.retry_at))
                    raise failure
                self.metrics[f'{site}:{self.stage}:retries'] += 1
                self.metrics['backoff_seconds'] += wait
                self.metrics[f'{site}:{self.stage}:backoff_seconds'] += wait
                self.sleep(wait)
                slept+=wait
        raise AssertionError('unreachable')

    def continued(self, site, params):
        continuation = {}
        seen = set()
        while True:
            payload = self.query(site,{**params,**continuation})
            yield payload
            continuation = payload.get('continue')
            if continuation is None:
                return
            key = repr(sorted(continuation.items()))
            if key in seen:
                raise SourceUnavailable('Repeated continuation cursor')
            seen.add(key)
