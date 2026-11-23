# FAST-20K preparation and Qwen3.5-4B handoff — 2026-11-24

`FAST_20K_SUBMISSION = BLOCKED`

Implementation and focused offline verification are complete. **No collection job
was submitted.** No Wikipedia/Wikidata HTTP acquisition was performed. No completed
20,000-article corpus is claimed. Compute HTTPS egress permission and viability
have not been established; the existing cache alone cannot supply the target.
The operator has been asked for the applicable UIT-approved source-access path.

## Reviewed implementation

Baseline `2756e1c9ccf49f2f845b2ce0eff328681bfa66c2`; isolated branch
`codex/fast20k-one-paragraph-20261124`. Only new FAST-20K files are added.
See [the execution contract](../../docs/fast20k.md) for source validation,
classification limitations, budget, checkpoint, graph and publication semantics.

- New first-paragraph extractor; legacy `lead-4` unchanged.
- Indexed campaign queue and immutable inventory binding; cache-first stable
  ordering over the existing `seed-only-2` universe, capped at 100,000.
- Serial historical viwiki acquisition, cumulative ceiling 200,000 physical
  attempts, pre-send conservative receipt, pacing >=1 second, maxlag=5, durable
  cooldown and three bounded candidate attempts. No Wikidata classification fan-out.
- Separate Qwen suggestion queue and separately validated annotation publisher.
  Unknown never becomes a quality gate; candidate edges never become verified gold.
- Closed persistent checkpoints with three owned DB generations; active SQLite
  on node-local ext4. Graceful TERM, no automatic requeue or extra submission.

This is not a Phase 2F trusted executor/witness implementation. No independent
rollback resistance, physical fencing or production-readiness claim is made.

## Verification actually executed

Final focused selection: new extraction/Qwen/CLI/campaign tests plus legacy
extraction, Vietnamese sentences, relaxed quality, graph states, frozen inventory,
namespace/source checks, historical identities and incremental collection.

| Environment | Result |
|---|---|
| Windows, offline socket guard | **172 passed, 1 skipped**, 25.19 s |
| Native Linux, new locked Python 3.12 venv, ext4 test scratch | **173 passed**, 28.22 s |
| Real historical cache, first batch | 100 processed, 36 eligible, 0 source attempts |
| Restore into new ext4 workspace, next batch | **200 total processed, 69 eligible, 0 source attempts** |
| Integrated Qwen3.5-4B CPU smoke | **READY**, schema-valid `Person`, uncertainty=false |
| Dedicated Slurm shell | `bash -n` passed; resource `sbatch --test-only` passed |

The Windows skip is the POSIX SIGTERM recovery test; it passed on Linux. Both real
cache runs intentionally stopped with `CHECKPOINT_STOP`, exit **130**; this is not
a failure or corpus completion. The second run retained the same run/binding and
restored counters. Source bytes were reprocessed under the new extraction policy.
No unrelated full regression was rerun, as requested.

Earlier development attempts are retained: Windows SQLite-close failures were
fixed; an interrupted initial smoke preparation exposed per-row initialization
and was replaced by a bulk transaction and ext4 workspace; Qwen timeout/string
boolean failures were corrected with explicit no-thinking, strict JSON Schema,
and ext4 model placement. Only the final results above support readiness claims.

## Self-hosted model

- Base: `Qwen/Qwen3.5-4B`.
- Quantized artifact: `unsloth/Qwen3.5-4B-GGUF`, Q4_K_M.
- Revision: `e87f176479d0855a907a41277aca2f8ee7a09523`.
- SHA-256: `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`.
- llama.cpp: `b12651`, build commit `f5e6915c2`.
- Runtime archive SHA-256:
  `17b242259181d5b1b1f7719aba27929700a8b0dab39bfaef1b971a992b397b38`.
