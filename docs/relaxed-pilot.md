# Relaxed feasibility and hyperlink-first Pilot

The new `quality-relaxed-1` policy requires one complete factual sentence in a
verified historical Full Lead. Length contributes only to ranking, with no hidden
minimum word/token count per sentence. A missing, unclassified or unresolved
primary remains null and does not reject the article.
Source hashes, selected revisions, historical title/namespace, manifest and
dependency replay, redirect/disambiguation/list exclusions and unresolved
extraction remain enforced. The signal recognizer reuses quality-2.1 literal
spans and adds bounded descriptive class predicates; it is not human fact gold.

`rank-hyperlink-1` reuses sparse deterministic bundle selection, with stronger
verified Lead connectivity and chain utility, capped graph contributions,
subtype novelty, soft group deficits and hub/overrepresentation penalties.
Unknown uses an internal accounting bucket and is never stored as a primary.
Comparison still requires a positively supported subtype. No-link articles
remain candidates. Graph reports separate trusted candidate pool, eligible
endpoints and selected endpoints. A distinct A→B→C path is potential only.

Run the separately authorized same-cohort offline Pilot with:

```powershell
& .\.tools\python312\python.exe -B .\scripts\relaxed-pilot.py `
  --context .\data\experiments\dump-seed-only-20261028-084004-499522\run-context.json `
  --integrity-registry .\data\integrity\blockers-80524a0f.sqlite `
  --output .\reports\verification\relaxed-pilot-NEW
```

The output directory must be new. The reader uses SQLite `mode=ro`, checks
qid-5/lead-4/quality-2.1 replay and the read-only anchored registry. Independent
Wikipedia intake may complete missing-hint slots without inventing a QID;
reviewed quarantine remains blocked. Missing cached identity proofs stay
unknown; current title matches and dump/category metadata never create edges.
All network/DNS calls are denied; the replay makes no LLM calls.
The tool does not create a source run, mutate its checkpoints, resume acquisition,
start 10k/Production, or freeze a reduced corpus. Exit5 means PARTIAL; completion
of the invocation is independent of source completeness and human acceptance.

The package contains article decisions, source manifests, graph witnesses,
ranking/selection, baseline comparison, single-change threshold ablations and
overlapping rejection counts. Never sum overlapping reasons. Preservation pins
all pre-existing durable data/reports/config/source files and API SQLite ledgers;
only credentials, volatile SHM and the new output directory are excluded.

The ordinary production provider/freeze gates remain separately authorized
operations. This offline Pilot does not grant production readiness or measured
human claim yield, and does not alter source classification or create human claims.
