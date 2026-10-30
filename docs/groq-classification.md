# Groq-assisted historical article classification

This is a separate CPU-only Python experiment. It never writes to the pilot database, accepted article classification, eligibility, graph, inventory, raw cache or integrity registry. `qid-5 / lead-4 / quality-2.1 / rank-1`, snapshot `2026-10-25T03:00:00Z`, depth12/node2048 and eight quotas remain unchanged.

`GroqAssistedClassifier` defaults to Wikidata-first assisted mode: a completed verified primary is returned unchanged without a Groq call. Unclassified/unresolved articles may receive a text-grounded suggestion with sentence IDs. Explicit `--mode blind` also calls the model on verified controls for agreement evaluation. Both modes send only historical title/lead/sentences; baseline outcomes, sampling hints, eligibility, provenance and human labels remain local. Predicted/reference-valid does not mean semantically supported, Wikidata-verified or eligible.

## Install on Windows

```powershell
Set-Location -LiteralPath 'C:\ViFC_MultiHop_Data'
# No CUDA, PyTorch, GPU, HPC, self-hosted model or Groq SDK is required.
& .\.tools\python312\python.exe -m pip install -e '.\backend[test,groq]'
& .\.tools\python312\python.exe -B .\scripts\prepare-groq-tokenizer.py
```

The REST client uses the repository's existing httpx dependency and official Groq HTTPS endpoints only. `GROQ_API_KEY` is read first from the process environment, then from ignored `api/.env`. Save the real key locally using the UTF-8 literal dotenv format shown by `api/.env.example`; do not paste it in chat, put it in command history/source, or commit it. The loader supports UTF-8 BOM, CRLF, quoted literals and `export`; it does not execute/interpolate dotenv content. `GROQ_MODEL` can select an exact model ID. Duplicate/malformed settings fail closed.

## Free-only execution and models

Do not infer account tier from a key, listed model or rate-limit headers. The API's models endpoint verifies availability, not chat permission, billing tier or free credit status. The current implementation has no account-tier attestation API. No code changes subscriptions, payment methods or spend limits.

Before generation, confirm your account is Free Tier, has no payment method and has not been upgraded. Set `GROQ_FREE_TIER_CONFIRMED=true` in ignored `api/.env` (or the process environment). Only the literal `true` (case-insensitive) confirms it; a key alone does not. Missing/false confirmation blocks POST with `LIVE_NOT_RUN_FREE_TIER_UNCONFIRMED`. This is explicit user confirmation, not provider billing attestation. No evidence JSON, screenshot, image checksum or reviewer identity is required. Revoke the flag if your account changes; all request/token/rate/timeout limits remain enforced. The old evidence example is unused historical material.

Supported capability profiles are documented candidates, not claims of access: current strict-schema profiles include `qwen/qwen3.8-27b`, `openai/gpt-oss-20b` and `openai/gpt-oss-120b`; legacy `qwen/qwen3-32b` uses JSON object mode if actually listed. Every client must successfully list authenticated models before POST. Prefer available strict Qwen, then available legacy Qwen. Other models require explicit configuration; an unavailable/retired model or failed strict mode never silently switches model/mode. Successful responses must identify the requested model. Raw response, model ID, prompt/schema hashes, parameters, usage, headers and latency are kept locally; hosted backend revision is not pinned and bitwise inference reproducibility is not claimed.

