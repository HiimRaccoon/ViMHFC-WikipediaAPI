"""Run the explicitly invoked offline Annotation Collection V2 builder."""
import argparse
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))
from vimfc.domain import canonical
from vimfc.pipeline.runner import Builder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--output', required=True, help='New isolated output, or its compatible checkpoint for resume')
    parser.add_argument('--integrity-registry', required=True)
    parser.add_argument('--target', type=int, default=20000)
    args = parser.parse_args()
    def denied(*args, **kwargs):
        raise RuntimeError('Annotation collection is offline')
    socket.socket.connect = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    context = json.loads(Path(args.context).read_text(encoding='utf-8'))
    result = Builder.annotation_collection(context, args.output,
        registry_path=args.integrity_registry, target=args.target)
    print(canonical({'manifest': result['manifest'], 'metrics': result['metrics'],
                     'blockers': result['blockers'], 'pilot_accepted': False}))


if __name__ == '__main__':
    main()
