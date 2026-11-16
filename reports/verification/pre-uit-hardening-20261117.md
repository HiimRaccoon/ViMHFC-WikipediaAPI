# Pre-UIT Hardening — Technical Review Handoff

Started 2026-11-16, completed 2026-11-17, Asia/Saigon. Base commit: `3c6aefa236e47b31487687787b8ce6ce416c79ea`. Changes are local and uncommitted.

**GO for a separately authorized UIT preflight. NO-GO for starting collection 10,000 now.** Real-cache smoke, native filesystem/signal tests and high-degree graph calibration remain execution gates. Passing local tests does not authorize transfer or execution.

No real source bundle or collection workspace/run was created. No live API, Qwen/Groq/LLM, SSH/upload, Slurm submission, Pilot 10,000, freeze, commit or push occurred. Execution used synthetic fixtures, MockTransport, byte-verification fixtures and local pytest/Bash only.

## 1. Bugs fixed and scope

| Bug at base commit | Fix |
|---|---|
| Nonretryable `SourceUnavailable` usually set a global sticky fatal, including ordinary namespace/model/access failures | Explicit missing/unsupported/needs-review/retryable/integrity dispositions. Unknown failures are withheld for review rather than admitted or globally poisoning unrelated candidates. |
| A benign code or retryable flag could obscure the actual severity | Hash/identity/cutoff contradictions take precedence, including wrapped causes. Ambiguous historical move chains and conflicting raw objects remain fatal. |
| Unsupported-model handling could precede collector identity/hash checks | Separate raw acquisition from legacy `_raw` validation. The collector validates ref, cutoff, historical title, registered raw reference, actual bytes/hashes and namespace before excluding a model. Boolean IDs and empty canonical titles are rejected; malformed namespace values need review. |
| Raw complete could appear as no outstanding work while processing had not started | Separate acquisition and processing backlogs, review blockers, terminal source exclusions and committed eligibility. |
| Interruption after SQLite commit but before a local counter increment understated eligibility | Final progress reads committed SQLite state. |
| Expected stops and real failures were hard to distinguish | UUID/timestamp receipts in SQLite and `last-invocation.json`, with status, expected-stop flag, exit code, deadline, budget scope, blockers and secondary receipt errors. CLI emits attached receipts for stops. |
| CLI published selection after releasing the worker lock | Selection is now atomically published inside the lock; failed publication preserves the previous export and reports that it was not updated. |
| Logging/progress failure could mask the original exception | Retain the original failure and record secondary receipt errors; unavailable progress is null rather than zero. Attempt the JSON receipt even if SQLite receipt insertion fails. |
| SQLite commit failure was outside rollback protection | Commit is inside the transaction try/rollback. Context cleanup closes the connection without masking an active failure by explicitly truncating WAL. |
| SIGTERM/orphan publication lacked enough resume evidence | Subprocess SIGTERM test proves receipt/lease cleanup. An interrupted durable raw blob is verified/reused without repeating its successful HTTP fixture. Repeated TERM is ignored during first-stop cleanup. |
| Slurm logs assumed a pre-existing `logs/` directory | Default files are placed directly in submission/`--chdir` directory. |
| Automatic scheduler restart was not blocked by template comments | Add `--no-requeue`, plus an early restart-count guard with a structured error. |
| Empty Bash arrays with nounset could fail on older Bash | Argument array is always nonempty. |

Modified runtime files: `pipeline/incremental_collection.py`, `pipeline/provider.py`, `pipeline/connectivity_rank.py`, `storage/pipeline_store.py`, `scripts/collection-readiness.py`, and the Slurm template. New benchmark/regression files support them. No snapshot, inventory, quality thresholds, Candidate Edge policy or ranking utilities changed. Original source/registry/archived artifacts were not edited.

The shared provider's legacy `_raw` behavior remains available. New collection uses `_acquire_raw` so source checks can precede ordinary exclusions. Existing old fatal flags are not auto-cleared: inspect them rather than assuming an old refusal was benign. Early binding/checksum errors stop before work; they do not attempt unsafe repair or fabricate new progress.

