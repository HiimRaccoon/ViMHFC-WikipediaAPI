# Verified dump inventory implementation plan

User authorization: implement, test offline, download verified official metadata and freeze a new inventory; no pilots or commits. Execute inline, with independent review before live import. Existing API inventory and historical data stay untouched.

## Design and invariants

`dump-inventory-1` is a Discovery input source, consumed by unchanged `seed-only-2`. Pin viwiki/20261001 completed jobs, official dumpstatus and SHA1SUMS. Select page, categorylinks and linktarget SQL gzip files (359,627,729 compressed bytes), rather than article/history content. Stream and verify compressed size/SHA-1 and record local SHA-256. No unverified or partial download becomes input. A dump date is not the fixed historical snapshot.

Parse SQL bytes without execution: validate CREATE columns, tuple arity, quoting/escaping and numeric identities; decode only title/type fields as UTF-8. Categorylinks uses cl_target_id joined to lt_id (namespace14). Binary sort keys are never decoded. Import into a NEW SQLite database with file-scoped transactions and completed-file receipts; interrupted imports roll back and restart that file without double counting. Namespace/title conflicts fail closed. Redirects remain in metadata but are excluded from candidate admission.

Walk configured category roots deterministically to depth3 (sampling only), with up to128 categories per stratum. Store category paths as hints, not qid-5 evidence. Select up to100,000 unique ns0 nonredirect identities using unchanged soft-quota/exploration sampler. Freeze immutable inventory, manifest, config and ordered ID digests. Retain full metadata universe for future extension. Publish staged validated output via atomic no-clobber links; never seal partial inputs. Record resources on failures too.

## Tasks

- [x] Tests RED/GREEN: manifest refusal, resumed/ignored ranges, retry/budget, corrupt bytes, SQL binary/escaping/schema/EOF, duplicate/conflicting identity, category joins, deterministic selection and crash recovery.
- [x] Implement official manifest verification and bounded serial resumable downloader with request/byte caps, durable cooldown, checksums and atomic publication.
- [x] Implement streaming SQL reader and transactional SQLite metadata import with explicit schemas and joins.
- [x] Implement deterministic strata and inventory freeze compatible with seed-only-2; CLI and failure telemetry.
- [x] Independent review; final full offline suite531PASS and33,666file preservation baseline before live.
- [x] Download359,627,729 verified bytes; freeze100,000 identities; repeat selection,100,000identity/68,733provenance-edge offline audit and preservationPASS.
- [x] Update SPEC/PLAN/TASK/operations and report actual counts, coverage/resources/source caveats. `reports/verification/dump-inventory-viwiki-20261028.md`; no pilot/commit/push.

Actual source compatibility correction: official SHA1SUMS repeats two UNUSED change_tag filenames with conflicting hashes. Selected artifacts must each retain exactly one distinct hash matching their completed job. Never pick a hash for a conflicting selected input. Scope review and final regression evidence are preserved in the report artifacts; first metadata-only stop was resumed on the same directory/budgets after the fix.
