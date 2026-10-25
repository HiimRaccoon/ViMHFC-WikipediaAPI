# Bounded 100 candidate assessment

Run: b0243673-5f0b-4ac6-b8b3-0ca7aa74ccc6; snapshot: 2026-10-25T03:00:00Z; config: e6c0df8c87da2efdccd059be2cf44356b80f4b625b0cce935bd9689e6f131139.

Eligible: 5; unresolved extraction: 65; source failures: 0.

| Stage | Wall seconds | Active seconds | New | Reused | New articles/minute |
|---|---:|---:|---:|---:|---:|
| process | 1841.779 | 1366.779 | 100 | 0 | 4.389883639464431 |
| acquire | 0.866 | 0.866 | 100 | 0 | 6931.809937197397 |
| extract | 0.984 | 0.984 | 100 | 0 | 6100.085319855797 |
| classify | 1373.294 | 898.294 | 100 | 0 | 6.679330394336015 |
| resolve_links | 460.873 | 460.873 | 100 | 0 | 13.01877154189544 |
| quality | 0.080 | 0.080 | 100 | 0 | 75398.10198103776 |
| discovery | 214.921 | 214.921 | 0 | 0 | 0.0 |
| pairs | 0.000 | 0.000 | 0 | 0 | 0.0 |
| selection | 0.000 | 0.000 | 0 | 0 | 0.0 |

End-to-end seconds: 2056.829. Stage times overlap and must not be summed. Queue time is zero for this serial scheduler; API backoff is separate.

Requests/rates: {"error_count":103,"error_denominator":3956,"error_rate":0.026036400404448937,"physical_request_count":3956,"retry_count":83,"retry_denominator":3956,"retry_rate":0.020980788675429726}. Raw cache: {"hit_rate":0.14214463840399003,"hits":513,"lookups":3609}.

Peak RSS bytes: 180965376; disk growth bytes: 290778140. Production ETA: {"reason":"Insufficient cold observations, or warm resume; full-size selection cost remains unmeasured.","seconds":null,"status":"unavailable"}.

No real frozen corpus delivered. Human Valid 2-hop Yield is unassessed. Inspect metrics.json/template-audit.json and source risks before continuing. Production does not start automatically.

## Verified outcome and recommendation

The cohort finished at **12:08:02 Asia/Saigon on 2026-10-25**, after **34 minutes 17 seconds** (2.92 attempted candidates/minute end to end). All 100 unique candidates were attempted; this does not mean all articles are completely classified or eligible.

| Outcome | Count |
|---|---:|
| Eligible | 5 / 100 (5%) |
| Quality rejected, excluding pending classification | 29 |
| Pending classification | 66 |
| Full Lead extraction completed | 35 / 100 |
| Unresolved extraction | 65 / 100 |
| Positive bridge/comparison pairs | 0 |

Classification and extraction outcomes overlap. Of the 66 pending classifications, **46** report `truncated_hierarchy` and **20** report exhausted maxlag retries. Separately, 17 candidates have supported classifications and 17 have no supported rule. The machine report's `source_failed=0` must not be interpreted as zero API problems: there were **103 physical request errors (2.60%)**, **83 retries (2.10%)**, and **475 seconds of backoff** across **3,956 requests**. Maxlag cases are retained as pending classification.

Classification took **22 minutes 53 seconds**, including backoff; lead-target resolution took **7 minutes 41 seconds**. These are measured stages within the pipeline and must not be added to the encompassing process stage. Peak RSS was **172.6 MiB**, disk growth **277.3 MiB**, and raw-cache hit rate **14.2%**. Historical cache contains 366 Wikipedia and 936 Wikidata revisions, including dependencies.

Frequent unresolved templates include `langx` (10 distinct articles), `thông tin khu dân cư` (5), `bài cùng tên` (4), and `thông tin máy bay` (4). Counts overlap and are not additive. Several Vietnamese infobox/hatnote aliases are currently treated as unsupported; distinguish excluded non-prose components from factual inline templates before adding historical-safe support. Never silently drop factual clauses or use live expansion.

**Recommendation: defer pilot 1,000 for now.** The 100-cohort execution boundary and artifact checks passed, but extraction/classification yield is too weak to make immediate scaling useful. First investigate hierarchy traversal/terminal-root behavior and missing rule coverage, recover the 20 transient maxlag cases, and add fixture-backed handling for frequent excluded aliases / safe factual templates. Rule changes require a new run with the same snapshot, compatible archived inputs, and renewed pilot reports. Do not raise depth or relax quality thresholds merely to increase yield.

Source/archived-data inspection found that traversal continues above already recognized roots; 27 of the 46 truncated cases contain configured roots in their historical closure. This is a concrete reason to investigate terminal-root/path handling, while preserving unresolved/conflicting dependency safeguards. No code or rule changes were made during this assessment.

There are no positive candidate pairs in the five eligible articles, so this cohort has not validated useful pair-selection quality or large-pool selection performance. Human Valid 2-hop Yield and production ETA remain unassessed; do not extrapolate 80,000-candidate throughput or feasible yield from this small cohort.

## Artifact/source verification

- Cohort JSON contains exactly 100 distinct canonical page IDs and matches its stored manifest hash.
- Report JSON matches the SQLite pilot record; template audit matches the report.
- SQLite integrity and foreign-key checks pass.
- All 1,666 selected revision references obey the common snapshot; all 1,302 raw objects pass gzip/object SHA-256 plus content SHA-256/SHA-1 and revision-cutoff checks.
- Production authorization is absent; only the size-100 pilot report exists. No 1,000/10,000 cohort or corpus freeze was run.
- Raw `metrics.json`, `template-audit.json`, and `cohort.json` are preserved unchanged. This assessment adds interpretation rather than altering measured results.
