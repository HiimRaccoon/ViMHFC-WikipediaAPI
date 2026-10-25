# User-run Pilot 100 replay assessment — 2026-10-25

The user executed the replay in their own terminal. This assessment reads the resulting local reports, exit-code file and archived classification rows; it does not start acquisition, retry, another pilot or Production.

Run: `9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e`; source run: `b0243673-5f0b-4ac6-b8b3-0ca7aa74ccc6`. Both reports have the same cohort hash and fixed snapshot `2026-10-25T03:00:00Z`. New config hash: `c29c2a66f744704373dcbebb8294154a6dbd5361dbbf98cc2904125d54e3175b`.

Evidence: `100/metrics.json`, `100/template-audit.json`, `100/classification-audit.json`, `100/summary.md`, and `100/pilot100-20261025-142947-8609746.exit-code.txt` (0). All 100 candidates were attempted; this does not mean all 100 are eligible.

| Metric | Original Pilot 100 | User replay |
|---|---:|---:|
| Elapsed | 2,056.83 s (34m17s) | 226.45 s (3m46s) |
| Extraction completed | 35 | 92 |
| Extraction unresolved | 65 | 8 |
| Classification supported | 17 | 27 |
| Classification unclassified | 17 | 30 |
| Classification pending | 66 | 43 |
| Pending reason: maxlag | 20 | 0 |
| Pending reason: truncated hierarchy | 46 | 43 |
| Eligible articles | 5 | 16 |
| Physical API requests | 3,956 | 531 |
| Request errors / retries | 103 / 83 | 0 / 0 |
| API backoff | 475 s | 0 s |
| Raw cache hit rate | 14.21% | 82.44% |
| Positive bridge/comparison pairs | 0 / 0 | 0 / 0 |

The observed replay elapsed time is 9.08x shorter and physical requests are 86.58% lower. It reused the archived cohort/raw cache, including all 100 article acquisitions. These ratios combine implementation changes, cache reuse and different source conditions; they are not a cold-cache speedup estimate or Production ETA.

Of the original 46 truncated classifications, 7 now complete, 7 are unclassified, and 32 remain truncated. Of the original 20 maxlag cases, 3 now complete, 6 are unclassified, and 11 reveal truncated hierarchies once metadata is available. Thus the current 43 truncated cases are 32 residual cases plus 11 formerly blocked by maxlag; their presence does not contradict the earlier offline retrospective.

All 43 current pending classifications are nonretryable `truncated_hierarchy`; 26 contain at least one configured root. Root presence alone does not establish that the necessary sibling branches are complete. Re-running the same fixed rules or using transient retry is not a remedy for these structural cases.

The eight residual extraction failures match the offline assessment: strict unsupported `sclass`, `cvt`, `grt`, `USA`/malformed markup, and unverified Vietnamese topic components. No eligibility threshold was lowered. Final quality outcomes: 16 eligible, 41 non-pending quality rejections, 43 pending classifications; article-level source failures 0.

Current dominant stage: link identity dependencies, 153.73 s / 390 requests; classification 65.94 s / 141 requests (122 Wikidata revisions, 19 pageprops hints). Stage times overlap and must not be summed. Cache counters include 81 historical association hits, 4,409 hierarchy memory hits and 36 negative source-cache hits. Zero physical request errors does not imply all historical dependencies exist.

Recommendation: **do not proceed to Pilot 1,000 yet**. Investigate the 43 required cutoff paths and remaining extraction forms under the existing snapshot/taxonomy constraints. Keep incomplete branches pending until supported by a sound versioned rule change. No conclusion about pair-selection quality follows from the current 16 eligible articles and incomplete classifications. Any further changed-rule run requires a distinct run ID; no follow-up execution is authorized by this report.
