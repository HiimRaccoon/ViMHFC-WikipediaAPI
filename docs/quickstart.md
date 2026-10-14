# Quickstart and Acceptance Validation

**Plan:** [plans.md](plans.md)  
**Status:** Planned installation and verification guide. Commands below become runnable after the corresponding implementation milestones; this planning turn has not created application code or a corpus.

## 1. Prerequisites and Explicit Installation

Use Windows PowerShell, the supported Python environment, Node.js LTS compatible with the locked frontend dependencies, enough local disk space for raw-source/cache data, and internet access for corpus construction. The future annotation workflow needs only the frozen package and local services.

From the repository root, after the installation helper is implemented:

```powershell
.\scripts\install.ps1
```

Expected: the Python environment and locked frontend dependencies are installed, application dependencies are checked, and no corpus or annotation data is deleted. Activate/use the created environment for the `python` commands below. Source API User-Agent contact details are configured explicitly before acquisition.

## 2. Run Fixture-backed Verification First

```powershell
python -m pytest tests/unit tests/integration
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
```

Expected: meaningful tests pass, including historical cutoffs/templates/identity, lead-only graph extraction, deterministic selection, freeze validation, source-authoritative Submit, idempotency and session exports. These checks prove behavior on fixtures, not successful creation of the research corpus.

## 3. Create a Snapshot Run

```powershell
python -m vimfc pipeline create --snapshot 2026-10-14T00:00:00Z --config configs/pipeline.json
```

The timestamp is an example, not an automatic source snapshot choice. Use the intended UTC cutoff. Record the returned run UUID. First run the three bounded pilot stages, rather than production collection:

```powershell
python -m vimfc pipeline pilot --run <run-id> --candidates 100
python -m vimfc pipeline pilot --run <run-id> --candidates 1000
python -m vimfc pipeline report --run <run-id> --pilot-size 1000
python -m vimfc pipeline pilot --run <run-id> --candidates 10000
python -m vimfc pipeline report --run <run-id> --pilot-size 10000
python -m vimfc pipeline status --run <run-id>
```

Expected: each pilot stops at its cohort boundary under the fixed snapshot. The 1,000-candidate report contains per-stage timing/throughput, API requests, cache hit and retry/error rates, CPU/RAM/disk usage and a qualified ETA for 80,000. The 10,000-candidate report adds scaling/stability, checkpoint/resume and candidate-pair assessment. Every pilot audits successful/unresolved extraction, frequent failing templates and impacts on lead completeness, sentence IDs and lead-only graph. These commands are documentation for future implementation, not instructions to run a pilot during this planning update.

Interrupt and resume the same bounded pilot to check reuse and duplicate prevention. Unsupported common templates require tested historical-safe formatters, without live expansion or factual-content removal. Rule/version changes require a new run with compatible raw reuse and revalidation of affected stages/reports; use the same snapshot for comparisons.

**Stop after the reports and present them to the user.** Resolve critical defects and obtain the user's decision before production. Pilots do not constitute a completed 20,000-article corpus. Do not automatically execute the production example below:

```powershell
python -m vimfc pipeline run --run <run-id> --production --pilot-report <reviewed-final-pilot-report>
```

Only after that decision, production targets the initial 80,000 candidates with the validated rule versions; historical revisions and graph extraction retain their snapshot guarantees.

Interrupt and resume authorized production with the same run/report identity. Completed valid work must be reused and records must not duplicate. If eligible supply remains short, extend without lowering quality:

```powershell
python -m vimfc pipeline extend --run <run-id> --additional 10000
python -m vimfc pipeline run --run <run-id> --production --pilot-report <reviewed-final-pilot-report>
python -m vimfc pipeline report --run <run-id>
```

