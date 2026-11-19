# Phase 2D.4.2 offline worker activation

- `backend/src/vimfc/pipeline/worker_activation.py` verifies fixture termination evidence, runs fresh-local restore using independently pinned expectations, and asks the transactional authority to activate the successor.
- `backend/src/vimfc/pipeline/transactional_control.py` reuses the existing one-shot launch transition and mock dispatch gate. The activation decision records the verified generation and synthetic termination identity. A successful launch is witnessed before the mock request callable is returned. The former standalone launch remains available for lower-level synthetic contract tests.
- `tests/integration/test_worker_activation.py` covers successful activation and dispatch, stale owner, old or mismatched generation, missing, string, mismatched or uncertain evidence, corrupt restore, authority rollback, unavailable witness, unresolved PREPARED, and duplicate activation under the same or a different decision ID.

The gate runs restore itself. A receipt supplied by a caller is not an input. Trusted fixture setup supplies the generation pins and a separate termination verifier. A structured assertion without verifier approval cannot pass. Under the authority write transaction, activation compares the verified previous launch with the termination identity, binds the restored generation to that worker's attempt and epoch, and requires the new claimed job, a strictly newer epoch, and an unused reservation. A matching reconciled pointer is required when the authority has one. For the 2D.2 history without a reconciled pointer, the independent generation pin is required in addition to the previous launch match. A denial after copy can leave a verified but inactive local directory.

Selection remains stale if present and receives no epoch binding. The verifier is a trusted dependency supplied by the synthetic fixture. It is not proof of physical fencing, authorization for a real process, or a production trust service. The local restore destination must remain private through activation.

Targeted SHA-256 matched before and after for `backup_recovery.py`, `incremental_collection.py`, and `test_annotation_builder.py`. The existing annotation-builder diff retained blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. `git diff --check` passed.

- Transactional control SHA-256 moved from `5137567F1CBBA1E17DA9C41FBB73D8F6240F6259ED840D38F56712AE95F56A5D` to `5BF073A071CC57753AC876529BEAB3D9074C66DD1CD27335121541135B48E490`.
- Worker activation SHA-256 is `11B85F88D54936F4A2DD3D1C1C017F2EBF1709E7926DB7E46E4727EE407F60AC`. Its integration test SHA-256 is `909CC0D7B584667896DEAE2CF187CA36ED88BCBA38192F813129A3D28A34D870`.
- Focused activation, authority and backup/restore suites passed 78 tests with 1 Windows symlink privilege skip.
- Full offline regression passed 1035 tests with 3 Windows symlink privilege skips in 511.47 seconds. Both runs used `VIMFC_OFFLINE=1` with pytest's socket guard.

The recommended next phase is a separately authorized offline selection epoch binding contract. It should bind revalidated selection to the verified generation and activation epoch and deny stale selection at its consumer boundary.