## 2. Operational contract

| Outcome | Handling | Sticky fatal | Exit |
|---|---|---|---:|
| Missing historical revision/content | Record `missing`; continue | No | 0 unless other blockers |
| Validated unsupported model/nonzero namespace | Record `unsupported`; continue | No | 0 |
| Namespace/title proof review, nonretryable access or unknown response | Withhold article as `needs_review`; continue others | No | 2 |
| 429/503, maxlag, Retry-After or retryable source error | Stop; keep pending retry and deadline | No | 75 |
| Invocation or collection request ceiling | Stop before send; keep partial stage | No | 5 |
| SIGTERM/KeyboardInterrupt/SQLite interrupt | Stop with committed records | No | 130 |
| SQLite busy/full/I/O or publication error | Stop for operational inspection | No | 75 |
| Hash/ref/title/cutoff contradiction or corrupt checkpoint | No admission; stop | Yes when writable | 3 |
| Binding/lock/runtime error before work | Preflight error on stderr | No unsafe repair | 3 |

`expected_stop=true` means normal completion, an explicit budget, retry deadline or interruption. It does not mean pilot acceptance. Storage errors and integrity errors remain distinguishable; no automatic retry/progression was added.

Progress within the explicit prefix:

- `not_seen`: IDs without a collection item.
- `remaining_to_acquire = not_seen + retryable`.
- `remaining_to_process = acquired`.
- `eligible_supply`: committed processed records passing unchanged relaxed quality; `quality_rejected` is processed minus eligible.
- `missing` / `unsupported`: terminal source exclusions, not quality rejections.
- `needs_review` / `blockers`: withheld IDs requiring a decision, not falsely counted as absence or eligibility.
- `remaining_in_prefix = remaining_to_acquire + remaining_to_process + needs_review`.

Receipts include invocation identity/time, `status`, `expected_stop`, `budget_scope`, `stop_reason`, retry deadline, `blockers`, `selection_published`, and `receipt_errors`. Request counts are conservative pre-send charges; a hard kill can leave an uncertain charged attempt. Exactly-once HTTP is not claimed before raw bytes become durable.

Failed selection replace keeps the old selection and valid pending output is not promoted. A later explicit offline call regenerates from committed records. When storage cannot write, receipts are best effort and CLI output remains authoritative. Preflight errors do not imply the previous stored receipt describes the new attempt. Hard-kill stage leases may need their recorded five-minute expiry; never delete leases/locks automatically.

## 3. Benchmarks

Local Windows / Python 3.12.10, sampled RSS. Individual timings, with some concurrent tests/hashing, are not statistical or native Linux guarantees. Every fixture is synthetic; no historical run was created.

### Sparse block graphs

Blocks contain up to 12 vertices and two forward Lead links per vertex. Whole-pool target is 20k; half-pool exercises actual constrained selection. All six full selection-manifest digests match the base implementation.

| Articles | Whole pool before → after | Half pool before → after | Graph metrics after | After RSS |
|---|---:|---:|---:|---:|
| 1k | 0.451 → 0.365 s | 0.256 → 0.220 s | 0.0019 s | 39.8 MiB |
| 5k | 2.920 → 1.964 s | 1.713 → 1.164 s | 0.0163 s | 97.7 MiB |
| 10k | 8.202 → 4.351 s | 4.246 → 2.740 s | 0.0495 s | 167.6 MiB |

At 10k: 17,498 directed edges, 29,992 distinct A→B→C paths. Graph metric code did not change; its timing variance is not claimed as an optimization. Paths remain structural diagnostics, not claims.

### Hub stress: measured bottleneck and remaining limit

The reciprocal-star fixture links every vertex both ways to one hub. At 1k it has 1,998 edges and 997,002 distinct paths. Baseline complete benchmark cases exceeded 60 seconds at 1k/5k/10k.

