# Large-Scale Inventory & Historical Acquisition Readiness — Technical Review Handoff

Date: 2026-11-16, Asia/Saigon. **Preparation only.**

**Implementation readiness: PASS for local offline tests.**
**UIT Pilot 10,000: CONDITIONAL — deployment, native Linux validation and execution await user review/authorization.**
**QWEN_FALLBACK = NOT_NEEDED.**

No additional Pilot 1,000, Pilot 10,000, Production 20,000, corpus freeze, source acquisition, SSH, upload, Slurm submission, model call/download/install, commit or push was performed. Tests used synthetic inputs and MockTransport. The actual 1,000-source archive was inspected read-only, not rerun through collection.

## 1. Audited baseline and inventory

The baseline remains the completed V2 result: **368 eligible**, **19 Candidate Internal Edges**, **one distinct structural A→B→C path**, `pilot_accepted=false`, snapshot `2026-10-25T03:00:00Z`, source run `80524a0f-87fe-4c01-af9f-dfa4b3951899`.

Its checksum-valid committed checkpoint contains 1,000 records. `checkpoint.tmp` still contains 250 records and is not used. The old result was not reset to 249 or relabelled as a new run. Original manifest: `68b4774114708f0f0efe6f3ed8a8abdc0face04ebb410444f5b4dcbec25af4b8`.

| Audit | Observed result |
|---|---:|
| Frozen dump inventory IDs | **100,000 unique**, all namespace 0 in discovery metadata |
| Duplicate inventory IDs | **0** |
| IDs missing from dump ns0 nonredirect metadata | **0** |
| Complete dump page metadata | 4,605,472 rows |
| Dump ns0 nonredirect universe | 1,304,753 IDs |
| Existing snapshot Wikipedia page IDs with raw cache | 4,599 |
| Cached raw page IDs also in the frozen inventory | **1,655** |
| Inventory IDs without that registered raw coverage | **98,345** |
| Cached pinned raw IDs in the deterministic first 10,000 | **1,221** |
| Existing cohort article sources fully checked | **1,000 / 1,000**, zero blockers |
| Missing / invalid registered Wikipedia raw objects | **0 / 0** |
| Registry observations | 105: 3 reviewed refusals, 102 missing historical dependencies |

The 1,655 figure is reusable raw coverage, not 1,655 verified eligible articles. Namespace/title/revision/source proof must still pass intake for each selected ID. Missing cache coverage is not proof that Wikipedia history is unavailable. The 102 missing historical dependencies and three refusal observations are preserved in the anchored registry; they do not authorize lowering source checks or trusting refused content. Optional classification acquisition prevents unrelated missing Wikidata metadata from gating Wikipedia-only intake.

Inventory root: `data/inventories/dump-viwiki-20261001-20261028-100k/`.
Inventory ID: `43c2a6420e67b562e5ba2e4558a8961dd4fd4e964027c8b7c0d6bfff774fa4ab`.
Inventory JSON SHA-256: `0e206e769456b9a7b315898273da29af99a3f912b8031238dd1c2ef415aad4ce`.

The October 1 dump is discovery metadata. It is never evidence that a title, namespace or article content was valid on October 25. Historical intake independently checks that frozen snapshot. Reusing the existing sampler for all 100,000 candidates produces cohort hash `dd6eff9097f086efafd86c748227dee035fc0ba80e446e08a66e949e5903ad5f`; **its first 1,000 candidates match the original cohort exactly**.

The older API listing checkpoint remains incomplete and unsealed: 6,848 unique IDs, zero duplicate IDs, 76 charged attempts, persisted HTTP 503/open-host state. It overlaps the chosen dump inventory at 1,077 IDs; 5,771 additional IDs are not merged. The chosen inventory already meets the requested 60k–100k size, so another listing acquisition or inventory rebuild is unnecessary.

### Diversity of the first 10,000 discovery slots

| Sampling stratum | Slots |
|---|---:|
| Person | 2,722 |
| Creative Work | 1,362 |
| Location | 970 |
| Organization | 695 |
| Landmark / Facility | 677 |
| Event | 650 |
| Science / Technology | 545 |
| Award / Competition | 379 |
| Exploration | 2,000 |

These are deterministic discovery allocations, not primary entity labels or verified corpus quotas. Several non-Person pools exhaust early; the full inventory has 48,557 Person slots and 44,380 exploration slots. Neither balanced eligible supply nor enough connected human annotation candidates has been demonstrated at scale.

## 2. Implementation and integration

