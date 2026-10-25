# Builder review and regression fixes

Date: 2026-10-25. Scope: backend/setup through T048; no real-source execution.

An independent read-only reviewer inspected the uncommitted implementation against the agreed docs/plan/model/contracts/tasks. It ran the original 54-test baseline successfully. Review found no Critical issues and ten Important issues; implementation was not ready at that point. The author reproduced and addressed the findings using regression tests, without a second review cycle.

| Finding | Regression evidence / resulting behavior |
| --- | --- |
| Transient identity failures and pending classification cached as completed | `test_transient_identity_error_is_propagated_and_can_be_retried`, `test_pending_metadata_is_not_a_completed_stage`: transient errors propagate, pending results retain retry state and never count as completed |
| Move proof older than current title lookup | `test_title_lookup_and_move_proof_use_same_recorded_horizon`: source lookup records server timestamp; move coverage is extended to that horizon and earlier proofs remain archived |
| Freeze trusted weak eligibility/classification/target flags | `test_freeze_revalidates_quality_and_classification_proofs`: replay archived classification/quality rules; reject short leads, invalid target revision and inconsistent QIDs/root paths; cross-check selected targets |
| Verification missed normalized operational tables | `test_verify_rejects_operational_tables_that_disagree_with_records`: altered article metadata, paragraphs, subtype membership, classification provenance and link target/edge occurrence cannot pass by merely updating the outer checksum |
| Claim races and stale worker completion | `test_stale_worker_cannot_overwrite_a_new_owner_result`: compare-and-claim is transactional; per-claim tokens fence stale completion and failure writes |
| Orphan raw blob redownloaded | `test_orphaned_durable_raw_blob_is_reused_without_network`: recover revision-addressed, identity/hash-checked blobs before making a source request |
| Changed-rule run rediscovered live cohort | `test_same_snapshot_new_rules_reuse_archived_manifest_and_cursors`: compatible discovery inputs inherit archived order/cursors; changed snapshots do not |
| Year attached to wrong attribute | `test_year_is_bound_to_its_factual_clause_not_every_attribute`: only the local factual clause supplies a comparable year; ambiguity gets no invented compatibility credit |
| Alphabetic namespace-only sampling | `test_seed_streams_and_namespace_prefixes_are_scheduled_round_robin`: interleave category seeds, namespace prefixes and fallback; listings remain hints, never historical evidence |
| Audit denominator and invocation accounting | `test_audit_counts_extraction_completed_before_downstream_source_failure`, `test_warm_report_counts_only_this_invocations_source_requests`, cold/warm ETA/rate tests: persisted extraction survives downstream errors; counters are invocation-specific, warm reuse is distinct, estimates are qualified |

The original regressions failed before their fixes. An additional replay regression caught mapping iteration order changing quality signal order after canonical JSON serialization; sorted rule application now makes it stable. Final acceptance also explicitly exercises soft deficits, subtype novelty, hub penalties and adaptive known-revision batches.

Deferred minor: selection currently copies the growing selected set during marginal-gain calculations. Its production-scale cost remains unmeasured. Retain correctness and measure this in the explicitly authorized pilots before optimizing; no 80,000-candidate performance guarantee is claimed.

Review boundaries: real API fidelity/historical coverage, actual template/classification yield, 80,000-candidate scaling, human pair quality, second-machine behavior and tasks T049 onward are unassessed. Fixture checks do not substitute for any of them. Setup checks were completed separately in `setup.md`.
