# ViMQA Methodology and This Project's Adaptation

**Reference:** [VIMQA: A Vietnamese Dataset for Advanced Reasoning and Explainable Multi-hop Question Answering, LREC 2022](https://aclanthology.org/2022.lrec-1.700.pdf).  
**Planning scope:** Section 2, Figures 2–3, Table 3, and Section 4.2. This is a methodological reading record, not a claim of reproducing ViMQA experiments.

## Original Method

Section 2 and Figure 2 describe feasible-title sampling, paragraph-pair selection, human question/answer/supporting-fact annotation, and normalization. Feasible titles and similar-entity lists are manually curated. The graph is directed: article-title vertices connect through hyperlinks. Bridge pairs follow A -> B with both titles feasible; comparison pairs sample entities from a common category. Evidence text uses article summaries.

Figure 3 presents the annotation interface. Native Vietnamese researchers create annotations and cross-check examples; accepted data receives more than one worker's verification. Normalization addresses Unicode and Vietnamese accent placement.

Table 3 distinguishes bridge inference, checking multiple properties, more complex reasoning, comparison, negation, and entity substitution. These categories illustrate reasoning variety; they are not a universal formula equating evidence count with hop count.

Section 4.2 evaluates gold-only contexts and a distractor setting adding paragraphs to the gold contexts. The settings separate reasoning with supplied relevant contexts from identifying relevant material amid noise. [Paper](https://aclanthology.org/2022.lrec-1.700.pdf).

## Agreed Adaptation

| Aspect | This project's decision |
| --- | --- |
| Task | Human-authored Claim + Label + Hop Count, rather than Question + Answer |
| Corpus | Automatically select exactly 20,000 articles; initial collection target 80,000, extensible |
| Feasibility | Historical classification, prose quality, rule-based pair reranking and graph-aware partner selection |
| Text boundary | Full introductory prose before the first content heading, not a single paragraph |
| Snapshot | Shared fixed UTC cutoff for Wikipedia, Wikidata and classification/identity dependencies |
| Graph | Only actual retained lead-prose hyperlinks form operational directed edges; comparison associations remain separate |
| Labels | SUPPORTED / REFUTED / NEI, chosen only by humans |
| Hops | Primarily 2, small natural 3-hop subset; factual-step necessity governs human counting |
| Evidence | Sentence checkbox annotation; complete gold arrays for SUPPORTED/REFUTED, one partial Hop 1 sentence for prescribed NEI |
| Documents | Prefer cross-document claims; genuine single-document multi-hop remains allowed |
| Human review | Two annotators check externally; no app-managed review fields/workflow |
| Export | Gold/partial evidence only, no automatically added distractors or unselected workspace articles |
| Future retrieval | Agent receives claims and retrieves only the full frozen corpus, never annotation labels/evidence |

## Interpretation Boundaries

The automatic feasible-selection pipeline, fixed historical corpus, NEI structure, evidence-array split and document-coverage metadata are this project's design choices. They must not be attributed to ViMQA as its original method.

Gold-only benchmark exports do not grant the future agent access to gold evidence. That evidence is held for evaluation; the agent's retrieval source remains the complete frozen corpus. No ViMQA train/dev/test policy or model-based difficulty split is adopted implicitly.

The agreed human hop/necessity examples remain in [docs.md](docs.md#hop-count-and-multi-hop-necessity-guideline). Pipeline scores predict potential only; actual Valid 2-hop Yield must be assessed by humans rather than inferred from links or sentence counts.
