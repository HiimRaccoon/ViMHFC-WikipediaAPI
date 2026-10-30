"""Synthetic-only browser fixture; never opens the real review profile."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend/src')]

from tests.integration.test_groq_review_packet import make_packet
from vimfc.experiments.groq_classification.review_offline import offline_review_network
from vimfc.experiments.groq_classification.review_server import create_review_app
import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8766)
args = parser.parse_args()
root = ROOT / '.tools/groq-review-ui' / ('browser-fixture-' + uuid4().hex)
root.mkdir(parents=True)
packet = make_packet(root)
with offline_review_network():
    app = create_review_app(packet, root / 'workspace')
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, log_level='warning'))

    @app.post('/__fixture/shutdown')
    def shutdown_fixture():
        # Test-fixture route only: avoid relying on Windows process-tree kill
        # permissions when Playwright finishes. Absent from the real app.
        server.should_exit = True
        return {'status': 'synthetic-fixture-stopping'}

    server.run()
