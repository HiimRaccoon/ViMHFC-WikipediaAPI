# Annotation edge repair — Technical Review Handoff

Status: adapter fixes implemented; end-to-end Builder integration remains pending. No Pilot or replay executed.

## Files and fixes

- `backend/src/vimfc/pipeline/annotation_edges.py`: missing archived move indexes now yield UNRESOLVED with an explicit MISSING_PROOF reason. A checked target remains CANDIDATE_EDGE when only ownership proof is missing. Missing raw/revision evidence cannot create an edge. Redirect contradictions and integrity errors remain blocked; conflicting strict V2 targets cannot silently leave a candidate in ranking.
- The new `annotation-collection-2` manifest binds source page/revision/raw/manifest, classification, quality, literal target title, scope, sentence/paragraph IDs, start/end spans, edge state/reason and the full target proof/redirect record. Quotas are included in the manifest. No archived manifest is rewritten. Article input ordering does not affect selection or the sorted input digest.
- Annotation dispatch rejects unlabelled records and wrong quality policy rather than silently dropping every graph link. Existing `select.py` version dispatch remains the entry point. Legacy dispatch is unchanged.
- `tests/unit/test_annotation_edges.py`: regression coverage for occurrence-digest mutations, missing move proof, contradictory redirects, and unlabelled Builder input.
- `docs/specs.md`, `docs/plans.md`, `docs/tasks.md`: FR-012's proven-primary requirement remains applicable to legacy policies; only the new relaxed collection path makes classification optional. Candidate edges remain discovery/ranking metadata, never verified evidence or human gold.

## Readiness and limits

The current Builder calls selection on its process records; it does not invoke `select_articles` or persist/check its adapter manifest. Simply changing the ranking version is therefore unsupported and now fails visibly for unlabelled records. Existing upstream checkpoint/resume is unchanged, but adapter-result resume and checked relaxed Builder intake still require wiring before an end-to-end Pilot is ready. This patch does not claim a completed collection runner or freeze.

The adapter still requires the existing checked archive contract for raw content, cutoff, namespace and redirect checks. Missing target raw/identity evidence stays unresolved, never guessed. Strict ownership authority remains unavailable; candidates do not solve that evidence gap and do not need to solve it for ranking. No new historical acquisition or semantic/claim validation was added.

## Verification

Completed 2026-11-16 (Asia/Saigon). Focused offline tests: **48 passed**. Full offline pytest: **972 passed in 361.52 seconds**. Snapshot remains `2026-10-25T03:00:00Z`. No LLM request, Pilot 1,000, offline Pilot replay, Pilot 10,000, production, commit or push was performed.

Preservation baseline includes the frozen run directory (source DB/raw/config/cohort), upstream source DB, November 1 release, November 14 offline graph output, and 109 files in `data/integrity` including Registry and evidence artifacts. The Registry baseline was added during the offline suite, not before it; this is a stated scope limitation. This is file hashing, not pipeline replay.

Post-test SHA-256 comparison: **12,340 files checked, 0 changed/missing**. Baseline and result are retained locally as `.tools/annotation-preservation-before.json` and `.tools/annotation-preservation-result.json`. No preservation claim is made for files outside that baseline.
