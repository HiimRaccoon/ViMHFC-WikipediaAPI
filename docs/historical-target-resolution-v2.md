# Historical target resolution, version 2

The new opt-in policy keeps the previous `historical-target-resolution-1`
implementation and checkpoints separate. `explicit-selector-1` uses disjoint
keys for title and page-ID selectors, including titles containing only digits.
Version 2 validates selected revisions, raw SHA-1/SHA-256, cutoff, namespace,
redirect chains and unique historical ownership before admitting a graph target.

The [MediaWiki logevents implementation](https://doc.wikimedia.org/mediawiki-core/1.45.1/php/ApiQueryLogEvents_8php_source.html)
shows `pageid` is joined to the page currently at the logged title, while
`logpage` comes from the log record. The [logging table documentation](https://www.mediawiki.org/wiki/Manual:Logging_table)
states that modern move records use the moved page ID in `log_page`, while
older records had different semantics. Therefore the two fields may differ;
this is not by itself an integrity failure. Correctly interpreting a particular
historical record also requires evidence of the deployed semantics at recording
time. The code does not infer that from the event date alone.

An `actionhidden` row withholds action details and must remain unknown. It is
not necessarily an oversight-suppressed row. A checked continuation chain binds
the complete sequence of *returned public API pages* to retained response hashes;
it does not prove that restricted events were absent from the underlying log.
An archived list without independently bound collection receipts cannot alone
establish completeness. Missing coverage or move semantics remains unresolved.
The standard Pilot wiring currently supplies no independent authority for
restricted move history or proof of the historical deployment semantics. Thus
version 2 fails closed before target acquisition; the CLI option alone does not
make a Pilot ready to certify identities. Fixture-injected authorities in tests
are synthetic and are not evidence for the real cohort.

The 19-edge [targeted audit](../reports/verification/historical-edge-audit-20261115.md)
inspects the old evidence without running a Pilot. Raw links, revisions and two
redirects match, but the archived move-log acquisition has no retained full
pagination receipt, so the old graph is not automatically recertified.

The Pilot CLI accepts `--recovery-version historical-target-resolution-2` in a
fresh output. It must not be executed on the frozen cohort until the user grants
explicit permission. The prepared path retains the earlier eligibility, snapshot,
cohort and ranking policy; it does not send LLM requests.