Changing snapshot or incompatible rules requires a new run; a compatible-data reuse option is documented in [contracts/interfaces.md](contracts/interfaces.md#builder-and-recovery-commands).

## 4. Freeze and Verify the Actual Corpus

```powershell
python -m vimfc pipeline freeze --run <run-id> --output data/corpora
python -m vimfc corpus verify --corpus data/corpora/<corpus-version>
python -m vimfc corpus export --corpus data/corpora/<corpus-version> --output exports/corpus
```

Expected: exactly 20,000 eligible unique articles, full stored lead sections, contiguous sentence IDs, supported classification provenance, consistent revision cutoffs, and lead-only internal directed edges. The new package contains standalone `corpus.sqlite` and its manifest. Independent exports include all selected articles, not only those later used in annotation.

The report shows soft-quota deviations, connectivity, subtype and pair counts. Human Valid 2-hop Yield remains unassessed until an actual external assessment is supplied; a fixture result or heuristic pair score must not be reported as verified yield.

Freeze must reject invalid/undersized selections and must not overwrite existing frozen versions. Verify the package can be copied without requiring a live WAL/SHM sidecar.

## 5. Start Annotation

```powershell
.\start.ps1 -CorpusPath data/corpora/<corpus-version>
```

Expected: prerequisites and corpus are validated, existing samples are retained, no pipeline is rerun, and the confirmed local URL is displayed. Open that URL and check:

1. A new UUID session and empty workspace appear.
2. Random opens Article A with six initial sentences or all sentences if fewer.
3. Show More exposes later sentences; collapsing does not lose selections.
4. Each discovery mode uses frozen local data, including strict subtype matching and external shared-entity identities.
5. At most five unique articles can be open. Article A has no Remove button; other removals and Re-random follow the agreed confirmation rules.

Disconnect external internet access while preserving localhost, then repeat Random/discovery/Submit/export. No Wikipedia/Wikidata requests may occur.

## 6. Validate Submit and Session Behavior

Using real corpus sentences, create human-entered test annotations:

- SUPPORTED and REFUTED, each with at least two distinct selected sentences; include a same-article case and a multiple-article case.
- NEI with human-entered hop_count=2 and exactly one partial sentence; human semantic review remains external.

After confirmed Submit, only claim/label/hop selections and evidence checkboxes clear. Open articles, display and discovery state remain. The stored count increases once. Simulate lost acknowledgement and retry the same submission: exactly one sample remains. Changed content under the same submission ID is rejected without overwrite.

Invalid counts/hops/source references must show structural errors and preserve the draft; the system must not pick replacement labels or hops. Stop the backend during Submit to confirm failure preservation.

Refresh with an unsubmitted draft: a new session and empty workspace appear, the draft is lost intentionally, and earlier successful samples remain stored.

## 7. Validate Export and Historical Recovery

The UI Export Dataset button is disabled for an empty current session. After submission, export and parse the JSONL: line count equals successful current-session samples, both evidence arrays exist, source evidence is exact, coverage metadata reflects distinct source pages, and no drafts/unselected contexts appear. Export must not reset the workspace.

Recover an older session through commands:

```powershell
python -m vimfc sessions list --annotations data/annotations.sqlite
python -m vimfc samples export --session <session-id> --annotations data/annotations.sqlite --output exports/VIMFC_SESSION_<session-id>.jsonl
```

Expected: all and only that session's saved samples are exported with the same record serializer. There is no date filter, review-status field or annotator ID.

## 8. Transfer to the Second Annotator

Copy source code, dependency locks, the frozen corpus package/manifest and README; do not initialize the second machine with the first machine's annotation DB. Install dependencies and start with the same corpus version.

Verify identical article/sentence/graph data on both machines, independently created session UUIDs, separate submitted samples, and matching corpus version in both exports. Corpus database hash must remain unchanged throughout annotation.

## 9. Record Delivery Evidence

Record commands/results, real corpus count/version/hash, API/cache/stage measurements, graph validation, portable/offline tests and unresolved source issues in an implementation acceptance report. Record human benchmark completion and semantic quality separately. Passing software tests does not imply 2,000 reviewed claims have been created.
