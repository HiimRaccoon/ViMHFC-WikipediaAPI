# Targeted historical edge evidence audit — 2026-11-15

**Verdict: 0 fully certified, 19 MISSING_PROOF, 0 CONTRADICTED. All 19 literal Lead link occurrences and their archived source/target bytes are supported.** This does not establish that the 19 edges are false. It also does not recertify the old graph under a more permissive policy.

Scope: the exact 19 directed eligible edges listed in the [November 1 release metrics](relaxed-pilot-20261101-release/metrics.json), snapshot `2026-10-25T03:00:00Z`, run `80524a0f-87fe-4c01-af9f-dfa4b3951899`. This was targeted evidence inspection using standard-library JSON/gzip/hash operations and direct SQLite `mode=ro` queries inside a read transaction. **No Pilot, pipeline replay, `evaluate_archive`, resolver, extractor, network or Groq call was run.** Prior artifacts were not modified. Eligibility/classification was not reassessed.

## Evidence and verdict standard

Primary records are [archived edge witnesses](relaxed-pilot-20261101-release/edge-witnesses.json), [articles](relaxed-pilot-20261101-release/articles.json), the original source database and raw cache named in [run-context.json](../../data/experiments/dump-seed-only-20261028-084004-499522/run-context.json), and the old/new handoffs: [November 1](relaxed-pilot-20261101-release/Technical%20Review%20Handoff.md), [November 14](graph-recovery-20261114-offline/reports/000001/Technical%20Review%20Handoff.md).

For each edge, the audit checked the actual source gzip object checksum, content SHA-256 and stored source SHA-1, page identity, wikitext model, revision cutoff, source-manifest binding, saved paragraph span, and literal wikilink before the first article section. The target raw object independently passed the same hash/model/cutoff checks and matched its saved page/revision/content reference. The six final targets and both redirect intermediates have retained raw evidence. The two redirect bodies close exactly as recorded below. These checks found no source corruption, fabricated literal target, or contradicted redirect.

**M1 — remaining full-edge proof gap:** each old target proof contains a cached lookup plus `historical_revision_and_move_log`/move-index hash, rather than a retained independently inspectable title-ownership/completeness witness. The original database has no HTTP response/continuation-receipt table for these old move-index fetches, and the inspected old edge records contain no response artifact establishing collection completeness or the semantics of the divergent move identifiers. Hash agreement authenticates the retained list against the old proof; it does not independently establish that the list exhausts historical ownership events. This audit therefore does not promote cached absence of a relevant event into a fully certified ownership result. A hidden event is **not** asserted for any of the target-specific indexes inspected here.

The distinction matters: every visible old move entry is unrelated to these targets, even when considering **both** identifier fields and **both** source/destination titles. A future evidence-backed, target-specific validator may be able to certify these links. The current blanket guard alone is neither proof that a particular edge is false nor sufficient justification to reaccept it.

## Target proofs and exact horizons

`identity_resolutions.lookup_title = '__move_index__:' + horizon` locates each list in the original database. `snapshot_revision_refs` is queried with the exact lookup title and snapshot. Counts apply to the **actual list for that target's initial lookup**, not a later global index. All times are UTC. H/A means hidden/titlehidden/suppressed/actionhidden markers or missing visible title/action details.

| Proof | Literal lookup → final page/revision | Initial lookup horizon | Events / divergent IDs | H/A | Target-related visible rows |
|---|---|---|---:|---:|---:|
| P1 | Tổ chức phi lợi nhuận → 396671 / 75555676 | 2026-10-25T17:03:29Z | 7 / 7 | 0 | 0 |
| P2 | Giải Oscar → 28366 / 75600870 | 2026-10-25T17:45:48Z | 7 / 7 | 0 | 0 |
| P3 | Phi lợi nhuận → redirect 396674/2145754 → 396671/75555676 | 2026-10-28T08:56:07Z | 25 / 25 | 0 | 0 |
| P4 | Fellow of the Royal Society → redirect 14586861/71509046 → 12023070/74501431 | 2026-10-28T09:03:44Z | 25 / 25 | 0 | 0 |
| P5 | Tổ chức Quyền Anh Thế giới → 19657971 / 71036920 | 2026-10-28T09:11:14Z | 25 / 25 | 0 | 0 |
| P6 | Hiệp hội Quyền Anh Thế giới → 19358668 / 73613430 | 2026-10-28T10:31:35Z | 25 / 25 | 0 | 0 |
| P7 | Trung tâm tài chính → 15852199 / 66922005 | 2026-10-25T04:39:57Z | 1 / 1 | 0 | 0 |

