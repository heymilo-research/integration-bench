"""task-0010 — time_window_chunked_queries (scenario: window_bounded_activity_ledger).

One audit-period extract of a 6,011-candidate / 405-placement / 21-agency
GlobalHire tenant at CHECKPOINT=60, then a second extract over the unchanged
tenant. Compliance's calendar (``repo/input/ledger_windows.csv``) lists seven
half-open UTC periods; every record the tenant holds must land in the period its
LAST activity falls in, or in none of them.

WHY A COMPETENT ENGINEER GETS THIS WRONG. ``docs/ledger-window-runbook.md`` is
Meridian's OWN runbook -- clearly attributed, dated January -- and it states,
positively and with an example, that GlobalHire's list endpoints take a PAIR of
incremental parameters:

    GET /v1/candidates?modified_since=<starts_at>&modified_until=<ends_at>

    "Ask for one period at a time and the response holds that period and
     nothing else."

``modified_until`` is not a parameter GlobalHire implements. FastAPI accepts an
undeclared query param and ignores it, so the request is answered as if only
``modified_since`` had been sent: a period query returns EVERYTHING at or after
its start, silently, with a 200 and the ordinary envelope. Measured on this
tenant, the first period is 10 minutes wide and holds 19 records; its runbook
query returns all 6,437. Nothing in the response says so -- there is no
``total`` and no ``has_more`` (LIES.md #3), the echoed ``offset``/``limit`` are
exactly what was asked for, and the rows carry ordinary stamps. The vendor's own
``docs/pagination.md`` lists the parameters it does implement, so one diff
against the runbook, or one look at a returned stamp, refutes it.

DIFFICULTY DEVICES.

1. (belief-vs-reality, task-local) The phantom upper bound. The consequence is
   expressed per period and per collection: 21 ``window_*_activity_count``
   checks, seven ``..._roster_admits_nothing_active_outside_it`` checks, three
   ledger totals and three outside-every-period totals. Measured on
   ``naive.patch``: 20 of the 21 grid cells are wrong (the 21st is right by
   arithmetic -- no candidate was touched after the last period starts).

2. (world design, and what stops device 1 being guessed away) The calendar does
   NOT tile the timeline. 4,131 of the tenant's 6,437 records sit in a gap
   between two periods and belong to no period at all. That is what makes the
   obvious repair -- "a record has one last activity, so keep the LAST period
   its query returned it in" -- wrong rather than merely lucky: a gap record's
   last period is the one BEFORE the gap. Graded through twelve
   ``unledgered_gap_record_*`` witnesses drawn one per gap-and-collection, the
   three ``records_outside_every_audit_period_*`` totals and the roster checks.
   Measured on ``variants/defensive.patch`` below.

   The upper bound therefore cannot come from the server on any route: it is
   the caller's to apply, whether the caller chunks its queries by period or
   reads each collection once and buckets locally
   (``variants/alt_correct.patch``, 1.000).

3. (competence, nothing lied about) Which stamp is the activity. 38 records
   carry a ``created_at`` and a ``modified_at`` that are different instants --
   every soft delete and every update in the 2026-04-30 burst -- so a ledger
   keyed on creation puts them in January instead of April. Graded through the
   nine ``activity_outcome_*`` witnesses (three per outcome), the three
   ``deleted_records_ledgered_in_the_period_of_their_deletion_*`` maps and the
   April periods' grid cells.

4. (competence) Soft deletes stay on the feed with ``is_deleted: true`` and are
   an activity like any other: the deletion is the record's last activity and
   belongs to the period it happened in, counted in that period's ``deleted``
   split rather than dropped.

WHAT STOPS THE DEVICE BEING DODGED. Nothing here needs arming, and nothing here
is a fault: both fault knobs are pinned off in ``docker-compose.yaml`` and
restated by the scenario's ``recreate``, and the dual-version flag stays off, so
``/v1`` is the only surface. There is no route on which the vendor applies the
upper bound -- ``/v1`` narrows on ``modified_since`` alone
(``vendor.yaml``: ``incremental.param: modified_since``) and ``/v2`` is not
registered at this configuration. The generator asserts the phantom parameter is
still ignored and refuses to write a key if GlobalHire ever starts honouring it.

WHAT IS DELIBERATELY NOT GRADED. LIES.md #3 (the docs promise a ``has_more`` key
the envelope has never carried) is demoted to ungraded transport plumbing here
and the starter ships a working ``crawl()``; graded, a page-1 stopper would hold
100 of 6,011 candidates and fail everything for a reason that has nothing to do
with window arithmetic ("a lie that swamps the device it shares a task with
measures the lie", AUTHORING-CHECKLIST). LIES.md #2 (per-record numeric offsets)
is likewise ambient rather than graded: it bites the local-bucketing route and
not the chunked-query route, and a device that only one correct shape has to
handle is an OPTIONAL device. ``windows.parse_instant`` is a full ISO-8601
parser in the starter for exactly that reason.

MEASURED (floor_rig2, 78 checks; see the task's WORKLOG entry for the probe
command):
    gold    78/78  1.000
    starter  0/78  0.000   (build_ledger is a stub; cli returns 1)
    stub     0/78  0.000   (probe RAN; `_stub_skipped` absent)
    naive   31/78  0.397   (runbook-faithful: the period pair, trusted. It
                            paginates correctly, classifies every outcome
                            correctly, reports the tenant totals correctly and
                            reruns identically -- a wrong answer, not a broken
                            one.)
    floor 0.000 · headroom 1.000 · discriminating 78 · naive/gold 0.397
    `audit_vacuous_checks` reads vac 0.0% and free 0.0%: the stub passes
    nothing, so there is no vacuous mass here at all.
    starter/naive differ on 31 checks, all 31 naive-favouring. 0
    starter-favouring is STRUCTURAL for a `build` task whose starter cannot
    produce an artifact at all.

THE WRONG-ANSWER BASINS, measured the same way (`variants/*.patch`, swapped in
over `naive.patch` for one probe each):
    variants/defensive.patch      42/78  0.538   the naive plus the one guard a cautious
        engineer adds without having observed anything -- "a record has one
        last activity, keep the latest period it appeared in". Right for every
        record that sits inside a period, wrong for all 4,131 that sit in a gap.
    variants/second_guess.patch   35/78  0.449   the engineer who notices the extra rows,
        concludes the upper bound has to be applied locally, and stops paging
        at the first record past the period end because a feed is served
        oldest-first. GlobalHire serves list responses in ID order, and the
        records with the newest stamps carry the LOWEST ids (the 2026-04-30
        burst edited cand_00017 and cand_00042), so the walk stops almost
        immediately.
    variants/alt_correct.patch    78/78  1.000   one unfiltered crawl per
        collection, bucketed locally against the calendar. Structurally
        different from gold -- different request shape, different traversal,
        no per-period query at all -- and it scores 1.000, so the checks are
        grading the answer rather than this implementation of it.

The answer key AND the calendar file are generated by
tools/rework/gen_answer_key_0206.py, which boots the real vendor at this
checkpoint, crawls it over HTTP and derives every expected value from what it
observed. It refuses to write a key if GlobalHire starts honouring
``modified_until``, if a wire stamp ever ends in ``Z``, if the offset pool
degenerates, if fewer than 100 records sit in a reachable gap, if the
last-period-wins reading stops being a different answer, or if any witness stops
discriminating.
"""

