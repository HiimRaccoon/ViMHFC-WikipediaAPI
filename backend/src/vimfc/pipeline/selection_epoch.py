"""Witnessed selection binding for a private restored fixture."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

from vimfc.domain import Incompatible, canonical, digest
from vimfc.source.identities import normalize_title
from . import generation_publication as publication
from . import local_snapshot as snapshot
from .annotation_edges import edge_report, rankable_links, select_articles
from .hyperlink_rank import graph_metrics
from .incremental_collection import CollectionArchive
from .relaxed_pilot import relaxed_rules
from .transactional_control import _transaction
from .trusted_control import require
from . import selection_contract
from . import annotation_edges


def policy_fingerprint(config, runtime):
    """Trusted setup supplies current config and runtime, independently of the envelope."""
    hashes = {k: digest(config[k]) for k in ('classification', 'extraction', 'ranking')}
    hashes.update(quality=digest(relaxed_rules(config)), quotas=digest(config['quotas']),
                  edge=digest(annotation_edges.VERSION), selection_ranking=digest(annotation_edges.RANK_VERSION))
    # Bind the small local selector implementation as well as its declared policy versions.
    sources = {name: snapshot.file_hash(Path(__file__).with_name(name + '.py')) for name in (
        'selection_epoch', 'annotation_edges', 'select', 'connectivity_rank', 'hyperlink_rank',
        'quality', 'extract', 'relaxed_pilot')}
    return {'identity': digest({'runtime': runtime, 'hashes': hashes, 'sources': sources}), 'hashes': hashes}


class SyntheticSelection:
    """Trusted fixture setup supplies the same generation and bundle pins as activation."""

    def __init__(self, authority, root, *, target, compatible, bundle, trusted_policy=None):
        self.authority = authority
        self.root = Path(root)
        self.target = deepcopy(publication.pointer(target))
        self.compatible = deepcopy(compatible)
        self.bundle = Path(bundle)
        self.trusted_policy = trusted_policy
        self._revalidated = set()

    def _policy(self):
        require(callable(self.trusted_policy), 'Trusted current policy identity required')
        current = deepcopy(self.trusted_policy())
        selection_contract.policy(current)
        return current

    def _selection(self):
        manifest = publication.verify_bytes(self.root, self.target)
        require(manifest['compatibility'] == self.compatible, 'Selection generation compatibility differs')
        require('selection.json' in manifest['artifacts'], 'Selection absent from trusted generation')
        value = publication.read_canonical(self.root / 'selection.json')
        require(digest(value) == manifest['artifacts']['selection.json']['sha256'], 'Selection bytes differ')
        return value

    def _activation(self, model):
        view = model.inspect()
        require(view['eligible'] and view['state']['owner']['state'] == 'RUNNING',
                'Acknowledged active owner required')
        state = view['state']
        require(state['runtime'] == self.compatible['runtime']
                and all(self.target['identity'][k] == v for k, v in state['binding'].items())
                and state['current'] in (None, self.target)
                and (state['publication'] is None or state['publication']['state'] == 'RECONCILED_COMMITTED'),
                'Active generation or runtime differs')
        launch = next((r for r in reversed(model.records) if r['action'] in ('launch', 'activate')), None)
        require(launch is not None and launch['action'] == 'activate'
                and launch['state']['owner'] == state['owner']
                and launch['payload']['activation']['generation'] == self.target,
                'Selection requires matching activation')
        return {'decision_id': launch['decision_id'], 'record_digest': digest(launch),
                'epoch': state['owner']['epoch']}

    def bind(self, decision_id, expected_version):
        current_policy = self._policy()
        value = self._selection()
        with _transaction(self.authority.path) as db:
            activation = self._activation(self.authority._load(db))
        policy = {}

        def revalidate(checked, header, rows):
            records = [json.loads(row['record_json']) for row in rows if row['status'] == 'processed']
            with CollectionArchive(checked.context) as archive:
                archive.registry = checked.registry
                try:
                    archive.collection_targets = {}
                    for record in records:
                        if 'redirect' not in record['article_kinds']:
                            archive.collection_targets.setdefault(normalize_title(record['title']), []).append(record)
                    quotas = archive.config['quotas']
                    replay = select_articles(records, archive, quotas, target=header['target'])
                    replay['selection'] = asdict(replay['selection'])
                    selected = set(replay['selection']['page_ids'])
                    replay['graph'] = graph_metrics(rankable_links(
                        a for a in replay['articles'] if a['page_id'] in selected))
                    replay['edges'] = edge_report(replay['articles'])
                    replay['pilot_accepted'] = False
                    require(digest(replay) == digest(value), 'Selection differs from current policy replay')
                    policy.update(quotas=digest(quotas), quality=digest(relaxed_rules(archive.config)),
                                  edge=digest(replay['policy']), selection_ranking=digest(replay['ranking_policy']))
                finally:
                    archive.registry = None

        checkpoint = snapshot.verify_generation(self.root, trusted=self.target, bundle=self.bundle,
                                                compatible=self.compatible, check=revalidate)
        binding = {'format': 'synthetic-selection-epoch-1', 'generation': self.target,
                   'checkpoint_sha256': checkpoint['checkpoint_hash'], 'selection_sha256': digest(value),
                   'policy_hashes': {**checkpoint['policy_hashes'], **policy}, 'activation': activation,
                   'policy_identity': current_policy['identity']}
        selection_contract.binding(binding)
        require(binding['policy_hashes'] == current_policy['hashes']
                and self._policy() == current_policy, 'Trusted current policy differs')
        # Witnessed assertions alone cannot certify that this gate actually replayed selection.
        self._revalidated.add(digest(binding))

        def check(model):
            require(self._activation(model) == activation, 'Activation changed during binding')
            require(not any(r['action'] == 'bind_selection' and r['payload']['activation'] == activation
                            for r in model.records), 'Selection already bound for activation')

        self.authority._decide(decision_id, expected_version, 'bind_selection', binding, check)
        return canonical({'decision_id': decision_id, 'binding': binding})

    def consume(self, encoded=None):
        require(type(encoded) is str, 'Selection binding required')
        try:
            envelope = json.loads(encoded)
        except ValueError as error:
            raise Incompatible('Invalid canonical selection binding') from error
        require(type(envelope) is dict and set(envelope) == {'decision_id', 'binding'}
                and type(envelope['decision_id']) is str and canonical(envelope) == encoded,
                'Invalid canonical selection binding')
        selection_contract.binding(envelope['binding'])
        current_policy = self._policy()
        require(envelope['binding']['policy_identity'] == current_policy['identity']
                and envelope['binding']['policy_hashes'] == current_policy['hashes'],
                'Trusted current policy differs')
        require(digest(envelope['binding']) in self._revalidated, 'Independent revalidation approval required')
        value = self._selection()
        manifest = publication.read_canonical(self.root / 'manifest.json')
        require(envelope['binding']['checkpoint_sha256'] == manifest['checkpoint']['checkpoint_hash'],
                'Binding checkpoint differs')
        with _transaction(self.authority.path) as db:
            model = self.authority._load(db)
            activation = self._activation(model)
            entry = model.decisions.get(envelope['decision_id'])
            require(entry is not None and entry['action'] == 'bind_selection'
                    and digest(entry['payload']) == digest(envelope['binding']),
                    'Selection binding is not witnessed')
            binding = entry['payload']
            selection_contract.binding(binding)
            require(binding['activation'] == activation and binding['generation'] == self.target
                    and binding['selection_sha256'] == digest(value), 'Selection binding is stale or mismatched')
        return value

