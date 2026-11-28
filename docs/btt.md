# BTT forest collection

The operator approved this variant after reviewing the stopped FAST-20K pool.
Use a new checkout, run/config identity and persistent directory. Do not overwrite
the stopped campaign, frozen inventory, fixtures or review exports.

`btt-forest-1` chooses roots from checked eligible first-paragraph records. Prefer
high distinct outgoing target counts, serve underrepresented root groups using
the eight existing soft quotas, and use page ID as the stable score tie-break.
Archived classifications and Qwen suggestions retain their existing separate
labels; Unknown is allowed. Root quota balance is a preference, not a guarantee
of exact group counts across all descendants.

For each root, expand breadth-first through at most three levels including the
root. Each node admits at most two new children, chosen by their distinct retained
first-paragraph target counts. One root contributes at most seven distinct pages.
Then switch to another root not already selected. Keep genuine cross-cluster links,
but never add a duplicate page or follow cycles as new members. Truncate the final
cluster at exactly 20,000; a one-slot tail can be a singleton root. The three-level
limit describes expansion decisions, not semantic claim hops or all paths in the
resulting graph.

All known eligible direct targets are scored. If fewer than two are available,
probe up to eight considered targets in total, with missing titles ordered
lexically. Choose the top two among those successfully checked, not an alleged
global top two over unacquired Wikipedia pages. Numeric titles are withheld from
the existing untyped lookup because they can alias page IDs. An archived unresolved
title is not promoted merely because it matches a cached article title.

Missing targets use the existing historical resolver, then normal checked intake,
namespace/source hashes, exactly one paragraph and `quality-relaxed-1`. Dynamic
children have their own candidate/provenance table. The frozen seed cohort remains
an immutable **root reservoir**; it does not claim all hyperlink targets are in
that inventory. At most 100,000 unique work IDs are admitted, including previously
processed work. Additional lookup accounting is conservative and can stop expansion
earlier. New roots or targets are never fabricated to fill a shortfall.

The fork imports completed acquisition/namespace evidence and compatible processed
records under a new run, with original record hashes retained. It copies private
working data, keeps the parent snapshot read-only, and imports the maximum of
checkpoint and persistent spending. Its new bound budget receipt records the parent
binding and receipt hash. The 200,000 cumulative physical-attempt ceiling includes
the prior spending; there is no new free request allowance. Every simulated
or actual attempt, including title lookup, revision/content, continuation and retry,
still passes CampaignTransport. Cache hits do not charge. Redirects/proxy environment
are disabled, viwiki/maxlag=5 pinned, serial pacing >=1 second retained.

Transient title discovery retries are durably bounded at three. Host cooldowns are
honored without a request loop; a cooldown checkpoint is saved once per deadline.
Other hash, snapshot, identity and storage failures keep existing fail-closed
behavior. Only the reviewed exact ambiguous-move case is skipped as before.

`btt_pool`, `btt_roots`, `btt_members`, `btt_expand`, `btt_links`, `btt_lookups` and
`btt_retry` persist scouting and selection state. Pending Qwen work is preserved;
Qwen availability is not an eligibility gate. Progress reports both eligible pool
size and **selected_articles**. Stop acquisition when selected count reaches the
target, not when an unselected reserve pool reaches 20,000.

Publication reuses the separate annotation publisher and validates only the
selected members. It checks parent/depth, unique IDs, branching, genuine retained
parent links and checked target edges, then replays article source/extraction/quality.
It publishes `corpus.sqlite`, `corpus.jsonl`, `graph.jsonl`, `manifest.json`, and
`forest.json`. The last contains expansion assignments, root decision groups and
considered targets/degrees. Candidate edges remain candidates, not certified gold.
Legacy freeze and annotation contracts are not weakened. Web reader compatibility
still requires its own integration/validation.

`scripts/btt.py` owns the entire initial fork/run/publication under one outer lock.
Active SQLite is node-local ext4; stopped DB backups and immutable raw objects go
to a private persistent campaign. The parent run and its request counter are never
reset. Native runtime staging uses metadata-free copying plus an explicit execute
bit, matching the verified repair. CPU-only Slurm template `configs/slurm/btt.sbatch`
requests job **btt**, four CPUs, 16 GiB, 72 hours, no GPU and no automatic requeue.
Only one actual batch submission is authorized. Capture its ID, verify once and
stop; do not wait for corpus completion or silently submit a replacement.

The target remains conditional on yield, finite remaining budget and walltime.
Candidate exhaustion, budget exhaustion, source problems or interruption produce
an explicit partial checkpoint, never a falsely complete corpus.

Repair after job 151174: suppressed move-log events have no `title`. The shared
historical resolver records an unresolved ownership proof instead of raising
`KeyError`. Malformed non-suppressed records still fail closed. BTT alone may
retain a provisional link candidate after typed historical revision lookup,
canonical-title, raw-source and redirect-chain checks. Its separate
`btt-revision-title-candidate-1` proof explicitly says `ownership_verified=false`;
the strict identity cache remains unresolved and these links are never gold.

SQLite backups and publication databases are now created and closed on the
node-local workspace. Only closed files are copied to persistent storage; no
SQLite write connection is opened on NFS. Checkpoint failures retain the original
error and both tracebacks. A new BTT workspace may fork an exactly compatible,
stopped BTT checkpoint, preserving forest state and the higher persistent request
receipt. The failed job spent 70,148 attempts, despite its older progress record
showing 70,146. Any bounded live repair preflight also counts toward the same
200,000 lifetime ceiling and is carried into the replacement campaign.

Native preflight exposed NFS `EDQUOT` during fsync, despite filesystem-wide free
space. New BTT snapshots therefore gzip the closed local SQLite backup before
upload, while retaining three checkpoint generations. Receipts declare the
encoding and retain the SHA-256 of the original uncompressed database. Restore
verifies the decompressed bytes before creating a workspace; old uncompressed
snapshots remain readable. Source data, request accounting and snapshot policies
are unchanged. Storage availability remains an execution prerequisite.

An operator-approved lifetime ceiling increase is an explicit fork, not an ordinary
resume with edited settings. `--authorized-request-limit` must equal the proposed
profile ceiling, strictly greater than the parent ceiling; every other profile
field must match. Fork only a checkpoint-stopped parent, keep the parent untouched,
rebind a new run/config and processed-record hashes, retain forest/scouting/Qwen
state, and carry the maximum workspace/persistent spending plus receipt lineage.
The new ceiling is cumulative, never a fresh request allowance.