Official sources checked2026-10-29: [models](https://console.groq.com/docs/models), [structured output modes](https://console.groq.com/docs/structured-outputs), [reasoning options](https://console.groq.com/docs/reasoning), [rate limits/header units](https://console.groq.com/docs/rate-limits), [billing tiers](https://console.groq.com/docs/billing-faqs). Model capabilities and limits can change; actual organization headers can reduce configured limits.

## Prepare and run

Use new directories. Existing outputs require explicit resume/ownership; rejected historical output paths receive no failure receipt. The dataset contains verified historical retained prose only: unresolved/provisional extraction, empty lead or unsuitable sentences have an audited exclusion reason. It replays raw identity/hash/cutoff, namespace/manifest bindings and exact lead extraction without calling Wikimedia. Source-derived outcome balancing and rare-group prioritization are declared sampling choices, not population weighting.

```powershell
$Python = Join-Path $PWD '.tools\python312\python.exe'
$Context = Join-Path $PWD 'data\experiments\dump-seed-only-20261028-084004-499522\run-context.json'
# Choose a NEW Groq namespace; never point output at old pilot/cache/inventory.
$Experiment = Join-Path $PWD 'data\experiments\groq-classification-MY-NEW-EXPERIMENT'
$Dataset = Join-Path $Experiment 'dataset'
$Inference = Join-Path $Experiment 'inference'
& $Python -B .\scripts\groq_classification.py prepare `
  --context $Context --output $Dataset --size 300 --seed 20261029
& $Python -B .\scripts\groq_classification.py preflight `
  --dataset $Dataset --output (Join-Path $Experiment 'preflight') --env-file .\api\.env
# After GROQ_FREE_TIER_CONFIRMED=true is saved in api/.env.
& $Python -B .\scripts\groq_classification.py run `
  --dataset $Dataset --output $Inference --limit 5 --smoke --mode blind `
  --env-file .\api\.env
# Separate authorization for300 is required; the current task permits smoke5 only.
# A matching PASS smoke is also required; existing valid/invalid responses are replayed.
& $Python -B .\scripts\groq_classification.py run `
  --dataset $Dataset --output $Inference --limit 300 --resume
& $Python -B .\scripts\groq_classification.py evaluate `
  --dataset $Dataset --results-root $Inference --output (Join-Path $Experiment 'evaluation-NEW')
& $Python -B .\scripts\groq_classification.py report `
  --metrics (Join-Path $Experiment 'evaluation-NEW\metrics.json') `
  --output (Join-Path $Experiment 'review-report-NEW.md')
```

An explicitly chosen fallback can be supplied with `--model openai/gpt-oss-20b` after that ID is actually listed. Selection is pinned in `experiment-config.json`; resume preserves mode/model/confirmation settings and refuses changed source config. `--limit` selects the deterministic prefix, so300 includes earlier smoke inputs. Assisted mode keeps verified labels and may make fewer calls; use blind mode for three-view comparison. A smoke with only verified bypasses is not an inference smoke and cannot PASS.

## Persistence, quota and failure behavior

`api/groq-experiment-ledger.sqlite` is the ignored shared experiment ledger; an explicit alternate ledger must also be a separate `.sqlite` file. Foreign/historical databases are refused before changing journal/schema. A process-identity lease serializes Groq work. Each reserved attempt is committed before network I/O. For qwen/qwen3.8-27b, the pinned official CPU tokenizer counts the rendered chat, the complete JSON schema and max output; this estimate is charged until provider usage settles it. Estimated tokens are not actual tokens or a guaranteed upper bound on an undisclosed provider template. This is a conservative reservation/physical-attempt budget, including uncertain/pre-wire outcomes, not a claim every reservation reached the provider. The whole safe HTTP success body/checksum is committed before suggestion publication, so a crash in between replays the response without another request. In-flight/unknown network outcomes stop for manual reconciliation; the code does not blindly repeat them. Terminal invalid JSON/schema/evidence outputs are not automatically regenerated.

Current defaults:310reserved requests and600,000total charged/reserved tokens per experiment across manual invocations, at most two attempts/input,30s HTTP timeout,60s maximum wait and1,800s execution budget. The shared ledger aggregates rolling-minute/day use conservatively across scopes/models and keeps host cooldown/pacing through restart. Qwen3.8 Free defaults are30RPM/1,000RPD/8,000TPM/200,000TPD; model-specific and actual header limits may only reduce them. The legacy profile has lower limits. RPD is organization-wide daily requests, TPM is minute tokens; these header units are not interchangeable. External clients using the same organization remain unobserved except through provider headers; the ledger is not a provider-wide usage meter.

429/5xx use bounded positive-jitter exponential backoff; a longer Retry-After wins. Day/quota deadlines or waits beyond60s return a checkpointed pause; there is no automatic next-day run, infinite retry, cap reset or paid-tier upgrade. Conservative reservations or runtime limits may prevent finishing300 in one invocation/day. A later manual resume preserves prior costs/results. Actual usage exceeding its reservation is durably charged and blocks further dispatch for review. Missing credentials return `LIVE_NOT_RUN_MISSING_API_KEY`; unsupported models, revoked confirmation, quota exhaustion and ambiguous request outcomes stop explicitly.

## Evaluation and human review

`evaluate` exports baseline verified-only, blind Groq and Wikidata-first suggestion views, outcome/group coverage, agreement/disagreements, abstention, invalid JSON/evidence, usage and latency. Baseline agreement is not accuracy. Accuracy/precision/recall/Macro-F1 and human confusion matrices remain unassessed until actual reviewed gold is supplied. Missing predictions and abstentions keep their denominators; no self-confidence is treated as calibrated accuracy.

Edit a COPY of `human-review/human-review.csv` or JSONL. Preserve page IDs/fingerprints, fill `human_primary` (or explicit `human_abstain`), `review_status=reviewed`, actual reviewer and ISO review date. Pending/empty labels are not gold; identity drift/duplicates/invalid groups fail. Re-evaluate into another new directory with `--human-labels PATH`. CSV text is formula-escaped; JSONL preserves exact source text. Disagreements and Event/Location/Science examples are investigation candidates, not proof of an ontology bug.

The four label namespaces are explicit: `wikidata_verified_primary`, `llm_suggested_primary`, `human_reviewed_primary`, and unresolved. A bound manual review can create a separate reviewed experiment record; it never replaces verified Wikidata or promotes an article into corpus eligibility. Future promotion requires a separately reviewed classification policy version/new run plus independent evidence and quality gates.

## Tokenizer and Free limits amendment ? 29/10/2026

The pinned compatible tokenizer is `Qwen/Qwen3.8-27B@366a23fd246985933964e9d21ac63eb3a93190ab`, loaded with Hugging Face `tokenizers` and a sandboxed Jinja environment. No Transformers, weights, GPU or runtime remote code is required. `scripts/prepare-groq-tokenizer.py` downloads only verified tokenizer/config files with finite attempts and publishes atomically. Inference never downloads assets implicitly. Missing/tampered files stop before dispatch; other model IDs need a verified compatible estimator and do not silently use byte counts.

The official config's embedded chat template is explicitly hash-pinned. The separate `chat_template.jinja` at that commit contains an undefined `tiger_royal` test and is not executed or patched. The official embedded template uses the standard string test. This choice is recorded as `tokenizer_config.json#chat_template`. The frozen Mustamäe input yields637local chat-template tokens, matching the previously observed637provider prompt tokens; that single matching control is not proof of every future hosted template revision.

`token_estimate` records chat/template tokens, schema tokens, estimated prompt tokens, output reservation, tokenizer revision/hash and `actual_tokens=null`. Groq `usage` remains separate authoritative provider observations. Full response-format JSON is tokenized as an explicit schema-overhead estimate; the provider can inject a different wrapper. An actual total exceeding a reservation is charged durably and stops further dispatch for review. Old cached records are not rewritten; a current estimate for one is a post-hoc diagnostic.

Public Free limits match30/1000/8000/200000 for this model. Actual headers can lower RPD/TPM; these generic headers do not expose RPM/TPD or independent ITPM/OTPM overrides. Exact organizational/project configuration also lives in the authenticated console; the client does not claim an undocumented billing/admin API attestation. 429/Retry-After and reset headers enforce tighter provider conditions. Temporary minute/day/pacing waits resume only within max_wait_seconds and the overall execution budget; a truly oversized individual request remains non-waitable.

The request/prompt/schema cache key is unchanged by operational token/rate policy changes. Changed pinned configs still fail closed on ordinary resume. Use an explicitly documented new operational attempt/scope for an authorized policy amendment, sharing the existing ledger and frozen dataset: old rows/costs stay intact, organization day/minute tracking aggregates scopes, and a cached successful input is never regenerated. This does not authorize a new dataset or300article experiment.
