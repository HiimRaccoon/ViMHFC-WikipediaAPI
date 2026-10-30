# Seed-only collection operations

Forward strategy: **seed-only-2**. Legacy/Hybrid commands remain historical compatibility tools. Do not prepare new comparison arms. Research rules remain qid-5/lead-4/quality-2.1/rank-1 at2026-10-25T03:00:00Z.

Active candidate acquisition source is now **dump-inventory-1**: use [official dump operations](dump-inventory.md). The API checkpoint at6,848 identities/76 attempts is preserved, not merged or automatically retried. The API listing and pilot examples below document compatibility and separately authorized future operations; the dump-inventory task does not authorize Pilot100/1000/10000 or Production.

The listing checkpoint contains present-day category/allpages observations, not historical evidence. Category BFS depth3/128 categories per stratum controls listing cost, never ontology traversal. Namespace0 listing and nonredirect filtering at discovery are intake hints; historical article revision, namespace and Wikidata association must still validate during processing. Exploration may encounter articles created after snapshot or with unsupported taxonomy; unknown inputs are not silently ineligible.

Commands use an existing context only for validated semantic config/contact and cache baseline; collection does not modify its database/cache. Use NEW output directories. `collect` can resume its own unfrozen SQLite checkpoint with the SAME policy/budget, respecting persisted cooldown and charged attempts; it does not reset the budget. After freeze it refuses more listing work. Extension requires new inventory/freeze, not mutation. A request cap/source stop is a review point, not permission to loop.

```powershell
$Python = Join-Path $PWD '.tools\python312\python.exe'
$SourceContext = Join-Path $PWD 'data\replays\quality21-20261026\run-context.json'
# Set NEW paths; review source state/caps before actual execution.
$InventoryDir = Join-Path $PWD 'data\inventories\seed-only-NEW'
$RunDir = Join-Path $PWD 'data\experiments\seed-only-NEW'
& $Python -B .\scripts\seed-only.py collect --context $SourceContext `
  --output-dir $InventoryDir --target 80000 --max-requests 800
if ($LASTEXITCODE -ne 0) { throw 'Listing stopped; inspect checkpoint/source before further work.' }
& $Python -B .\scripts\seed-only.py prepare --context $SourceContext `
  --inventory (Join-Path $InventoryDir 'inventory.json') --output-dir $RunDir `
  --source-review .\configs\reviews\integrity-23640705-20261027.json
if ($LASTEXITCODE -ne 0) { throw 'Preparation failed; do not run pilot.' }
```

Preparation creates an inactive refusal draft, not an approval. The current user request authorizes a separately scoped approved copy ONLY for Q10761835/revision23640705 after independent RCA/evidence/run/config/snapshot hash review; record that actual authorization, do not invent a reviewer. Preserve the original draft. Other revisions/unknown integrity failures stop.

`run --size 100 --max-requests 3000 --reviewed-quarantine <approved-copy>` executes one invocation and writes an immutable guarded receipt. Log stdout/stderr with Tee-Object and save native LASTEXITCODE immediately. Exit5 is incomplete, exit3/other failure requires inspection, exit0 is not pilot acceptance. `verify --size 100 --output <NEW.json>` is cache-only and checks replay. For an incomplete prefix, create a separate scoped exploratory-progression receipt pinning the actual report hash and verified prefix before `run --size 1000 --max-requests 10000 --exploratory-progression <permit>`. Without that explicit validated permission the original completeness gate remains mandatory. No automatic resume or larger stage is implemented.

Missing hints keep24h TTL and finite negative-observation budget; do not query repeatedly or guess QIDs. Quarantine stays in the denominator and supplies no evidence. Observe API counters, errors/retries, host circuit deadlines and report-inclusive sampled RSS. Compare yield using full candidate budget and processed denominators separately; category/discovery strata do not establish primary-group balance. Heuristic pairs do not establish human Valid2-hop Yield.

## Recovery after HTTP503

`inventory-host-1` preserves the original listing policy and cumulative800-attempt ceiling. It paces starts by at least1second and locks the checkpoint across workers. Every query remains one HTTP attempt with maxlag5; new503 ends the invocation. Backoff5s×2^streak plus bounded positive jitter (cap300s) and Retry-After determine the next authorized retry time. Deadlines/circuit/lease are shared across streams; no probe loop or automatic second recovery.

New immutable receipts and resource reports are in `acquisition-attempts/<invocation_id>/`, including aborts. Safe HTTP headers/body fingerprints are diagnostics, not trusted historical source. Old75 rows and successful provenance are never rewritten to add missing headers retrospectively. `--freeze-minimum` explicitly permits a valid >=60000 snapshot even with target80000 shortfall after a source503/cumulativebudget stop. This flag never starts a pilot or changes completeness/acceptance. Freeze uses the host lease and staged checksummed publication; recovery of missing aliases needs zero API calls. Invalid existing artifacts are refused rather than overwritten.
