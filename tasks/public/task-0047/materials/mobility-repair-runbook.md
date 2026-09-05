# Mobility Operations: stage repair decisions

The CSV columns are:

```text
case_ref,candidate_id,placement_id,agency_id,requested_stage
```

Whitespace around values is not significant. Blank required values make the
case `rejected / invalid_input`.

## Duplicate references

Group the full file by `case_ref` before making vendor calls.

- Identical normalized rows under one reference are one logical case. Preserve
  the first line number and report how many physical rows carried it.
- If any normalized business value differs within a reference, reject the
  logical case as `conflicting_duplicate`. Do not decide it from the first or
  last row and do not write it.

## Authority checks

For a non-conflicting case, use current record reads and apply these checks in
order. The first failed check supplies the reason code.

1. Candidate, placement, and agency must resolve: `candidate_not_found`,
   `placement_not_found`, or `agency_not_found`.
2. None may be soft-deleted: `candidate_deleted`, `placement_deleted`, or
   `agency_deleted`.
3. The placement must be `active`: `placement_not_active`.
4. Its `candidate_id` and `agency_id` must equal the file:
   `placement_candidate_mismatch` or `placement_agency_mismatch`.
5. Both the current and requested candidate stages must be members of the
   progression below. Otherwise use `invalid_current_stage` or
   `invalid_requested_stage`.

```text
sourced < screening < submitted < interview < offer < placed
```

A requested stage below the current stage is `rejected / stage_regression`.
An equal stage is `unchanged / already_at_stage`. A later stage is eligible for
writeback and becomes `updated / stage_applied` only after GlobalHire confirms
that candidate at that stage.

## Reporting

Every logical reference gets exactly one report row in first-seen order,
including conflicts and missing records. `current_stage` is empty when no
current candidate record was available or its current stage was not a valid
wire stage.
