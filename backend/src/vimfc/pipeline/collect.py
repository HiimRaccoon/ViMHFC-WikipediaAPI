"""Archive complete continuation/buffer state; live listings are hints only."""
from vimfc.domain import digest, SourceUnavailable


class CandidateCollector:
    def __init__(self,client,identities,config):
        self.client,self.identities,self.config=client,identities,config
        self.definitions=config['streams']+[{'id':'prefix:'+prefix,'kind':'allpages','prefix':prefix} for prefix in config.get('namespace_prefixes',[])]

    def discover(self,state,limit):
        state={**state}
        streams=state.setdefault('streams',{s['id']:{'continuation':{},'buffer':[],'exhausted':False} for s in self.definitions})
        index=state.get('round_robin',0)
        output=[]
        while len(output)<limit and not all(s['exhausted'] and not s['buffer'] for s in streams.values()):
            definition=self.definitions[index%len(self.definitions)]
            index+=1
            stream=streams[definition['id']]
            if not stream['buffer'] and not stream['exhausted']:
                if definition['kind']=='categorymembers':
                    params={'action':'query','list':'categorymembers','cmtitle':definition['title'],'cmnamespace':0,'cmtype':'page','cmlimit':50}
                    key='categorymembers'
                else:
                    params={'action':'query','list':'allpages','apnamespace':0,'aplimit':50,'apprefix':definition.get('prefix','')}
                    key='allpages'
                payload=self.client.query('viwiki',{**params,**stream['continuation']})
                continuation=payload.get('continue')
                if continuation is not None and continuation==stream.get('last_continuation'):
                    raise SourceUnavailable('Discovery cursor did not advance')
                stream['last_continuation']=continuation
                stream['continuation']=continuation or {}
                stream['exhausted']=continuation is None
                stream['buffer']=sorted(payload.get('query',{}).get(key,[]),key=lambda p:(digest([self.config['seed'],p['pageid']]),p['pageid']))
            if stream['buffer']:
                hint=stream['buffer'].pop(0)
                result=self.identities.resolve(hint['title'])
                if result['status']=='resolved':
                    output.append({'page_id':result['page_id'],'title':result['title'],'discovery_origin':definition['id'],'lookup_title':hint['title'],'identity_proof':result})
                else:
                    state.setdefault('unresolved_lookups',[]).append({'title':hint['title'],'resolution':result})
        state['round_robin']=index
        return output,state,all(s['exhausted'] and not s['buffer'] for s in streams.values())
