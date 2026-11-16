# Annotation Collection V2 — Pilot 1,000 Resume Handoff

## Execution

- One offline resume was run against the existing frozen cohort and output directory. The resume started from the valid committed 249-record checkpoint; the legacy 250-record `checkpoint.tmp` was not promoted or used.
- Processed: **1,000 / 1,000** cohort identities. Snapshot: `2026-10-25T03:00:00Z`. Source run: `80524a0f-87fe-4c01-af9f-dfa4b3951899`.
- Policies: `quality-relaxed-1`, `annotation-edge-1`, `rank-annotation-1`; manifest: `annotation-collection-2`, ID `68b4774114708f0f0efe6f3ed8a8abdc0face04ebb410444f5b4dcbec25af4b8`.
- The CLI returned exit code 0, no blockers, and `pilot_accepted=false`. Selection returned all **368 eligible** articles, short of the 1,000 target by **632**; no corpus was frozen.
- Elapsed wall time was approximately **73 seconds**, measured across the resumed command session; the CLI does not persist a runtime metric.
- Network access was disabled by the CLI. No Wikimedia API or LLM was called.

## Metrics

| Measure | Result |
|---|---:|
| Eligible articles | 368 |
| Link occurrences: `VERIFIED_EDGE` | 0 |
| Link occurrences: `CANDIDATE_EDGE` | 1,875 |
| Link occurrences: `UNRESOLVED` | 2,377 |
| Link occurrences: `BLOCKED_EDGE` | 0 |
| Internal selected-pool edges: verified / candidate | 0 / 19 |
| Selected-pool graph vertices | 368 |
| Weak connected components, including isolates | 350 |
| Isolated / non-isolated vertices | 346 / 22 |
| Largest component | 12 |
| Distinct A→B→C paths | 1 |
| Selection outcome | `insufficient_supply` |

The sole path is `[3662957, 12023070, 28366]`. It is a structural ranking diagnostic, not a claim or evidence. Candidate links remain candidate links and were not counted as verified evidence. The all-verified graph has zero edges and 368 isolated vertices. Of the 4,252 link occurrences, 1,650 point outside the eligible selected pool; they do not become internal graph edges.

## Comparison with November 1 Relaxed Pilot

The earlier Relaxed Pilot assessment reported 998 processed, 367 eligible, 19 legacy-policy verified eligible edges, 22 non-isolated vertices, largest component 12, and one distinct path. This completed V2 assessment processed 1,000, with **one additional eligible article**. Its selected-pool diagnostic has the same 19 internal connections, 22 non-isolated vertices, largest component 12, and one path, but those 19 V2 links are `CANDIDATE_EDGE` and **zero are verified**. The metrics therefore do not show a verified-edge increase or support treating candidate links as historical ownership proof.

## Resume and preservation

- The builder resumed from the 249-record committed checkpoint; the old 250-record temporary file was not used for recovery. The completed checkpoint contains 1,000 records and the run returned its persisted result.
- Preservation comparison against `.tools/annotation-pilot1000-preservation-before.json`: **12,340 tracked files; 0 changed; 0 missing**. This includes the source database and sidecars, raw cache, Integrity Registry and archived outputs.
- The run wrote only to `.tools/annotation-collection-v2-pilot1000-20261116/`. The old `checkpoint.tmp` remains an ignored legacy artifact in that owned output directory.

## Readiness and limits

The V2 offline intake and deterministic selection completed on the authorized frozen cohort. The available eligible supply is 368, so this Pilot does not establish readiness to fill a 1,000-article target, much less the 20,000-article target. Hyperlink connectivity is sparse: 19 candidate internal edges and one structural two-hop path. The run was not accepted, no corpus was frozen, and no claim quality or human annotation utility was tested.

No source, cache, registry, archived policy, or archived result was changed. No further Pilot or production run was performed.
