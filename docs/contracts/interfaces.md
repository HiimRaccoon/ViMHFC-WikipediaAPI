# API and Command Contracts

**Plan:** [../plans.md](../plans.md)  
**Status:** Implemented builder classification contracts reflect qid-5; legacy frozen packages remain readable. Other research/annotation boundaries are unchanged.

## Local HTTP Interface

Separate builder experiment CLI: `scripts/discovery-experiment.py offline --context <source-context> --output-dir <NEW-folder>` performs cache-only validation/association audit/review79 generation/three-arm archived comparison, with sockets denied. `prepare --context <source-context> --inventory <inventory.json> --plan <cohort.json> --output-dir <NEW-isolated-folder> [--private-cache] [--source-review <old-approval>]` creates a separate run/context after verifying inputs; an old approval yields only an inactive draft for new scope. `run --context <experiment-context> --size 100|1000 --max-requests <1..10000> [--max-wait-seconds <0..3600>] [--retry-rounds <0..5>] [--reviewed-quarantine <new-approved-review>]` is the only source-using operation. `status`, `report --size <size> --output <NEW-json>` and `compare --contexts <legacy> <seed-only> <hybrid> --size <size> --output <NEW-json>` are read-only apart from new output files. PowerShell wrapper: `Invoke-DiscoveryPilot.ps1 -ContextFile <context> -Size 100|1000 -MaxRequests <cap> -MaxWaitSeconds <budget> -RetryRounds <rounds> [-ReviewedQuarantine <review>]`. See [exact commands](../discovery-experiments.md). No HTTP/annotation contract changes.

Ordinary startup serves the built frontend and API on `http://127.0.0.1:8000`. Development may use Vite on port 5173 with a configured local API proxy. No source collection endpoint is exposed by the annotation API.

Responses use UTF-8 JSON; typed errors have `{ "error": { "code": "...", "message": "Vietnamese explanation", "details": {} } }`. Unknown request fields are rejected where they could change annotation/source semantics.

Builder defaults now use lead-4/quality-2/pipeline-6 with unchanged qid-5. Versioned quality signals carry literal sentence offsets and optional date/unit spans; `text`/`quantity` values are not year Comparison evidence. Legacy rules/results remain valid under their archived version. This adds no annotation request fields and changes no claim/label/hop contract. `scripts/retrospective-extraction-quality.py --database <db> --run <archived-run> --config configs/pipeline.json --raw-cache <raw-root> --controls tests/fixtures/factual_signal_controls.json --output <NEW-json-path>` is offline analysis, requires the archived 1,000-candidate cohort, refuses output overwrites and never creates a run or invokes source acquisition. Cached-only pair counts must disclose unverified target resolutions.

