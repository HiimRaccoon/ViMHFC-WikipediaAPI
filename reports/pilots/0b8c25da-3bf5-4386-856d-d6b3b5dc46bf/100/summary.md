# Bounded 100 candidate assessment

Run: 0b8c25da-3bf5-4386-856d-d6b3b5dc46bf; snapshot: 2026-10-25T03:00:00Z; config: 6320532acedaa40689021bb96399a647c0a9119d1a1603cae3963d0576e7a8f7.

Eligible: 16; unresolved extraction: 8; source failures: 0.

| Stage | Wall seconds | Active seconds | New | Reused | New articles/minute |
|---|---:|---:|---:|---:|---:|
| process | 19.217 | 19.217 | 100 | 0 | 312.2187624448635 |
| acquire | 0.000 | 0.000 | 0 | 200 | None |
| acquire_manifest | 6.898 | 6.898 | 100 | 0 | 869.7873030822998 |
| extract | 1.455 | 1.455 | 100 | 0 | 4123.747901357905 |
| classify | 4.674 | 4.674 | 100 | 0 | 1283.824688341645 |
| quality | 0.070 | 0.070 | 100 | 0 | 85415.0888772511 |
| resolve_links | 0.053 | 0.053 | 16 | 0 | 18098.38735727518 |
| discovery | 0.008 | 0.008 | 0 | 0 | 0.0 |
| pairs | 0.000 | 0.000 | 0 | 0 | 0.0 |
| selection | 0.001 | 0.001 | 0 | 0 | 0.0 |

End-to-end seconds: 33.830. Stage times overlap and must not be summed. Queue time is zero for this serial scheduler; API backoff is separate.

Requests/rates: {"error_count":0,"error_denominator":0,"error_rate":null,"physical_request_count":0,"retry_count":0,"retry_denominator":0,"retry_rate":null}. Raw cache: {"hit_rate":1.0,"hits":21512,"lookups":21512}.

Peak RSS bytes: 601960448; disk growth bytes: 299761968. Production ETA: {"reason":"New-rule replay reuses historical raw cache; this is not a cold production measurement.","seconds":null,"status":"unavailable"}.

No real frozen corpus delivered. Human Valid 2-hop Yield is unassessed. Inspect metrics.json/template-audit.json and source risks before continuing. Production does not start automatically.
