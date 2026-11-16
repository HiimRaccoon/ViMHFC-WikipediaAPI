# Historical annotation collection on UIT — preparation only

No deployment or execution is authorized by this document. Review the handoff first. All commands below are future operator steps after explicit permission. The active collection policies here are `quality-relaxed-1`, `annotation-edge-1`, `rank-annotation-1` at `2026-10-25T03:00:00Z`; the earlier WikiGraph experiment is a separate path and is not used by this worker.

Current review: [Pre-UIT hardening handoff](../reports/verification/pre-uit-hardening-20261117.md). Hardening changes supersede commit `3c6aefa` for stop dispositions, progress, publication locking and Slurm safeguards. Server execution still requires separate approval.

## Reuse and data flow

`inventory → pinned historical revision → checked raw → historical Lead → quality-relaxed-1 → V2 candidate-edge connectivity selection`.

Reuse the frozen dump inventory and `plan_seed_cohort` ordering, `StageExecutor`, `HistoricalIdentities`, `RevisionSource`, `RawCache`, `HistoricalProvider._raw`, exact namespace checks, `CheckedArchive`, `extract_lead`, relaxed quality and V2 selection. The sampler supplies deterministic diversity from eight discovery strata plus exploration. Strata are discovery hints, never verified primary classes. Classification acquisition is optional; matching archived classification metadata is retained without fetching Wikidata. Metadata attached to a different source revision is not reused.

`prepare` materializes inventory IDs and an isolated DB/cache copy; it does not fetch historical Leads. A batch acquires at most 250 candidates by default. Each next call is explicit. `--through 10000` is a hard candidate-prefix envelope, not an eligible target and not an instruction to launch another pilot. Future approved prefixes can increase up to the frozen inventory size. Processing stops admitting articles when eligible supply reaches 20,000, without lowering quality. Request ceilings remain binding; do not edit checkpoint/header values to extend a budget.

All V2 links remain Lead-only. Existing strict cached identity resolutions are checked as before. A newly processed canonical namespace-0 article can also supply a revision-backed **Candidate Edge** target when no old title-resolution record exists. Duplicate canonical titles stay unresolved; redirects are not guessed and existing integrity errors or unresolved ownership decisions are not overridden. These candidate links serve ranking/discovery only. Neither claims nor evidence are generated.

## Runtime layout and environment

```text
<UIT-approved-root>/
  repo/                         reviewed source files, including uncommitted readiness files
  envs/annotation/              freshly created Linux Python environment
  wheels/                       Linux/CPython/architecture-compatible wheels
  inputs/<bundle-id>/            immutable portable source bundle
  work/annotation-10k/           private writable collection DB, raw-cache, selection
  logs/                         Slurm stdout/stderr; create before submission
  checkpoints/                  stopped-worker backups and transfer receipts
```

