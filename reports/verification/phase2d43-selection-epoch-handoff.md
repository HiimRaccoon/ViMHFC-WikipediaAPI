# Phase 2D.4.3 offline selection epoch binding

- `backend/src/vimfc/pipeline/selection_epoch.py` revalidates inherited selection on a private restored fixture. The generation and bundle pins come from trusted fixture setup. The gate verifies the generation manifest, file set and compatibility, and replays the existing selector and graph/edge reporting on verified processed checkpoint rows. It compares the full output with the inherited canonical selection. It does not rewrite selection or run collection.
- `local_snapshot.py` exposes an optional check while the verified workspace archive and bundle registry are available. `transactional_control.py` witnesses the binding as a decision and reserves that action for the isolated gate. `tests/integration/test_selection_epoch.py` uses the actual restore and activation fixture.

The canonical envelope contains a decision ID and a `synthetic-selection-epoch-1` binding. The binding includes the full generation pointer (workspace, run, bundle, generation, ancestry and manifest digest), checkpoint and selection SHA-256, classification/extraction/ranking, quality, quotas, edge and selection ranking hashes, plus the activation decision ID, record digest and current owner epoch. The authority witnesses the complete binding. One binding can be issued per activation. A valid binding may be read again during that same activation.

The mock consumer verifies the file bytes and generation pins, then checks acknowledged authority, running owner, matching latest activation and exact witnessed binding in the authority transaction. It returns the verified in-memory content. Admission applies at that check and does not fence later use or changes outside the fixture.

| Failure | Result |
| --- | --- |
| Inherited selection without binding | Deny even after successful restore and activation. |
| Changed selection bytes, generation, owner, or epoch | Deny. |
| Recomputed or tampered envelope without the matching witnessed record | Deny. |
| Current selector replay differs | Deny binding and leave the file unchanged. |
| Authority rollback or witness outage | Deny until authority verifies. |
| Interruption after binding commit, before acknowledgment | Deny while pending. Explicit witness recovery can acknowledge the matching record. No automatic rebind. |
| Binding replay after owner change | Deny. Duplicate issuance in the same activation is also denied. |

Targeted SHA-256 matched for `worker_activation.py`, `incremental_collection.py` and `test_annotation_builder.py`. The pre-existing annotation-builder diff retained Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. `git diff --check` passed.

| Changed source/test | Final SHA-256 |
| --- | --- |
| `selection_epoch.py` | `4962C6CECA08C56F698DE7FA350CEEBBD8ABF2FFB56C6DDF19C67F1B7869AD8E` |
| `test_selection_epoch.py` | `B3F745E3C632FDD542203BBC189B02B50524CBA332D77DA587D9F3FA2B8CD620` |
| `local_snapshot.py` | `C438608A18B8B6071FDCD2B0D59F074D9610BCB5D032EE60657DEBD9B6E1F8CF` |
| `transactional_control.py` | `7A7F2584333DB245A9887FBC00755E210FF36E96C3AAF871CCC12160CFBE05DD` |

Production limitations include the private immutable fixture assumption, synthetic authority and witness, coordinated rollback of both control files, and no production consumer integration or physical fencing. Owner validity uses the witnessed running epoch and latest activation decision. It does not derive a persistent worker lease from the process-local mock dispatch TTL.

Focused selection, activation, authority and snapshot tests passed 93 cases with 2 skips. Full offline regression passed 1045 cases with 3 skips in 522.48 seconds. All skips were Windows symlink privilege cases. Both runs used `VIMFC_OFFLINE=1` and the existing pytest socket guard.