Move-index SHA-256 digests, recomputed over canonical retained JSON and matching the saved target proof:

- P1/P2: `b4d88acb8dc5e23aa5705cf17c3c0dedacc79d483b841954940510905b7e7f78`.
- P3/P4/P5/P6: `a426d38bc952da34da60f49d85a66ead1030f2be9fb76a4aa7baca7e1bdb060c`.
- P7: `d7b6dc8f2bc1d9b55ea4a100c8efa330ec161d1959bd9c791c076f43bf5479eb`.

P3 raw redirect is `#ĐỔI [[Tổ chức phi lợi nhuận]]`; raw cache object `viwiki/2145754/07a846a9cd3d1af81adc9b0f06e91b564f6fe04812e27d534b71170d3ca7f745.json.gz`. P4 is `#đổi [[Thành viên Hội Vương thất]]`; object `viwiki/71509046/f01a26b9fde88c16b468a4b4b350b3eb512098a172cb43469ead3facd829660c.json.gz`. Both object/content hashes and historical cutoffs passed. Final-page lookup horizons can differ: P3 final page is pinned at `2026-10-25T17:03:29Z`, P4 at `2026-10-28T09:03:45Z`, P6 at `2026-10-28T09:11:09Z`, and P7 at `2026-10-25T04:39:58Z`; their retained indexes also have H/A=0 and no target-related visible rows. A horizon difference is not itself a contradiction.

## Individual edge verdicts

`p/s/[start,end)` identify the exact saved paragraph, sentence and text span in `articles.json` and `edge-witnesses.json`. Each row's source raw is directly addressable through `source_revisions(site='viwiki', revision_id=<source revision>).cache_ref`; each witness also contains its full source/target content hashes and source-manifest ID. “Literal OK” means the actual historical raw contains the target link and the saved Lead span matches; it does not substitute for M1. Every row references its own target horizon/H/A result through P1–P7 above.

| # | Source → target page | Source article / revision | Lead witness p/s/[start,end) | Target proof | Verdict / root cause |
|---:|---|---|---|---|---|
| 1 | 32414 → 396671 | Tổ chức từ thiện / 75525963 | 0/0/[29,50) | P1, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 2 | 352150 → 396671 | Thanh thương Hội Quốc tế / 74795810 | 0/0/[90,108) | P1, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 3 | 357690 → 28366 | The Wizard of Oz / 75425364 | 1/4/[188,198) | P2, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 4 | 824514 → 396671 | Quỹ Phần mềm Tự do / 75437967 | 0/0/[105,126) | P1, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 5 | 968712 → 396671 | Hiệp hội Bảo vệ Động vật Thế giới / 70865304 | 0/0/[165,178) | P1, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 6 | 1764445 → 396671 | Global Witness / 74469666 | 0/0/[53,66) | P1, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 7 | 3218423 → 396671 | Liên đoàn Cơ học Lý thuyết và Ứng dụng Quốc tế / 74480439 | 0/0/[221,234) | P3, H/A=0 | MISSING_PROOF — literal/redirect OK; M1 |
| 8 | 3220113 → 396671 | Chương trình Khí hậu Thế giới / 65408335 | 0/0/[103,116) | P3, H/A=0 | MISSING_PROOF — literal/redirect OK; M1 |
| 9 | 3220123 → 396671 | Hiệp hội Di truyền học Thần kinh và Hành vi Quốc tế / 67686814 | 0/0/[158,171) | P3, H/A=0 | MISSING_PROOF — literal/redirect OK; M1 |
| 10 | 3220189 → 396671 | Nhà Hóa học Không biên giới / 70702538 | 0/0/[102,115) | P3, H/A=0 | MISSING_PROOF — literal/redirect OK; M1 |
| 11 | 3220233 → 396671 | Liên minh Dữ liệu Hành tinh Quốc tế / 74480521 | 0/0/[124,137) | P3, H/A=0 | MISSING_PROOF — literal/redirect OK; M1 |
| 12 | 3282846 → 396671 | Hội đồng Lưu trữ Quốc tế / 74473769 | 0/0/[155,168) | P3, H/A=0 | MISSING_PROOF — literal/redirect OK; M1 |
| 13 | 3425303 → 28366 | Tài xế taxi / 74866933 | 1/3/[157,167) | P2, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 14 | 3662957 → 12023070 | Vinton Cerf / 75615081 | 0/0/[32,40) | P4, H/A=0 | MISSING_PROOF — literal/redirect OK; M1 |
| 15 | 12023070 → 28366 | Thành viên Hội Vương thất / 74501431 | 1/2/[97,107) | P2, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 16 | 19358668 → 19657971 | Hiệp hội Quyền Anh Thế giới / 73613430 | 0/0/[311,337) | P5, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 17 | 19657971 → 19358668 | Tổ chức Quyền Anh Thế giới / 71036920 | 0/1/[384,411) | P6, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 18 | 19843513 → 15852199 | Khu trung tâm / 75225776 | 0/0/[138,157) | P7, H/A=0 | MISSING_PROOF — literal OK; M1 |
| 19 | 19925976 → 28366 | Field of Dreams / 75595154 | 1/4/[131,141) | P2, H/A=0 | MISSING_PROOF — literal OK; M1 |

