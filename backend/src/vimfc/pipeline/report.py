from collections import Counter, defaultdict, deque
from contextlib import AbstractContextManager
import os
from pathlib import Path
import platform
import threading
import time
import psutil
from .pairs import comparison_blocks


def corpus_report(articles,pairs,quotas,assessment=None):
    ids={a['page_id'] for a in articles}
    edges={(a['page_id'],link['target']['page_id']) for a in articles for link in a['extraction']['links']
        if link['scope']=='retained_lead_prose' and link.get('target',{}).get('status')=='resolved' and link['target']['page_id'] in ids}
    incoming,outgoing=Counter(),Counter()
    neighbors=defaultdict(set)
    for source,target in edges:
        outgoing[source]+=1
        incoming[target]+=1
        neighbors[source].add(target)
        neighbors[target].add(source)
    remaining=set(ids)
    components=[]
    while remaining:
        stack=[min(remaining)]
        remaining.remove(stack[0])
        count=0
        while stack:
            i=stack.pop()
            count+=1
            for j in neighbors[i]:
                if j in remaining:
                    remaining.remove(j)
                    stack.append(j)
        components.append(count)
    groups=Counter(a['classification']['primary_entity_type'] for a in articles)
    subtypes=Counter(s['subtype_id'] for a in articles for s in a['classification']['subtypes'])
    assessed={'status':'unassessed','reviewed':0,'valid':0,'ratio':None}
    if assessment is not None:
        reviewed,valid=assessment.get('reviewed'),assessment.get('valid')
        if type(reviewed) is not int or type(valid) is not int or not 0<=valid<=reviewed or not assessment.get('provenance'):
            raise ValueError('External assessment requires valid <= reviewed integer counts and provenance')
        assessed={**assessment,'status':'assessed' if reviewed else 'unassessed','ratio':valid/reviewed if reviewed else None}
    return {'article_count':len(ids),'group_counts':dict(sorted(groups.items())),'quota_deviations':{g:groups[g]-n for g,n in quotas.items()},'subtype_counts':dict(sorted(subtypes.items())),
        'graph':{'internal_edge_count':len(edges),'in_degree_distribution':dict(Counter(incoming[i] for i in ids)), 'out_degree_distribution':dict(Counter(outgoing[i] for i in ids)),
            'isolated_article_count':sum(not neighbors[i] for i in ids),'weak_component_sizes':sorted(components,reverse=True)},
        'positive_pairs':dict(Counter(p['kind'] for p in pairs if p['score']>0 and p['endpoint_a'] in ids and p['endpoint_b'] in ids)),
        'valid_2hop_yield':assessed}


def template_audit(records,cohort_size):
    attempted=success=unresolved=other=0
    templates=defaultdict(dict)
    supported,nonprose=Counter(),Counter()
    impacts=[]
    for article in records:
        extraction=article.get('extraction')
        if extraction is None:
            continue
        attempted+=1
        supported.update(extraction.get('template_usage',{}).get('supported',{}))
        nonprose.update(extraction.get('template_usage',{}).get('excluded_nonprose',{}))
        if extraction['outcome']=='completed':
            success+=1
        elif extraction['outcome']=='unresolved_extraction':
            unresolved+=1
            impacts.append({'page_id':article['page_id'],'revision_id':article['revision_id'],'raw_hash':article['raw_hash'],
                'provisional_sentence_count':len(extraction.get('sentences',[])),'provisional_paragraph_count':len(extraction.get('paragraphs',[])),
                'validated_lead_link_count':0,'full_lead_validated':False,'factual_clauses_silently_removed_from_corpus':False})
        else:
            other+=1
        for diagnostic in extraction.get('diagnostics',[]):
            templates[diagnostic['template']].setdefault(article['page_id'],{'page_id':article['page_id'],'revision_id':article['revision_id'],'raw_hash':article['raw_hash'],'diagnostic':diagnostic})
    return {'attempted':attempted,'successful':success,'unresolved':unresolved,'other_failure':other,'not_attempted':cohort_size-attempted,
        'success_rate':success/attempted if attempted else None,'unresolved_rate':unresolved/attempted if attempted else None,
        'templates':[{'name':name,'affected_articles':len(affected),'affected_page_ids':sorted(affected),'examples':list(affected.values())[:5]} for name,affected in sorted(templates.items(),key=lambda x:(-len(x[1]),x[0]))],
        'supported_templates':dict(sorted(supported.items())),'excluded_nonprose_templates':dict(sorted(nonprose.items())),'unresolved_impacts':impacts,
        'provisional_outputs_validated':False,'impact':'Unresolved full lead, sentence boundaries and lead links remain provisional; no unresolved output enters validated selection.',
        'excluded_nonprose_is_formatter_failure':False}