The selector now maintains exact selected incoming/outgoing/reciprocal counts, including verified weights, avoids copied selected sets in each gain and deduplicates identical heap keys. No cap, sampling, utility, normalization, tie, diversity or ordering semantics changed. **30 full-manifest oracles from commit `3c6aefa`**, including weighted, reciprocal, ineligible, grouped, constrained and reversed inputs, still match exactly.

Final optimized 1k hub: whole pool **15.45 s**, half pool **10.98 s**, approximately **42 MiB** peak sampled RSS. The 5k/10k complete cases still exceeded the **90-second safety timeout**, with approximately **96 / 165 MiB** sampled RSS before termination. These are censored runtimes, not proof of fitting the four-hour job limit. High-degree two-hop proposal refresh remains expensive. Sparse results must not be extrapolated to these graphs; this remains an execution/calibration gate.

### Full bundle verification per batch

Byte-only fixtures use a 2,476,113,920-byte synthetic database-shaped payload plus roughly 8-kB objects. The payload is not opened as a historical SQLite database or used to prepare a run. Every manifest byte is checked; no mtime cache, reduced verification or skipped hashes were introduced.

| Objects | Bytes verified/call | Three times | Median × acquire/process calls, batch 250 |
|---|---:|---|---:|
| 1k | 2,484,535,812 | 2.094 / 2.462 / 2.048 s | 16.8 s over 8 calls |
| 5k | 2,518,227,812 | 5.144 / 5.074 / 4.980 s | 203.0 s over 40 calls |
| 10k | 2,560,342,812 | 7.866 / 7.922 / 8.163 s | 633.7 s over 80 calls |

OS caching affects repeats. Verification is material but not the dominant paced-acquisition scenario, so integrity checks remain complete. This hash-only benchmark excludes registry parsing, source validation, extraction and selection; native shared-storage performance remains unknown.

Reproduce with `scripts/benchmark-collection-offline.py --output <NEW-directory>`, `--shape hub --timeout 90`, or `--bundle-hash`. The baseline module was copied from `git show 3c6aefa:backend/src/vimfc/pipeline/connectivity_rank.py`; it was not edited. JSON receipts, sizes and digests are in the evidence directory.

## 4. Slurm, dependencies and filesystem

