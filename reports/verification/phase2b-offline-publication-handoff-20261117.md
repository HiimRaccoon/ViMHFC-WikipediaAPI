# Phase 2B Offline Publication Handoff — 2026-11-17

Baseline: accepted Phase 2A.1 working tree. Git history was not reconciled: local HEAD stays at b520f11; accepted Phase 2A/2A.1 files remain in the workspace. No Git history mutation, network, UIT, Slurm or acquisition operation was performed. Git status/diff were read locally.

## Files

Created `backend/src/vimfc/pipeline/generation_publication.py`, `tests/integration/test_generation_publication.py`, this handoff and `.tools/phase2b-evidence/` logs/preservation receipts. Existing Phase 2A verifier and tests were reused without edits. The pre-existing modified `tests/integration/test_annotation_builder.py` was preserved. No production caller, CLI, policy, checkpoint or scheduler configuration imports the new module.

## Architecture and contracts

`publish(source, root, bundle=..., compatible=..., expectations=..., synthetic=True)` requires an independent caller-provided authorization and expectations. Source generation is verified with Phase 2A `verify_generation()`. Storage root must be local and separate from source, immutable bundle and all caller-listed protected inputs, in either containment direction. Lexical symlink/junction ancestors are rejected before resolution. Generation IDs are restricted to safe single components.

Minimal expectations have exactly:

```python
{
    'authorized': True,
    'target': {
        'identity': {  # all six Phase 2A identity fields required
            'workspace_id': 'synthetic-workspace', 'run_id': '...',
            'attempt_id': '...', 'epoch_id': '...',
            'generation_id': 'g2', 'bundle_id': '...'
        },
        'manifest_sha256': '<independently expected SHA-256>',
        'parent_generation': 'g1'
    },
    'expected_pointer': {  # exact expected prior pointer, or None only at bootstrap
        'identity': {'workspace_id': '...', 'run_id': '...', 'attempt_id': '...',
                     'epoch_id': '...', 'generation_id': 'g1', 'bundle_id': '...'},
        'manifest_sha256': '<trusted prior manifest SHA-256>',
        'parent_generation': None
    }
}
```

Target and prior must have the same workspace/run/bundle; generation IDs must differ and parent must match prior generation. Missing/false authorization, missing keys, malformed hashes/identity, unsafe names and parent mismatch are refused. A caller boolean is only an explicit synthetic interface: it does not authenticate a real operator or establish a production trusted authority. Deriving expectations from the storage pointer itself defeats the boundary and is not supported as a trust claim.

The existing generation-manifest-v1 bytes are copied unchanged. A unique `.staging-<uuid>` receives the closed generation, followed by file-only verification of canonical manifest SHA-256, identity, exact artifact file set, sizes and hashes, including sidecar rejection. Destination SQLite is never opened. Source artifacts and manifest digest are compared again after transfer. Logical immutability means the publisher refuses existing final generations and never rewrites/deletes them; OS/WORM immutability against other actors is not established.

Explicit transitions: `CREATED -> STAGED -> VERIFIED -> COMMITTED`. Transitions are checked by `advance()`. Canonical atomic attempt receipts record each state outside generation directories. Failure leaves partial staging, pending control files or promoted orphan generations for inspection; none are automatically selected. Staging and final share one checked local filesystem; `st_dev` mismatch or rename EXDEV fails without a copy fallback. The final directory is never replaced by this protocol under its cooperating single-host ownership assumption.

Canonical `latest.json` carries exactly identity, manifest SHA-256 and parent generation. A same-directory unique temporary file is flushed/fsynced, then replaced with `os.replace`. The old pointer is checked under the exclusive lock, before temporary-file creation and again immediately before replacement. An injected unexpected pointer change before replacement is refused without overwriting it. Existing `exclusive()` OS locking serializes cooperating local publishers without stale takeover. Prior generation is byte-verified before proceeding. This lock is not cross-node fencing; uncooperative writers and concurrent hostile filesystem replacement remain outside the guarantee. Atomic rename and file fsync do not prove power-loss durability: directory fsync, filesystem/server guarantees and a trusted durable control authority are not established here.

## Recovery and retention

`recover(root, expected_current=..., expected_previous=..., synthetic=True)` performs only reads and byte verification. Missing independent expectations, pointer mismatch, noncanonical controls, corruption, missing files or retention expectation mismatch returns `UNKNOWN_REQUIRES_OPERATOR_REVIEW`. It does not infer a winner from attempt receipts, promote staging, retry a publication, delete files, unlock another publisher or choose an older generation. No fallback API is implemented; separately trusted hashes and operator authorization would be required for future fallback.

With independently supplied target expectations, a post-replace pointer can be byte-verified even if the COMMITTED receipt failed. The report explicitly labels publication receipt authority UNVERIFIED: verification of current bytes is not proof of durable commit or request-budget authority. A caller still expecting the prior pointer receives UNKNOWN rather than silently accepting the new pointer.

