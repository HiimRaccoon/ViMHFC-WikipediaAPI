# Phase 2A.1 Safety Hardening Handoff — 2026-11-17

Reviewed baseline: `da5d14fecfc080b3a38afe18ed624ae5fe2e88ef`. Local Git HEAD remains `b520f11` because the prior Phase 2A commit was published through the GitHub connector; the accepted Phase 2A files were already present locally. This task used no network, UIT, Slurm, acquisition, commit or push.

## Exact changes

- `backend/src/vimfc/pipeline/local_snapshot.py`: add shared `separate_destination()`; call it from snapshot and restore before creating output or verifying/copying a generation. Add optional keyword-only `protected_inputs=()` to both synthetic entry points.
- `tests/integration/test_local_snapshot.py`: add overlap and path-alias regression tests.
- This handoff and new local evidence under `.tools/phase2a1-evidence/`.

The pre-existing `tests/integration/test_annotation_builder.py` modification was retained. No acquisition, provenance, quality, ranking, selection, production integration, NFS publication, ownership or budget code was modified.

## Root causes and fixes

Snapshot checked overlap only with the source workspace and compared a resolved source with a merely absolute output. It did not protect the immutable bundle. Restore only required a nonexistent output and verified the input before checking that destination: a nested destination could recursively copy into the generation or mutate a trusted tree.

Both entry points now inspect lexical path ancestors for symlink/junctions before resolving paths. The shared guard compares canonical paths in both directions, including equality, against source/generation, bundle and every explicitly supplied protected tree. It rejects overlap and existing destinations before output creation, input audit or copytree. Existing Path semantics handle Windows case comparisons; resolve removes dot/dotdot aliases. No output parent directories are created by the guard.

Additional protected trees must be explicitly supplied by the caller. Existing callers retain their signatures and automatically protect their source/generation and bundle.

## Regression coverage

24 parametrized cases cover snapshot/restore × source-or-generation/bundle/explicit tree × nested/dotdot/ancestor/equality. Each case checks exact path set and all protected file bytes, including manifests; monkeypatch traps ensure no verification or copytree occurs before rejection. Two native alias cases exercise directory symlink and Windows junction paths for both entry points. Existing successful sibling snapshot/restore tests remain.

Focused final command:

```powershell
.tools/python312/python.exe -m pytest tests/integration/test_local_snapshot.py -q -p no:cacheprovider --basetemp=.tools/phase2a1-focused
```

Result: **45 PASS, 2 SKIPPED in 19.34 seconds**. Both skips require Windows symlink creation privileges. Native Windows junction rejection PASS. Linux ext4/findmnt and native Linux symlink execution were not run; Linux verification remains outstanding under the no-UIT restriction.

Full final command:

```powershell
.tools/python312/python.exe -m pytest -q -p no:cacheprovider --basetemp=.tools/phase2a1-full
```

**882 PASS, 2 SKIPPED, 0 FAIL in 435.29 seconds (exit 0)**. Both focused and full suites ran against the final modified code and tests; there were no subsequent implementation edits. The two full-suite skips are the same Windows symlink privilege cases.

## Preservation evidence

Independent SHA-256 comparison: **46,743 pre-existing protected files, zero changed or missing**. Regression overlap cases additionally preserve every fixture input file, its manifest and the complete path set.

Local evidence: `.tools/phase2a1-evidence/focused.log`, `full-offline.log`, `before.json`, `preservation.json`, `preservation.py`. The baseline covers tracked files, existing reports/data and the prior protected inventory, including the unrelated modified test. Baseline hashing started before implementation edits; protected inputs were never written by this task. Synthetic test output uses newly named `.tools/phase2a1-focused` and `.tools/phase2a1-full` trees.

## Limits and recommendation

These are path containment guards for the synthetic stopped-writer boundary, not ownership/fencing controls. Concurrent hostile filesystem replacement between validation and copying remains outside this phase. Hard-link aliasing, Windows mount-point aliases beyond rejected junctions, and production storage ownership are not certified. Existing pending-WAL refusal and local filesystem requirements remain. No production readiness or NFS durability claim follows from these tests.

Phase 2B may be considered for separate review after accepting these results; execution and production integration remain unauthorized. Native Linux regression/symlink verification should be included when separately authorized.

`PHASE_2A_1 = PASS`; `PATH_CONTAINMENT = PASS`; `OFFLINE_REGRESSION = PASS`; `PROTECTED_ARTIFACTS = PRESERVED`; `PHASE_2B = NOT_AUTHORIZED`; `PILOT_10000 = NO_GO`.


