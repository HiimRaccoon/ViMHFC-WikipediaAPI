# Planning Research and Decisions

**Date:** 2026-10-14  
**Plan:** [plans.md](plans.md)  
**Scope:** Primary-source findings for the fixed corpus and local annotation design. Statements below describe design decisions, not successful live acquisition or measured performance.

## 1. Historical Wikipedia and Wikidata Revisions

**Decision:** Select historical revisions per page/item at the common cutoff, then batch retrieval by known revision IDs and cache raw contents.

**Rationale:** The Revisions API provides timestamp-directed historical enumeration; multi-page queries cannot be combined with the single-page historical enumeration parameters. Validate returned revision timestamps rather than trusting a requested cutoff alone. [MediaWiki Revisions API](https://www.mediawiki.org/wiki/API:Revisions).

**Alternatives:** Current extracts or current entity JSON are convenient but do not satisfy the agreed snapshot requirement. Wikidata supports revision-specific entity access as another transport, but the chosen main-slot retrieval keeps a common provenance model. [Wikidata data access](https://www.wikidata.org/wiki/Help:Data_access).

Current pageprops can suggest an entity ID, but historical association must be checked against archived item sitelinks. P31/P279/P106 and their referenced classification entities require historical versions, not current traversal results. [Pageprops API](https://www.mediawiki.org/wiki/API:Pageprops), [Wikidata membership properties](https://www.wikidata.org/wiki/Help:Basic_membership_properties).

## 2. Historical Rendering and Lead-only Links

**Decision:** Use historical raw wikitext as the authoritative source for prose and explicit lead links. Use an AST plus recorded local inline-template formatters; unresolved factual-template dependencies quarantine the candidate. Parsed API output is optional diagnostic/audit data.

**Rationale:** `action=parse` supports revision IDs and full-page internal link output, but that result is not inherently lead-only. [Parse API](https://www.mediawiki.org/wiki/API:Parsing_wikitext). Historical page viewing can still use current transcluded templates and other dynamic material; pinning the article revision does not pin all rendering dependencies. [MediaWiki page history](https://www.mediawiki.org/wiki/Help:History).

**Alternatives:** Raw text regexes alone are too fragile for nested markup. mwparserfromhell provides a local AST to distinguish headings, links, templates and tags. [mwparserfromhell documentation](https://mwparserfromhell.readthedocs.io/en/latest/api/mwparserfromhell.html). A complete historical-template renderer is possible only with a pinned dependency closure and adds substantial complexity; it is not the default implementation.

The decision is an engineering inference from the documented rendering behavior. It must be tested against archived Vietnamese articles; no successful extraction yield is claimed yet.

## 3. Historical Identity and Redirects

**Decision:** Treat current title/redirect responses as lookup hints. Preserve snapshot title ownership, historical redirect contents, and move-resolution provenance; unresolved mappings do not create operational edges.

**Rationale:** Page moves and redirect pages affect title-to-page identity. Use source revisions and relevant move logs to validate historical mappings instead of declaring current redirects historical facts. [Page moving](https://www.mediawiki.org/wiki/Manual:Page_moving), [Logevents](https://www.mediawiki.org/wiki/API:Logevents), [Redirects](https://www.mediawiki.org/wiki/API:Redirects).

**Alternatives:** Title-string joins or live `redirects=1` alone are faster but can merge different identities incorrectly. A complete historical dump-based identity index remains an alternative if API reconstruction is impractical; it must retain the same snapshot guarantees and be versioned.

## 4. Discovery Manifest and Request Performance

**Decision:** Archive category/namespace discovery streams and complete continuation objects; initial target is 80,000 candidates, with same-snapshot extensions as needed. Use one in-flight external request per host, batching where supported, reusable HTTP connections, cache, maxlag and retries.

**Rationale:** MediaWiki recommends serial requests, batching, informative User-Agent and caching. Noninteractive requests should handle lag failures and Retry-After. [API etiquette](https://www.mediawiki.org/wiki/API:Etiquette), [Maxlag](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter).

Allpages/categorymembers enumerate potential candidates and support continuation, but are not a complete past-state inventory. Their live output is a discovery hint, not historical classification/evidence. Save the full continuation object and do not infer stream exhaustion from an empty page that still has continuation. [Allpages](https://www.mediawiki.org/wiki/API:Allpages), [Categorymembers](https://www.mediawiki.org/wiki/API:Categorymembers), [Continue](https://www.mediawiki.org/wiki/API:Continue).

**Alternatives:** Unbounded parallel requests and large live subtype queries do not solve provenance. Avoid a single broad SPARQL query as a production classification source; service query limits reinforce the need for bounded discovery and cached historical item processing. [Wikidata query limits](https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/query_limits).

Batch sizes, worker counts, thresholds, pair caps and weights in the plan are versioned engineering defaults. They are not official service guarantees or benchmark-quality claims.

## 5. Portable Frozen Data and Atomic Annotation

**Decision:** Use standard Python SQLite repositories, one writer per writable store, read-only frozen corpus connections, and a separately published standalone corpus database without outstanding WAL state.

**Rationale:** SQLite URI `mode=ro` provides read-only opening; `immutable=1` is safe only when the application guarantees that the file cannot change. [SQLite URI filenames](https://www.sqlite.org/uri.html). A WAL can contain committed data not yet in the database file; copying only the main file can lose that state. [SQLite WAL](https://www.sqlite.org/wal.html). Python supports explicit transactions and SQLite URI connections. [Python sqlite3](https://docs.python.org/3/library/sqlite3.html).

**Alternatives:** A shared writable corpus database increases accidental mutation risk. A distributed database or Docker deployment is unnecessary for two separate local installations and outside the agreed delivery.

## 6. Deterministic Multi-hop Potential

**Decision:** Use lead-derived factual-rule signals, bounded subtype/attribute comparison blocks, and lead bridge adjacency. Rank with fixed-point features and select through reproducible partner-aware greedy gains.

**Rationale:** This is a project design choice derived from the agreed spec, not an externally validated semantic detector. It avoids quadratic all-pairs comparison and supports replayable scores without an LLM/Cross-Encoder.

**Alternatives:** Individual article ranking loses partner utility. Unbounded all-pairs enumeration is unnecessary. Learned relevance/hop judgement would exceed scope and must not replace human annotation authority.

## Resolution Summary

All planning decisions have a concrete implementation direction. Data availability, actual extraction yield and runtime throughput remain empirical validation risks, with explicit pending/failure handling and pilot measurement. None justifies live-data substitution, silent rule changes, or automatic label/hop inference.