- CPU-only, 3 threads, one slot, context 4096, `127.0.0.1:18865`.
- Final measured smoke: 142 prompt tokens / 17.70 s; 13 completion tokens /
  5.27 s. This is a login-node bounded smoke, not a compute throughput guarantee.
- No persistent model server is left on the login node. The batch owns its child
  server, copies weights/runtime to ext4, and stops it on exit.

## Evidence locations

The new UIT stage is `/datastore/binhvp/BuiTrongTan/ViFC/fast20k-20261124`.
Paths below are relative to that stage:

- `evidence/linux-publish-final.xml`, `logs/linux-publish-final.stdout/.stderr`,
  `evidence/linux-publish-final.exitcode`.
- `evidence/qwen-v3/qwen-smoke.json`, `evidence/qwen-v3/qwen-server.log`.
- `evidence/final-environment/environment.json`, `evidence/packages.txt`.
- `evidence/source-transfer.json`, `evidence/model-artifacts.json`.
- `cached-final-checkpoints/progress.json`, `coverage.json`, `latest.json`:
  **smoke-only**, not the future collection campaign.
- `evidence/cached-final.exitcode`, `evidence/cached-v3.exitcode`;
  `logs/cached-final.stdout/.stderr`, `logs/cached-v3.stdout/.stderr`.
- `evidence/slurm-test-only.stdout/.stderr`: scheduler simulation only. Its printed
  test-only identifier is **not** a submitted Job ID.

Local final Windows evidence is under the isolated checkout's
`.tools/fast-evidence/windows-publish-final.xml` and corresponding `.stdout`.
The immutable bundle ID is
`af48780cfbb456a31b68424f37fc5718d50b134f9d9de28a09034b13c4eaf267`;
its transferred tar SHA-256 is
`1579a33525f3779681a5a8f78f62f405544aabdb85490c0ad200bbfd64b637b7`.
The old annotation-builder fixture remains SHA-256
`35f3a77ed26f8039fa97124a223c6d93e6448b0b7cfaf65dc201bbf054472cae`.
Old repositories, source datasets and package installations were not edited.
This is targeted preservation evidence, not proof about every untracked artifact.

## Planned job — not submitted

| Field | Value |
|---|---|
| Server | `uit` / `login01.cm.cluster`, account user `binhvp` |
| Name / partition / account / QoS | `vifc-fast20k` / `normal` / `uit` / `mig1-18` |
| CPUs / memory / GPUs / walltime | **4 / 16 GiB / 0 / 3-00:00:00** |
| Candidate cap / eligible target | **100000 / 20000** |
| Snapshot | `2026-10-25T03:00:00Z` |
| Strategy | HYBRID: existing checked historical cache, then approved API |
| Existing inventory | 100000 distinct candidates; discovery metadata only |
| Previously verified article coverage | 1106 in the preliminary 20K selection |
| New-policy smoke eligibility | 69 of 200 reprocessed articles; not all available sources |
| Qwen status | READY, self-hosted CPU; inference remains optional and bounded |
| Actual Job ID | **NOT_SUBMITTED** |

Future collection paths relative to the stage are `campaign/progress.json`,
`campaign/latest.json`, `campaign/raw-cache`, and `corpus/` (the four final artifacts).
They are **planned**, not populated by the smoke runs. Active job workspace is
`/local/fast20k-binhvp-${SLURM_JOB_ID}/workspace`; logs should be directed to
`logs/slurm-%x-%j.out/.err`. Use the exact tested Git commit for deployment and
the resume command in `docs/fast20k.md`, after stopping the previous writer.

Remaining submission gate: a confirmed, permitted compute HTTPS path to viwiki,
or an approved acquisition host / sufficient compatible historical bulk source.
Direct compute SSH was unavailable; login-node access does not establish compute
egress. General free space and inodes were checked; per-user quota tooling was
unavailable, so no per-user quota guarantee is claimed. Once a viable path is
established, the authorized limit remains **one** actual batch submission, capture
and verify its accepted Job ID, then stop immediately.
