# Article Collection & Selection — Technical Review Handoff

Status: **policy adapter implemented; no Pilot or production run**.

## Change

- New `annotation-edge-1` labels checked historical Full Lead links as
  `VERIFIED_EDGE`, `CANDIDATE_EDGE`, `BLOCKED_EDGE` or `UNRESOLVED`.
  `CheckedArchive.resolve` supplies the target revision/raw/redirect checks.
  A legacy checked resolution is only a candidate. A strict V2 resolution may
  upgrade a matching target after its own authority checks. Numeric titles
  require V2's typed title selector; a bare numeric legacy key stays unresolved.
- New `rank-annotation-1` reuses the deterministic selected-set connectivity
  selector. Candidate edges have weight 1, verified edges weight 2. Its
  internal-edge and distinct-vertex path gains favor connected groups; factual
  quality, diversity, eight soft quotas and singleton fallback remain. No
  minimum hyperlink count or graph connectivity requirement was added.
- `select_articles` accepts an already checked article pool of any size and
  returns the selected IDs, separate edge-state counts, checked article/link
  records and a versioned input/selection manifest. Candidate links remain
  collection/discovery metadata and are never added to strict verified-edge
  counts or human gold. No old ranking/resolution policy was edited.

`quality-relaxed-1` remains the article admission rule: checked historical
Wikipedia source and Full Lead, at least one factual sentence, no minimum 40
tokens or two sentences, and no classification hard gate. The new adapter
requires records bearing that policy and a source-validation manifest. Its
caller must obtain those records through the existing checked archive path;
this adapter does not fetch or revalidate the original source bytes itself.

## Boundaries and limits

No edge was promoted in the real cohort. The 19 old edges remain
`MISSING_PROOF` for strict historical ownership; a future authorized
collection run may label an old checked target as a candidate after rechecking
its archived target bytes. No archived Pilot result was reinterpreted. Title
only links without a trusted page ID stay unresolved. Any source/target
integrity contradiction, wrong snapshot or namespace remains blocked.

The new adapter is an opt-in selection path, not a newly wired source runner or
an authorized 20,000-article freeze. It uses the full caller-supplied candidate
pool and can report insufficient eligible supply. At 100,000 candidates, memory
and runtime still need measurement before a production decision; no large
Pilot or production job was run. Existing source checkpoint/resume remains the
upstream mechanism; a future run must persist this policy's manifest under a
new run identity. There is no claim, hop or semantic validation in collection.

## Verification

Focused tests cover candidate labeling, redirect preservation, numeric title
ambiguity, wrong snapshot, integrity failure, verified/candidate ranking,
selected-set paths, singleton fallback and deterministic input digest.
Focused offline pytest: **44 passed**. Full offline pytest: **963 passed in
367.84 seconds**. The full run used a workspace-local `--basetemp`; a first
full run was interrupted at 37% because the manifest digest code was edited
during that run, then restarted against the final code.

Before/after SHA-256 of the frozen run DB matched
`169a76e8cd0010e479241a31407b73a7837dd731c9710218313760464557dc1d`;
the source DB matched
`a6b47b049fa07b567a2e5afb3344d91acaa2d2dc023e8fe154383330e88a475e`.
All **9,959** raw-cache files matched the sorted path-and-file-hash digest
`07fc5aacbe4d7e5eb70035c0e5096397a704dc5217802d89ca1ebae7786e6640`.

During verification, 15 tracked `docs/superpowers` files appeared deleted;
they were restored from HEAD. Two previously untracked files,
`docs/superpowers/plans/2026-11-01-relaxed-pilot.md` and
`docs/superpowers/plans/2026-11-06-graph-recovery.md`, are now missing. A
workspace and VS Code local-history filename search did not find recoverable
copies. Their content was not available to reconstruct; this is a workspace
preservation limitation requiring review.

No Pilot 1,000, offline Pilot replay, Pilot 10,000, Production 20,000, LLM
request, commit or push was performed.
