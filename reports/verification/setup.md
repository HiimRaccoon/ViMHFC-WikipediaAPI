# Phase 1 — Setup verification (2026-10-25)

Scope: T001–T006 setup, not product UI or real acquisition.

- Python 3.12.10 (ignored workspace runtime), Node 24.14.0.
- `python -m pip install --no-index --no-build-isolation --no-deps .\backend`: built/installed vimfc 0.1.0; imports of FastAPI, Pydantic, HTTPX, mwparserfromhell, psutil and Uvicorn succeeded.
- `python -m pip check`: no broken requirements.
- Clean lockfile installation into isolated `.tools/clean-site` completed with all 26 pinned packages; imports from that target succeeded.
- `npm --prefix frontend ci --offline --ignore-scripts --no-audit --no-fund`: clean locked install, 63 packages.
- `npm --prefix frontend run typecheck`: exit 0.
- `npm --prefix frontend run build`: exit 0, Vite 7.3.7.
- `npm --prefix frontend run test`: configured Vitest 3.2.7, no test files yet. This checks the setup command only; it does not prove UI behavior.
- Backend tests discover and run offline. Real sockets are denied; marked network checks require explicit opt-in. No real-network tests were invoked.
- `.agents`, runtime data/DBs, detailed pilot telemetry and raw artifacts are ignored; curated verification and compact pilot summaries remain visible to Git. No corpus or pilot was started during setup.

Source/fixture provenance is in `tests/fixtures/manifest.json`; the paper and official source API/AST docs were read before methodology implementation. All test data is controlled synthetic data, not acquired Wikipedia history.
