# Bounded 100 candidate assessment

Run: 9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e; snapshot: 2026-10-25T03:00:00Z; config: c29c2a66f744704373dcbebb8294154a6dbd5361dbbf98cc2904125d54e3175b.

Eligible: 16; unresolved extraction: 8; source failures: 0.

| Stage | Wall seconds | Active seconds | New | Reused | New articles/minute |
|---|---:|---:|---:|---:|---:|
| process | 226.236 | 226.236 | 100 | 0 | 26.5210113158591 |
| acquire | 0.000 | 0.000 | 0 | 100 | None |
| extract | 0.983 | 0.983 | 100 | 0 | 6100.934469935828 |
| classify | 65.942 | 65.942 | 100 | 0 | 90.98856349126915 |
| quality | 0.069 | 0.069 | 100 | 0 | 87093.220220517 |
| resolve_links | 153.734 | 153.734 | 16 | 0 | 6.24455219331086 |
| item_hint | 6.242 | 6.242 | 19 | 0 | 182.6193038696237 |
| discovery | 0.008 | 0.008 | 0 | 0 | 0.0 |
| pairs | 0.000 | 0.000 | 0 | 0 | 0.0 |
| selection | 0.001 | 0.001 | 0 | 0 | 0.0 |

End-to-end seconds: 226.452. Stage times overlap and must not be summed. Queue time is zero for this serial scheduler; API backoff is separate.

Requests/rates: {"error_count":0,"error_denominator":531,"error_rate":0.0,"physical_request_count":531,"retry_count":0,"retry_denominator":531,"retry_rate":0.0}. Raw cache: {"hit_rate":0.8244370308590492,"hits":1977,"lookups":2398}.

Peak RSS bytes: 190201856; disk growth bytes: 297811930. Production ETA: {"reason":"New-rule replay reuses historical raw cache; this is not a cold production measurement.","seconds":null,"status":"unavailable"}.

No real frozen corpus delivered. Human Valid 2-hop Yield is unassessed. Inspect metrics.json/template-audit.json and source risks before continuing. Production does not start automatically.
