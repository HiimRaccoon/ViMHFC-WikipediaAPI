# Phase 2D.3.1 path containment

- `backend/src/vimfc/pipeline/backup_recovery.py` adds optional `protected_inputs=()` to both entry points. Transfer protects the source and caller-supplied paths. Restore also protects the bundle and the authority and witness database paths. The existing snapshot guard checks lexical link ancestors and resolves path aliases before checking equality and overlap in both directions. The guard runs before generation reads, output creation, copying, and SQLite access.
- `tests/integration/test_backup_recovery.py` checks 21 combinations of input role and equal, nested, or ancestor outputs via `..`. The tests compare fixture directory entries and file bytes after rejection and fail if a generation is read or SQLite is opened. Link cases cover both output and protected input aliases, including a junction before `..`. The two-generation test now transfers and restores into valid sibling paths written through `..`, with explicit protection enabled.

## Targeted preservation

Pre/post SHA-256 checks matched for `local_snapshot.py`, `generation_publication.py`, `transactional_control.py`, and `test_annotation_builder.py`. The existing annotation-builder diff retained Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. `git diff --check` passed.

| Changed file | Before SHA-256 | After SHA-256 |
| --- | --- | --- |
| `backend/src/vimfc/pipeline/backup_recovery.py` | `9DE8C16D4AFEFAF32F07F8040929872077D422CC1CA689A3D97CD044DE26740C` | `4ED1E8DC4B281629721D51C5CE0B5895A7B7745DE85F5257D859E785E7118674` |
| `tests/integration/test_backup_recovery.py` | `FE1F280BF072396EC53B059FD3D4054D19AFE940EBC77B1CF6E380CC32084BDE` | `1C8A4406FD40276E90A58051F5C83BFF858D94BFD2044CDD3495A6F2FCFF1476` |

## Limits

The shared guard assumes a quiescent local fixture and a private output path. It does not fence concurrent path replacement. The real junction test ran on Windows. Actual symlink creation requires privileges unavailable on this host.

## Verification

- Containment and valid sibling tests: 23 passed, 1 skipped.
- Focused backup/restore, snapshot, and publication suites: 116 passed, 3 skipped. All skips were Windows symlink privilege checks.
- Full offline regression: 1016 passed, 3 skipped in 499.96 seconds. All skips were Windows symlink privilege checks.
- All runs used `VIMFC_OFFLINE=1`, the pytest socket guard, and separate local fixture directories under `.tools`. Full command: `.tools/python312/python.exe -B -m pytest -q --tb=short -p no:cacheprovider --basetemp=.tools/phase2d31-full`.

