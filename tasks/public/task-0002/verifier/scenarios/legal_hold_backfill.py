"""task-0002 — subresource_completeness_sweep, pass 1: the pristine tenant.

An 18-row legal-hold roster, keyed by email, against a tenant of 400
candidates. Resolving it produces 19 custodians, 26 notes and 2 emails that
match nobody. Five devices are planted in that arithmetic, and each one is
graded on its own evidence.

TWO of them are belief–reality divergences, and they are independent: DEVICE 3
(a field the written source mis-names and mis-types) and DEVICE 5 (a field the
written source says is on the page it is not on). Neither fix implies the
other, and — the point of the pair — DEVICE 5 cannot be defended against
blindly. A cautious engineer can write a tolerant timestamp coercion without
ever having looked at the wire, and that one helper used to take this task from
0.596 to 1.000 (measured; see the table below). No helper recovers a key the
response does not contain: the only way to have `phone` and `pipeline_status`
is to have gone and read the record.

DEVICE 1 — the sub-collection is paginated, and it pages small.
    `/rest/candidates/{id}/notes` is a cursor-paginated collection like any
    other, and this tenant serves it TWO records at a time while
    `docs/pagination.md` says "Page size is fixed at 50 records". Nobody here
    has 50 notes, so the tempting shortcut — one GET per custodian, take
    `data`, move on — looks perfectly safe, and is wrong for exactly the six
    custodians who have three notes (cand_0071, cand_0158, cand_0171,
    cand_0236, cand_0280, cand_0398).

    `lh_multipage_note_tails_requested` reads the wire (was the second page of
    cand_0071's notes ever asked for?); `lh_note_ids_exact_per_custodian` reads
    the consequence. Both are kept: the payload check alone cannot tell "asked,
    and there was nothing there" from "never asked".

DEVICE 2 — eight of the nineteen custodians have no notes at all.
    cand_0042, cand_0052, cand_0099, cand_0151, cand_0157, cand_0301, cand_0322
    and cand_0351 are on hold and have an empty file, which is itself the fact
    counsel asked to be told. A connector that treats "nothing on file" as
    "nothing to hand over" emits a shorter, entirely plausible export.
    `lh_childless_custodians_exported_with_empty_notes` grades the row and
    `lh_every_custodian_subresource_requested` grades the VISIT — a childless
    custodian emitted without ever being asked about is a guess, not an export.

DEVICE 3 — the documented candidate timestamp is wrong in two ways at once.
    This is the task's doc-vs-wire divergence, and the only one of the four a
    connector written faithfully from `docs/` still walks into.
    `docs/entities.md` documents the candidate's last-modified field as
    `updatedAt`, "string (ISO 8601)", and `docs/openapi.yaml` repeats it as
    `updatedAt: {type: string, format: date-time}`. The wire has no `updatedAt`
    key at all: it has `modified_at`, carrying a bare integer of epoch
    milliseconds. Reading the documented name yields `None` on every candidate
    in the tenant — a silent, total corruption of one graded field.

    The trap has a second jaw — a NOTE's `created_at` genuinely IS an ISO 8601
    string, so a connector that over-corrects and decodes epochs platform-wide
    destroys 26 note timestamps instead (`lh_note_payloads_carried_across_
    verbatim`).

DEVICE 4 — a roster address is not a candidate.
    `xavier.tesla@hey.test`, `emmy.babbage@inbox.test` and
    `richard.shannon@example.test` are each carried by two different
    candidates, and in every case the second holder sits pages away from the
    first — cand_0322 on page 7, cand_0355 on page 8, the last one. Indexing
    the tenant as email -> candidate instead of email -> candidates silently
    halves those three holds. `lh_shared_email_keeps_every_holder`.

DEVICE 5 — the list page is not the record. THE SECOND DIVERGENCE.
    This tenant is provisioned on TalentForge's summary candidate list view
    (`TF_CANDIDATE_LIST_VIEW=summary`, a boot knob on the vendor image that
    DEFAULTS to `full`, so every other task on this image is byte-unaffected —
    verified: all ten sibling talentforge tasks produce identical verdicts with
    and without it). `GET /rest/candidates` serves the search index's
    projection, and `phone` and `pipeline_status` are simply not keys of a list
    record. `GET /rest/candidates/{id}` answers from the primary store and
    carries them, always.

    The false written source is task-local and clearly attributed:
    `input/HANDOVER-legal-hold.md`, the outgoing contractor's note, states that
    "the candidate list response carries the whole candidate record — the id,
    given and family name, the email address, the phone number, the pipeline
    stage, the created and last-modified stamps and the delete flag", that she
    built every row of the March production straight off the list pages, and
    that she would not bother re-reading a candidate through its own endpoint.
    The wire contradicts that on the very first list call.

    Why this device is the one that makes the task robust: DEVICE 3 is
    defensible blind — a tolerant "either name, either type" timestamp helper
    fixes it without the engineer ever having observed anything. There is no
    blind fix for a key that is not in the payload. The consequence is asserted
    at two layers: in the payload, on every custodian row
    (`phone`/`pipeline_status` are in RECORD_FIELDS, so all 19 backfill rows
    and all 4 moved resweep rows fail), and on the wire, where
    `lh_custodian_detail_read_for_every_custodian` proves the connector went
    and asked. Both are kept: an export carrying a blank phone number and an
    export whose author never asked are indistinguishable on disk.
    `lh_no_detail_read_outside_the_roster` and `lh_custodian_detail_not_reread`
    close the brute-force escape — re-reading four hundred people to publish
    nineteen is a different job, and this tenant meters the data plane.

PRICING: ONE CHECK PER CUSTODIAN, NOT ONE SUMMARY CHECK.
    Every device above corrupts *records*, so the custodian rows are graded one
    check each (`lh_custodian_record_<candidate_id>`), each covering that row's
    identity, its tenant-owned scalars, its `updated_at` and its note id set.
    Measured two rewrites ago, with the same devices graded by two summary
    checks: a docs-faithful connector whose `updated_at` was null on all
    nineteen custodians scored 34/37 = 0.919 — total failure of the task's
    central divergence priced at three checks. Per record it costs what it is.

HISTORICALLY MEASURED — every number below is `tools/rig/floor_rig2.py` against the live
vendor, scored with the harness's own `check_fraction`
(`tools/rework/score_0078.py`). 62 checks; each variant is one implementation
run through both passes. Every variant patch is committed under
`tasks/task-0002/variants/` and is reproducible with
`tools/rework/probe_variant_0078.sh <patch>`.

    gold (solution.patch)                            62/62   1.000
    empty submission (starter)                        0/62   0.000
    harness stub                                      0/62   0.000

    v0  naive.patch — docs- and handover-faithful     33/62   0.532
        `updated_at` null on all 23 graded rows (device 3) AND blank
        `phone`/`pipeline_status` on all of them plus five wire checks
        (device 5). This is the shipped naive.
    v1  defensive: v0 + a tolerant timestamp helper   33/62   0.532
        The single most obvious blind guard — accept `updatedAt` or
        `modified_at`, accept a string or an epoch int. Against the PREVIOUS
        version of this task the same guard scored 1.000; the task was one line
        deep. It now buys nothing, because it cannot conjure a missing key.
    v2  hydrated, but the documented timestamp        39/62   0.629
        Device 5 fixed, device 3 left: the by-id read lands, the five wire
        checks pass, and all 23 rows still fail on `updated_at`.
    v3  second guess: creation time as last-modified  59/62   0.952
        Hydrates, finds no `updatedAt` anywhere, and falls back to the record's
        creation time. Right for every record the tenant never touched, wrong
        for cand_0017, cand_0042 and cand_0099 in the resweep. Honest weak
        spot: this connector found both devices and slipped on a third thing.
    v4  second guess: ask for a wider list view       33/62   0.532
        Notices the blank columns and adds `?view=full&fields=*` to the list
        call. TalentForge ignores unknown query params, so the projection comes
        back unchanged and the guess buys exactly nothing.
    v5  ALTERNATIVE-CORRECT (streaming, page at a
        time, rows built off the detail record,
        `datetime` instead of the given formatter,
        reverse id order)                             62/62   1.000
    v6  first page of notes only (device 1 alone)     51/62   0.823
    v7  childless custodians dropped (device 2 alone) 43/62   0.694
    v8  one candidate per address (device 4 alone)    44/62   0.710

    v1 and v2 are the pair that proves the two divergences are independent and
    each expensive: fix device 3 and leave device 5 -> 0.532; fix device 5 and
    leave device 3 -> 0.629. Neither repair on its own gets within 0.37 of gold.

The 2026-08-12 human source review added two exact whole-export checks, one per
tenant epoch, covering raw custodian multiplicity, every top-level count, every
record and note field, and unmatched addresses. The current authored universe
is 64 checks; the 62-check figures above remain historical probe evidence.

The last parent page earns its place too: four custodians (cand_0351,
cand_0355, cand_0378, cand_0398) exist only there, and the vendor ends the walk
by OMITTING the `cursor` key rather than sending it as null the way
`docs/pagination.md` promises. In Python an absent key and a null value both
read as `None`, so that third documented lie is survivable here — which is
exactly why the task does not rest on it.

Request budget: gold makes 55 `GET /rest/*` in the busier (resweep) epoch — 9
list pages, 20 by-id reads, 26 sub-collection pages — against this tenant's
90/60s ceiling (`TF_RATE_LIMIT_GET`, a knob that defaults to the published 60
and is raised, never lowered, so nothing is throttled harder than
`docs/index.md` promises). 35 requests of slack: a tidy connector is never
throttled, and hydrating the whole tenant still is not affordable.

Grading is property-based throughout: field-by-field against a key generated
from the live vendor (tools/rework/gen_answer_key_0078.py), plus the vendor's
own request log. Nothing here compares an output file to a recorded blob.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _legal_hold import (  # noqa: E402
    COLLECTION_PATH,
    SUBRESOURCE,
    VENDOR,
    all_note_rows,
    custodians_by_id,
    export_contract_problems,
    load_key,
    no_repeated_note,
    note_ids_by_custodian,
    note_payloads_verbatim,
    read_export,
    record_problems,
)
from _pagination_family import (  # noqa: E402
    p2_range_covered,
    p3_no_redundant_rereads,
)
from _subresource_family import (  # noqa: E402
    collection_requests,
    detail_requests,
    requested_offsets,
    s1_all_parents_visited,
    s2_no_unscoped_parents_visited,
    s3_tail_pages_fetched,
    s4_no_subresource_page_refetched,
    s5_child_ids_exact,
    s6_all_records_hydrated,
    s7_no_hydration_outside_scope,
    s8_no_record_rehydrated,
    subresource_requests,
)


async def run(ctx) -> None:
    key = load_key(ctx)
    phase = key["phases"]["backfill"]
    expected = phase["custodians"]

    ctx.vendor(VENDOR).recreate(checkpoint=phase["checkpoint"])

    code, _out, err = ctx.app.run(["export"])
    export = read_export(ctx)
    ctx.check_l1(
        "lh_backfill_export_written",
        code == 0 and isinstance(export, dict) and isinstance(export.get("custodians"), list),
        f"exit={code} export={type(export).__name__} stderr={err[:400]}",
    )

    actual = custodians_by_id(export)
    contract_problems = export_contract_problems(
        export, phase, roster_row_count=key["roster_row_count"]
    )
    ctx.check_l1(
        "lh_backfill_full_export_contract_exact",
        not contract_problems,
        f"{len(contract_problems)} export contract defect(s); first={contract_problems[:6]}"
        if contract_problems
        else f"all {phase['custodian_count']} custodians and {phase['note_count']} notes match",
    )

    # -- who ended up on the export ----------------------------------------
    want_ids = set(phase["custodian_ids"])
    got_ids = set(actual)
    ctx.check_l1(
        "lh_custodian_set_matches_roster_resolution",
        got_ids == want_ids,
        f"missing {sorted(want_ids - got_ids)} unexpected {sorted(got_ids - want_ids)} "
        f"(expected {len(want_ids)} custodians from {phase['candidate_total']} candidates)",
    )

    shared = {
        email: ids
        for email, ids in phase["duplicate_email_groups"].items()
    }
    dropped = {
        email: [cid for cid in ids if cid not in actual]
        for email, ids in shared.items()
    }
    dropped = {email: ids for email, ids in dropped.items() if ids}
    ctx.check_l1(
        "lh_shared_email_keeps_every_holder",
        bool(shared) and not dropped,
        f"{len(dropped)} roster email(s) lost a holder: {dropped}"
        if shared
        else "the fixture has no shared roster email — nothing to prove",
    )

    reported_unmatched = sorted((export or {}).get("unmatched_roster_emails") or [])
    ctx.check_l1(
        "lh_unresolvable_roster_rows_reported",
        reported_unmatched == sorted(phase["unmatched_emails"]),
        f"reported {reported_unmatched}, expected {sorted(phase['unmatched_emails'])}",
    )

    ctx.check_l1(
        "lh_roster_row_count_declared",
        (export or {}).get("roster_row_count") == key["roster_row_count"],
        f"declared {(export or {}).get('roster_row_count')}, "
        f"the roster has {key['roster_row_count']} rows",
    )

    # -- what each custodian row says, ONE CHECK PER CUSTODIAN --------------
    # Per record, not per summary. The candidate timestamp divergence corrupts
    # `updated_at` on every custodian in the export; priced as a single summary
    # check that total corruption cost 1/37th of the task and a docs-faithful
    # connector scored 0.919. Priced per record it costs what it is.
    for cid in sorted(expected):
        problems = record_problems(
            actual.get(cid), expected[cid], phase["note_ids_by_custodian"].get(cid, [])
        )
        ctx.check_l1(
            f"lh_custodian_record_{cid}",
            not problems,
            "; ".join(problems[:6]) if problems else "row matches the tenant",
        )

    ctx.check_l1(
        "lh_note_payloads_carried_across_verbatim",
        *note_payloads_verbatim(actual, expected),
    )

    ctx.check_l1(
        "lh_note_ids_exact_per_custodian",
        *s5_child_ids_exact(note_ids_by_custodian(export), phase["note_ids_by_custodian"]),
    )

    childless = phase["zero_note_custodians"]
    missing_empty = [
        cid
        for cid in childless
        if cid not in actual or (actual[cid].get("notes") or []) != []
    ]
    ctx.check_l1(
        "lh_childless_custodians_exported_with_empty_notes",
        bool(childless) and not missing_empty,
        f"{len(missing_empty)} custodian(s) with no notes were dropped or given "
        f"notes they do not have: {missing_empty}"
        if childless
        else "the fixture has no childless custodian — nothing to prove",
    )

    ctx.check_l1(
        "lh_export_holds_no_repeated_note",
        *no_repeated_note(all_note_rows(export)),
    )

    declared_notes = (export or {}).get("note_count")
    ctx.check_l1(
        "lh_declared_note_total_matches_the_tenant",
        declared_notes == phase["note_count"],
        f"declared {declared_notes} notes, the roster's custodians hold "
        f"{phase['note_count']}",
    )

    # -- what the wire says ------------------------------------------------
    log = ctx.vendor(VENDOR).request_log()
    parent_offsets = requested_offsets(
        collection_requests(log, collection_path=COLLECTION_PATH)
    )

    ok, detail = p2_range_covered(parent_offsets, expected=phase["parent_list_offsets"])
    ctx.check_l1(
        "lh_parent_cursor_walk_reached_the_last_page",
        ok,
        f"cursor offsets (the vendor OMITS the cursor key on the last page): {detail}",
    )

    decoded = [o for o in parent_offsets if o is not None]
    if not decoded:
        # p3 counts repeats, and nothing repeats in an empty list — so the
        # emptiness has to be caught here or a submission that never walked the
        # collection banks a "did not re-walk it" pass.
        ctx.check_l1(
            "lh_parent_pages_not_rewalked",
            False,
            "no parent-collection request recorded — no evidence to judge",
        )
    else:
        ok, detail = p3_no_redundant_rereads(decoded)
        ctx.check_l1("lh_parent_pages_not_rewalked", ok, f"cursor offsets: {detail}")

    # The list view this tenant is provisioned on does not carry the whole
    # record, so the by-id read is not an optimisation detail — it is the only
    # place `phone` and `pipeline_status` exist. Graded on the wire as well as
    # in the payload, because an export carrying an empty phone number and an
    # export built by a connector that never asked look identical on disk.
    hydrated = detail_requests(log, collection_path=COLLECTION_PATH)
    ctx.check_l1(
        "lh_custodian_detail_read_for_every_custodian",
        *s6_all_records_hydrated(hydrated, phase["custodian_ids"]),
    )
    ctx.check_l1(
        "lh_no_detail_read_outside_the_roster",
        *s7_no_hydration_outside_scope(hydrated, phase["custodian_ids"]),
    )
    ctx.check_l1(
        "lh_custodian_detail_not_reread",
        *s8_no_record_rehydrated(hydrated),
    )

    visited = subresource_requests(
        log, collection_path=COLLECTION_PATH, subresource=SUBRESOURCE
    )
    ctx.check_l1(
        "lh_every_custodian_subresource_requested",
        *s1_all_parents_visited(visited, phase["custodian_ids"]),
    )
    ctx.check_l1(
        "lh_no_subresource_sweep_outside_the_roster",
        *s2_no_unscoped_parents_visited(visited, phase["custodian_ids"]),
    )
    ctx.check_l1(
        "lh_multipage_note_tails_requested",
        *s3_tail_pages_fetched(visited, phase["note_offsets_by_custodian"]),
    )
    ctx.check_l1(
        "lh_note_pages_not_rewalked",
        *s4_no_subresource_page_refetched(visited),
    )

    await builtin_l2(ctx)
