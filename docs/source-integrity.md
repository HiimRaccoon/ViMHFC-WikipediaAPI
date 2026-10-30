# Historical source integrity diagnostics

`RevisionSource` validates the exact UTF-8 content against the reported revision SHA-1 before publishing trusted raw. Dependency materialization independently checks raw identity, content hashes and historical cutoff. A diagnostic observation, equivalent JSON entity, or whole-export checksum never replaces these gates.

Wikibase may deserialize historical content and serialize it with a newer schema. API revision/slot SHA-1 values can come from stored metadata while emitted content is serialized separately. This is a supported failure mechanism, not proof of a particular migration or source corruption. Historical Wikibase nominal size used PHP-serialized native data; it does not establish the original JSON blob length.

The pure offline inspector uses format `vimfc-integrity-diagnostic-1`. It binds a separately pinned captured-response SHA-256 and an exact page/QID/revision/timestamp/cutoff, rejects ambiguous response structure and reports content hashes. It always returns `trusted_historical_evidence=false`, including matching-hash controls. It has no source client/store/raw-publication path. Exit0 means inspection completed, not source recovery or pilot acceptance.

```powershell
# Offline only; output must be a NEW path.
& .\.tools\python312\python.exe -B .\scripts\inspect-wikidata-integrity.py `
  --response .\tests\fixtures\wikidata-23658997-unverified-response.json `
  --response-sha256 ceb2a3a36f27084e4513d1a9487da5a0d8c2125a81a685c84ac43e4c45eab97f `
  --qid Q10766570 --page-id 12040430 --revision-id 23658997 `
  --timestamp 2013-04-09T05:01:00Z --snapshot 2026-10-25T03:00:00Z `
  --output .\.tools\systematic-sha1\case3-inspection-NEW.json
```

Unknown source-integrity mismatches hard-stop. Reviewed refusal is exact revision/run/config/snapshot/evidence scoped and supplies no corpus/graph evidence; it is not ordinary deferral or semantic ineligibility. The existing additive approval set is append-only and cannot be downgraded to omit active components. A disposition proposal is not an approval. No case3 approval or Pilot resume is authorized by the systematic RCA task.

## Operational blocker registry

`vimfc-integrity-registry-1` is an opt-in, run/config/snapshot-scoped SQLite sidecar, supplied with `seed-only.py run --integrity-registry PATH`. It does not change qid-5, extraction, quality, raw validation or progression semantics. Once the run contains registry anchors, omission of the registry fails closed. Keep the database and its sibling `*-artifacts` directory outside trusted raw and preserve both with the run.

The sidecar contains one canonical metadata header and immutable `events(sequence, event_hash, record_json)` rows. Each event hashes its sequence, previous hash and payload; every event also has a matching append-only `integrity_registry_anchor` in the operational run's event history. Full-chain validation checks every anchor, captured response/failure artifact, authorization and referenced approval. Missing evidence, rollback, altered scope or an unanchored tail fails closed. This is a local audit boundary, not a cryptographic human signature.

Observations start as `UNKNOWN_INTEGRITY`, `SOURCE_UNAVAILABLE`, `SEMANTIC_UNRESOLVED` or `MISSING_HISTORICAL_DEPENDENCY`. Only exact validated mismatch observations can receive explicit dispositions: `REVIEWED_REFUSAL` requires the matching reviewed approval component and authorization artifact; `VERIFIED_RECOVERY` requires an exact response matching the original reported SHA-1. Neither state publishes trusted raw. Existing source validation, explicit quarantine policy and progression gates still decide runtime behavior. There is no actual verified-recovery proof for the three historical cases investigated so far.

Known unknown observations block preflight before source work. Exact selected refs are checked before source cache/network and cache-only manifest materialization. An approved revision used as a shared dependency is refused too; an unexpected affected root must stop the operator batch for scope review. Restored valid completed stages and reviewed refusals do not increase worker attempts. Registry state counts describe observations, not the number of affected candidates.

The opt-in HTTP hook retains the last decoded response in memory and publishes diagnostic bytes only when an integrity error is caught. Exact mismatch capture independently checks identity and hashes against the original failure. A stale response or incomplete metadata becomes an opaque unknown observation, never guessed proof. Failure to write diagnostic storage preserves the original integrity exception and adds the reporting failure; it cannot turn the failed input into accepted evidence.

Sidecar append and run-event anchoring use different SQLite databases. A crash between their commits deliberately leaves a fail-closed mismatch. Preserve the files and original receipts and obtain a reviewed repair disposition; do not delete the registry, truncate its tail or remove anchors. The registry cannot grant another source invocation, reset a budget or automatically approve a new mismatch.

For same-run recovery, use a new exclusive, fsynced operator claim and a separately sealed finite authorization referencing prior receipts/costs, unchanged context/cohort, the full active approval set and the initial registry head. Check cooldown, Retry-After, circuits and leases before claiming. Use one bounded invocation, then offline replay/preservation audits; a new integrity/source/budget/checkpoint failure ends that authorization. Incomplete and acceptance flags remain governed by native gates.

Research and evidence: [pinned official source analysis](../reports/verification/wikibase-historical-serialization-research-20261028.md). Historical evidence-pinned RCAs/approvals remain unchanged; corrections belong in new reports.
