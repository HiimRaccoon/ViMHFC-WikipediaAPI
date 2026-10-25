# Pilot 100 — explicit user authorization

Recorded: 2026-10-25. Task: T049 only.

The user explicitly authorized **“Cho phép pilot 100”**, supplied the local snapshot **2026-10-25 10:00 AM**, and provided an email for Wikimedia API contact. With the conversation timezone Asia/Saigon (UTC+7), the fixed source cutoff is **2026-10-25T03:00:00Z**. The email is stored in the ignored local pipeline config and archived run configuration, not repeated in this curated report.

- Run ID: `b0243673-5f0b-4ac6-b8b3-0ca7aa74ccc6`.
- Configuration: `configs/pipeline.local.json`.
- Configuration SHA-256: `e6c0df8c87da2efdccd059be2cf44356b80f4b625b0cce935bd9689e6f131139`.
- Versions: pipeline-1, lead-1, qid-1, rank-1 / greedy-1.
- Authorized scope: bounded 100-candidate real-source pilot, checkpoints/retries and performance/template audit, then stop and report.
- Not authorized: 1,000/10,000 cohorts, 80,000 production, real corpus freeze, or GitHub push.

Builder readiness evidence before starting is in `reports/verification/pilot-readiness.md` (81 offline tests passed). This authorization releases the Manual Pilot Start Gate for T049 only; T097's production gate remains closed. T049 stays unchecked until real pilot execution and reports are verified.