def classification_audit(records,cohort_size,rules):
    articles=[]
    for record in records:
        cls=record.get('classification')
        if cls is None:
            continue
        items=cls.get('items',{})
        articles.append({'page_id':record['page_id'],'title':record.get('title'),'outcome':cls['outcome'],'reason':cls.get('reason'),
            **{k:cls.get(k) for k in ('primary_status','primary_decision','primary_provenance','primary_candidates','subtype_status','subtype_search_status','subtype_provenance','policy_version','manifest_id')},
            'retryable':cls.get('retryable',False),'retry_at':cls.get('retry_at'),'error_code':cls.get('error_code'),'error_details':cls.get('error_details',{}),
            'primary_entity_type':cls.get('primary_entity_type'),'subtypes':cls.get('subtypes',[]),'provenance':cls.get('provenance',[]),
            'diagnostics':cls.get('diagnostics',[]),'configured_roots_present':sorted(set(items)&set(rules['roots'])),
            'closure_summary':cls.get('closure_summary',{}),
            'dependency_count':len(items),'dependencies':[{'qid':qid,**{k:item.get(k) for k in ('revision_id','revision_timestamp','raw_hash')}} for qid,item in sorted(items.items())]})
    return {'attempted':len(articles),'not_attempted':max(0,cohort_size-len(articles)),
        'outcome_counts':dict(Counter(a['outcome'] for a in articles)),'reason_counts':dict(Counter(a['reason'] for a in articles if a['reason'])),
        'pending_with_configured_root':sum(a['outcome']=='pending' and bool(a['configured_roots_present']) for a in articles),
        'retryable_pending':sum(a['outcome']=='pending' and a['retryable'] for a in articles),'articles':articles,
        'terminal_unresolved':sum(a['outcome']=='unresolved' for a in articles),
        'rule_version':rules['version'],'root_presence_alone_proves_completion':False}


def pair_generation_audit(records,pairs,rules):
    articles={a['page_id']:a for a in records if a.get('quality',{}).get('eligible')}
    edges=set()
    self_links=outside=0
    for source,a in articles.items():
        for link in a['extraction']['links']:
            if link['scope']!='retained_lead_prose' or link.get('target',{}).get('status')!='resolved':
                continue
            target=link['target']['page_id']
            if target==source:
                self_links+=1
            elif target in articles:
                edges.add((source,target))
            else:
                outside+=1
    blocks=comparison_blocks(articles)
    multi=sum(len(ids)>=2 for ids in blocks.values())
    reasons=[]
    if not edges: reasons.append('no_internal_nonself_lead_edge')
    if not multi: reasons.append('no_shared_subtype_attribute_block')
    return {'eligible_article_count':len(articles),'directed_internal_edge_count':len(edges),'directed_internal_edges':sorted(edges),
        'self_link_occurrences':self_links,'external_or_ineligible_link_occurrences':outside,
        'comparison_blocks':[{'subtype_id':key[0],'attribute_key':key[1],'value_kind':key[2],'page_ids':sorted(ids)} for key,ids in sorted(blocks.items())],
        'multi_article_comparison_block_count':multi,'generated_counts':dict(Counter(p['kind'] for p in pairs)),
        'positive_counts':dict(Counter(p['kind'] for p in pairs if p['score']>0)),
        'zero_pair_reasons':reasons if not pairs else [],'rule_version':rules['version'],'human_yield':'unassessed'}


class ProgressWindow:
    def __init__(self,target,window_seconds=60):
        self.target,self.window=target,window_seconds
        self.observations=deque()

    def observe(self,elapsed,processed):
        self.observations.append((elapsed,processed))
        while len(self.observations)>1 and self.observations[0][0]<elapsed-self.window:
            self.observations.popleft()
        first_time,first_count=self.observations[0]
        seconds=elapsed-first_time
        speed=(processed-first_count)/seconds if seconds>0 and processed>first_count else None
        return {'processed':processed,'target':self.target,'queued':max(0,self.target-processed),
            'speed_per_minute':speed*60 if speed else None,'eta_seconds':max(0,self.target-processed)/speed if speed else None,
            'window_seconds':self.window}


