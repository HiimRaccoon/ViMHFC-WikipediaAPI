# Phase 2C.2 offline ownership and budget handoff

- `backend/src/vimfc/pipeline/trusted_control.py` adds `SyntheticOwnership` over the existing initialized `SyntheticAuthority` and `FakeWitness`. It adds `release_stopped` for a confirmed synthetic stop with no outstanding publication intent. The existing publication transitions remain available.
- `tests/integration/test_trusted_control.py` adds eight synthetic cases covering owner identity, budget, one-shot launch, interruption, lost scratch, witness availability and authority rollback.

Every operation checks the acknowledged authority and, after claim, the attempt, epoch, job and expected owner state. Launch records the reservation as spent for launch purposes before returning a callable fake worker. The worker checks ownership and witness status on every call and consumes only positive integer units within its remaining allowance. Lifetime allocation is historical debit plus all reservations. Unused units stay allocated and reservation IDs stay spent across owners.

| Interruption boundary | Reserve | Launch |
|---|---|---|
| Before storage | No new debit | Existing reservation held, no worker issued |
| After storage, before acknowledgment | Pending debit blocks operations | Pending launch blocks operations |
| After acknowledgment, before caller receives result | Debit remains and cannot be reserved again | Launch remains spent and cannot issue another worker |

Tests also verify that explicit uncertain termination marks `OWNER_UNKNOWN`, keeps the debit and blocks worker calls, release and new claims. Witness unavailability blocks operations and rollback behind the witness is rejected. Manual witness acknowledgment of a pending launch gives no worker. A pending decision requires explicit resolution in the synthetic authority.

Focused test result: **26 passed**. Full offline suite: **939 passed, 2 skipped** in 480.93 seconds. Both skips required Windows symlink privileges (`test_local_snapshot.py:75` and `:205`). Full command: `python -B -m pytest -q -p no:cacheprovider --basetemp=.tools/phase2c2-full` with `VIMFC_OFFLINE=1`.

Targeted SHA-256 before and after:

| Path | Before | After |
|---|---|---|
| `backend/src/vimfc/pipeline/trusted_control.py` | `7A0CB535C894B87295E68EDB313A3D03ABF0A9372B9F56AE19BD65DC99EAF6F0` | `10DF70E4113C6D547595F03E725EBA776A62C70072B19DB46C3BF71843BC5A87` |
| `tests/integration/test_trusted_control.py` | `754ED2FB8E18904EF88C05D497329C173C3AFED166F6DEE57ACB7CE1AD5B0F48` | `2A4893F0EBE7318476EAFE95DE58D71A009040D89E0EC47726A385FA3641FD5D` |

Read-only Git status and targeted diff show the same pre-existing tracked edit to `tests/integration/test_annotation_builder.py`. Preservation evidence is limited to these checks. Historical Phase 2C.1 preservation remains partially evidenced.

The model is single-threaded and in memory. Termination and fencing evidence are fixture inputs. Losing the authority and witness together falls outside this model. An unavailable witness can prevent recording uncertainty until it is available again. Physical fencing and durable anti-rollback remain unverified.

```text
PHASE_2C_2_IMPLEMENTATION = PASS
OWNER_ORCHESTRATION = PASS
ONE_SHOT_LAUNCH = PASS
BUDGET_RESERVATION = PASS
CRASH_RECOVERY_MODEL = PASS
OFFLINE_REGRESSION = PASS
PRODUCTION_AUTHORITY = NOT_IMPLEMENTED
PHYSICAL_FENCING = UNVERIFIED
PILOT_10000 = NO_GO
```

