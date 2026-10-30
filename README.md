# ViMFC Wikipedia corpus builder

Implementation follows [docs/tasks.md](docs/tasks.md) and [the agreed requirements](docs/docs.md).

Forward corpus collection uses **Stratified Seed-only Discovery**: candidate identities now come from [verified official dump metadata](docs/dump-inventory.md), consumed by unchanged [seed-only-2](docs/seed-only.md). It retains qid-5/lead-4/quality-2.1/rank-1, eight sampling quotas and historical evidence guards. [Legacy/Hybrid experiment commands](docs/discovery-experiments.md) remain for historical reproducibility; no further comparison arms are planned. Inventory acquisition never starts a pilot or authorizes Production.
The current builder defaults use historical Primary-First, Optional-Subtype classification (`qid-5`), `lead-4`, and `quality-2` with explicit revision `quality-2.1` (`pipeline-6.1`). New-rule source validation uses an isolated replay run only when explicitly requested. Existing Pilot 1,000 resume contexts retain their pinned lead-3 rules, durable deferrals, bounded retries and explicit source completeness. See [Windows resume commands](docs/pilot1000-resume.md) for that historical run. Earlier assessments remain historical records. Annotation and UI features in T054+ are not implemented yet; the frontend directory is setup only (T002).

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

Current classification policy remains **Primary-First, Optional-Subtype**, `qid-5`. Historical P31/P279 proves required primary; P106 may support Person subtypes but never primary by itself. Optional subtype incompleteness does not reject a proven primary. Required primary conflicts remain unresolved unless recorded global-precedence dominance proves the decision safe. Depth 12, union budget 2,048, quality thresholds, rank-1 and exact 20,000 gate remain unchanged. Immutable role-aware manifests bind input revisions/hashes; cache warmth never expands the semantic graph. Bridge needs no subtype; Comparison/Same Entity Type still require a positively supported shared subtype. New extraction/quality semantics require a new run; historical handoffs/assessments retain their archived versions.

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

The forward `seed-only-2` entry point samples a frozen `dump-inventory-1` of official page/category metadata with deterministic soft quotas. Dump metadata schedules candidates only; historical identity, namespace, sitelink and classification proofs remain mandatory. The API-listing checkpoint and older collectors are retained for historical compatibility without merging dump input into them. Unknown factual templates remain unresolved. Quality/comparison signals use explicit archived rules; version changes require a new run.

## Data and reproducibility artifacts

Builder state is `data/pipeline.sqlite`, with compressed hash-verified historical objects under `data/raw`. A frozen transfer package contains standalone `corpus.sqlite` and `manifest.json`; never copy an active WAL database. Each annotator will use their own `annotations.sqlite`. Ordinary annotation must never initiate collection.

`.agents/` is local and ignored. All local DB/sidecars, data/raw/corpus packages, bulk exports, build outputs, environments and raw logs are ignored. Curated verification reports/audits, `reports/pilots/<run>/assessment.md`, and cohort `summary.md`/compact `summary.json` may be committed; detailed telemetry, cohort manifests and raw template audits are builder artifacts. Do not put raw data inside allowlisted summaries. The [review evidence bundle](reports/verification/pilot100-review-evidence.json) preserves selected archived measurements, version identities, checksums and classification transitions for remote review without publishing private contact configuration or the raw cache. Historical `qid-3`/`lead-3` cached retrospective results must not be confused with measurements of the round-2 `qid-2`/`lead-2` run or the current qid-5 policy.

## Research boundaries

The [ViMQA paper](https://aclanthology.org/2022.lrec-1.700.pdf) was read for Section 2, Figures 2–3, Table 3 and Section 4.2; the original QA methodology and this project's adaptations remain separate in [research_vimqa.md](docs/research_vimqa.md). Historical raw wikitext, not live template rendering, supplies all retained lead prose and operational links. Wikidata only classifies entities. Scores estimate partner potential and never create claims, labels, hops, or human quality assessments.

## Optional Groq classification experiment

The separate [Windows/local Groq workflow](docs/groq-classification.md) prepares historical inputs, validates text-grounded suggestions and exports human review. It does not change Wikidata-verified labels, article eligibility or the accepted graph; no GPU is required.

The [local Human Review UI](docs/groq-human-review-ui.md) provides blind semantic labeling, durable revision history, historical evidence audit and partitioned RQ1/RQ2/RQ3 evaluation for saved responses. Start it with `python scripts/groq_review.py serve --profile configs/groq-review.local.json`; preparation, review and evaluation require no Groq API calls. The original corpus-annotation frontend roadmap remains separate from this experiment-only interface.
