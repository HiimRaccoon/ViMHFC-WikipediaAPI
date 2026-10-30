import json

from fastapi.testclient import TestClient
import pytest
import socket

from tests.integration.test_groq_review_packet import make_packet
from tests.integration.test_groq_review_store import semantic, audit


@pytest.fixture(autouse=True)
def offline_loopback_runtime(deny_network):
    # Windows asyncio uses stdlib socketpair's loopback self-pipe. The actual
    # service guard allows only that exact stdlib caller, never HTTP sockets.
    from vimfc.experiments.groq_classification.review_offline import offline_review_network
    with offline_review_network():
        yield


def test_service_guard_still_denies_outbound_and_arbitrary_loopback():
    with socket.socket() as sock:
        for address in [('127.0.0.1', 9999), ('8.8.8.8', 443)]:
            with pytest.raises(RuntimeError, match='offline'):
                sock.connect(address)
    with pytest.raises(RuntimeError):
        socket.getaddrinfo('api.groq.com', 443)


def client(tmp_path):
    from vimfc.experiments.groq_classification.review_server import create_review_app
    p = make_packet(tmp_path)
    app = create_review_app(p, tmp_path / 'workspace')
    return TestClient(app, base_url='http://127.0.0.1:8765'), p


def test_blind_http_contract_and_comparison_lock(tmp_path):
    c, p = client(tmp_path)
    session = c.get('/api/session').json()
    assert session['progress']['reviewed'] == 0
    rid = session['order'][0]
    article = c.get('/api/articles/' + rid)
    assert article.status_code == 200
    body = article.json()
    assert body['reference'] == p['references'][rid]
    forbidden = {'predicted_group', 'baseline_primary', 'baseline_outcome', 'classification_reason',
                 'groq_reason', 'partition', 'stratum', 'sampling_stratum', 'request_key', 'primary_provenance'}
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(v) for v in value.values()), set())
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value), set())
        return set()
    assert not forbidden & keys(body)
    assert not forbidden & keys(session)
    assert c.get(f'/api/articles/{rid}/comparison').status_code == 400
    assert c.post(f'/api/articles/{rid}/audit', json={'expected_revision': 0, 'expected_semantic_revision': 0, 'payload': audit()}).status_code == 400
    saved = c.post(f'/api/articles/{rid}/semantic', json={'expected_revision': 0, 'payload': semantic()})
    assert saved.status_code == 200 and saved.json()['revision'] == 1
    comparison = c.get(f'/api/articles/{rid}/comparison')
    assert comparison.status_code == 200 and 'baseline_outcome' in comparison.json()['comparison']
    assert c.get(f'/api/articles/{rid}').json()['comparison_exposed'] is True
    assert c.post(f'/api/articles/{rid}/semantic', json={'expected_revision': 0, 'payload': semantic()}).status_code == 400
    assert len(c.get(f'/api/articles/{rid}/history').json()) == 1
    assert c.get('/api/session').json()['progress']['reviewed'] == 1


@pytest.mark.parametrize('headers', [
    {'Host': 'evil.example'}, {'Origin': 'https://evil.example'},
    {'Origin': 'http://localhost:9999'}, {'Sec-Fetch-Site': 'cross-site'},
])
def test_localhost_api_rejects_foreign_origin_and_host(tmp_path, headers):
    c, p = client(tmp_path)
    assert c.get('/api/session', headers=headers).status_code == 403
    assert c.post('/api/import', headers=headers, json={'format': 'jsonl', 'content': ''}).status_code == 403


def test_localhost_assets_import_export_metrics_and_no_source_routes(tmp_path):
    c, p = client(tmp_path)
    page = c.get('/')
    assert page.status_code == 200 and 'text/html' in page.headers['content-type']
    assert 'Content-Security-Policy' in page.headers and 'no-store' in page.headers['cache-control']
    assert c.get('/app.js').status_code == 200
    assert c.get('/style.css').status_code == 200
    for path in ('/api/.env', '/coordinator/comparison.jsonl', '/source', '/docs', '/openapi.json'):
        assert c.get(path).status_code == 404
    assert c.get('/api/evaluation').json()['status'] == 'UNASSESSED'
    exported = c.get('/api/export?format=jsonl')
    assert exported.status_code == 200
    assert len(exported.text.splitlines()) == 90
    assert c.post('/api/import', json={'format': 'jsonl', 'content': exported.text}).json()['imported'] == 0
    assert c.get('/api/session').json()['progress']['reviewed'] == 0
    rid = next(iter(p['references']))
    assert c.post(f'/api/articles/{rid}/semantic', json={'expected_revision': 0, 'payload': semantic()}).status_code == 200
    assert c.get('/api/evaluation').json()['reviewed_count'] == 1
    assert c.post(f'/api/articles/{rid}/semantic', content='{}', headers={'content-type': 'text/plain'}).status_code == 415
