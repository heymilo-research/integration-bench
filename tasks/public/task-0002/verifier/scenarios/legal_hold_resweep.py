"""task-0002 — subresource_completeness_sweep, pass 2: the tenant has moved.

Same roster, same code, a world five mutations further on (CHECKPOINT=5). The
export is re-derived from scratch against that world, and three things about it
are different in ways a completeness sweep has to survive:

A NINTH PARENT PAGE APPEARED. cand_0900 was created upstream and sits alone at
offset 400 — a page that did not exist during the backfill. It is the only
candidate carrying `dana.reeve@example.test`, a roster row that resolved to
nobody last time. So the roster row that was correctly reported unresolvable
must now resolve, and it can only be found by a walk that goes all the way to a
page the previous run never had to reach. A crawl bounded by anything it
learned last time — a remembered page count, a remembered cursor — stops at
eight pages and reports `dana.reeve@example.test` unmatched for the second
time, with no error anywhere.

A CUSTODIAN WAS SOFT-DELETED. cand_0017 now carries `is_deleted: true` and
stays in list responses (this platform has no tombstone endpoint;
`docs/index.md` says so). A connector that filters deleted records out of a
crawl — an entirely reasonable instinct — removes a person from a LEGAL HOLD
along with their two notes. The export must keep the row, keep the flag, and
keep the notes.

TWO CUSTODIANS WERE EDITED, AND BOTH EDITS LAND ON DETAIL-ONLY FIELDS.
cand_0042's phone moved from +1-555-5848 to +1-555-0142 and cand_0099's
pipeline status moved to `placed`. Neither of those two columns exists in this
tenant's candidate LIST payload (device 5 in the backfill's docstring: the
tenant is provisioned on the summary list view), so the only place either edit
is visible is the record's own endpoint — and both records' `modified_at` moved
with them, which is device 3's epoch-millisecond integer under the key the docs
mis-name. The two divergences therefore both re-fire here, on values that did
not exist when the connector was written, and they fire on different fields of
the same rows.

The four records the tenant moved — cand_0017 (soft-deleted), cand_0042 and
cand_0099 (edited) and cand_0900 (created) — are graded ONE CHECK EACH
(`lh_resweep_moved_record_<candidate_id>`), the same per-record pricing the
backfill applies, so a divergence that corrupts a field on all of them costs
all of them.

The by-id read is graded here too, and cand_0900 is why it is graded per run
rather than per custodian-list: a connector that hydrates from a remembered
custodian set never reads the newcomer's record and cannot know its phone or
pipeline stage, even though it found the person.

Measured (62 checks; full variant table in the backfill scenario's docstring):
naive.patch 33/62 = 0.532 against gold's 1.000, failing all four moved records
here and all nineteen in the backfill, plus five wire checks across the two
passes.

The vendor is recreated at CHECKPOINT=5 first, which is a fresh process with a
fresh request log — no index is carried over from the backfill scenario, and
`builtin_l2` is scoped to this vendor lifetime exactly as it was to the last.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _legal_hold import (  # noqa: E402
    COLLECTION_PATH,
    SUBRESOURCE,
    VENDOR,
    custodians_by_id,
    export_contract_problems,
    field_mismatches,
    load_key,
    note_ids_by_custodian,
    read_export,
    record_problems,
    scalar_mismatches,
)
from _pagination_family import p2_range_covered  # noqa: E402
from _subresource_family import (  # noqa: E402
    collection_requests,
    detail_requests,
    requested_offsets,
    s1_all_parents_visited,
    s3_tail_pages_fetched,
    s5_child_ids_exact,
    s6_all_records_hydrated,
    s7_no_hydration_outside_scope,
    subresource_requests,
)


async def run(ctx) -> None:
    key = load_key(ctx)
    backfill = key["phases"]["backfill"]
    phase = key["phases"]["resweep"]
    expected = phase["custodians"]

    ctx.vendor(VENDOR).recreate(checkpoint=phase["checkpoint"])

    code, _out, err = ctx.app.run(["export"])
    export = read_export(ctx)
    ctx.check_l1(
        "lh_resweep_export_written",
        code == 0 and isinstance(export, dict) and isinstance(export.get("custodians"), list),
        f"exit={code} export={type(export).__name__} stderr={err[:400]}",
    )

    actual = custodians_by_id(export)
    contract_problems = export_contract_problems(
        export, phase, roster_row_count=key["roster_row_count"]
    )
    ctx.check_l1(
        "lh_resweep_full_export_contract_exact",
        not contract_problems,
        f"{len(contract_problems)} export contract defect(s); first={contract_problems[:6]}"
        if contract_problems
        else f"all {phase['custodian_count']} custodians and {phase['note_count']} notes match",
    )
    want_ids = set(phase["custodian_ids"])
    got_ids = set(actual)
    ctx.check_l1(
        "lh_resweep_custodian_set_tracks_the_moved_tenant",
        got_ids == want_ids,
        f"missing {sorted(want_ids - got_ids)} unexpected {sorted(got_ids - want_ids)} "
        f"(the tenant grew from {backfill['candidate_total']} to "
        f"{phase['candidate_total']} candidates)",
    )

    # The custodian that only exists on the page the previous world did not have.
    newcomers = sorted(set(phase["custodian_ids"]) - set(backfill["custodian_ids"]))
    # Resolution only: whether the newcomer's row is *right* is graded per
    # record below, so a connector that found them and then mis-decoded their
    # timestamp is not billed twice for the same mistake in this check.
    problems = field_mismatches(
        actual, {cid: expected[cid] for cid in newcomers}, "roster_email"
    )
    still_unmatched = [
        expected[cid]["roster_email"]
        for cid in newcomers
        if expected[cid]["roster_email"] in ((export or {}).get("unmatched_roster_emails") or [])
    ]
    ctx.check_l1(
        "lh_newly_created_custodian_on_the_ninth_page_resolved",
        bool(newcomers) and not problems and not still_unmatched,
        f"newcomers {newcomers}: {problems[:4]}"
        + (f"; still reported unresolvable: {still_unmatched}" if still_unmatched else "")
        if newcomers
        else "the fixture grew no new custodian — nothing to prove",
    )

    reported_unmatched = sorted((export or {}).get("unmatched_roster_emails") or [])
    ctx.check_l1(
        "lh_resweep_unresolvable_roster_rows_reported",
        reported_unmatched == sorted(phase["unmatched_emails"]),
        f"reported {reported_unmatched}, expected {sorted(phase['unmatched_emails'])}",
    )

    # The soft-deleted custodian: still listed upstream, so still on the hold.
    deleted = sorted(cid for cid, row in expected.items() if row["is_deleted"])
    lost = [
        cid
        for cid in deleted
        if cid not in actual
        or actual[cid].get("is_deleted") is not True
        or sorted(str(n.get("note_id")) for n in (actual[cid].get("notes") or []))
        != sorted(phase["note_ids_by_custodian"][cid])
    ]
    ctx.check_l1(
        "lh_soft_deleted_custodian_retained_with_notes",
        bool(deleted) and not lost,
        f"{len(lost)} soft-deleted custodian(s) dropped, unflagged, or stripped of "
        f"notes: {lost}"
        if deleted
        else "the fixture has no soft-deleted custodian — nothing to prove",
    )

    # -- the records the tenant actually moved, ONE CHECK EACH --------------
    # The created, the soft-deleted and the edited: every custodian whose row
    # differs from the one the backfill world would have produced. These are
    # the rows a connector written against the previous world gets wrong, and
    # each is priced as its own record — the same rule the backfill applies, so
    # a divergence that corrupts a field on all of them costs all of them.
    edited = {
        cid
        for cid in set(phase["custodian_ids"]) & set(backfill["custodian_ids"])
        if expected[cid] != backfill["custodians"][cid]
    }
    moved = sorted(edited | set(newcomers) | set(deleted))
    if not moved:
        # A silent zero-check loop would let a fixture whose two worlds are
        # identical grade as if the resweep proved something.
        ctx.check_l1(
            "lh_resweep_moved_record_none",
            False,
            "the two worlds hold identical custodian rows — this pass proves nothing",
        )
    for cid in moved:
        problems = record_problems(
            actual.get(cid), expected[cid], phase["note_ids_by_custodian"].get(cid, [])
        )
        ctx.check_l1(
            f"lh_resweep_moved_record_{cid}",
            not problems,
            "; ".join(problems[:6]) if problems else "row tracks the moved tenant",
        )

    problems = scalar_mismatches(actual, expected)
    ctx.check_l1(
        "lh_resweep_custodian_fields_match_the_moved_tenant",
        not problems,
        f"{len(problems)} field mismatch(es): {problems[:5]}",
    )

    ctx.check_l1(
        "lh_resweep_note_ids_exact_per_custodian",
        *s5_child_ids_exact(note_ids_by_custodian(export), phase["note_ids_by_custodian"]),
    )

    # -- the wire ----------------------------------------------------------
    log = ctx.vendor(VENDOR).request_log()
    parent_offsets = requested_offsets(
        collection_requests(log, collection_path=COLLECTION_PATH)
    )
    ok, detail = p2_range_covered(parent_offsets, expected=phase["parent_list_offsets"])
    ctx.check_l1(
        "lh_resweep_parent_walk_reached_the_page_that_did_not_exist_before",
        ok,
        f"cursor offsets (the tenant now has "
        f"{len(phase['parent_list_offsets'])} pages, it had "
        f"{len(backfill['parent_list_offsets'])}): {detail}",
    )

    # Including cand_0900, who did not exist during the backfill: a connector
    # that hydrates from a remembered custodian list rather than from this
    # run's resolution never reads the newcomer's record and cannot know its
    # phone or pipeline stage.
    hydrated = detail_requests(log, collection_path=COLLECTION_PATH)
    ctx.check_l1(
        "lh_resweep_custodian_detail_read_for_every_custodian",
        *s6_all_records_hydrated(hydrated, phase["custodian_ids"]),
    )
    ctx.check_l1(
        "lh_resweep_no_detail_read_outside_the_roster",
        *s7_no_hydration_outside_scope(hydrated, phase["custodian_ids"]),
    )

    visited = subresource_requests(
        log, collection_path=COLLECTION_PATH, subresource=SUBRESOURCE
    )
    ctx.check_l1(
        "lh_resweep_every_custodian_subresource_requested",
        *s1_all_parents_visited(visited, phase["custodian_ids"]),
    )
    ctx.check_l1(
        "lh_resweep_multipage_note_tails_requested",
        *s3_tail_pages_fetched(visited, phase["note_offsets_by_custodian"]),
    )

    await builtin_l2(ctx)
