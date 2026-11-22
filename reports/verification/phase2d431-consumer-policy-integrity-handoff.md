# Phase 2D.4.3.1 consumer policy integrity

The consumer previously compared the envelope with the witnessed payload without checking a separately supplied current policy contract. The low-level binding transition also accepted arbitrary dictionaries.

## Changes

- `backend/src/vimfc/pipeline/selection_epoch.py` requires a trusted current-policy provider for binding and consumption. `policy_fingerprint(config, runtime)` derives config fingerprints, declared edge/ranking versions, runtime identity, and hashes of eight small local selector-related Python sources. Trusted setup supplies the config and runtime independently of the envelope. Consumer checks this identity and every policy fingerprint without replaying selection.
- The gate retains the digest of each successfully replayed binding in private in-memory approval state. A witnessed low-level assertion alone cannot pass consumption. This approval is created before the authority write so explicit recovery after interrupted witness acknowledgment remains usable in the same gate instance.
- `backend/src/vimfc/pipeline/selection_contract.py` validates exact binding, policy and activation fields, digest formats, pointer shape, and positive integer epoch. Missing identity and unexpected fields fail closed.
- `backend/src/vimfc/pipeline/transactional_control.py` validates the schema, active ownership, latest actual activation record ID/digest, generation, and one-binding-per-activation. Consumer additionally checks the checkpoint fingerprint against the independently pinned generation manifest.
- `tests/integration/test_selection_epoch.py` covers changes after binding, absent/malformed trusted identity, declared policy version drift, tampered policy fields, six malformed payload cases, witnessed low-level assertions, unchanged repeated consumption without selector replay, and the existing rollback/epoch/interruption cases.

## Failure behavior

| Case | Result |
| --- | --- |
| Provider absent, malformed or changed after bind | Consumer denies. |
| Policy or checkpoint field differs | Schema or trusted expectation check denies. |
| Well-shaped low-level assertion without replay | Consumer denies without independent in-memory approval. |
| Wrong activation, extra field, invalid digest or boolean epoch | Authority transition denies. |
| Witness acknowledgment pending | Consumer denies until explicit matching recovery. |
| Duplicate issuance or prior owner epoch | Authority or consumer denies. |
| Valid unchanged policy | Consumer accepts without full selector replay. |

## Preservation

Targeted pre/post hashes matched for `local_snapshot.py`, `incremental_collection.py` and `test_annotation_builder.py`. Annotation-builder's existing diff retained Git blob hash `872bf37b961eaa8a000850b64614c2c7c318c0fd`. `git diff --check` passed.

| Changed source/test | Final SHA-256 |
| --- | --- |
| `selection_epoch.py` | `FC45D7C60EB5BA2E12FD38DD1326DF520C197944CB7C409D6E672B9DD424B860` |
| `selection_contract.py` | `80072D2015C5B14BC80F0D486BD4CDF0F1D682E7CC754E6F2AD53ACAEA6B6F15` |
| `transactional_control.py` | `4898E18C02DF7DE477C10A105FC6959FBA89D3E14096191EFA64B516B173313A` |
| `test_selection_epoch.py` | `FF2978485F341209C98D31AB8BE4483B325F2C95DE0FAAD7D960DD756EFD76F6` |

## Limitations

Current policy config/runtime and the provider are trusted fixture dependencies. The source fingerprint covers the explicitly named local modules and does not authenticate the entire dependency environment or in-memory code replacement. Consumer still performs the existing generation byte verification, while the added policy check hashes only eight small source files. Independent replay approval is process-local. A fresh gate after restart denies consumption of old bindings, and the one-binding-per-activation rule remains enforced. Persistent approval recovery requires a separately designed trust contract. Older schema payloads without policy identity fail closed. Physical fencing and production integration remain outside this synthetic contract.

## Verification

- Focused selection, activation and transactional authority suites passed 62 tests.
- Full offline regression passed 1059 tests with 3 skips in 449.01 seconds. All skips were Windows symlink privilege cases.
- Runs used `VIMFC_OFFLINE=1` and the existing socket guard. Full command was `.tools/python312/python.exe -B -m pytest -q --tb=short -p no:cacheprovider --basetemp=.tools/phase2d431-full`.

