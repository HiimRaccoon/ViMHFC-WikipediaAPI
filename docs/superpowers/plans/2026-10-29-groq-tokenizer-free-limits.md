# Groq Free limits and tokenizer reservation implementation

Goal: use the requested Qwen Free limits, replace byte-count reservation with a pinned compatible CPU tokenizer and finish at most5 blind smoke records with existing cache.

This is an authorized change to the existing experiment controller. Execute inline; no commit/push, no research-policy/source mutation, no300. Defaults30RPM/1000RPD/8000TPM/200000TPD; lower observed provider limits win. Preserve explicit experiment request/token/runtime budgets and stop semantics.

- [x] Check official Groq rate-limit documentation, previous authenticated headers and official tokenizer provenance. Download only tokenizer assets, never weights.
- [x] Watch failing tests for defaults, tokenizer components/tamper and individual-size versus temporary rate-window handling.
- [x] Add pinned tokenizer/config/template selection, persisted estimates distinct from provider usage, output/schema reservation and unchanged cache keys. Preserve successful Mustamäe row.
- [x] Fix exact-window reset timing; preserve bounded 429/cooldown/retry/resume.
- [x] Focused offline142PASS. Full suite acceptance and code seal required before live.
- [x] Full722PASS; run one bounded authorized smoke continuation on same dataset-v2/shared ledger:4new calls plus1cached, nativeSMOKE_PASS.
- [x] Independently validate token metadata/evidence/cache counts, all old ledger rows and source8files/21tables preservation. Report `reports/verification/groq-tokenizer-smoke5-20261029.md`; stop before300.
