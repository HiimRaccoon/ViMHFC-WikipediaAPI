# ViMFC Wikipedia corpus builder

Implementation follows [docs/tasks.md](docs/tasks.md) and [the agreed requirements](docs/docs.md).
The builder and offline remediation after two authorized Pilot 100 runs are implemented. The latest repair passes 177 offline backend tests; the user has not yet run its new-rule replay. See the [round-2 assessment](reports/pilots/9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e/assessment.md), [offline repair acceptance](reports/verification/pilot2-remediation.md), and [operator commands](docs/pilot100-replay.md). Pilot 1,000 and Production remain unapproved; no research corpus has been delivered. Annotation and UI features in T054+ are not implemented yet; the frontend directory is setup only (T002).

## Windows development setup

Use standard Python **3.12+**, PowerShell, and Node **22.12+** (tested locally with Node 24.14.0). The frontend locks Vite 7; its [supported Node baseline](https://vite.dev/guide/) includes Node 22.12+. No Docker is required.

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.lock
python -m pip install --no-build-isolation --no-deps -e backend
npm --prefix frontend ci
python -m vimfc --help
python -m pytest
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
```

Default tests deny real socket connections and use synthetic historical inputs, never live Wikipedia. The Playwright script/config is prepared, but no browser scenarios exist before the later frontend tasks; an empty test suite is not application acceptance. Do not use fixture databases for research annotation.

This machine's registered system Python was inaccessible. Session verification uses an ignored local Python 3.12.10 embedded runtime in `.tools/python312`; this is a development workaround, not the normal installation path. Embedded Python requires `--no-build-isolation` for installing this package. Verification scratch files remain under `.tools`.

For this workspace runtime, use `& .\.tools\python312\python.exe -m pytest --basetemp .tools/test-run-<unique-name>` so tests stay within writable workspace paths. Reusing another process's test directory can cause Windows ACL/cleanup errors. All default fixture tests are offline.

## Builder commands and manual stops

Run creation, status and reports are local-only commands. A snapshot is always supplied explicitly:

```powershell
python -m vimfc pipeline create --snapshot 2026-10-14T00:00:00Z --config configs/pipeline.json
python -m vimfc pipeline status --run <run-id>
python -m vimfc pipeline report --run <run-id>
```

Configure a real Wikimedia API contact and User-Agent in the pipeline config before creating an acquisition run; defaults deliberately contain no invented contact. The example date is not an automatically chosen snapshot. Config and rule contents are archived and immutable for each run. If configuration changes, create a new run with `--reuse-run <old-run>`; compatible same-snapshot discovery cohorts/cursors and raw inputs can be reused, while affected derived stages are recalculated.

**Respect the manual pilot gate.** The original T049 Pilot 100 and the user's round-2 replay are recorded in their archived assessments. Subsequent source execution still requires an explicit user invocation; completed code/tests/reports do not authorize Pilot 1,000 or Production. Each bounded invocation writes metrics, performance, template/classification/pair audits and a readable summary in `reports/pilots/<id>/<size>/`. Remediation tests and retrospective analyses use local fixtures/cache without source requests. Fixture-controlled pilot orchestration tests are synthetic and do not release any execution gate.

After the final pilot assessment, stop again for T097's separate production decision. A deliberate future `pipeline run --run <id> --production --pilot-report <reviewed-10000-metrics.json>` checks archived report/run/config identities and critical defects; a report alone does not grant permission. `pipeline retry` only requeues eligible pending/transient work and never starts acquisition. `pipeline extend` is denied before authorized production.

`pipeline freeze --run <id> --output data/corpora` is reserved for authorized production and requires exactly 20,000 eligible selected articles. It publishes a new standalone package and never overwrites one. `corpus verify --corpus <package-directory>` rechecks hashes, source/classification/quality/sentence invariants and every operational graph table. A package from tests is not the research corpus.

The default discovery schedule interleaves category seeds with namespace prefixes; current listings schedule candidates only. Historical identity, sitelink and classification proofs remain mandatory. Unknown factual templates quarantine articles. Quality/comparison signals use explicit archived rules; version changes require a new run.

## Data and reproducibility artifacts

Builder state is `data/pipeline.sqlite`, with compressed hash-verified historical objects under `data/raw`. A frozen transfer package contains standalone `corpus.sqlite` and `manifest.json`; never copy an active WAL database. Each annotator will use their own `annotations.sqlite`. Ordinary annotation must never initiate collection.

`.agents/` is local and ignored. All local DB/sidecars, data/raw/corpus packages, bulk exports, build outputs, environments and raw logs are ignored. Curated verification reports/audits, `reports/pilots/<run>/assessment.md`, and cohort `summary.md`/compact `summary.json` may be committed; detailed telemetry, cohort manifests and raw template audits are builder artifacts. Do not put raw data inside allowlisted summaries. The [review evidence bundle](reports/verification/pilot100-review-evidence.json) preserves selected archived measurements, version identities, checksums and classification transitions for remote review without publishing private contact configuration or the raw cache. Current `qid-3`/`lead-3` cached retrospective results must not be confused with measurements of the round-2 `qid-2`/`lead-2` run.

## Research boundaries

The [ViMQA paper](https://aclanthology.org/2022.lrec-1.700.pdf) was read for Section 2, Figures 2–3, Table 3 and Section 4.2; the original QA methodology and this project's adaptations remain separate in [research_vimqa.md](docs/research_vimqa.md). Historical raw wikitext, not live template rendering, supplies all retained lead prose and operational links. Wikidata only classifies entities. Scores estimate partner potential and never create claims, labels, hops, or human quality assessments.