Read-only retention reports verify both current and independently expected previous generation. First publication reports `BOOTSTRAP_ONE_GENERATION`, not a false two-generation PASS. After a second publication the two verified generations yield retention PASS. Pending/failed directories and temporary controls are listed and preserved; there is no automatic cleanup.

## Fault-injection evidence

| Scenario | Expected observed behavior |
| --- | --- |
| Before staging | No stage selected; prior pointer unchanged |
| During copy, after at least one file | Partial stage retained; prior pointer unchanged |
| Destination hash phase; corrupt, partial, extra files | Transfer rejected; no final/current replacement |
| Before promotion | VERIFIED stage retained; prior unchanged |
| After promotion before pointer | Orphan final retained; prior unchanged |
| During pointer temporary creation | Incomplete temp retained; prior unchanged |
| Immediately before pointer replacement | Complete temp retained; prior unchanged |
| Immediately after pointer replacement | Old expectations return UNKNOWN; independent new expectations verify current bytes |
| Final receipt publication | New pointer can exist with older VERIFIED receipt; no rollback or automatic retry |
| Corrupt/missing current file, damaged manifest, pointer/hash mismatch | UNKNOWN, no fallback or mutations |
| Stale publisher | Prior mismatch rejected |
| Concurrent cooperating local call | OS lock rejects nested publisher |
| Existing final directory/repeated publication | Refused, generation preserved |
| Cross-device rename failure | No copy fallback or pointer publication |
| Missing/untrusted authorization, identity/hash/parent/name mismatch | Rejected before storage creation |
| Nested protected destinations | Refused, exact protected bytes retained |
| Successful publication and second generation | Repeated byte verification; prior retained; two-generation report PASS |
| Destination SQLite prohibition | SQLite connect trap confirms no destination DB opened |

The tests additionally verify source generation, workspace and bundle bytes unchanged, exact control bytes, state receipts and read-only recovery. Real process crash/power loss and distributed concurrency were not simulated or certified.

## Test commands and results

```powershell
.tools/python312/python.exe -m pytest tests/integration/test_generation_publication.py tests/integration/test_local_snapshot.py -q -p no:cacheprovider --basetemp=.tools/phase2b-focused-rechecked
.tools/python312/python.exe -m pytest -q -p no:cacheprovider --basetemp=.tools/phase2b-full-final
```

Focused final: **76 passed, 2 skipped in 38.28 seconds**, exit 0. Includes 31 new publication cases and the unchanged Phase 2A tests. Both skips require Windows symlink privileges; native Windows junction rejection is exercised by Phase 2A.1.

Full final: **913 passed, 2 skipped, 0 failures in 401.69 seconds (exit 0)**. Both skips are the same Windows symlink privilege limitations. Both final test commands ran on the final modified implementation/tests. No code/test edits followed.
An earlier full-suite attempt was deliberately stopped after a final review identified the additional pre-replace pointer check. Focused and full commands above use the final code/tests; the abandoned log is not final regression evidence.

Evidence: `.tools/phase2b-evidence/focused-rechecked.log`, `full-offline-final.log`, `before.json`, `preservation.json`, `preservation.py`. Default suite socket denial remains active; no network opt-in was supplied.

## Preservation

Independent SHA-256 scan after the final full suite: **46,744 pre-existing protected files; zero changed or missing** (exit 0). Coverage reuses tracked-file paths, prior protected inventory, existing data and reports. The baseline was started before edits. Synthetic tests additionally compare the source generation, bundle and source workspace byte-for-byte, including manifests. Final implementation/test hashes match the hashes recorded before the final full suite; see `final-code-hashes.json`. Only newly named `.tools/phase2b-*` fixture/evidence trees were used. No old fixture workspace was reused for writes.

## Remaining gates and recommended Phase 2C

Native Linux ext4 rename/locking/symlink tests require separately authorized execution. Real NFS remains unauthorized and unverified: never infer NFS durability, distributed ownership or policy authorization from these local tests. A production design still needs approved independent trusted control records, ownership/fencing, coherent publication authorization, durable budget reservations, supported writable SQLite storage and explicit retention/cleanup rules. No such ledger or coordinator was invented here.

Recommended Phase 2C is a design review of those trust/ownership and durability contracts, plus separately approved native Linux synthetic validation. Production integration, NFS deployment, acquisition and Pilot 10,000 remain gated.

`PHASE_2B_IMPLEMENTATION = PASS`; `IMMUTABLE_GENERATION = PASS`; `OFFLINE_PUBLICATION = PASS`; `POINTER_RECOVERY = PASS`; `TRUST_BOUNDARY = ENFORCED`; `FAULT_INJECTION = PASS`; `OFFLINE_REGRESSION = PASS`; `PROTECTED_ARTIFACTS = PRESERVED`; `PRODUCTION_NFS = NOT_AUTHORIZED`; `UIT_EXECUTION = NOT_AUTHORIZED`; `PILOT_10000 = NO_GO`.

PASS/ENFORCED apply only to the explicitly authorized offline synthetic, cooperating single-host boundary. They do not certify production authority, cross-node ownership or durable commit.