import csv
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "globalhire"
ENTITIES = ("candidates", "placements", "agencies")

# A ledger this short cannot have been produced by a run that did the work, so
# the absence-shaped checks below refuse to credit it.
MIN_LEDGER_ROWS = 100


# ---------------------------------------------------------------------------
# artifact readers
# ---------------------------------------------------------------------------

def _read_json(ctx, name):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_csv(ctx, name):
    """(header, rows) for a CSV artifact, or (None, []) when it is unreadable."""
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None, []
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return None, []
    if not rows:
        return None, []
    return rows[0], [r for r in rows[1:] if r]


def _ledger_index(header, rows, columns):
    """(placements set, per-window rosters, outcome map) from the ledger CSV.

    ``placed``   {(entity, record_id)} -- every record the ledger carries anywhere
    ``roster``   {window_id: {(entity, record_id)}}
    ``outcome``  {(window_id, entity, record_id): outcome}
    """
    placed = set()
    roster = {}
    outcome = {}
    if header != columns:
        return placed, roster, outcome
    for row in rows:
        if len(row) != len(columns):
            continue
        record = dict(zip(columns, row))
        wid = record.get("window_id")
        entity = record.get("entity")
        rid = record.get("record_id")
        placed.add((entity, rid))
        roster.setdefault(wid, set()).add((entity, rid))
        outcome[(wid, entity, rid)] = record.get("outcome")
    return placed, roster, outcome


def _summary_cell(summary, window_id, entity, field):
    if not isinstance(summary, dict):
        return None
    for bucket in summary.get("per_window") or []:
        if isinstance(bucket, dict) and bucket.get("window_id") == window_id:
            node = bucket.get(entity)
            return node.get(field) if isinstance(node, dict) else None
    return None


# ---------------------------------------------------------------------------
# request-log evidence
# ---------------------------------------------------------------------------

