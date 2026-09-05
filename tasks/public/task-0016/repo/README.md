# HireWire connector

A two-way HireWire integration: it pushes staged stage-change events back into
HireWire (writeback) and keeps an incremental poll of candidates fresh
(polling).

## Commands

    python -m hirewire_connector push   # drain input/pending_events.json -> HireWire, write output/writeback_result.json
    python -m hirewire_connector poll   # backfill / incremental reconcile, write output/candidates.json
    python -m hirewire_connector dump   # re-emit output/candidates.json from the current store

See `../PROBLEM.md` for the ticket and `../docs/` for the HireWire API.
