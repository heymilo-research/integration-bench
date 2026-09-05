# Agency consolidation handover — 2026-07-29

The old CrewCall tenant was retired after the agency consolidation. Its final
worker watermark was `wkr_9999`; that value was copied into the replacement
job so the first production run would not replay old workers.

Worker ids have always increased, so keep the copied watermark and process only
ids after it. An empty first export is expected while the new tenant catches up.