def _collections_listed(log):
    return {
        str(entry.get("path") or "").rsplit("/", 1)[-1]
        for entry in log
        if entry.get("method") == "GET"
        and entry.get("status") == 200
        and str(entry.get("path") or "") in {f"/v1/{e}" for e in ENTITIES}
    }


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    expected_summary = {
        "per_window": [
            {
                "window_id": window["window_id"],
                **{
                    entity: {
                        "total": key["grid"][window["window_id"]][entity],
                        "live": key["grid"][window["window_id"]][entity]
                        - key["deleted_in_window"][window["window_id"]][entity],
                        "deleted": key["deleted_in_window"][window["window_id"]][entity],
                    }
                    for entity in ENTITIES
                },
            }
            for window in key["windows"]
        ],
        "tenant": key["tenant"],
        "outside_windows": key["outside_windows"],
    }
    columns = key["ledger_columns"]
    window_ids = [w["window_id"] for w in key["windows"]]
    expected_roster = {
        wid: {(entity, rid) for entity in ENTITIES for rid in key["rosters"][wid][entity]}
        for wid in window_ids
    }
    handle = ctx.vendor(VENDOR)

    # One vendor lifetime for the whole scenario: one recreate, one builtin_l2.
    # The checkpoint is restated here rather than inherited from compose, and
    # both fault knobs are cleared explicitly -- this task's device is a
    # parameter the vendor does not implement, and must not be measured through
    # a fault.
    handle.recreate(
        checkpoint=key["checkpoint"],
        env={"FAULT_5XX_ON_PAGE": "", "FAULT_RATE_LIMIT": "0"},
    )

    code, _out, err = ctx.app.run()

    # Conduct is graded on THIS run's traffic, before the scenario reads
    # anything else, so a submission that never called the vendor cannot bank
    # conduct credit off traffic it did not generate.
    await builtin_l2(ctx)

    summary = _read_json(ctx, "result.json")
    header, ledger_rows = _read_csv(ctx, "activity_ledger.csv")
    placed, roster, outcome = _ledger_index(header, ledger_rows, columns)

    ledger_sha256 = hashlib.sha256(
        json.dumps([header, *ledger_rows], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    ctx.check_l1(
        "activity_ledger_artifact_exact",
        header == columns and ledger_sha256 == key["expected_ledger_sha256"],
        f"ledger has {len(ledger_rows)} row(s); expected "
        f"{key['ledger_rows_total']} exact, calendar-ordered row(s)",
    )
    ctx.check_l1(
        "activity_summary_artifact_exact",
        summary == expected_summary,
        "result.json differs from the exact per-window/live/deleted/tenant/outside summary",
    )

    ctx.check_l1(
        "activity_ledger_run_completed",
        code == 0 and isinstance(summary, dict) and header is not None,
        f"exit={code} result={type(summary).__name__} ledger_header={header} "
        f"stderr={err[:400]}",
    )

    did_work = len(ledger_rows) >= MIN_LEDGER_ROWS and isinstance(summary, dict)

    # -- device 1: one count per period per collection -------------------------
    for wid in window_ids:
        for entity in ENTITIES:
            want = key["grid"][wid][entity]
            got = _summary_cell(summary, wid, entity, "total")
            ctx.check_l1(
                f"window_{wid}_{entity}_activity_count",
                got == want,
                f"{entity} whose last activity falls in {wid} "
                f"[{key['windows'][window_ids.index(wid)]['starts_at']}, "
                f"{key['windows'][window_ids.index(wid)]['ends_at']}) number {want}; "
                f"result.json reports {got!r}",
            )

    # -- device 1 + 2, as an opposed pair per period ---------------------------
    # A connector that trusts the runbook's upper bound passes the first of
    # these for every period and fails the second for every period; one that
    # over-prunes fails the first and passes the second. Neither can be
    # satisfied by inaction: the second requires the period to have been
    # ledgered at all.
    for wid in window_ids:
        want = expected_roster[wid]
        got = roster.get(wid, set())
        missing = sorted(want - got)
        ctx.check_l1(
            f"window_{wid}_roster_covers_every_record_active_in_it",
            not missing and bool(want),
            f"{len(want & got)} of {len(want)} records active in {wid} are ledgered "
            f"there; missing {missing[:5]}",
        )
        extra = sorted(got - want)
        ctx.check_l1(
            f"window_{wid}_roster_admits_nothing_active_outside_it",
            bool(got) and not extra,
            f"{wid} carries {len(got)} record(s), {len(extra)} of which were last "
            f"active outside it; e.g. {extra[:5]}",
        )

    # -- the ledger's size, per collection -------------------------------------
    for entity in ENTITIES:
        want = key["ledger_rows_by_entity"][entity]
        got = sum(1 for row in ledger_rows
                  if len(row) == len(columns) and row[columns.index("entity")] == entity)
        ctx.check_l1(
            f"ledger_rows_placed_{entity}",
            got == want,
            f"the calendar's periods hold {want} {entity} between them; "
            f"the ledger carries {got} {entity} row(s)",
        )

    # -- device 2: what the calendar does not cover ----------------------------
    for entity in ENTITIES:
        want = key["outside_windows"][entity]
        got = (summary or {}).get("outside_windows", {}).get(entity) \
            if isinstance(summary, dict) else None
        ctx.check_l1(
            f"records_outside_every_audit_period_{entity}",
            got == want,
            f"{want} {entity} the tenant holds were last active in no listed period; "
            f"result.json reports {got!r}",
        )

    for witness in key["gap_witnesses"]:
        rid = witness["record_id"]
        entity = witness["entity"]
        ctx.check_l1(
            f"unledgered_gap_record_{rid}",
            did_work and (entity, rid) not in placed,
            f"{rid} was last active at {witness['modified_utc']}, after {witness['after_window']} "
            f"ends and before the next period begins, so no period may carry it; "
            f"ledger has {len(ledger_rows)} row(s) and "
            f"{'carries' if (entity, rid) in placed else 'does not carry'} it",
        )

    # -- device 3: which stamp is the activity ---------------------------------
    for witness in key["outcome_witnesses"]:
        rid = witness["record_id"]
        cell = (witness["window_id"], witness["entity"], rid)
        got = outcome.get(cell)
        ctx.check_l1(
            f"activity_outcome_{rid}",
            got == witness["outcome"],
            f"{rid} was last active at {witness['modified_utc']}, which lands in "
            f"{witness['window_id']} as {witness['outcome']!r}; the ledger has "
            f"{got!r} for that period",
        )

    # -- device 4: soft deletes are an activity --------------------------------
    for entity in ENTITIES:
        want = {wid: key["deleted_in_window"][wid][entity] for wid in window_ids}
        got = {wid: _summary_cell(summary, wid, entity, "deleted") for wid in window_ids}
        ctx.check_l1(
            f"deleted_records_ledgered_in_the_period_of_their_deletion_{entity}",
            got == want,
            f"deleted {entity} per period should be {want}; result.json reports {got}",
        )

    # -- the tenant's own totals -----------------------------------------------
    for entity in ENTITIES:
        want = key["tenant"][entity]
        got = (summary or {}).get("tenant", {}).get(entity) if isinstance(summary, dict) else None
        ctx.check_l1(
            f"tenant_total_{entity}",
            got == want,
            f"GlobalHire holds {want} {entity}; result.json reports {got!r}",
        )

    # -- artifact shape --------------------------------------------------------
    order = {wid: i for i, wid in enumerate(window_ids)}
    entity_order = {e: i for i, e in enumerate(ENTITIES)}
    sortable = [
        (order.get(r[0], len(order)), entity_order.get(r[1], len(entity_order)), r[2])
        for r in ledger_rows if len(r) == len(columns)
    ]
    ctx.check_l1(
        "ledger_columns_and_calendar_order",
        header == columns and bool(sortable) and sortable == sorted(sortable),
        f"header={header!r} (expected {columns!r}); {len(sortable)} well-formed row(s), "
        f"ordered={sortable == sorted(sortable)}",
    )

    log = handle.request_log()
    listed = _collections_listed(log)
    ctx.check_l1(
        "every_collection_was_read_from_the_tenant",
        listed == set(ENTITIES),
        f"the extract listed {sorted(listed)}; never listed "
        f"{sorted(set(ENTITIES) - listed)}",
    )

    # -- the tenant has not changed, so neither may the extract -----------------
    first_summary = json.dumps(summary, sort_keys=True) if isinstance(summary, dict) else None
    first_ledger = (header, ledger_rows)

    code, _out, err = ctx.app.run()
    again_summary = _read_json(ctx, "result.json")
    again_header, again_rows = _read_csv(ctx, "activity_ledger.csv")
    ctx.check_l1(
        "activity_ledger_rerun_completed",
        code == 0 and isinstance(again_summary, dict),
        f"exit={code} result={type(again_summary).__name__} stderr={err[:400]}",
    )
    second_summary = json.dumps(again_summary, sort_keys=True) if isinstance(again_summary, dict) else None
    ctx.check_l1(
        "activity_ledger_rerun_artifacts_unchanged",
        len(again_rows) >= MIN_LEDGER_ROWS
        and first_summary is not None and first_summary == second_summary
        and first_ledger == (again_header, again_rows),
        f"a second extract over an unchanged tenant must produce the same two files; "
        f"rows={len(again_rows)} summary_equal={first_summary == second_summary} "
        f"ledger_equal={first_ledger == (again_header, again_rows)}",
    )
    ctx.check_l1(
        "activity_ledger_rerun_artifacts_exact",
        again_summary == expected_summary
        and again_header == columns
        and hashlib.sha256(
            json.dumps([again_header, *again_rows], separators=(",", ":")).encode("utf-8")
        ).hexdigest() == key["expected_ledger_sha256"],
        "second run differs from the exact canonical ledger or summary",
    )
