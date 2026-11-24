# FAST-20K: one-paragraph annotation collection

This is a separate, explicitly authorized collection variant. It does not alter
`lead-4`, strict classification, the legacy frozen schema, earlier experiments,
or the Phase 2E synthetic transport's prohibition on real network transport.
No claim, gold label, evidence judgment or semantic hop is generated.

## Binding and source reuse

`configs/fast20k.json` binds snapshot `2026-10-25T03:00:00Z`, at most 100,000
unique namespace-0 candidates, exactly 20,000 eligible articles, the eight existing
soft quotas, `quality-relaxed-1`, `annotation-edge-1` and `rank-annotation-1`.
`plan_seed_cohort()` provides the universe and stable ordering. A documented stable
partition places cached historical references first. The resulting ordering gets
its own manifest/cohort identity. Discovery strata remain hints.

The verified bundle is immutable. Preparation copies its SQLite, registry and raw
objects into a new private workspace. Only snapshot-compatible raw lookup metadata
and checksummed namespace proofs are reused; extraction and quality are recomputed.
The existing historical provider and `_checked_acquisition()` retain selected
revision, move horizon, namespace, registry and source hash checks. A previous
classification with matching source identity and record checksum is labeled
`archived_project_metadata`, with `historical_proof_replayed=false`; it is not
promoted to independently replayed Wikidata proof. Qwen never replaces a known
archived primary. No fresh Wikidata acquisition is performed.

## New extraction and publication contract

`lead-first-paragraph-1` uses the existing AST renderer and Vietnamese sentence
splitter on the first meaningful lead prose boundary. It never extends a short
first paragraph. Links only come from that paragraph. Unknown content affecting
it remains unresolved; safely discarded later content cannot poison it.

The separate `fast20k-annotation-corpus-1` publisher replays source admission,
source SHA-1/SHA-256, extraction, sentence/link spans and relaxed quality. It requires
20,000 distinct articles. It writes `corpus.sqlite`, `corpus.jsonl`, `graph.jsonl`
and `manifest.json` to a new directory only, and preserves four edge states.
SQLite includes original raw revisions as provenance objects, separate from the
single retained paragraph. Target full revisions are not duplicated per link.
Candidate edges and potential A→B→C paths are not verified annotation evidence.

Admission stops as soon as the eligible pool reaches the target. Existing
deterministic graph-aware selection then orders/selects that pool. This does not
claim the globally best 20,000 among all 100,000, nor authorize further acquisition
to improve graph scores. Unknown labels and quota deviations are reported.
`corpus_complete` becomes true only after publication validates successfully.

## Self-hosted Qwen3.5-4B

