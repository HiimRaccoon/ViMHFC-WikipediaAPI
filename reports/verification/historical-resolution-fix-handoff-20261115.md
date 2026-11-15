# Historical target resolution: implementation handoff

Status: **partial; no Pilot run**. Version 2 is implemented and tested, but the
real cohort does not yet have independent evidence establishing coverage of
restricted move actions. The standard Pilot wiring therefore fails closed; its
selection alone cannot certify recovered targets. Test fixtures with injected
authorities do not establish evidence for real Wikipedia pages.

## Changes

- Explicit, separate title and page-ID selectors and cache keys support numeric
  titles and redirects without treating their names as page IDs. The previous
  selector and version-1 recovery remain available.
- Move proof preserves the original records and hash. An operational view uses
  `logpage` when the applicable deployed semantics are independently verified;
  hidden actions, incomplete pagination and unverified semantics are unknown.
- Version-2 recovery verifies selected-revision receipts, response hashes,
  pagination, cutoff, raw content, redirect/namespace and ownership evidence.
  Checkpoints and acquisition remain isolated and bounded.
- The Pilot CLI accepts `--recovery-version historical-target-resolution-2`;
  invoking it for the frozen cohort requires the user's separate authorization.

MediaWiki's [logevents implementation](https://doc.wikimedia.org/mediawiki-core/1.45.1/php/ApiQueryLogEvents_8php_source.html)
distinguishes `pageid` joined at the logged title from the logged `logpage`.
The [logging-table documentation](https://www.mediawiki.org/wiki/Manual:Logging_table)
describes the change in `log_page` semantics for move events. An API response
can withhold action fields or omit restricted entries, so public pagination by
itself does not establish the absence of hidden moves.

## Earlier 19 edges

The [targeted audit](historical-edge-audit-20261115.md) examined each edge
without running the Pilot. All 19 saved Lead occurrences, source and target
raw hashes, cutoffs and both redirect closures matched; no edge was contradicted.
None is fully recertified: the archived move indexes lack independently bound
collection-completeness/ownership evidence. The earlier version's blanket
`pageid != logpage` guard explains the collapse in coverage, but does not prove
that the 19 edges are false or that they can safely be restored.

## Verification and remaining work

- Focused regression: **81 passed**.
- Full pytest with `VIMFC_OFFLINE=1`: **955 passed in 376.58 seconds**.
- No real Pilot 1,000, offline replay of that Pilot, Wikimedia source API,
  Groq, Qwen, other LLM API, production run, commit or push in this turn.
- Existing source caches, source databases, integrity registry and archived
  Pilot outputs were not intentionally modified; no new preservation hash sweep
  was performed in this turn.

An independently verifiable source for the relevant restricted move history
and evidence for event-time MediaWiki semantics are still needed. Until then,
the real target resolutions remain unresolved and an improvement in graph
coverage cannot be claimed. Further Pilot execution must wait for the user.
