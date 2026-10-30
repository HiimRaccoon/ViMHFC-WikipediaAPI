# Groq-assisted classification experiment implementation plan

> User explicitly requests autonomous implementation, offline tests and bounded Free-only execution. No commit/push, no extra design-approval pauses. Preserve the existing workspace/data and all historical policies.

**Goal:** Evaluate text-grounded Groq suggestions on a deterministic300-article historical sample without changing verified classification, eligibility or graph.

**Architecture:** Separate experiment package, immutable source dataset/manifest, deterministic Vietnamese prompt with repository precedence, validated suggestions and human-review namespace. Official REST over existing httpx; no GPU/SDK dependency required. Durable SQLite attempt/reservation/response ledger with serial pacing and conservative caps. Evaluation keeps baseline agreement distinct from human-gold metrics.

**Contract:** Input row `{input:{title,lead_text,sentences:[{id,text}]},evaluation:{page_id,revision_id,snapshot,baseline_outcome,baseline_primary,classification_reason,sampling_stratum,eligible},provenance:{...},input_fingerprint}`. Only `input` enters model user message. Prompt includes fixed group definitions plus config precedence, never per-article baseline/stratum/gold. Output `{predicted_group,abstain,evidence_sentence_ids,reason,ambiguity}`; strict independent validation, evidence references not truth guarantees. Store states `wikidata_verified_primary`, `llm_suggested_primary`, `human_reviewed_primary`, `unresolved` separately.

## Tasks and interfaces

- [ ] Dataset (`dataset.py`, tests): read-only Archive+trusted raw/manifest/namespace and extraction-sentence checks, exclude unresolved/provisional extraction, deterministic balanced sample including3outcomes/allgroups/focus strata, immutable JSONL+manifest, `prepare_dataset(context,output,size=300,seed=20261029)` and `load_dataset(root)` integrity validation.
- [ ] Provider (`provider.py`, tests): official REST models and chat, dotenv without secrets in logs, explicit model selection/capability profiles, finite timeout/retries, safe HTTP receipts/Retry-After parsing, no silent fallback/tools. Free tier must be independently evidenced; model-list/rate headers alone cannot certify billing tier.
- [ ] Prompt/schema/runner (`prompt.py`,`store.py`,`classifier.py`,`cli.py`, tests): repository precedence, no leakage, full JSON validation, per-input/model/prompt/config key, exclusive process lease, durable attempt/token reservations and cooldown/RPM/RPD/TPM/TPD limits across resume, no repeated valid/invalid terminal requests, explicit uncertain crash state, smoke≤5 and cumulative experiment cap310HTTP/600000tokens across manual multi-day resumes. Rolling24h TPD≤180000 and TPM≤7000 are below current documented200k/8k caps for preferred models; profile/header limits may reduce them. Never auto-wait into another day or increase caps. Verified bypass in assisted mode; explicit blind evaluation mode enables baseline-comparison calls without labels in prompt.
- [ ] Evaluation (`evaluation.py`, tests): predictions/baseline/assisted coverage/agreement, strict human-review imports fingerprint-bound, accuracy/F1 only with reviewed labels, templates/disagreements/latency/token metrics. No source DB write interface.
- [ ] Integration/preflight: full offline suite, separate output dir, source preservation/hash audit, independent review. If key+verified free/model readiness hold, smoke5→bounded300 resume; otherwise explicit LIVE_NOT_RUN reason while delivering complete code/data/commands. No Wikimedia calls or pilot.
- [ ] Handoff: exact dataset/results/commands/source lines/tests/costs/free/model verification limits and preservation. No fabricated labels, suggestions never affect eligible counts.

## Review focus / mandatory controls

- Source/schema/config/prompt/input tampering and sentence-ID/Unicode offsets must fail closed; no current-Wikipedia fallback.
- Prompt injection and baseline/stratum metadata must remain input data only; strict schema does not prove factual sufficiency.
- Resume/crash/in-flight requests must not silently duplicate paid inference; reserve worst-case tokens before I/O, including failed attempts.
- Free plan cannot be inferred from a model ID or familiar rate limits; insufficient evidence blocks generation, never assumes upgrade permission.
- HTTP429/date Retry-After, org-shared quotas, malformed/error bodies, unsupported strict output and exhausted daily budgets persist stop state. No auto mode/model downgrade or subscription changes.

## Execution result

Implementation tasks completed; accepted fullsuite705PASS338.55s after finalreceipt regression. Separate dataset-v2 froze300rows, preservingoriginalDiscoveryhints vs experimentbucket after an independently auditedmetadatafix. Initial dataset kept as intermediate, neverinferred. CLIreadiness MissingAPIKey: actualapi/.env0bytes/envabsent, no selectedmodel or realcompletion. Baseline-only evaluation+CSV/JSONLready; nohumanlabels/F1/liveclaim. Source all21tables/46157files/90oldcorecodeconfigs preserved. Final technicalhandoff: `reports/verification/groq-assisted-classification-handoff-20261029.md`. Explicit user autonomousimplementation scope superseded design/plan pause/commit instructions; nocommit/push or historical/pilot semantic changes.