def performance_report(stages,elapsed,requests,cache,resources):
    stages={k:{**v,'new_articles_per_minute':60*v.get('new',0)/v['active_seconds'] if v.get('active_seconds') else None} for k,v in stages.items()}
    hits,lookups=cache.get('hits',0),cache.get('lookups',0)
    physical=sum(v for k,v in requests.items() if k.endswith(':requests'))
    retries=sum(v for k,v in requests.items() if k.endswith(':retries'))
    errors=sum(v for k,v in requests.items() if k.endswith(':errors'))
    new=stages.get('process',{}).get('new',0)
    reused=stages.get('process',{}).get('reused',0)
    eta={'status':'unavailable','seconds':None,'reason':'Insufficient cold observations, or warm resume; full-size selection cost remains unmeasured.'}
    if new>=1000 and reused==0 and physical and elapsed>0:
        seconds=elapsed*80000/new
        eta={'status':'estimate','seconds':seconds,'uncertainty_seconds':[seconds*.5,seconds*2],
            'observed_new_candidates':new,'request_fanout':physical/new,'assumptions':'Same cold-cache dependency fan-out, source latency, backoff and stage throughput; 0.5–2x sensitivity range is not a confidence interval. Full-size selection/memory cost is unmeasured.'}
    return {'stages':stages,'end_to_end_seconds':elapsed,'physical_requests':dict(requests),
        'cache':{**cache,'hit_rate':hits/lookups if lookups else None},'resources':resources,
        'request_rates':{'physical_request_count':physical,'retry_count':retries,'retry_denominator':physical,'retry_rate':retries/physical if physical else None,'error_count':errors,'error_denominator':physical,'error_rate':errors/physical if physical else None},
        'derived_cache':{name:{'hits':stage.get('reused',0),'lookups':stage.get('new',0)+stage.get('reused',0)+stage.get('failed',0)+stage.get('deferred',0),'hit_rate':stage.get('reused',0)/max(1,stage.get('new',0)+stage.get('reused',0)+stage.get('failed',0)+stage.get('deferred',0))} for name,stage in stages.items()},
        'production_eta':eta}


class ResourceSampler(AbstractContextManager):
    def __init__(self,root,interval=1):
        self.root=Path(root)
        self.interval=interval
        self.stop=threading.Event()
        self.samples=[]
        self.process=psutil.Process()

    def sample(self):
        try:
            cpu=self.process.cpu_times()
            io=self.process.io_counters()
            self.samples.append({'elapsed':time.perf_counter()-self.started,'rss_bytes':self.process.memory_info().rss,
                'cpu_seconds':cpu.user+cpu.system,'read_bytes':io.read_bytes,'write_bytes':io.write_bytes})
        except (psutil.Error,AttributeError):
            pass

    def __enter__(self):
        self.started=time.perf_counter()
        self.initial_disk=self.disk_usage()
        self.sample()
        def loop():
            while not self.stop.wait(self.interval):
                self.sample()
        self.thread=threading.Thread(target=loop,daemon=True)
        self.thread.start()
        return self

    def disk_usage(self):
        return sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file()) if self.root.exists() else 0

    def __exit__(self,*args):
        self.finish()

    def finish(self):
        if self.stop.is_set():return
        self.stop.set()
        self.thread.join(timeout=2)
        self.sample()

    def report(self):
        # Snapshot the series before JSON serialization while the sampler is live.
        samples=list(self.samples)
        first,last=(samples[0],samples[-1]) if samples else ({},{})
        elapsed=last.get('elapsed',0)-first.get('elapsed',0)
        cpu_seconds=last.get('cpu_seconds',0)-first.get('cpu_seconds',0)
        return {'sampling_interval_seconds':self.interval,'samples':samples,'peak_rss_bytes':max((s['rss_bytes'] for s in samples),default=None),
            'cpu_seconds':cpu_seconds,'cpu_utilization_percent':100*cpu_seconds/elapsed if elapsed>0 and self.samples else None,
            'io_read_bytes':last.get('read_bytes',0)-first.get('read_bytes',0),'io_write_bytes':last.get('write_bytes',0)-first.get('write_bytes',0),'available_measurements':bool(self.samples),
            'disk_growth_bytes':self.disk_usage()-self.initial_disk,'available_disk_bytes':psutil.disk_usage(self.root.resolve().anchor).free,
            'environment':{'python':platform.python_version(),'platform':platform.platform(),'cpu_count':os.cpu_count()},
            'measurement_scope':'This Python process; no local worker subprocesses launched'}
