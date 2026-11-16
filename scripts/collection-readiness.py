"""Explicit preparation and bounded collection commands; no automatic progression."""
import argparse
import json
import os
from pathlib import Path
import platform
import signal
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))
from vimfc.domain import canonical, Incompatible
from vimfc.pipeline.collection_bundle import export_bundle, read_bundle
from vimfc.pipeline.incremental_collection import prepare_collection, collect_batch


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    bundle = sub.add_parser('bundle')
    for option in ('context', 'inventory', 'integrity-registry', 'output'):
        bundle.add_argument('--' + option, required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--bundle', required=True)
    prepare.add_argument('--output', required=True)
    prepare.add_argument('--request-limit', type=int, required=True)
    batch = sub.add_parser('batch')
    batch.add_argument('--bundle', required=True)
    batch.add_argument('--workspace', required=True)
    batch.add_argument('--mode', choices=['acquire', 'process'], required=True)
    batch.add_argument('--through', type=int, required=True)
    batch.add_argument('--batch-size', type=int, default=250)
    batch.add_argument('--max-requests', type=int, default=1000)
    batch.add_argument('--allow-network', action='store_true')
    check = sub.add_parser('preflight')
    check.add_argument('--bundle', required=True)
    check.add_argument('--workspace-parent', required=True)
    check.add_argument('--min-free-gib', type=float, default=20)
    args = parser.parse_args(argv)
    if args.action == 'bundle':
        context_path = Path(args.context).resolve()
        context = json.loads(context_path.read_text(encoding='utf-8-sig'))
        for key in ('database', 'raw_cache'):
            context[key] = str((context_path.parent / context[key]).resolve())
        result = export_bundle(context, args.inventory, args.integrity_registry, args.output)
        print(canonical({'bundle_id': result['bundle_id'], 'files': len(result['files'])}))
    elif args.action == 'prepare':
        print(canonical(prepare_collection(args.bundle, args.output, request_limit=args.request_limit)))
    elif args.action == 'preflight':
        manifest, _, _ = read_bundle(args.bundle)
        parent = Path(args.workspace_parent).resolve()
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK) or args.min_free_gib <= 0:
            raise Incompatible('Workspace parent must exist and permit write/search')
        free = shutil.disk_usage(parent).free
        if free < args.min_free_gib * 1024 ** 3:
            raise Incompatible('Insufficient free storage')
        inodes = os.statvfs(parent).f_favail if hasattr(os, 'statvfs') else None
        if inodes is not None and inodes < 100000:
            raise Incompatible('Insufficient free inodes for article/cache objects')
        print(canonical({'bundle_id': manifest['bundle_id'], 'platform': platform.system(),
                         'python': platform.python_version(), 'free_bytes': free, 'free_inodes': inodes,
                         'quota_status': 'REQUIRES_UIT_CONFIRMATION', 'network_status': 'NOT_PROBED',
                         'filesystem_locking': 'REQUIRES_UIT_VALIDATION', 'gpu_required': False}))
    else:
        if args.mode == 'process':
            os.environ['VIMFC_OFFLINE'] = '1'
        result = collect_batch(args.bundle, args.workspace, mode=args.mode, through=args.through,
                               batch_size=args.batch_size, max_requests=args.max_requests,
                               allow_network=args.allow_network)
        if result['selection'] is not None:
            workspace = Path(args.workspace)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=workspace,
                    prefix='selection-', suffix='.pending', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(canonical(result['selection'])); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(workspace / 'selection.json')
        print(canonical(result['summary']))
    return 0


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Scheduler requested stop; committed checkpoints retained')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(canonical({'stopped': True, 'reason': 'interrupted', 'checkpoint_retained': True}), file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(canonical({'stopped': True, 'type': type(error).__name__, 'error': str(error)}), file=sys.stderr)
        raise SystemExit(getattr(error, 'exit_code', 3))
