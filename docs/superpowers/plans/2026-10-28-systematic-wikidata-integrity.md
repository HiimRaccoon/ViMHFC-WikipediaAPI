# Systematic historical Wikidata integrity investigation

**Goal:** independently audit three historical mismatch cases; find verified recovery or retain fail-closed source handling. User attachment authorizes at most6 new exact diagnostic requests, no case3 approval or pilot resume.

**Architecture:** keep source validation and all research rules unchanged. Add a pure offline captured-response inspector and CLI. It binds response SHA-256 and exact selected revision/page/QID/cutoff, computes exact content hashes, distinguishes mismatch from valid representation without granting trust, and emits reproducible diagnostic receipts. It never imports a store/client or publishes raw. Runtime blocker-registry and alternate-source adapters remain proposals until independently justified.

**Files:** `backend/src/vimfc/source/integrity_diagnostics.py`, `scripts/inspect-wikidata-integrity.py`, `tests/integration/test_systematic_integrity_diagnostics.py`, captured unverified23658997 fixture, new reports. Historical files/DBs/approvals remain unchanged.

- [x] Baseline all durable files and DB tables; independently inspect source pipeline, receipts, two old RCAs and fixtures.
- [x] Research pinned official MediaWiki/Wikibase code/history and record facts/hypotheses separately.
- [x] Bounded diagnostics:3exact API captures,1misrouted raw API error,1correct raw415;5charged attempts, no retry or sixth request.
- [x] RED13failed/3passed: all3captured mismatches, exact-scope/tamper/cutoff/error rejection, valid Unicode control still diagnostic-only, no raw/store publication, safe CLI no-overwrite.
- [x] GREEN: minimal offline inspector/CLI, unchanged production source semantics; independently compare .NET SHA-1 for all3. Review found same-second fractional cutoff bug in new inspector: added RED and fixed via at_or_before; focused34PASS. Fixture response hashes pinned to independent captured receipts.
- [x] Independent review confirmed code/report scope; final full offline558PASS/exit0/411.44s after fractional-cutoff fix. Earlier557PASS run is intermediate, not final acceptance.
- [x] Offline790replay, allrows in21tables and43234preexistingdurablefiles unchanged; two existing policies, no case3 activation,0newpilot. Final RCA/options/disposition proposal and safe future execution plan written; no verified historical recovery found, case3 remains unapproved.

Execution is inline under the user's explicit autonomous authorization; no commit/push or worktree migration of historical data.