## Root cause and interpretation

The November 1 graph reported 19 eligible edges, 22 non-isolated vertices, one distinct three-vertex path and largest component 12. The November 14 stricter report retains those as archived measurements but publishes zero newly certified edges. The direct cause of that coverage drop is `_complete_moves`: it rejects an entire global move index when **any** row has divergent or zero page identifiers, before target-specific relevance is examined. For example, the one-row P7 index concerns **Thảm hoạ Sân bay Madrid → Thảm hoạ sân bay Madrid**, log 53753731, `pageid=20043482`, `logpage=19773213`, timestamp `2026-10-25T04:04:11Z`; neither name nor identifier concerns Trung tâm tài chính (15852199). It still blocks P7. This is a conservative global refusal, not an observed contradiction of edge 19843513→15852199.

All inspected source-page horizons likewise have no hidden flags or visible event identifying the audited source under either ID field. Sources 968712 and 1764445 use 39-event October 29 indexes; other source indexes contain 1 or 25 events. The suppressed events observed during the separate interrupted November 14 online acquisition must not be attributed to these older target indexes: none was found in the exact lists above. No numeric-title case occurs among these 19 edges.

No graph metric was recomputed here, no edge was promoted, and no prior output was revised. The next evidence task is to establish move-field semantics and retain authoritative collection/ownership evidence, then obtain separate authorization before any evaluation. Silently dropping unrelated rows or switching to an optimistic acceptance policy would not close M1.

## Artifact integrity checked

Both packages' `manifest.json` and `release-manifest.json` self-digests and listed file hashes matched: November 1, 6/18 files; November 14 offline, 6/13 files. These are package integrity checks, not independent source completeness certificates.

- Old `edge-witnesses.json` SHA-256: `959a42676bcfabbfd58f8c11888c8afb116ef69ce590fd2b66264bddb0cec0da`.
- Old `articles.json`: `3451275f72d394e2031f778d25ecdd63529fae633dcf47722d2f1bbc9f706b06`.
- Old `metrics.json`: `de623def2259536e64585d19934b8b30391b490e2a5b15ec1f7c25ffdffdeab3`.
- Old result manifest ID: `b6987dfc0246ce64cdb0aa8f9980c61478ea874bb423d35b1edf2862b5008c4e`.
- New offline result manifest ID: `fff129f9be00639e2b923e90c5a0b96ce61bbfbb31f34923869fc725d643663f`.

Only this new report was written by this audit.