The implementation reuses the existing pipeline rather than replacing acquisition or evidence policy:

```text
Audited frozen inventory + seed-only-2 order
  → private DB/cache workspace with immutable binding
  → bounded HistoricalProvider._raw / StageExecutor
  → checked revision, raw hashes, namespace and historical title
  → offline CheckedArchive.article_source
  → historical extract_lead
  → quality-relaxed-1
  → annotation-edge-1 / rank-annotation-1 selection
```

### Incremental behavior

- `prepare_collection` copies a checked source bundle into a new workspace and records the full inventory order. It acquires no historical Leads. It is implemented for later use; it was only exercised on synthetic fixtures in this phase.
- `collect_batch(mode='acquire')` processes at most the requested batch and candidate prefix. It uses existing revision pins, raw cache, acquire stages and move indexes first. New source requests only concern Vietnamese Wikipedia; no item hints, Wikidata acquisition or Wikidata health probe are required.
- Every physical request is charged durably before sending, against both a per-invocation bound and a fixed workspace ceiling. Requests remain serial, maxlag=5, paced at least one second apart, with one HTTP attempt per query. New 429/5xx/Retry-After stops the invocation; persisted deadlines survive resume. Budget stops do not become permanent source failures.
- Source acquisition validates page/revision/snapshot identity, cutoff, canonical historical title and move proof, raw-object address, content model, SHA-1, SHA-256 and namespace. Original registry refusals remain active through a read-only guard. Integrity or serious checkpoint failures produce a sticky fatal stop requiring review.
- Explicit unavailable historical revisions/content and known namespace exclusions are recorded separately, rather than presented as quality-rejected articles. Interruption after raw/stage commit reuses that stage on resume. The shared OS writer lock prevents concurrent workers.
- `mode='process'` is offline: validate source bytes/provenance, extract Lead, keep matching archived classification metadata, run unchanged relaxed quality, then V2 selection. Previously processed source bytes are rechecked before selection. Classification tied to another source revision is not reused; repeated full Wikidata item payloads are omitted from the metadata projection as in the existing checked archive.
- Collection checkpoints use per-record JSON hashes in SQLite; binding covers source bundle, config/snapshot, full candidate order, target and request ceiling. No giant growing JSON checkpoint is rewritten after every article. Through-prefix cannot decrease past already recorded work, and it never extends itself.
- Eligible supply reaching the 20,000 target stops further admission without lowering quality. Further approved collection can extend the explicit prefix up to 100,000 within its reviewed request ceiling. Spent budgets stop; do not edit bindings to continue.
- Selection uses checked Lead links and existing connectivity-aware ranking. A new checked canonical article supplies a **Candidate Edge** target only if no archived title-resolution decision exists. Ambiguous titles, redirects without old checked resolution and old integrity/unresolved ownership decisions are not guessed or overridden. There is no promotion to `VERIFIED_EDGE`, factual evidence or human gold.
- A successful offline CLI batch publishes `selection.json` atomically. It includes four edge states, selection and graph metrics; SQLite invocation receipts retain runtime, acquired/processed/missing counts, remaining prefix, eligible supply, request counts and blockers. Interrupted exports can be regenerated offline from committed records.

Archived snapshot, policies, source bytes, registry and Pilot results are unchanged. The 20k article target and 900 SUPPORTED / 900 REFUTED / 200 NEI research distribution remain intact. No sentence-level evidence rules, annotation UI semantics, claims or semantic multi-hop validation were changed. Earlier WikiGraph-first experiment outputs are preserved but are not the active policy path for this readiness worker.

### Files and functions

| File | Change |
|---|---|
| `backend/src/vimfc/pipeline/collection_bundle.py` | `export_bundle`, `read_bundle`, `inside`: checked SQLite/raw/artifact transport with portable relative paths and unchanged provenance bytes |
| `backend/src/vimfc/pipeline/incremental_collection.py` | `prepare_collection`, `collect_batch`, `CollectionArchive`, source validation, metadata reuse, per-record checkpoints and durable request guards |
| `backend/src/vimfc/pipeline/provider.py` | Optional `classification_required=False` constructor path avoids Wikidata setup/health; existing callers retain the default behavior |
| `backend/src/vimfc/pipeline/relaxed_archive.py` | Optional artifact resolver on read-only archive/registry; defaults retain original behavior |
| `backend/src/vimfc/pipeline/quarantine.py` | Optional path resolution for copied approval/evidence/RCA objects, including Windows-origin paths on Linux; approval records and verification rules retained |
| `scripts/collection-readiness.py` | Explicit bundle/prepare/preflight/batch commands, concise logging, atomic selection export, graceful SIGTERM handling |
| `configs/slurm/annotation-pilot10000.sbatch` | CPU-only one-batch template, offline default, explicit path/egress/review environment and TERM forwarding |
| `configs/slurm/annotation-pilot10000.json` | Planning reference with fixed policies, prefix, target, request ceilings and unchanged claim distribution |
| `configs/slurm/requirements-tested.txt` | Direct versions used locally; Linux environment must be rebuilt and captured separately |
| `tests/integration/test_incremental_collection.py` | Acquisition, reuse, V2 candidates/metadata, missing sources, budget/interruption resume, fatal integrity, checksums, duplicates, path portability and locking regressions |
| `docs/large-scale-annotation-readiness.md` | Detailed reviewed deployment and operating procedure |

