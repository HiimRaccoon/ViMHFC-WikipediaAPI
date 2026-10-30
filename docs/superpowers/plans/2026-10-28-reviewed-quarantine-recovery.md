# Same-run reviewed refusal recovery

Explicit user authorization: at most2 exact-revision24242903 HTTP diagnostics, then at most ONE recovery of existing80524a0f run with10000 additional attempts after RCA/evidence/tests/preflight PASS. No new run/cohort, new integrity exceptions,10000/Production or commit/push.

## Design

The two original per-revision approvals stay immutable and individually scoped. Add a versioned refusal-set envelope containing exact single-revision approval records. Activation appends only missing `reviewed_integrity_policy` events; never replace the existing approval. Every invocation must present exactly the complete active set, so an old single approval cannot omit the new refusal. Existing case receipts retain their component review IDs and case IDs, allowing restart without changing historical cases. Source refusal delegates to each exact component; unknown hashes still hard-stop. The original single-review schema remains readable.

RCA first: capture exact response/UTF-8 bytes, revision+slot hashes/sizes/model/encoding; compare with old captured case. Confirm mismatched source representation without assuming a shared serializer migration. No normalization acceptance path. Store unverified diagnostics outside trusted raw. New refusal approval only after independent evidence verification; record actual explicit user authorization.

Recovery uses a separate one-shot authorization/claim/log/receipt, no reset of prior claims, attempts or costs. Baseline547 completed/current stable stages/manifests and5hints; revalidate cache-only; preserve old files and rows. Pass an exact same-run scoped progression permit pinning original100 report, without changing incomplete flags. One advisory preflight within recovery cap, no probe during cooldown. Restore completed results cache-only; refuse approved bad root before claiming interrupted stage; process independent candidates. Stop all further live work at a new failure/cap/unknown candidate.

## Tasks

- [x] Two exact HTTP diagnostics captured; default and explicit JSON both return mismatched SHA-1; reported slot size213 differs serialized252.
- [x] RED/GREEN refusal-set validation, append-only activation, downgrade refusal, legacy case restoration, cache/network isolation and unknown failure guards.
- [x] Integrate source/report/progression compatibility; update contracts/docs without changing semantic rules.
- [x] Independent review and full offline suite (541 PASS); validate547 checkpoints/5hints and same cohort/config; create independently verified scoped approval set.
- [x] Exactly one bounded recovery:2460physical attempts,exit3 at new Q10766570/revision23658997/page863879, ordinal802. No new approval or repeat invocation.
- [x] Post-run790/790offline replay;547oldmanifest/stage rows and historicalfiles preserved.790processed,128eligible,10deferred,1reviewedquarantine,1interrupted,198unattempted; cumulativepipeline9070attempts plus2RCA. Source/integrity incomplete; no10k/Production acceptance.
