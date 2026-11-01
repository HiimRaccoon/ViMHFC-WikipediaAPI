# Wikidata-first + Groq-assisted Classification Shadow

Policy `wikidata-first-groq-provisional-1`; source processed denominator **985**.

**Descriptive experimental coverage only; human accuracy UNASSESSED.**
LLM provisional primary is not Wikidata-verified, human gold or accepted corpus evidence.

| State | Count |
|---|---:|
| wikidata_verified_primary | 431 |
| llm_provisional_primary | 46 |
| abstained | 28 |
| out_of_taxonomy | 0 |
| unresolved | 480 |

Baseline assigned 431/985 (43.76%); assisted assigned 477/985 (48.43%).
Verified eligible remains **162**; separate provisional eligible candidates **15**.

| Group | Verified | Provisional | Verified Extraction pass | Provisional Extraction pass | Verified Quality pass | Provisional Quality pass | Verified eligible | Provisional candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Person | 234 | 0 | 149 | 0 | 60 | 0 | 60 | 0 |
| Organization | 55 | 4 | 33 | 4 | 26 | 3 | 26 | 3 |
| Location | 9 | 21 | 3 | 21 | 2 | 3 | 2 | 3 |
| Event | 10 | 7 | 4 | 7 | 3 | 4 | 3 | 4 |
| Creative Work | 73 | 5 | 57 | 5 | 46 | 2 | 46 | 2 |
| Award / Competition | 32 | 2 | 28 | 2 | 19 | 0 | 19 | 0 |
| Landmark / Facility | 15 | 7 | 7 | 7 | 6 | 3 | 6 | 3 |
| Science / Technology | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 0 |

Quality-pass columns apply every saved quality criterion other than the verified-classification prerequisite. No threshold is lowered; provisional candidates remain unaccepted.

Trusted retained leads 531; legacy cache reused 74; legacy records requiring new subject/scope validation 24.
Pending new-schema calls 174; invalid terminal outputs 0.

OUT_OF_TAXONOMY is explicitly model-declared, not human or historical truth. Legacy abstention has unspecified membership.
Subject-title/citation/technical-scope checks are conservative textual gates, not semantic entailment or a calibrated quality guarantee.

## Operations

```json
{
  "status": "OFFLINE_REPLAY",
  "new_api_requests": 0
}
```

## Source integrity and next experiment

The source cohort is1000:985processed and15outside this processed frame (13deferred,2reviewed quarantine in the saved handoff). The integration does not recover source failures or missing historical dependencies. Original qid-5 ambiguity/no-rule outcomes remain visible; model text judgments cannot repair historical Wikidata evidence.

Proposed Pilot1000: freeze the same ordered1000identities/snapshot; paired arms A=originalqid-5 and B=Wikidata-first+this provisional wrapper. Use identical source, extraction, quality, inventory and quotas. Keep15source-blocked slots in both denominators. Report985processed separately; stratify paired coverage/quality/cost deltas by original outcome/group/reason. Freeze a finite request/token/runtime scope and shared account meter; preserve response/prompt/cache identities and an untouched quality evaluation subset. Do not treat provisional candidate gains as verified eligibility, accuracy or permission for production. This is a design, not an executed Pilot.
