# Windows annotation checkpoint persistence — Technical Review Handoff

Status: persistence fix implemented; no real Pilot resume/replay executed.

## Investigation

The failed Pilot published 249 records and left a checksummed 250-record temp.
Both envelopes validate, bindings match, every committed record is unchanged,
and the temp adds only page `2305627`. Both files currently have normal Archive
attributes, not ReadOnly. The checkpoint ACL is readable and the owner is
`RACCOON\CodexSandboxOffline`. This does not establish effective permissions at
the time of failure or identify a process holding a handle.

Windows returned WinError 5 at atomic replacement; the old implementation
already closed the temp handle before replacing it. No lock-holder telemetry
was retained. A transient reader/scanner or access-control denial is possible,
but neither is established as the cause. PermissionError alone must not be
reported as proof of antivirus interference. The observed defect is immediate
failure without bounded retry, durability flush or output single-writer lock.

## Changes

Only persistence in `backend/src/vimfc/pipeline/annotation_builder.py` changes:

- Hold an OS-level nonblocking writer lock for the entire invocation. Windows
  uses `msvcrt.locking`, POSIX uses `flock`; close releases ownership even after
  interruption. The lock file may remain and is not itself evidence of an
  active writer. A second invocation fails before checkpoint/source processing.
- Serialize to a unique same-directory `.pending` file, flush and fsync, close
  the handle, then atomically replace the checkpoint. Existing failed temps
  and legacy `checkpoint.tmp` are not overwritten or deleted.
- Retry only Windows PermissionErrors 5/32/33, at most 8 attempts with capped
  exponential backoff (2.25 seconds total sleeping). Persistent denial and
  unrelated errors propagate. No unlink-then-write fallback or pipeline retry.
- On failed replacement or interruption, the previous checkpoint remains
  authoritative and the fsynced pending file remains available for inspection.
  Normal checksum/input-binding/manifest checks are retained. This provides
  atomic visibility, not a guarantee against every filesystem/power failure.

The audited 250-record temp is **not promoted**: checksum and prefix agreement
alone do not independently revalidate its added article/source evidence. The
safe recovery policy is to load the valid 249-record checkpoint and process
the one uncommitted article again only after a separately authorized resume.
Corrupt committed checkpoints fail closed; temp/pending files never silently
replace them. The original Pilot checkpoint and temp remain byte-identical.

No eligibility, edge classification, ranking, source integrity, frozen policy
or old artifact is changed. `tests/integration/test_annotation_builder.py` adds
tests through the Builder interface for transient and persistent replace
failure, retry bounds, single-writer exclusion, interrupted writes, retention
of old/temp states, and deterministic fixture resume.

## TDD and verification

Transient-lock test failed with PermissionError before retry implementation,
then passed. Single-writer test failed before locking (nested execution), then
passed. Focused offline tests: **30 passed**. Full offline pytest:
**985 passed in 399.19 seconds**. All tests ran with `VIMFC_OFFLINE=1` and
workspace-local temporary directories.

Preservation: **12,342 existing files checked, 0 changed or missing**. This
includes the frozen run/source DBs and raw cache, Integrity Registry/evidence,
November 1 and November 14 archived packages, and both failed Pilot checkpoint
files. Baseline/result are local `.tools/checkpoint-repair-preservation-before.json`
and `.tools/checkpoint-repair-preservation-result.json`. This certifies the
files in that baseline, not every unrelated workspace file.

All execution in this turn uses synthetic fixtures. The real 249/250 artifacts
were read/hash inspected only. No Pilot 1,000, resume, offline Pilot replay,
Pilot 10,000, production, API/LLM, commit or push was executed. A further real
invocation remains subject to explicit user authorization.
