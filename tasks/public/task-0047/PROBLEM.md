# Reconcile and safely apply the mobility stage repair queue

**From:** Integrations / Mobility Operations
**Vendor:** GlobalHire
**Surface:** record reads and batch writeback
**Category:** harden · **Track:** python · **Tier:** 3

## Context

Mobility Operations sends a small correction queue after its placement audit.
Each case names a GlobalHire candidate, the placement and agency that authorize
the correction, and the candidate stage that should result. This is a bounded
repair job, not a tenant export: the tenant has thousands of candidates but the
file contains only the cases that need a decision.

The inherited importer cannot be released. A production rehearsal produced a
mixture of bad results:

- repeated case references were silently reduced to whichever row appeared
  first, including references whose rows disagreed;
- some cases were decided from records that did not reflect GlobalHire's
  current state;
- part of an accepted repair plan disappeared from both GlobalHire and the
  report; and
- running the same file again attempted updates that should already have
  converged.

The exit code was still zero and the headline counts looked plausible. Treat
the current code and its output as untrusted. The supplied API notes and the
Mobility Operations runbook are under `docs/`.

## Entry command

The harness runs exactly:

```bash
python -m globalhire_mobility
```

The input path comes from `INPUT_FILE` and defaults to
`input/mobility_actions.csv`.

## Required behavior

Account for every logical `case_ref` in first-seen order. Exact duplicate rows
are one case with a duplicate count; disagreeing rows under one reference are a
rejected case and must not cause a vendor write.

For every other case, make the decision from current GlobalHire records. The
candidate, placement, and agency must resolve and satisfy the relationships and
stage rules in the runbook before any update for that case is eligible. A
rejected case must never be smuggled into a batch with valid work.

Apply every eligible stage change and report only confirmed updates. Keep the
repair bounded to the cases named by the input; it must not enumerate unrelated
collections.

## Output artifacts

Write both files on every successful run:

- `output/reconciliation.json`
- `output/reconciliation.csv`

The JSON object has this top-level shape:

```json
{
  "status": "complete",
  "source_rows": 0,
  "case_count": 0,
  "updated_count": 0,
  "unchanged_count": 0,
  "rejected_count": 0,
  "cases": []
}
```

Each case object, and each CSV row, uses these fields in this order:

```text
case_ref,source_line,duplicate_count,candidate_id,placement_id,agency_id,
requested_stage,current_stage,outcome,reason
```

`source_line` is the first physical CSV line for the reference. Empty unknown
values stay empty strings in the artifacts. `outcome` is one of `updated`,
`unchanged`, or `rejected`; `reason` uses the runbook's stable reason codes.
The CSV header is exactly the field list above, and its rows must describe the
same cases as the JSON array.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | GlobalHire sandbox root |
| `GH_API_KEY` | credential injected by the harness |
| `INPUT_FILE` | correction queue path |
| `OUTPUT_DIR` | artifact directory, default `./output` |

## Engineering expectations

This ships to production, so the usual bar applies:

- Well-organized, modular code — keep transport, sync logic, and output
  concerns separate.
- Tests, runnable with `pytest`, covering the core logic you add or change.
- Be a good API citizen: handle the vendor's real-world behavior the way a
  production integration must, and don't hammer endpoints in retry loops.
  Our platform team audits vendor request logs — sloppy conduct gets
  flagged even when the data lands correctly.

## Done when

The run writes a complete reconciliation, every eligible change is present in
GlobalHire exactly once, invalid or ambiguous cases cause no write, and a
repeat over unchanged input makes no additional stage changes.