Use Python 3.12+ on the actual target architecture. Recreate the environment on Linux; a Windows venv is not portable. [Python venv documentation](https://docs.python.org/3.12/library/venv.html).

After approval, on the approved Linux setup host:

```bash
python3.12 -m venv "$VIFC_ROOT/envs/annotation"
PYTHON="$VIFC_ROOT/envs/annotation/bin/python"
"$PYTHON" -m pip install -r configs/slurm/requirements-tested.txt
"$PYTHON" -m pip install --no-deps --no-build-isolation ./backend
"$PYTHON" -m pip freeze > "$VIFC_ROOT/envs/annotation/environment-linux.txt"
```

The direct versions in `requirements-tested.txt` are those used for local verification; they are not a fully resolved Linux lock. Archive the resolved Linux environment and run the offline suite there before any source request. If setup has no Internet, prepare compatible wheels on an approved matching Linux host, then install with `--no-index --find-links "$VIFC_ROOT/wheels"`. Include build requirements. Do not transfer the Windows interpreter or wheels. No GPU, model weights, Qwen or Groq packages are required.

## Portable transfer and preparation

After transfer preparation is authorized, export from the original host:

```text
python -B scripts/collection-readiness.py bundle --context <original-run-context.json> --inventory <frozen-inventory.json> --integrity-registry <original-registry.sqlite> --output <NEW-bundle-directory>
```

The exporter opens original DBs read-only and uses SQLite backup for a consistent snapshot, including committed WAL contents. It copies raw objects with before/copy/after cache checks, inventory, registry and all referenced refusal/evidence/authorization/RCA artifacts. A portable manifest binds file sizes and SHA-256; registry path translation uses a manifest map. Registry events, approval records, source bytes and historical hash identities are not rewritten. Moving the bundle does not depend on old Windows paths being accessible. Keep the original source directory untouched.

The source DB includes historical acquire checkpoints, revision pins, move indexes and existing identity resolutions. The old V2 JSON checkpoint is an archived result, not a worker checkpoint: retain it separately as a checksummed archive rather than importing it as a 249-record resume. Do not carry a live SQLite main file without its committed WAL; do not copy while another writer runs. Do not use `git archive HEAD` to transfer this working tree: it omits the new uncommitted implementation. Transfer the reviewed runtime file manifest and source files after approval, excluding credentials and local caches.

After the readiness implementation has been committed, a checkout of that reviewed commit can supply the runtime sources. Use the handoff's `runtime-git-files.json` for Git blob/LF hashes; its original `runtime-files.json` records native Windows working bytes. Pin the reviewed commit before transfer.

Once the approved bundle is placed on UIT, verify/preflight and create a NEW workspace:

```bash
"$PYTHON" -B scripts/collection-readiness.py preflight \
  --bundle "$COLLECTION_BUNDLE" --workspace-parent "$VIFC_ROOT/work" --min-free-gib 20
"$PYTHON" -B scripts/collection-readiness.py prepare \
  --bundle "$COLLECTION_BUNDLE" --output "$COLLECTION_WORKSPACE" --request-limit 40000
```

The request limit is a reviewed cumulative physical-attempt ceiling, including failures and continuations. The example 40,000 is a planning ceiling for the 10,000-prefix pilot, not production authorization. The full inventory model suggests reviewing a ceiling around 400,000 if a later collection is intended to cover 100,000 candidates. Choose the appropriate operational scope before preparation; a spent ceiling stops and requires review. Neither a larger ceiling nor preparation authorizes a larger prefix.

Bundle manifest checks precede work. The workspace binds snapshot/config, full ordered cohort, source bundle, all three policies, target and request ceiling. Its collection checkpoint uses per-record JSON hashes in SQLite. Original DB/cache/registry and archived results are never opened for writing. New acquisition only writes to the private workspace.

## Incremental operation and Slurm

One acquisition batch on an explicitly approved egress host:

```bash
"$PYTHON" -B scripts/collection-readiness.py batch \
  --bundle "$COLLECTION_BUNDLE" --workspace "$COLLECTION_WORKSPACE" \
  --mode acquire --through 10000 --batch-size 250 --max-requests 1000 --allow-network
```

Then an offline processing batch:

```bash
VIMFC_OFFLINE=1 "$PYTHON" -B scripts/collection-readiness.py batch \
  --bundle "$COLLECTION_BUNDLE" --workspace "$COLLECTION_WORKSPACE" \
  --mode process --through 10000 --batch-size 250
```

Alternate batches and inspect receipts/yield. Do not acquire all 60k–100k Leads at once. `collection_invocations` and `last-invocation.json` distinguish completed, missing/unsupported, needs-review, retryable, budget, interruption, operational and integrity-fatal outcomes. Progress reports `remaining_to_acquire` (unseen + retryable), `remaining_to_process` (acquired raw), committed `eligible_supply`, `missing`, `unsupported` and review `blockers`. `remaining_in_prefix` includes both backlogs and review blockers. A missing/unsupported source is not a quality rejection. Unknown nonretryable source failures are withheld for review; identity/hash/cutoff contradictions remain fatal. Old fatal flags are not automatically cleared.

The worker publishes `selection.json` atomically under its writer lock, before its receipt. It contains the existing four edge states and selected graph metrics. Failed replace preserves the old export and reports `selection_published=false`; pending files are not promoted. An explicit later offline invocation can regenerate export from committed records. `pilot_accepted` remains false; no corpus freeze occurs.

Exit codes: 0 completed; 2 review required; 5 request budget reached; 75 retryable source or operational/storage stop; 130 interrupted; 3 integrity/preflight failure. Read `status`, `expected_stop`, `budget_scope`, `stop_reason.retry_at`, `blockers` and `receipt_errors`. Unavailable DB progress is null rather than zero. Receipts are best effort if storage itself is unavailable; the CLI preserves the original error/receipt. A preflight failure is emitted to stderr and does not update a stale stored receipt. Request counters are conservative pre-send charges, including uncertain in-flight attempts after a hard kill.

Use `configs/slurm/annotation-pilot10000.sbatch` only after review. Supply UIT account/partition and actual paths outside the template. It requests one CPU node, four CPUs, 16 GiB RAM and four hours, with early TERM forwarded to Python. It runs exactly one bounded batch, defaults to offline processing, requests `--no-requeue` and rejects nonzero `SLURM_RESTART_COUNT`. Acquisition mode requires explicit egress confirmation. Default logs are `slurm-%x-%j.out/.err` in Slurm's submission/`--chdir` directory. For custom paths, create their parent before submission. Directives do not expand shell variables; Slurm does not transfer application files. [Official sbatch documentation](https://slurm.schedmd.com/sbatch.html).

## Compute nodes without Internet and checkpoint transport

No Internet on compute is acceptable: acquire on an approved data-transfer/egress host, then run `process` under Slurm. Never run acquisition on a login node without UIT approval. Prefer a shared, persistent, SQLite-compatible storage service if both hosts can access it, with exactly one writer and reliable OS locks. Do not assume SQLite WAL works on NFS or that shared storage provides compatible shared-memory/locking semantics.

If acquisition and compute need separate copies, stop the writer first, acquire its writer lock, checkpoint/close SQLite, create a coherent SQLite backup, and copy its complete private raw-cache plus `workspace.json` and latest selection. Hash the closed DB/cache/header/selection set, transfer to a NEW directory, rehash on arrival, and resume only that copy with the same immutable bundle. Keep the previous copy read-only; never run both copies. Source inputs are already covered by the immutable bundle manifest. A non-graceful kill can leave a stage lease of up to five minutes; wait for its recorded expiry, never delete leases/locks or promote pending files automatically. Orphan raw objects are validated by the existing revision source on recovery.

Ephemeral node-local scratch without a durable stopped-worker backup is a deployment blocker. Keep SQLite working files on storage whose locking/WAL semantics UIT supports, and retain checkpoints on persistent storage. A transfer tool should copy bytes only; provenance translation belongs in the verified artifact map, not edits to source records or registry events.

## UIT checks required before approval to execute

- Account, partition, CPU/RAM/time limits, Python 3.12 availability, target architecture and package/wheel installation policy.
- Persistent storage quota, actual free bytes and inodes, retention/backups, supported SQLite WAL and `flock`, one-writer ownership and directory search/write permissions. `preflight` checks ordinary disk free space, permissions and available inodes; it cannot establish per-user quota or filesystem crash guarantees.
- Working directory/logs exist at submission; `umask 077`, appropriate owner/group, writable private outputs and readable immutable inputs; no credentials embedded in code or logs.
- Approved `vi.wikipedia.org` HTTPS egress, DNS/proxy/CA trust, configured contact/User-Agent and cluster network policy. No network probe is performed during readiness. The client uses maxlag=5, serial requests paced at least one second apart, one HTTP attempt per query and durable Retry-After; new transient failures stop the invocation.
- Hash-verified source bundle and runtime file manifest, original source preservation receipt, native Linux focused/full tests and real filesystem interruption/resume checks before data collection.
- No server action until the user reviews the technical handoff and authorizes transfer/preparation/execution. Production progression and human claim creation remain separate decisions.
