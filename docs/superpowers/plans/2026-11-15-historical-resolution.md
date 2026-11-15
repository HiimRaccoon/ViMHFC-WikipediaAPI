# Historical target resolution repair

The user authorizes implementation, targeted source acquisition if needed, and
offline tests; Pilot1000 and any offline replay of that Pilot remain blocked until
the user explicitly authorizes them. Keep source archives and old policy behavior.

1. Establish logevents `pageid`/`logpage` semantics from MediaWiki code and retain
   hidden-action uncertainty, original events and their hashes. Test visible
   mismatches, hidden events and horizon boundaries.
2. Introduce explicit typed historical title/page-ID lookup and isolated cache
   keys, including numeric names and redirects. Preserve existing lookup behavior.
3. Integrate the typed lookup and move proof in a separate versioned recovery
   path, with source hash/cutoff checks and resumable budgets. Test cache-only,
   target ownership, namespace, redirects, failures and old-policy compatibility.
4. Audit the 19 earlier edges individually using saved evidence only, identify
   proven / missing / contradicted proofs and explain the former zero coverage.
5. Run focused tests and full pytest offline. Prepare, but do not execute, the
   authorized-cohort Pilot command; report unresolved blockers and await the user.
