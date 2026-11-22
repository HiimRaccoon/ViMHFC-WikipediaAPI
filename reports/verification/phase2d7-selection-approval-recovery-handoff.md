# Phase 2D.7 offline selection approval recovery

## Implementation

- `backend/src/vimfc/pipeline/selection_epoch.py` adds `SyntheticSelection.recover_approval(decision_id)`. Binding and recovery share the extracted `_revalidate()` implementation. The existing consumer remains unchanged.
- `tests/integration/test_selection_approval_recovery.py` adds 29 fixture cases for fresh gates, real process restarts/exits, repeated recovery, pending acknowledgment, malformed or mismatched inputs, policy/owner/generation changes and preservation.

Recovery reads an existing bind_selection decision from the verified authority history. It requires acknowledged authority, a running current owner, the matching activation decision and epoch, the independently pinned generation and the current trusted policy identity. It never accepts an envelope as its source of authority.

It independently verifies the generation bytes, manifest and compatibility, actual checkpoint, bundle and policy hashes. It replays the unchanged selector and graph/edge reporting and reconstructs the full binding. Its canonical payload must exactly equal the original witnessed payload. In a final authority transaction it rechecks the generation bytes, current policy/owner/activation and the unchanged original decision, including its expected version. Only then is its digest added to local approval memory.

The returned canonical envelope is the same as the original. Recovery does not call authority decision creation, witness recovery/acknowledgment, reservation or worker launch. Normal consume still performs the existing file, policy, authority and local-approval checks without selector replay. Repeated explicit recoveries revalidate independently and preserve the one-binding-per-activation rule.

## Failure behavior

| Case | Result |
| --- | --- |
| Fresh gate and acknowledged unchanged binding | Independent replay restores local approval. |
| Process exits during replay, after replay or after local approval | Authority, witness, generation and bundle bytes are unchanged. A new process can recover again. |
| Binding process exits after commit but before witness acknowledgment | Recovery denies. An explicit, separate authority recovery is required before approval recovery can succeed. |
| Binding process exits after acknowledgment but before returning its envelope | Recovery locates and revalidates the original decision by ID. |
| Policy absent/changed, owner stopped, newer epoch, wrong generation, pending publication, witness unavailable/mismatched or authority rollback | Deny without adding local approval. |
| Altered selection/checkpoint bytes | Generation verification denies. |
| Well-shaped witnessed assertion with a different selection/checkpoint fingerprint | Exact independently reconstructed binding comparison denies. |
| Owner/policy/selection changes after replay | Final checks deny local approval. |
| Missing decision, wrong action or invalid decision ID | Deny. |

## Preservation

Targeted pre/post SHA-256 matched for the existing selection tests, selection schema, transactional authority, durable publication, snapshot verifier, collection implementation and `test_annotation_builder.py`. The existing annotation-builder diff retained Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. `git diff --check` passed.

| Source/test | SHA-256 |
| --- | --- |
| `selection_epoch.py` before | `FC45D7C60EB5BA2E12FD38DD1326DF520C197944CB7C409D6E672B9DD424B860` |
| `selection_epoch.py` final | `88E3E03AE1A13D0AACA3352CF2C43A64EEA73BFAA3E2A64B1191D6A8DB25A630` |
| `test_selection_approval_recovery.py` final | `B146AB781B6D194811C5E27AA1069C9BDA6F5429FC094FD2529A7FA3909D99D0` |

Fixture tests compare the full authority and witness file bytes plus generation and bundle bytes across recovery and hard exits. Budget and state after interrupted binding are compared with their pre-binding values. Tests make decision creation, authority recovery and launch fail if invoked by approval recovery. SQLite audit directories and subprocess result files are isolated test outputs outside the protected generation.

## Final verification

Final implementation/test hashes above were unchanged through both final runs. Both used `VIMFC_OFFLINE=1`, `.tools/python312/python.exe -B -m pytest -q --tb=short -p no:cacheprovider` and isolated `.tools/phase2d7-*` temporary fixtures.

- Focused: `test_selection_approval_recovery.py`, `test_selection_epoch.py`, `test_worker_activation.py` and `test_transactional_control.py`: **91 passed** in 63.43s. This includes all 29 new recovery cases.
- Full offline regression (no explicit test filter): **1121 passed, 3 skipped** in 511.87s. The three existing backup/snapshot symlink cases were skipped because Windows symlink privileges were unavailable; native Linux validation remains unverified.
- Final targeted SHA-256, tracked Git status and annotation-builder diff checks confirmed the preservation findings above. No Git mutation, dependency installation, network operation or dataset inventory was performed.

## Limitations

Recovery restores permission to consume selection under the existing acknowledged activation. It does not revive a worker, renew a lease, establish termination or perform physical fencing. It relies on private stable local fixture bytes and independently trusted current-policy inputs. A changed policy/build identity fails closed, including the implementation source fingerprint. Same-policy process restart is covered. Approval remains process-local and must be explicitly recovered after each restart. Coordinated rollback of both control databases remains outside the synthetic trust model.

`PHASE_2D_7 = PASS`

`SELECTION_APPROVAL_RECOVERY = PASS`

`NO_SECOND_BINDING = PASS`

`STALE_EPOCH_REJECTION = PASS`

`OFFLINE_REGRESSION = PASS`

`PRODUCTION_INTEGRATION = NOT_AUTHORIZED`

`PILOT_10000 = NO_GO`
