# Official viwiki dump identity inventory

`dump-inventory-1` supplies Discovery metadata to unchanged Seed-only2. It does not supply historical article text, Wikidata evidence, primary labels, subtype labels or eligibility. Fixed snapshot remains2026-10-25T03:00:00Z; dump date20261001 has a different meaning.

The acquisition CLI pins official page/linktarget/categorylinks SQL gzip metadata (about360MB, no full history). It requires8GiB free disk for import/index/staging headroom. A new `data/inventories/dump-*` directory contains official manifests, partial/verified downloads, persistent capped download state, transactional metadata SQLite, immutable source.json and final inventory.json/manifest.json. The OS lock prevents concurrent writers. Compressed checksums/size are rechecked before each import. Matching completed import receipts are reusable; incomplete file transactions roll back. Final manifest is the publication seal; an inventory without that manifest is not frozen.

```powershell
# Acquisition/import only; never starts Pilot or Production.
& .\.tools\python312\python.exe -B .\scripts\dump-inventory.py `
  --context .\data\replays\quality21-20261026\run-context.json `
  --output-dir .\data\inventories\dump-viwiki-20261001-20261028-100k `
  --target 100000
```

Resume uses the same arguments/path and cumulative request/byte state, after reviewing the prior receipt/cooldown. It does not reset attempts, replace corrupt input, refetch missing pinned manifests, or create a new inventory to evade caps. HTTP throttling honors Retry-After; waits above120s stop for an operator decision. Three transfer attempts/file and twelve cumulative artifact attempts; metadata request cap4 separately. Conservative reserved bytes1.2GB count complete expected responses even for partial resumes/failures; actual delivered artifact bytes reported separately. Initial source/schema investigation is a separate diagnostic scope.

SQL is parsed as data, never executed. Unknown SQL/XML/schema is refused. Category sortkeys are binary. Current format joins cl_target_id→lt_id restricted to namespace14; pages join cl_from→page_id. Title/ns conflicts fail closed. Redirect metadata is retained and excluded from selection. Duplicate identical records are counted, conflicting identities refused. Eight configured category roots, depth3/max128 categories each, deterministic BFS paths supply sampling hints only. Unassigned pages use exploration/fallback. Page-ID hash ordering is deterministic; namespace and association at the historical snapshot remain required intake checks. A namespace0 listing is not proof of historical namespace0.

Selection can extend beyond100,000 by a future explicitly versioned inventory/cohort; existing frozen inventories are never edited. Do not reinterpret inventory counts as eligible articles, population yield, or sufficient support for20,000 eligible corpus articles. Legacy/Hybrid runs remain preserved for reproducibility and are not active comparison arms.
