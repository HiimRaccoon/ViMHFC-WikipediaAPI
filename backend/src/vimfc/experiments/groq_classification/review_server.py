"""Loopback-only review API; no source acquisition or provider routes."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .review_evaluation import evaluate_review
from .review_schema import ERROR_TAGS, MEMBERSHIPS, VERIFICATIONS
from .review_store import ReviewStore

STATIC = Path(__file__).with_name('review_static')
MAX_BODY = 8 * 1024 * 1024


def create_review_app(packet, workspace):
    store = ReviewStore(packet, workspace)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.review_store = store

    @app.middleware('http')
    async def local_only(request, call_next):
        host = request.headers.get('host', '')
        try:
            parsed = urlsplit('http://' + host)
            valid = parsed.hostname in ('127.0.0.1', 'localhost') and parsed.username is None and parsed.password is None
        except ValueError:
            valid = False
        origin = request.headers.get('origin')
        if not valid or (origin and origin != 'http://' + host) or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Only same-origin localhost access is allowed'}, status_code=403)
        if request.method == 'POST':
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'error': 'Use application/json'}, status_code=415)
            try:
                size = int(request.headers.get('content-length', '0'))
            except ValueError:
                size = MAX_BODY + 1
            if size > MAX_BODY:
                return JSONResponse({'error': 'Review import is too large'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(ValueError)
    async def invalid_review(request, error):
        return JSONResponse({'error': str(error)}, status_code=400)

    async def body(request, fields):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise ValueError('Request is too large')
        from .human_review import _json
        value = _json(raw.decode('utf-8'))
        if not isinstance(value, dict) or set(value) != set(fields):
            raise ValueError('Unexpected or missing request fields')
        return value

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/app.js')
    def javascript():
        return FileResponse(STATIC / 'app.js', media_type='text/javascript')

    @app.get('/style.css')
    def stylesheet():
        return FileResponse(STATIC / 'style.css', media_type='text/css')

    @app.get('/api/session')
    def session():
        return {'manifest_id': packet['manifest']['manifest_id'], 'order': list(packet['references']),
                'progress': store.progress(), 'taxonomy': packet['taxonomy'], 'memberships': MEMBERSHIPS,
                'error_tags': ERROR_TAGS, 'verifications': VERIFICATIONS,
                'guideline': (packet['root'] / 'blind/INSTRUCTIONS.md').read_text(encoding='utf-8')}

    @app.get('/api/articles/{rid}')
    def article(rid: str):
        ref = store._reference(rid)
        return {'reference': ref, 'current': store.current(rid), 'audit': store.current(rid, 'audit'),
                'comparison_exposed': store.was_exposed(rid)}

    @app.post('/api/articles/{rid}/semantic')
    async def save(rid: str, request: Request):
        value = await body(request, ('expected_revision', 'payload'))
        return store.save(rid, value['payload'], expected_revision=value['expected_revision'])

    @app.get('/api/articles/{rid}/comparison')
    def comparison(rid: str):
        store.mark_exposed(rid)
        return {'comparison': packet['comparisons'][rid], 'historical_evidence': packet['evidence'][rid],
                'human': store.current(rid), 'note': 'Groq is a suggestion. Semantic labels do not promote historical classification.'}

    @app.post('/api/articles/{rid}/audit')
    async def save_audit(rid: str, request: Request):
        value = await body(request, ('expected_revision', 'expected_semantic_revision', 'payload'))
        return store.save_audit(rid, value['payload'], expected_revision=value['expected_revision'],
                               expected_semantic_revision=value['expected_semantic_revision'])

    @app.get('/api/articles/{rid}/history')
    def history(rid: str):
        return store.history(rid)

    @app.get('/api/export')
    def export(format: str = 'jsonl'):
        data = store.export(format)
        return Response(data, media_type='text/csv; charset=utf-8' if format == 'csv' else 'application/x-ndjson',
                        headers={'Content-Disposition': f'attachment; filename="human-review.{format}"'})

    @app.post('/api/import')
    async def import_labels(request: Request):
        value = await body(request, ('format', 'content'))
        if not isinstance(value['content'], str):
            raise ValueError('Import content must be text')
        return {'imported': store.import_labels(value['content'].encode('utf-8'), value['format'])}

    @app.get('/api/evaluation')
    def evaluation():
        return evaluate_review(packet, *store.snapshot())

    return app
