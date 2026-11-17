# Phase 2A â€” local snapshot and consistency verifier

Base commit: `b520f11`. New implementation is isolated in `backend/src/vimfc/pipeline/local_snapshot.py`; new tests are `tests/integration/test_local_snapshot.py`. No production CLI, acquisition, policy, ranking, Slurm template or existing test was edited. The earlier modified `test_annotation_builder.py` remains pre-existing work.

## Components and contracts

- `snapshot()` requires explicit `synthetic=True` and `stopped_writer=True` caller attestations, a fresh separate output and a local filesystem. Windows fixed volumes and Linux ext4 are supported; network/unknown mounts, junctions and symlinks are refused. This is not an ownership or process-fencing implementation. Caller must actually stop all writers.
- Source DB is opened read-only immutable only after rejecting any WAL/SHM/journal sidecars; SQLite Backup API creates the output database, which alone is converted to DELETE. Source files are hashed before/after, with no journal-mode change. Thus a cleanly closed WAL-format source is supported; a stopped source with pending WAL is deliberately refused, not checkpointed or silently copied.
- `verify_workspace()` validates actual incremental header/DB/run/config/cohort/bundle identity, integrity and schema version, request counter, collection record checksums, completed-stage checksums, registered source objects, raw content SHA-256/SHA-1 and cutoff, selected source references and checked article provenance. Existing `CheckedArchive`/registry verification and portable bundle resolver are reused. For bundle source/registry DB audit, closed temporary local Backup API copies avoid mutations by the old read-only adapters. Nonempty bundle WAL/journal is refused. All reachable `cache_ref` objects in JSON database columns are checked; orphan objects are retained.
- `generation-manifest-v1` has exactly `format`, `identity`, `compatibility`, `checkpoint`, `artifacts`. Identity contains workspace/run/attempt/epoch/generation/bundle IDs. Checkpoint includes the original bound header (snapshot/config/cohort/inventory/policy labels, request limit), policy hashes, record-state digest, attempts, count and selection disposition. Compatibility includes caller runtime pin, SQLite user version and actual schema fingerprint. Artifacts are canonical relative paths with byte size/SHA-256. Duplicate JSON keys, unsafe paths, links, unsupported versions and mismatches are rejected.
- Selection is **always stale if present**, otherwise absent. Existing production selection has no epoch binding: this phase does not invent one or certify selection as current. It copies the artifact but returns its explicit disposition. Classification/dependency processing, evidence semantics and ranking are not recomputed.
- `verify_generation()` requires caller-supplied `trusted={identity,manifest_sha256}` and `compatible={runtime,user_version,schema_hash}`. It verifies these before opening SQLite, verifies exact artifact files/hashes, then checks actual schema and workspace consistency. This is a minimal expectations interface, not a trusted ledger. The caller must protect and independently choose those expectations; deriving them from an untrusted manifest defeats the trust boundary. Runtime is an opaque caller pin, not automatic attestation of installed code; use a reviewed runtime-files digest in future integration.
- `restore()` verifies the generation first, copies only into a fresh local directory, repeats verification, opens SQLite read-only with no migration and returns a receipt. It does not invoke `PipelineStore`, activate WAL or resume acquisition.

No production callers import this module. Reuse points are `RawCache.get`, canonical/digest helpers, streaming `file_hash`, `read_bundle`, and `CheckedArchive` including read-only registry proofs. No NFS pointer or publication API exists here.

## Offline evidence

Focused final command:

```powershell
.tools/python312/python.exe -m pytest tests/integration/test_local_snapshot.py -q -p no:cacheprovider --basetemp=.tools/phase2a-verified
```

**20 passed, 1 skipped** (Windows lacks symlink creation privilege). Tests cover valid restore; WAL-header source preserved byte-for-byte; closed DELETE output without sidecars; missing/corrupt raw and SQLite; trusted hash, path, binding, snapshot, request/checkpoint, schema/runtime and malformed/duplicate/version metadata; absent/stale selection; retained orphan; source pending-WAL refusal; interrupted snapshot without a completed manifest; and NFS rejection before `sqlite3.connect`. Symlink rejection code exists but its native fixture is skipped on this Windows host; junction checks use the same path guard.

Full offline suite command:

```powershell
.tools/python312/python.exe -m pytest -q -p no:cacheprovider --basetemp=.tools/phase2a-full-new
```

**857 passed, 1 skipped in 380.71s.** The full suite ran during implementation; the final registry-audit and local path refinements were subsequently checked by the focused suite. No pre-existing production module changed. Logs: `.tools/phase2a-evidence/full-offline.log`, `focused-verified.log`.

Snapshot and independent restore receipts, including trusted manifest digest, compatibility metadata, verified checkpoint and `source_unchanged=true`:

`.tools/phase2a-verified/test_snapshot_restore_source_p0/verification-receipts.json`

The valid test asserts both workspace and bundle inventory hashes unchanged. All fixtures and partial/interrupted outputs are under newly named `.tools/phase2a-*` temporary directories. Test-level network denial comes from existing `tests/conftest.py`; default network-marked tests are not opted in. No UIT connection, job, source download or API call was performed.

## Preservation and remaining work

The independent preservation scan covers **46,742 pre-existing files; zero changed or missing**, from tracked files, data, reports and the prior protected-artifact inventory. Its before/result records are `.tools/phase2a-evidence/before.json` and `preservation.json`; this scan is supplemental to per-fixture source/bundle byte checks. No old workspace is opened through writable SQLite.

Remaining restrictions are explicit: synthetic caller attestations are not process fencing; source pending WAL capture is unsupported; bundle DB audit needs a stopped/empty-WAL boundary; selection has no valid epoch binding; runtime pin/control expectations need external provenance; no ownership transfer, ledger/reservations, NFS publication, crash durability or production operation is implemented. File-copy interruption can leave a partial local directory, but no verified receipt is returned and restore cannot accept it without valid independent expectations. Phase 2A does not establish UIT storage approval or production readiness.

Recommended Phase 2B: review the manifest/runtime and selection epoch contracts, add failure-injection for registry refusals and deeper malformed-source cases, define the controlled ownership/request-reservation interface, then implement generation publication/restore orchestration only under separate approval. Any pending-WAL support needs a separately reviewed source-preservation boundary. UIT/NFS validation requires its own authorization.

`NFS_PUBLICATION = NOT_AUTHORIZED`; `UIT_EXECUTION = NOT_AUTHORIZED`; `PILOT_10000 = NO_GO`.

Within the documented synthetic, closed-source boundary: `PHASE_2A_IMPLEMENTATION = PASS`; `LOCAL_SQLITE_SNAPSHOT = PASS`; `WORKSPACE_CONSISTENCY_VERIFIER = PASS`; `FRESH_LOCAL_RESTORE = PASS`; `OFFLINE_TESTS = PASS`; `PRODUCTION_SEMANTICS_PRESERVED = VERIFIED` (no pre-existing production source changed, with preservation hash evidence). These do not certify production deployment or the explicitly unsupported cases above.