No existing acquisition runner's Pilot-size gates were loosened, and no default builder was redirected to a new policy.

## 3. Scaling assessment

### Measurements used

- Wikipedia raw cache: **34,112,989 compressed bytes**, **104,875,791 UTF-8 bytes**, across 4,599 page IDs: approximately 7.4 kB compressed / 22.8 kB text per ID.
- Source DB: **2,476,113,920 bytes**; complete source raw-cache including Wikidata: **70,090,544 bytes**; inventory JSON: **63,964,937 bytes**. The minimum portable bundle is roughly 2.61 GB plus registry evidence/manifest overhead; the private workspace duplicates the DB/cache.
- Archived cohort mean acquire record: **12,093 bytes**, p95 **44,251**. Mean V2 article/Lead record: **7,748 bytes**, p95 **18,913**. These are local sample distributions, not universal article-size limits.
- Source DB payload is dominated by old classification/derived data: stage JSON about 1.423 billion characters and classification-result JSON about 628 million characters (SQLite text lengths; UTF-8 byte lengths differ). This implementation preserves them for safe reuse instead of trimming the historical schema. New collection does not acquire more Wikidata payloads.
- Existing 5,228 move-index rows total about 20.5 million JSON characters, maximum stored JSON length 10,225 characters. Later lookup horizons will span more move history than this archived sample; API/storage cost is not assumed constant.
- A previous mixed historical invocation had **193 new acquire stages**, **387 revision requests** and **192 move-log requests**: about **3 revision/move requests per new article**, excluding 193 item-hint and all Wikidata/lead-dependency calls. Its 9,610-second overall duration and 613 MB sampled RSS included classification, extensive warm-cache reads and reporting; it is not a cold Wikipedia-only throughput benchmark.
- The V2 resume took about 73 seconds with warm cache and 249 already committed articles. It is not a benchmark for acquiring 1,000 new histories.

### Conditional source/request model

Assume 3–4 physical revision/move/namespace requests per newly needed article, cached pinned raw coverage reusable after checks, and one-second request start spacing. Initial move-history pagination, unavailable sources, cooldowns, transfer, DB verification and extraction/selection add cost. These are scenarios, not measured server forecasts.

| Scenario | Candidates | Cached pin/raw coverage upper bound | New sources | Request model | Pacing-only hours |
|---|---:|---:|---:|---:|---:|
| Pilot prefix 10,000 | 10,000 | 1,221 | 8,779 | 26,337–35,116 | 7.3–9.8 |
| 20k eligible if observed 36.8% yield persists | 54,348 | 1,521 | 52,827 | 158,481–211,308 | 44.0–58.7 |
| 20k eligible at 25% yield | 80,000 | 1,613 | 78,387 | 235,161–313,548 | 65.3–87.1 |
| Entire inventory, at least 20% yield needed | 100,000 | 1,655 | 98,345 | 295,035–393,380 | 82.0–109.3 |

At an effective 1–3 seconds per serial request start/response, the Pilot scenario spans roughly **7–29 hours** of acquisition and the 36.8% scenario roughly **44–176 hours**, before pauses and offline work. Assumed acquisition throughput is approximately **300–1,200 candidates/hour**. No Linux throughput measurement exists yet. Early 368/1,000 yield is conditioned on its cohort and cannot establish full-inventory supply, diversity quotas or graph connectivity. Below 20% aggregate yield, this frozen 100k inventory cannot deliver 20k eligible without separately reviewed additional inventory.

### Storage and I/O model

