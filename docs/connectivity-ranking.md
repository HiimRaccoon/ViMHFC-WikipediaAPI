# Connectivity-first selection

`rank-connectivity-1` is a separate selection policy. It retains the eligibility
decisions of `quality-relaxed-1` and treats the existing primary classification
as metadata: a missing primary group never prevents selection. Group quotas and
subtype diversity are soft preferences.

Only verified historical links in retained lead prose contribute graph utility.
The selector evaluates the marginal edges, non-isolated vertices, and directed
paths with three distinct vertices in the selected set. It considers singleton,
linked-pair, and bounded linked-triple proposals. Comparison and shared-entity
pair evidence can provide a fallback; it does not manufacture graph edges.
Hub normalization gives common neighbors diminishing influence. A primary path
starting with verified Wikidata instance-of (P31) is a weak final tie-break
over subclass-of (P279) evidence, not proof that an entity is concrete or useful.
Isolated eligible articles remain
selectable. Paths are structural annotation opportunities, not verified claims.

The proposal cap limits search cost, not graph measurement; the greedy result
does not guarantee an optimal or connected corpus. Hub-heavy inputs can require
substantial computation and should be profiled before scaling to 20,000 items.
If the eligible pool fits within the target, every eligible article is selected:
the resulting selected graph cannot establish a selection improvement. A smaller
diagnostic target is an ablation, not a replacement corpus.