- Retain one task, 4 CPUs, 16 GiB RAM, four hours, no GPU. Sparse fixtures fit in memory; no claim that serial acquisition/selection scales fourfold with CPUs. Real-data and native Linux RSS remain unmeasured.
- Logs default to `slurm-%x-%j.out/.err` in submission/`--chdir`; custom parents must exist before submission. Template has LF and passes local Bash syntax checking.
- `B:TERM@120` signals the batch shell, which forwards TERM to Python. `--no-requeue` can be overridden by site policy, so nonzero `SLURM_RESTART_COUNT` now stops before worker paths are used. This behavior is based on [official sbatch documentation](https://slurm.schedmd.com/sbatch.html); actual UIT preemption/cgroups still need native tests.
- A subprocess regression invokes Python's actual SIGTERM dispatcher during a synthetic StageExecutor operation and checks exit 130, lease cleanup and receipt. A local Bash regression checks the forced-requeue guard. Neither is a Slurm job or native UIT test.
- Preflight checks Python 3.12+ and SQLite JSON support. No dependencies were installed/added. The direct tested Windows requirements are not a complete Linux lock; rebuild for the approved architecture and archive resolved versions/wheels.
- SQLite WAL needs supported locking/shared-memory semantics. Free bytes or Windows success does not prove NFS/Lustre suitability. Use approved persistent SSD/block storage; ephemeral scratch without durable stopped-worker backups is a blocker. Native UID quota/inodes/permissions, independent-process locking, WAL interruption, TERM delivery and full Linux tests remain required.

## 5. Proposed real-cache smoke — NOT EXECUTED

This needs a new real collection UUID and therefore explicit approval. No real bundle/workspace was created in this review.

1. Pin original snapshot, inventory/order, source context and read-only registry. Check the old committed 1,000 checkpoint checksum/source fingerprint. Keep both old checkpoint files unchanged; never promote `checkpoint.tmp` or roll back to 249.
2. After approval, export an isolated bundle and prepare a new workspace. Use the first **100 deterministic candidates** inside the already checked original 1,000 prefix. Keep corpus target 20k; this is a cache/source smoke, not acceptance.
3. Run four 25-candidate acquire batches with **`VIMFC_OFFLINE=1` and `--max-requests 0`**, in the explicit acquire mode. The legacy `--allow-network` flag selects that mode; offline environment plus zero request allowance prohibit sends. Abort on any cache miss, review/missing/unsupported result, budget stop or network attempt rather than fetching.
4. Alternate offline process batches through the same prefix. Compare raw/Lead identity and relaxed eligibility to corresponding baseline records; retain available metadata. Do not require the new run/pool's manifest to equal the old 1,000 manifest.
5. Repeat offline processing: identical new-workspace graph/selection/manifest, zero new requests, zero source/review blockers and zero processing backlog. Inject one interruption only in this isolated workspace, explicitly resume and verify reuse.
6. Rehash originals and archive separate receipts/new UUID. `pilot_accepted=false`; no claims or freeze. Repeat native tests on UIT only after transfer/access approval.

## 6. Validation, preservation and decision

**Final focused: 112 passed, 10.17s, exit 0. Final full offline: 837 passed, 374.52s, exit 0.** The final full command used `python -B -m pytest -q -p no:cacheprovider --basetemp=.tools/pre-uit-hardening-20261116/full-offline-verified --tb=short`. Focused coverage includes hardening/incremental/provider/resume regressions plus annotation/connectivity and frozen ranking oracles. Earlier 834-test full passes preceded the last identity/namespace and shell-guard tests and are not the final evidence.

The final focused command selected `tests/integration/test_pre_uit_hardening.py`, `test_incremental_collection.py`, `test_manifest_provider.py`, `test_resume.py`, and `tests/unit/test_rank_hardening_semantics.py`, `test_connectivity_rank.py`, `test_annotation_edges.py`. Evidence files: `focused-verified.log`, `full-offline-verified.log`, `verification-summary.json`, all benchmark JSON summaries and a current native-byte `runtime-files.json`. The archived readiness runtime manifests describe the old commit and were not rewritten.

Preservation: **46,398 files / 23,761,477,512 bytes**, zero changed/missing and zero unexpected protected files. Scope includes original data DB/raw/inventory/registry, archived pilot/verification outputs, V2 and earlier WikiGraph outputs. Only new owned hardening reports, volatile SHM and credentials are excluded. Completion crossed into November 17; new report paths did not exist in the baseline and their explicit exclusion retained all original baseline file hashes. Archived readiness artifacts were not rewritten.

**GO for UIT preflight after user approval. NO-GO for deploying and starting collection 10k now.** Remaining gates: approved real-cache smoke; native environment/full tests; quota/WAL/locks/signals; hub calibration; explicit access/transfer/job authorization. Existing old fatal flags require review, and unwritable storage cannot guarantee a durable receipt. No further stage is scheduled.

Evidence: `reports/verification/pre-uit-hardening-20261117/`; raw scratch/logs: `.tools/pre-uit-hardening-20261116/`. The base commit remains `3c6aefa`. **STOP for review; no commit/push or server action.**

## GitHub publication — subsequent authorization

After this review, the user explicitly authorized committing and pushing the hardening changes. Statements above and `verification-summary.json` describe the completed review before that authorization; the test/preservation evidence is unchanged. This publication does not authorize UIT transfer, preflight execution, a real-cache smoke or a collection job.

Read this handoff first, then `docs/large-scale-annotation-readiness.md` and `configs/slurm/annotation-pilot10000.sbatch`. Benchmark and validation receipts are in the evidence directory. For a Git checkout, use the publication's `runtime-git-files.json`, whose hashes reflect repository LF-normalized bytes; the original `runtime-files.json` retains the native Windows verification hashes.