Using the cache averages, extra raw compression is roughly **62 MiB for the Pilot**, **374 MiB for 54,348 candidates**, **554 MiB for 80k**, or **696 MiB for 100k**. Acquire-plus-Lead rows alone add approximately **0.16 / 0.98 / 1.45 / 1.82 GiB**, respectively, before move histories, indexes, duplicate derived/export payload, WAL and backups. Apply a **2–4× sensitivity** to row storage rather than treating those payload minima as required quota. Article-size tails and later move horizons remain unmeasured.

Plan at least **20 GiB free workspace storage** for preflight and request **50 GiB persistent quota for the 10k preparation/pilot envelope**; review **100 GiB or more for the 20k-eligible campaign**, depending on retention and actual growth. These are provisioning assumptions to validate with UIT, not claims about available quota. Allow immutable bundle + writable workspace + stopped-worker backups, logs, Python/wheels and large inode counts. Do not retain a complete duplicate workspace after every small batch indefinitely.

Each batch verifies the immutable bundle, including its approximately 2.48 GB source DB. At batch 250, 40 acquire + 40 process calls for a 10k prefix imply roughly **200 GB of source-manifest verification reads**, before normal processing I/O. A 54k campaign can exceed **1 TB of such reads**. Use approved SSD-backed storage and measure hash/SQLite throughput. The design bounds verification per batch, avoiding the old per-article whole-archive scan; a larger reviewed batch can amortize checks after calibration, within request and interruption limits.

One default acquisition batch at the scenario pace is about 750–1,000 requests / 12–50 minutes; this is why the four-hour Slurm template is a bounded batch rather than a promise that the entire Pilot fits in one job. Offline parser/ranking time, large graph memory and native Linux RSS remain unmeasured and must be recorded on UIT. Initial resource proposal is **4 CPUs, 16 GiB RAM, no GPU**.

## 4. UIT deployment plan and unresolved server checks

The detailed commands and layout are in [operations](../../docs/large-scale-annotation-readiness.md). No command in that future procedure was run against UIT.

1. User reviews this implementation, audit and budgets. Confirm UIT account/partition, CPU/RAM/time caps, architecture, Python, persistent quota/inodes, filesystem semantics, permissions and allowed egress host.
2. After authorization, export the source bundle using read-only SQLite snapshots and checked raw copies. Transfer reviewed source files identified by the runtime manifest, bundle and evidence receipts. Original discovery dump files may remain in their archive; the transported inventory retains their source hashes/origins and audited inventory identity, and supplies no historical evidence. Do not transfer Windows venvs, credentials or an incomplete live SQLite main file without its WAL.
3. Rebuild the Linux venv/wheel environment, verify hashes, run preflight and native Linux tests. Create a new private collection workspace from that bundle; do not resume or overwrite the old V2 Pilot directory.
4. Validate storage locking/WAL and interruption recovery on the actual UIT filesystem. The Slurm log directory must exist before submission. Use one writer; a job array or concurrent acquisition/process workers on the same workspace are unsupported.
5. If compute has approved Internet, acquire under the explicit egress mode. If it has no Internet, acquisition runs on an approved transfer/egress host and compute runs offline processing. Login-node acquisition is not assumed permitted. For separate hosts, move only a closed, coherently backed-up workspace with hashes to a new root; keep the old copy read-only. Node-local ephemeral scratch without durable checkpoint transfer is a blocker.
6. Only after the user's separate execution approval, process the bounded 10k prefix in explicit batches, inspect each receipt, preserve source state, and stop on new integrity or serious source/checkpoint failures. Do not auto-progress to Production or freeze corpus.

**Current server blockers:** account/partition and quota are unknown; no native Linux runtime/Slurm or actual filesystem lock/crash test has run; compute egress/DNS/CA/proxy/User-Agent acceptance is untested; cold source availability, larger-horizon move cost, Linux throughput/RSS and graph yield are unmeasured. `preflight` reports quota/network/locking as requiring confirmation rather than claiming success from ordinary local free space.

The template is syntactically checked with Git Bash `bash -n`; POSIX lock branching is regression-tested with a simulated `fcntl`. Those do not replace native UIT validation. The template requests early TERM and forwards it to Python, which retains committed records and releases the current stage on interruption. A hard kill can require waiting for the existing five-minute lease expiry; no stale lock/lease deletion or pending-file promotion is performed.

## 5. Qwen assessment

**QWEN_FALLBACK = NOT_NEEDED for current inventory/acquisition/collection readiness.**

