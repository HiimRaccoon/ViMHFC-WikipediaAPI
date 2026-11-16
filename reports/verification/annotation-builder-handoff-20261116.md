# Annotation Collection V2 Builder — Technical Review Handoff

Status: implemented offline Builder entry point; no real Pilot or replay executed.

## Wiring and operation

`Builder.annotation_collection(context, output, registry_path=..., target=20000)` invokes the new `AnnotationCollectionBuilder`. It opens the existing `CheckedArchive` read-only, checks acquisition stage receipts and historical Wikipedia bytes/namespace/cutoff/Registry, extracts historical Lead, applies `quality-relaxed-1`, labels links with `annotation-edge-1`, then calls `select_articles` and `rank-annotation-1`. Primary classification is optional: this intake records it as unclassified metadata and does not acquire/replay Wikidata dependencies. Legacy Builder behavior is unchanged. Broken Wikipedia intake is recorded as an article blocker, never admitted to selection.

CLI, prepared for a later explicitly authorized invocation:

```powershell
.\.tools\python312\python.exe scripts/annotation-collection.py --context <frozen-run-context.json> --integrity-registry <existing-registry.sqlite> --output <new-isolated-output> --target 20000
```

The same arguments resume that output. The CLI disables networking. It has no freeze, source acquisition or stage expansion. The corpus target is configurable and the source pool is the full cohort bound by context; there is no hardcoded 1,000-article limit. A larger inventory must first have its own checked acquisition cohort/context. An inventory title alone never becomes an article or graph endpoint.

## Resume and metrics

`checkpoint.json` is atomically replaced inside isolated output and carries an envelope checksum. The binding includes actual config, cohort/context, policy versions, target and file fingerprints of source database (including WAL when present), raw cache and Registry/evidence. Different inputs/config/target reject resume. Checked article decisions and blockers are persisted per visited article; interrupted intake resumes without duplicate pages or redoing valid intake. A completed result is returned after input/checkpoint checks without rerunning selection. Its embedded `annotation-collection-2` manifest checksum and selected IDs are checked against the saved selection.

Results contain eligible article count, separate four-state edge occurrence/internal-edge counts, selected graph metrics (candidate plus verified), and strict verified graph metrics separately. Components and distinct A→B→C paths describe annotation potential only. Candidate edges never count as strict verified edges or gold.

The output must be outside source database/raw/Registry trees. No source/cache/Registry or archived output is written. All saved checkpoints belong to the new output. Resume rehashes protected sources, so verification has I/O cost. Checkpoint serialization and connectivity ranking have not been benchmarked at 100k inventory scale; no throughput or supply sufficiency is claimed. Missing source stages remain blockers; this offline path cannot download them. Strict V2 authority is still unavailable, so checked legacy targets become candidate edges and verified counts may remain zero. This does not block safe article intake.

## Changed files

- `backend/src/vimfc/pipeline/annotation_builder.py`: checked intake, checkpoint/resume, selection and separate metrics.
- `backend/src/vimfc/pipeline/runner.py`: explicit opt-in Builder method.
- `scripts/annotation-collection.py`: offline CLI.
- `tests/integration/test_annotation_builder.py`: real archive fixtures, resume, input drift, interruption, corruption and output boundaries.
- `docs/plans.md`, `docs/tasks.md`: current integration status.

## Verification

Focused offline tests: **38 passed**. Full offline pytest: **977 passed in 354.83 seconds**. Tests use tiny synthetic archives, not the frozen Pilot cohort. No Pilot 1,000, offline Pilot replay, Pilot 10,000, production/freeze, LLM calls, commit or push.

Preservation: **12,340 files checked, 0 changed/missing** between pre-full-suite and post-suite hashes. Scope includes frozen source run/raw/config/cohort, upstream source DB, `data/integrity` Registry/artifacts, November 1 release and November 14 offline graph package. Local baseline/result: `.tools/builder-preservation-before.json` and `.tools/builder-preservation-result.json`. This comparison covers existing baseline files; it does not certify unrelated workspace files.

Readiness: ready for an explicitly authorized offline checked-cohort selection invocation. The path can select toward 20,000 articles when sufficient checked supply exists; it cannot acquire missing sources or guarantee supply/throughput at inventory scale. No new execution authorization is inferred from these tests.