CPU-only llama.cpp `b12651`, Q4_K_M artifact
`unsloth/Qwen3.5-4B-GGUF` at
`e87f176479d0855a907a41277aca2f8ee7a09523`, based on
[`Qwen/Qwen3.5-4B`](https://huggingface.co/Qwen/Qwen3.5-4B).
Model SHA-256 is pinned in the profile. The server listens only on
`127.0.0.1:18865`, with 3 CPU threads, no GPU and one inference slot.
`chat_template_kwargs.enable_thinking=false` explicitly disables default thinking,
as documented in the model card and
[llama.cpp server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

The input is historical title, the complete retained paragraph and discovery
hints. Temperature/seed are zero, response tokens bounded, and schema/taxonomy
validated. A separate suggestion contains model/revision, prompt version,
parameters, uncertainty and a raw-response table reference. Invalid, uncertain,
over-context or failed inference yields Unknown and preserves the article and
retry evidence. One background inference overlaps serial acquisition. Pending
and failed records remain in the durable queue; failed inference is not silently
repeated or promoted. Pending classification need not finish before publication.
Model loading and inference deadlines are bounded; no application collection
walltime limit is added. The child server is stopped when its owner exits.

## Orchestration, network and recovery

FAST-20K treats the exact nonretryable `SourceUnavailable` error
`Ambiguous historical move chain` (code `source_unavailable`) as article-local:
save `skipped_ambiguous_move` with its reason and page ID, exclude that article,
and continue. Resume does not retry it. All other source-integrity guards remain.
An older checkpoint stopped by exactly this error can recover only when a failed
acquisition record for a pending/deferred article proves the same error. Recovery
records skipped IDs and the former fatal reason in `ambiguous_move_recovery`,
preserves stage evidence and spending, and clears only that matching fatal flag.
A missing matching record or a different fatal reason continues to block resume.

`scripts/fast20k.py run` drives an indexed candidate/work queue across successive
100-article checkpoint intervals. It stops on target, candidate exhaustion,
HTTP budget, cooldown, interruption, integrity failure or operational error.
Deferred candidates have bounded attempts; cooldowns never busy-loop. Coverage
snapshots preserve quality/rejection, classification and candidate graph metrics.
Graph reporting failure cannot discard a checkpoint; publication remains strict.

The separate authorized transport pins GET/HTTPS to viwiki `/w/api.php`, requires
`maxlag=5`, disables transport retries/proxy environment, and charges before I/O.
The **200,000-attempt campaign ceiling** is independent of the candidate cap:
nominally up to two calls per uncached candidate (revision selection/content).
Move-log continuations, namespace lookups and retries also consume that same
ceiling; therefore reaching 100,000 processed candidates is not guaranteed.
Cache hits cost no new attempt. The receipt and SQLite counters reconcile upward
on every resume, including cache-only resumes. Starts are at least one second
apart, one host request is active, and existing durable Retry-After is retained.
No budget extension or reset is automatic.

This is ordinary single-writer campaign accounting, not independent rollback
protection, a production witness, physical fencing or a claim of Phase 2F readiness.
An uncertain sent attempt is conservatively spent. The private campaign lock
prevents concurrent cooperating writers.

Active WAL must be on verified node-local **ext4**. Every 100 processed articles
and on graceful stop, the main writer pauses for a closed SQLite backup and raw
cache copy to persistent storage; an atomic receipt selects the complete checkpoint.
Keep three owned DB generations. Originals are not cleaned or changed. Node loss
may lose article work since the latest completed checkpoint; the separately
persisted attempt receipt prevents refunding those attempts. NFS is not treated
as safe active WAL or an independent anti-rollback witness. Existing raw hashes
are verified on reuse/publication. A hard-killed stage can retain a five-minute
lease: wait for expiry; never delete its lease to force recovery.

SIGTERM/SIGINT requests stop at the next safe boundary. The running HTTP/inference
attempt is bounded, then a checkpoint is saved. Slurm sends TERM 600 seconds early
to allow the bounded inference and a closed persistent backup to finish.
No automatic requeue, second job or post-submission polling is implemented.

## Operator execution

Before submitting, confirm UIT account/partition/QoS, approved and viable compute
HTTPS egress, a real contact/User-Agent inherited from the source configuration,
storage quota, immutable source identity, locked Python 3.12 environment, pinned
model/runtime, and final tested commit. Missing source access blocks submission:
the available historical cache alone is too small for 20,000 eligible articles.
The shell template requires `FAST20K_EGRESS_APPROVED=1`; do not set it based solely
on a login-node connection, general Internet availability or Slurm permissions.

`configs/slurm/fast20k.sbatch` requests 4 CPUs, 16 GiB, 0 GPUs, 72 hours and
`--no-requeue`. Supply approved site account/partition/QoS outside the template.
Set `FAST20K_REPO`, `FAST20K_COMMIT`, `FAST20K_BUNDLE`, `FAST20K_STAGE`,
`FAST20K_PYTHON`, `FAST20K_RUNTIME`, `FAST20K_MODEL` and the confirmed egress flag.
Precreate Slurm log directories. Only one `sbatch --parsable` submission is allowed.
After acceptance capture Job ID, optionally query once, and stop immediately.

Explicit manual resume, only after the previous writer has stopped:

```bash
FAST20K_EGRESS_APPROVED=1 "$FAST20K_PYTHON" -B scripts/fast20k.py run \
  --bundle "$FAST20K_BUNDLE" --workspace /local/NEW-PRIVATE-RESUME/workspace \
  --durable "$FAST20K_STAGE/campaign" --output "$FAST20K_STAGE/corpus" \
  --runtime "$FAST20K_RUNTIME" --model "$FAST20K_MODEL" \
  --logs "$FAST20K_STAGE/logs/manual-resume" --allow-network
```

Create and verify the new ext4 parent first. Existing workspace directories are
never reset. Resume restores the latest complete checkpoint into a new local
directory and reuses the original profile, source bundle and campaign counter.
Interpret the explicit stop reason, not merely process exit: 0 published target,
2 shortfall, 3 integrity/preflight, 5 HTTP ceiling, 75 cooldown/operational, 130 stop.