Frozen article classification requires a proven primary group; `subtypes: []` is valid. qid-5 adds explicit primary/subtype status/provenance, policy and manifest identity to the existing classification JSON. Consumers must not treat incomplete optional search as unresolved primary. Same Entity Type/Comparison return only supported subtype matches and never broaden to primary; primary-only articles remain eligible for Bridge/Shared Entity/Random Article if other rules pass. Annotation evidence/label/hop/export schema is unchanged. See [data model](../data-model.md#versioned-classification-json).

| Method and route | Request / result |
| --- | --- |
| `GET /api/health` | Readiness and frozen corpus_version; no enumeration of raw candidate data |
| `GET /api/corpus` | Corpus version, snapshot, frozen status, count and display-relevant group/subtype summary |
| `POST /api/sessions` | Empty body; create fresh UUID session and return session_id, corpus_version, created_at, sample_count=0 |
| `GET /api/sessions/{session_id}` | Current stored session metadata and successful sample count; unknown session -> 404 |
| `GET /api/articles/random` | Optional repeated exclude_page_id filters; uniformly choose an eligible frozen article |
| `GET /api/articles/{page_id}` | Frozen title, page/revision/source metadata, primary type/subtypes, full lead and all stable sentences |
| `GET /api/articles/{page_id}/shared-entities` | Stored entities linked from retained lead prose, including outside-corpus targets; stable target_key |
| `GET /api/articles/{page_id}/candidates` | mode, optional entity_key/subtype_id, cursor, limit, exclude_page_id; local paginated eligible candidates |
| `GET /api/articles/{page_id}/candidates/random` | Same mode/entity/subtype/exclusion filters; uniform sample from the entire eligible candidate set, not just a result page |
| `POST /api/sessions/{session_id}/samples` | Structural Submit contract below; return persisted benchmark record and stored session sample_count |
| `GET /api/sessions/{session_id}/export` | JSONL download for that session using the common serializer; UI invokes only its current session |

Candidate `mode` is one of `hyperlink`, `shared_entity`, or `same_entity_type`. Shared Entity requires a target_key actually linked from the source lead; Same Entity Type requires a supported source subtype_id. Unsupported filter values -> 422. Valid filters without matches -> 200 with `items: []` and a Vietnamese no-results message. Operational failure -> typed error, never a fabricated candidate.

Pagination defaults to 25 and is capped at 100. Cursors preserve stable order for an immutable corpus/version and filter set. Exclusions affect both manual and random selection. Candidate summaries contain enough title/type/lead preview for selection; full article retrieval returns the authoritative sentence list. Random Article and all candidates are corpus members only.

## Submit Request

```json
{
  "submission_id": "client-generated-uuid",
  "corpus_version": "frozen-version",
  "claim": "Human-entered Vietnamese claim",
  "label": "SUPPORTED",
  "hop_count": 2,
  "workspace_page_ids": [101, 202],
  "evidence_refs": [
    { "page_id": 101, "revision_id": 1001, "sent_id": 0 },
    { "page_id": 202, "revision_id": 2002, "sent_id": 1 }
  ]
}
```

The server supplies article title, source URL and evidence text from the frozen corpus; the browser does not submit authoritative text/metadata. Validate UUID/session/corpus identities, nonblank claim, strict allowed label/hop values, no more than five distinct workspace pages, unique evidence references, and reference membership in the submitted workspace and frozen article revision.

SUPPORTED/REFUTED require at least two distinct sentence references; NEI requires exactly one and hop_count=2. A same-article two-sentence SUPPORTED/REFUTED request is valid structurally. Do not infer semantic correctness or require an article for each hop. Do not preselect, coerce, repair or overwrite human label/hop values.

Successful response: `{ "sample": <BenchmarkRecord>, "sample_count": <committed current-session count>, "replayed": false }`, with HTTP 201 for creation or HTTP 200 and `replayed: true` for an identical retry. Unique-key races return the same receipt result after transaction resolution, not a second sample.

Canonical payload digest includes session/corpus identity, exact human claim/label/hop values, and a stable ordering of workspace/evidence identities. If a known submission ID is reused with different canonical content, return 409 `submission_conflict`. Samples remain unchanged. Any storage failure rolls back the entire new submission.

## Record and Export Contract

Use the full [Benchmark Record Contract](../specs.md#benchmark-record-contract) without adding receipt IDs, review fields or annotator IDs. Always include both evidence arrays and `reasoning_chain: null`.

Coverage is `single_document` for one observed source page ID and `cross_document` for at least two; count gold sources for SUPPORTED/REFUTED, partial sources for NEI. It describes evidence sources only and never establishes hop count.

HTTP export uses `application/x-ndjson; charset=utf-8` and a download disposition with `VIMFC_SESSION_<session_id>.jsonl`. Serialize Vietnamese characters directly, use one JSON object and newline per record, and iterate a consistent read snapshot ordered by `(created_at, id)`.

An empty known session returns 409 `empty_session`; unknown session returns 404. A failed download can be retried and does not delete or reset anything. No date-selection endpoints are provided.

## Builder and Recovery Commands

Commands below are proposed interfaces, available after installation from the workspace root through the project's Python entry point `python -m vimfc`.

| Command | Behavior |
| --- | --- |
| `pipeline create --snapshot <UTC> --config configs/pipeline.json` | Create UUID run; archive config and versions; print run_id |
| `pipeline prepare-replay --database <db> --source-report <metrics.json> --config configs/pipeline.json --output-dir <new-bundle>` | Validate archived report/run/snapshot; inherit cohort/discovery/operator settings and use current classification/extraction rules, without API calls or run creation |
| `pipeline pilot --run <run_id> --candidates <100\|1000\|10000>` | Process only the archived bounded pilot cohort, resume compatible work, emit performance/template audit reports and stop; no automatic production escalation |
| `pipeline run --run <run_id> --production --pilot-report <reviewed-report>` | Deliberate invocation only after the user's decision and critical-defect resolution; record matching reviewed report/run/config identity, then collect/process the production target and resume compatible progress |
| `pipeline extend --run <run_id> --additional 10000` | Extend the same-snapshot manifest without changing its quality/rule versions |
| `pipeline status --run <run_id>` | Show stage counts, failures, cache reuse, speed and ETA |
| `pipeline retry --run <run_id>` | Offline requeue of due transient failed/pending/deferred stages; never retries structural unresolved or bypasses deadlines; optional because bounded resume automatically recognizes due work |
| `pipeline create --snapshot <UTC> --config <new-config> --reuse-run <old-run>` | New run identity; reuse only matching raw/stage dependency results; preserve old run |
| `pipeline report --run <run_id> [--pilot-size <100\|1000\|10000>] [--assessment <path>]` | Corpus or bounded-pilot metrics/template-impact report; optional external human reviewed/valid counts, otherwise yield unassessed |
| `pipeline freeze --run <run_id> --output data/corpora` | Enforce exactly 20,000 eligible selected articles and all integrity checks; publish a new immutable version package |
| `corpus verify --corpus <package-directory>` | Full package hash, count, revision/provenance, sentences and graph validation; nonzero exit on failure |
| `corpus export --corpus <package-directory> --output <directory>` | Independent complete corpus.jsonl, graph.jsonl and manifest |
| `sessions list --annotations data/annotations.sqlite` | List persisted session UUIDs, corpus versions, creation timestamps and successful counts |
| `samples export --session <UUID> --annotations data/annotations.sqlite --output <file>` | Export exactly that historical/current session with the same serializer as HTTP |

`pipeline pilot` requires the staged 100 -> 1,000 -> 10,000 progression and leaves production stopped. Pilot-size bounds are execution controls on archived cohorts, not changes to the fixed snapshot or permission to freeze fewer than 20,000 articles. Reports include the performance and extraction fields specified in [plans.md](../plans.md#b-pilot-performance-benchmark). Finishing a pilot or generating a report does not authorize production.

Each bounded invocation emits metrics/performance/template/classification/pair audits, `dependency-manifest-audit.json`, `fingerprint-audit.json`, `subtype-audit.json`, content-addressed `manifests/`, cohort/summary and immutable `attempts/<id>/` copies. Classification reports distinguish primary status/decision/provenance and optional subtype status/search/provenance. Manifest-wide incomplete can coexist with completed primary classification. `report --output <file>` exports locally without source access; `--pilot-size` retrieves a completed archived pilot report. Missing declared physical artifacts are role-tagged input errors; transient required source errors retain retry deadlines. Optional acquisition frontiers remain pinned within a run and do not auto-refresh with cache warmth.

The production command must be deliberately invoked only after the user reviews the results and decides to continue. It rejects missing/stale reports, mismatched rule/run identities or known critical defects; the command/record is not an automatic substitute for the user's decision. If extraction rules change, create a new run, reuse compatible raw data and regenerate the affected stage results and required reports before production. This is a builder safeguard, not an annotation review workflow.

`pipeline run` keeps progress if interrupted after authorized production starts. Retry/extend commands must respect pilot bounds and cannot initiate unapproved production. A changed fingerprint returns a nonzero incompatibility error and instructions to create a new run; it does not silently repair the existing run. Freeze never overwrites an existing package. Commands must report insufficient candidate supply rather than loop forever when discovery sources are exhausted.

Use exit 0 for successful command completion (never human acceptance), 2 for invalid arguments/configuration, 3 for incompatible checkpoint, 4 for insufficient eligible supply/freeze validation failure, 5 for a reported bounded pilot with incomplete source data, and 1 for operational failure/interruption. Diagnostics distinguish deferred work, terminal input errors and semantic eligibility rejections. Export commands write temporary output and publish only after successful completion.

`pipeline pilot` accepts `--max-wait-seconds` (0..3600, default 60) and `--retry-rounds` (0..5, default 2). `status` adds `candidate_work_counts` and host cooldown deadlines. Reports add `work_counts`, `deferred_count`, `data_complete`, `pilot_accepted=false`, `acceptance_status`, scheduler version/budgets and `work-audit.json`. `completed=true` only denotes invocation completion. Attempted overlaps outcomes; for a full visited cohort `processed + deferred + source_failed + quarantined_integrity = cohort size` (absent quarantine count means zero). Data completeness does not assert eligibility or human Valid 2-hop Yield. Old attempts stay immutable; pre-resume aliases are preserved in `previous-latest/`. [Exact Windows resume commands](../pilot1000-resume.md) use the existing run/context.

The opt-in local driver `scripts/run-reviewed-pilot.py` accepts `--reviewed-quarantine <approval.json>` together with its existing `--context`, `--size`, request/wait/retry limits. The approval is operational, immutable by review ID and scoped to run/config/snapshot plus exact revision/evidence hashes; it is not a source-validation bypass. Affected candidates have `quarantined_integrity`, no classification/eligibility/graph evidence, `quarantined_count`, `quarantine-audit.json`, a critical defect and false completeness/acceptance gates. Changing/omitting an installed review on subsequent execution fails closed. Preserved checkpoints remain stored; generic checkpoint reports cannot revive quarantined evidence. Unreviewed integrity errors still exit3 and stop before another candidate. Partial guarded receipts retain an invocation-specific quarantine audit even if normal report generation never completes.

`--deferred-only` (`Resume-Pilot1000.ps1 -DeferredOnly`) retries only due deferred workers, restoring terminal checkpoint records with offline artifact validation; no discovery, terminal-stage execution or HTTP recovery for restored artifacts. `scheduler.checkpoint_restored` is distinct from current invocation worker attempts. Reports still cover the entire archived cohort. Stage backoff uses actual work deltas, materialization is timed separately, and resource scope includes primary report exports (`includes_report_exports=true`) while excluding the declared final telemetry flush. `source_hash_mismatch` is a typed non-retryable rejection with source/ref/hash diagnostics; existing generic failures are not rewritten or automatically requeued.

Host preflight can return an exit5 **pause before data processing**, with `paused=true`, `completed=false`, `host_health`, `health_report_path` and scope `host_preflight_no_candidate_processing`. It writes `health-checks/<id>/health-report.json` and `host-health.json`; previous `metrics.json`/pilot_reports remain the last actual processing attempt. `pipeline report --pilot-size 1000` still exports that data report, not the newer host receipt. `status` adds `host_circuits`. Diagnostic state `closed/probe_passed_advisory` is not a health guarantee; `stability_confirmed` remains false. Circuit/deadline/lease information is operational only; it is absent from classification fingerprints. No command automatically crosses pilot/production authorization gates.

## Startup Interface

`start.ps1` accepts `-CorpusPath <package-directory>` and optional `-Dev`; default corpus path comes from local application configuration rather than rewriting the shared corpus. It uses the existing writable annotations database or creates one when absent, checks bound corpus version and readiness, and prints the local URL only after success.

Installation is an explicit `scripts/install.ps1` action. Ordinary startup must not secretly install packages, rebuild the evidence corpus, replace annotation storage or bind publicly accessible network interfaces. Port conflicts and missing/corrupt prerequisites return actionable errors.
