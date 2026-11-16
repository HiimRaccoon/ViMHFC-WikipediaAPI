# Annotation Collection V2 — Offline Pilot 1,000 Handoff

Status: **STOPPED during checkpoint persistence; partial metrics only. No retry performed.**

## Invocation

- Frozen context: run `80524a0f-87fe-4c01-af9f-dfa4b3951899`, cohort 1,000, hash `08bc22892cb821f9b8f52ac6e18f903d5bbb77142b21b549f22bccf214ec676c`.
- Snapshot: `2026-10-25T03:00:00Z`.
- Policy: `quality-relaxed-1` / `annotation-edge-1` / `rank-annotation-1`; target 1,000.
- Invocation: `scripts/annotation-collection.py` with sockets disabled by the CLI. No Wikimedia request, LLM call, freeze or production command.
- Elapsed wall time: approximately **14.8 seconds** from tool invocation to process exit.

The process failed saving a progress checkpoint: Windows returned
`PermissionError: [WinError 5] Access is denied` while replacing
`checkpoint.json` from `checkpoint.tmp` at `annotation_builder.py:88`. It had
visited 250 cohort identities. The last atomically replaced checkpoint has
249 records; the temp file contains 250 records and has a valid envelope
checksum. That temp state was inspected read-only for partial metrics. No
selection result exists. As requested, the process was not repaired or run
again. The output is at
`.tools/annotation-collection-v2-pilot1000-20261116/`; it contains the partial
checkpoint and temp checkpoint. Root cause of Windows refusing the replace is
not established.

## Partial metrics

These describe the 250 visited identities only, not a completed Pilot:

| Metric | Partial result |
|---|---:|
| Checked article records | 250 |
| Eligible articles | 87 |
| Intake blockers | 0 |
| Link occurrences | 1,025 |
| `VERIFIED_EDGE` occurrences / internal edges | 0 / 0 |
| `CANDIDATE_EDGE` occurrences / internal edges | 450 / 5 |
| `UNRESOLVED` occurrences | 575 |
| `BLOCKED_EDGE` occurrences | 0 |
| Internal graph vertices | 87 |
| Weak connected components (including isolates) | 82 |
| Largest component | 4 |
| Non-isolated vertices | 7 |
| Distinct A→B→C paths | 0 |
| Selection | Not reached |

Candidate edges remain ranking metadata. They do not increase verified edge
counts or constitute evidence/gold.

## Comparison with prior Relaxed Pilot

The November 1 Relaxed Pilot completed its offline evaluation of the same
frozen cohort: 998 processed, 367 eligible, 19 legacy-policy eligible edges,
22 non-isolated vertices, largest component 12 and 1 distinct A→B→C path.
The current 250-record prefix has 87 eligible articles and 5 candidate
internal edges. These are different scopes and different edge policies; the
partial figures cannot establish a full-pilot gain or loss. The 19 archived
edges were not rewritten or promoted by this invocation.

## Preservation and next blocker

SHA-256 comparison covered **12,340 files**, including the frozen run DB/raw
cache/context/config/cohort, upstream source DB, `data/integrity` Registry and
evidence, November 1 Relaxed Pilot package and November 14 graph package:
**0 changed or missing files**. Only the new `.tools` output and this new
handoff were written. No code, source DB/cache, Registry, archived policy or
archived result was changed. No commit or push.

The operational blocker is checkpoint replacement on Windows. No safe cause
or retry was established. This run provides no final 1,000-article selection
metrics; a further invocation requires a new explicit user instruction.