Historical identity, revision cutoff, bytes, redirects and integrity require deterministic source checks. Missing source coverage, 98k uncached IDs, API time and sparse graph topology cannot be repaired by an LLM. Classification is optional, and deterministic discovery strata plus existing metadata/quality/connectivity ranking provide the current selection signals.

Reconsider only after the UIT pilot demonstrates a specific unmet metadata-labeling or semantic tie-break need and deterministic methods are shown insufficient. At that point report the measured issue and await explicit Qwen integration approval, with a separate resource plan. Any model output would remain optional metadata subject to review, never historical-source validation, Page ID/title/redirect/hyperlink repair or integrity override. No Qwen/Groq/LLM was installed, downloaded or called.

## 6. Tests, preservation and stopping state

Focused command (final code):

```text
.tools/python312/python.exe -B -m pytest tests/integration/test_incremental_collection.py tests/integration/test_integrity_registry.py tests/integration/test_reviewed_quarantine.py tests/integration/test_annotation_builder.py tests/unit/test_annotation_edges.py tests/integration/test_seed_inventory.py tests/integration/test_dump_inventory.py tests/integration/test_manifest_provider.py -q -p no:cacheprovider --basetemp=.tools/readiness-20261116/focused-final-3 --tb=short
```

**119 passed, 17.69s, exit 0.**

Full offline command (final code):

```text
.tools/python312/python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.tools/readiness-20261116/full-offline-final --tb=short
```

**774 passed, 360.31s, exit 0.** This is the current working-tree suite; counts are not comparable to old 985-test evidence preceding the user's Groq cleanup. An earlier full pass of 773 tests preceded the final new-target/source checks and POSIX branch regression and is not the final-code evidence.

The new tests cover frozen inventory duplicate rejection, isolated preparation, portable bundle checksums/path containment, Windows-origin registry refusal artifacts after relocation, cached and fresh MockTransport acquisition, no Wikidata requests, preserved metadata, new checked Candidate targets, bounded/durable requests and partial acquisition resume, interruption between raw commit and collection commit, missing histories, sticky integrity failure, processed raw corruption, checkpoint checksum, one writer, preflight and Slurm policy, and POSIX locking branch. Existing source, registry/refusal, extraction/quality and V2 tests also pass.

**Preservation PASS: 46,388 durable files / 23,761,424,159 bytes; zero changed/missing files and zero unexpected files in protected roots.** Scope: `data/`, archived `reports/pilots/`, pre-existing verification outputs, original V2 output and previous WikiGraph experiment outputs. Credentials `.env`, volatile SQLite SHM and new owned readiness outputs are excluded. Original source DB/cache/Integrity Registry and archived outputs are covered. The original V2 source fingerprint also remains exactly `21b3fcb5e5093c0b9130602b07856f4f2b8d7aeea7ff61d8ca5fd0004af9f7fa` (10,070 files in that narrower binding scope).

Runtime files for later reviewed transfer: 83 files, manifest ID `171170db8b2fa41db55a70fb456db4caa807880ddcfa4d84df5ecda67bafb1b2`. These are the actual uncommitted runtime sources/configs, not an assumption that HEAD contains the implementation. There are pre-existing unrelated dirty changes in the repository; they were not reset or committed.

Evidence directory: [large-scale-readiness-20261116](large-scale-readiness-20261116/).

- `inventory-audit.json`, `scaling-assessment.json`: measured inventory/cache/state and explicit cost scenarios.
- `pilot-binding-check.json`, `preservation-after.json`: original binding match and preservation result; full baseline remains `.tools/readiness-20261116/preservation-before.json`.
- `focused-final.log`, `full-offline-final.log`: final test evidence.
- `runtime-files.json`: hashes of sources/configs to review before transfer.

### GitHub publication

The local verification above predates Git publication. The user subsequently authorized commit/push; that authorization does not authorize UIT transfer or execution. README now links the main files to read. Published test logs are UTF-8 copies of the unchanged local test evidence.

For a Git checkout, use `runtime-git-files.json` (manifest `7b1e8f0d8253afb3aba236a737163f046ccc9c3dee7b9e1b54025fcd6d4c9a9e`): it hashes repository blob bytes after LF normalization. The earlier `runtime-files.json` remains the native Windows working-file manifest; only `backend/src/vimfc/pipeline/runner.py` differs because its working copy uses CRLF. This is a line-ending difference, not an implementation change.

**STOPPED at readiness handoff.** No actual collection workspace or source bundle was prepared from production inputs in this phase; only synthetic test workspaces were created. No server deployment or Pilot execution is authorized by passing local tests. Await user review and explicit permission for the server stage.
