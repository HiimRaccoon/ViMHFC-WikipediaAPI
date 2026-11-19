# Phase 2D.3 offline backup, restore, and recovery

`backend/src/vimfc/pipeline/backup_recovery.py` copies a trusted current generation and, when required, its independently trusted immediate parent. It reuses publication pointer and manifest verification. The backup directory is fresh, generation bytes are checked before and after copy, and the atomic pointer is written last. Transfer opens no SQLite database.

## Restore verification order

1. Validate trusted pointer expectations and immediate ancestry, require a fresh separate local path, and verify the current pointer and both generation manifests and artifact sets.
2. Copy only the current generation. Check its exact file set, byte sizes, SHA-256 hashes, manifest identity, and trusted compatibility metadata. Recheck the backup pointer and ancestry. No SQLite has opened through this point.
3. Inspect the local authority against its witness. Require acknowledgment and a matching workspace, run, bundle, and runtime. Any unresolved publication intent requires operator review. If the authority has a reconciled pair, it must match. For an initialized 2D.2 authority without a reconciled pointer, independent expectations identify the historical backup. Restore does not adopt it as the authority head.
4. Open the fresh generation for SQLite checkpoint, schema, and provenance verification using the existing snapshot verifier and the independently provided local bundle. Recheck the restored bytes, pointer, and authority view. Selection remains stale if present, with no epoch binding or permission to activate it.

## Failure cases

| Case | Outcome |
| --- | --- |
| Interrupted backup before pointer replacement | Partial bytes may remain in the fresh destination. It has no usable pointer. |
| Interrupted restore or copy corruption | Local files may remain. No success receipt is returned. A deliberate later attempt requires another fresh path. |
| Missing/extra files, SQLite sidecars, corrupt current or parent | Deny before SQLite opens. No fallback. |
| Stale expectations, pointer mismatch or pointer replacement during copy | Deny before SQLite opens. No automatic republish. |
| Actual checkpoint/schema differs with self-consistent artifact hashes | Local SQLite verification denies the restore. |
| PREPARED with or without witness acknowledgment | Deny before opening restored SQLite. Authority, witness, backup, and reservation totals remain unchanged. |
| Matching two-generation history | Preserve both backup generations. Restore only the independently expected current generation. |

## Targeted preservation

The pre/post SHA-256 values matched for `local_snapshot.py`, `generation_publication.py`, `publication_reconciliation.py`, `transactional_control.py`, and `tests/integration/test_annotation_builder.py`. The existing annotation-builder patch retained Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. Tracked status showed only that pre-existing modification and `git diff --check` passed. Fixture tests also compare source workspace, bundle and generation bytes.

- `backend/src/vimfc/pipeline/backup_recovery.py` SHA-256 `9DE8C16D4AFEFAF32F07F8040929872077D422CC1CA689A3D97CD044DE26740C`.
- `tests/integration/test_backup_recovery.py` SHA-256 `FE1F280BF072396EC53B059FD3D4054D19AFE940EBC77B1CF6E380CC32084BDE`.

## Verification

Focused offline tests: 37 passed, including 18 backup/recovery cases and 19 Phase 2D.2 authority regression cases.

Full offline regression: 994 passed, 2 skipped in 506.09 seconds. Both skips were existing Windows symlink privilege checks. Ran with `VIMFC_OFFLINE=1` using `.tools/python312/python.exe -B -m pytest -q --tb=short -p no:cacheprovider --basetemp=.tools/phase2d3-full`.

The transfer, restore, pre-SQLite checks, fail-closed recovery, and offline regression passed in the synthetic scope. Real NFS remains untested, production authority and UIT execution remain unapproved, and the 10,000-item pilot remains no-go.

## Production blockers

Local fixture verification does not establish power-loss durability, real NFS semantics, or physical fencing. The source must remain quiescent and the copy must remain private through verification. A receipt is evidence about copied data, not permission to resume. Production still needs fenced state adoption, a selection revalidation/epoch binding step, and separately administered authority and witness recovery. Phase 2D.2 holds the authority SQLite write lock while invoking mock transport. Extending that pattern to real transport could block control decisions for an unbounded time and would need a separate dispatch design. No network transport was added.

