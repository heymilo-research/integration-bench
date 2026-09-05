"""task-0031 — composite_availability_windows.

One rebuild of Harborline Facilities' morning bookable-crew board out of
CrewCall, then the same rebuild again to prove the board is stable.

The board is a composite: it is not derivable from any one of CrewCall's three
collections. A roster row says whether somebody still works here and what they
are doing right now; an assignment row says somebody was joined to a piece of
work; a gig row says whether that work is still happening. Only all three
together say whether a controller may ring that person this morning.

`docs/` holds CrewCall's own documentation, byte-identical to the vendor's, plus
ONE task-local document — `docs/harborline-dispatch-runbook.md`, Dispatch Ops'
internal note, disclaimed in its third line as "our own note, not CrewCall's
documentation". The vendor is honest (`LIES.md`: `docs.lies: []`); the runbook
is where this tenant's beliefs live, and three of them are false.

The world is booted at CHECKPOINT 88 and holds 125 roster rows (9 soft-deleted),
13 gigs (2 soft-deleted, 4 more cancelled) and 63 assignments — past the
vendor's max page size of 50, so no single request can hold the feed.

FOUR independent things have to go right.

1. **DIVERGENCE — "cancelled work does not reach us."** The runbook states as
   measured fact that CrewCall drops assignment rows when a client cancels a
   gig, and that Dispatch Ops therefore removed the join to `/v1/gigs` "in the
   spring" and never found a row it would have caught. The wire disagrees: 13
   live workers hold a live-status row (offered/accepted/checked_in) whose gig
   is cancelled or soft-deleted, and every one of those rows is served forever.
   A connector that believes the runbook marks all 13 `committed`, with a
   `blocking_gig_id` naming a gig nobody is working.

2. **DIVERGENCE — "finished work does not reach us either."** The runbook says
   settled assignments are archived off the feed overnight. On the wire 20 of
   the 63 rows are `completed` or `no_show` and 13 of them belong to workers who
   are otherwise free. Believing it blocks those 13 too.

3. **DIVERGENCE — "`is_deleted` is a worker column."** The runbook says CrewCall
   only ever soft-deletes people and that assignments are removed rather than
   flagged. Six assignments carry `is_deleted: true`, each the only row its
   worker holds, each naming a gig that is still taking crew. Believing the
   runbook blocks those 6 — and, in the other direction, bars them from the very
   gig their dead row names.

   Together 1-3 are the rule the runbook prints in a block quote: *"a person
   with a row in the assignment feed is committed; a person with no row is
   free."* Under it the board offers 11 people instead of 43 -- and because a
   commitment outranks the worker's own status, the damage reaches 46 of the 58
   audited lines, not just the 32 workers it wrongly blocks.

4. **COMPETENCE — the same rows mean two different things.** A terminal or
   soft-deleted row must not hold a worker back generally. But a NON-deleted row
   naming a particular gig does bar that worker from that gig whatever its
   status says, because they have already been put forward for it — while a
   soft-deleted row bars nothing at all, not even for its own gig. Ten workers
   sit on the first half of that distinction (`barred_by_own_row` in the key)
   and six on the second, so a connector that applies one filter in both places
   is wrong in one direction or the other.

Two more devices carry no divergence but must be handled to reach 1.000:

5. **THE ROSTER CHURNS.** `docs/pagination.md` is honest and explicit about it,
   and the runbook agrees. A single forward pass never sees wkr_0084 at
   limit=50, and wkr_0076/wkr_0084/wkr_0100 at limit 10 or 25 (measured live and
   pinned as `single_pass_misses_by_limit`). wkr_0100 is `off_shift` with no
   assignment, so losing it costs a board row AND a slot in all six windows.

6. **SOFT-DELETED WORKERS STAY IN LIST RESPONSES.** Nine of them, carrying
   `is_deleted: true`. They are board rows (`off_roster`), not absences, and
   they are not eligible for anything.

MEASURED (rig probe, this scenario, 101 checks; every number below is a probe
reading, not an estimate):

| submission | what it is | score |
|---|---|---|
| gold (`solution.patch`) | joins all three collections | **101/101 = 1.000** |
| starter | the shipped skeleton | 0/101 = 0.000 |
| harness stub | empty submission | 0/101 = 0.000 |
| `naive.patch` | tidy, faithful to `docs/` INCLUDING the runbook: a row in the feed is a commitment | **39/101 = 0.386** |
| `variants/gigjoin.patch` | naive + the join back to `/v1/gigs` and nothing else (repairs divergence 1) | 62/101 = 0.614 |
| `variants/terminal.patch` | naive + the assignment-status filter and nothing else (repairs divergence 2) | 63/101 = 0.624 |
| `variants/deleted.patch` | naive + honouring `is_deleted` on assignments and nothing else (repairs divergence 3) | 51/101 = 0.505 |
| `variants/secondguess.patch` | every commitment call correct, then the same filter reused for the picking lists -- "if a row does not hold somebody it cannot bar them either" | 84/101 = 0.832 |
| `variants/alt-correct.patch` | legitimate and structurally different: converges the roster by comparing two whole snapshots for equality, streams pages through a generator, derives the board from the picking lists rather than the other way round, and keeps the already-put-forward sets as lists | **101/101 = 1.000** |

The three repair variants are the multi-device proof: each fixes exactly one of
the runbook's three false claims and leaves the other two, and none of them
reaches even 0.63, so no single line of the wrong connector recovers the task.
`secondguess` is the one to watch: it gets every board line right and loses only
the six picking lists and ten of the seventeen per-place checks, which prices
device 4 at 17 checks.

Evidence: the board and the windows are compared field by field against an
answer key measured from a live vendor by `tools/rework/gen_answer_key_0105.py`;
the roster row set is compared against the roster this verifier crawls itself
over the vendor's published port; the crawl-shape checks read the vendor's own
request log, sliced to the connector's traffic only. Every check is emitted
unconditionally, so a submission that produced no artifact scores zero rather
than shrinking the denominator, and every check that could be satisfied by
silence first proves the connector produced rows. `builtin_l2` fires once, after
the last connector run, with the verifier's own request indices excluded.
"""

import csv
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "crewcall"
CRAWL_LIMIT = 50
BOARD_COLUMNS = ("worker_id", "worker_status", "availability", "blocked_by",
                 "blocking_gig_id")
WINDOW_COLUMNS = {
    "gig_id", "gig_status", "pay_rate_cents", "eligible_count",
    "eligible_worker_ids",
}
TOTAL_COLUMNS = {
    "roster_rows", "offerable", "committed", "not_available", "off_roster",
}


def _read_board(ctx) -> tuple[list[str], list[dict]] | None:
    path = Path(ctx.output_dir) / "availability_board.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
    except (OSError, csv.Error, ValueError):
        return None
    return list(reader.fieldnames or []), rows


def _read_windows(ctx) -> dict | None:
    path = Path(ctx.output_dir) / "windows.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _crawl_roster_ids(ctx) -> set[str]:
    """The roster ids as the VENDOR holds them, read over the verifier's own path.

    Dedupes by id and re-crawls from offset 0 until a whole pass turns up nothing
    new, so the verifier is not itself fooled by the churn it is grading the
    connector on. The tenant's churn schedule is finite, so this terminates.
    """
    base = ctx.vendor(VENDOR).base_url
    api_key = ctx.secrets.get("CC_API_KEY", "")
    known: set[str] = set()
    while True:
        discovered = 0
        offset = 0
        while True:
            req = urllib.request.Request(
                f"{base}/v1/workers?offset={offset}&limit={CRAWL_LIMIT}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            envelope = json.load(urllib.request.urlopen(req, timeout=30))
            rows = envelope.get("data") or []
            for record in rows:
                if record["id"] not in known:
                    known.add(record["id"])
                    discovered += 1
            used = int(envelope.get("limit") or CRAWL_LIMIT)
            if len(rows) < used:
                break
            offset += used
        if discovered == 0:
            return known


def _list_requests(log, collection: str) -> list[tuple[int, int]]:
    """(offset, limit) of every 2xx list request the slice holds for `collection`."""
    out: list[tuple[int, int]] = []
    for entry in log:
        if str(entry.get("path") or "") != f"/v1/{collection}":
            continue
        if str(entry.get("method") or "GET").upper() != "GET":
            continue
        if int(entry.get("status") or 0) != 200:
            continue
        query = entry.get("query") or {}
        try:
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", 10))
        except (TypeError, ValueError):
            continue
        out.append((offset, min(max(limit, 1), 50)))
    return out


def _class_sets(board_rows) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in board_rows:
        out.setdefault(str(row.get("availability") or ""), set()).add(
            str(row.get("worker_id") or "")
        )
    return out


def _set_detail(label: str, got: set[str], want: set[str]) -> str:
    missing = sorted(want - got)
    extra = sorted(got - want)
    if not missing and not extra:
        return f"{label}: {len(got)} worker(s), exact"
    return (f"{label}: {len(got)} worker(s) vs {len(want)} expected; "
            f"missing {missing[:6]}{'...' if len(missing) > 6 else ''}; "
            f"unexpected {extra[:6]}{'...' if len(extra) > 6 else ''}")


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    ctx.vendor(VENDOR).recreate(checkpoint=key["checkpoints"]["board"])

    # -- the morning run -------------------------------------------------------
    code, _out, err = ctx.app.run()
    board_doc = _read_board(ctx)
    board = board_doc[1] if board_doc is not None else None
    windows_doc = _read_windows(ctx)
    ctx.check_l1(
        "dispatch_board_run_completed",
        code == 0 and isinstance(board, list) and bool(board)
        and isinstance(windows_doc, dict),
        f"exit={code} board={type(board).__name__}({len(board or [])}) "
        f"windows={type(windows_doc).__name__} stderr={err[:400]}",
    )

    # Emitted unconditionally from here on. A submission that produced no
    # artifact fails each of these rather than shrinking the denominator.
    rows = board if isinstance(board, list) else []
    doc = windows_doc if isinstance(windows_doc, dict) else {}
    by_worker = {str(r.get("worker_id") or ""): r for r in rows}

    # -- the request log, before this verifier adds any traffic to it ----------
    log_after_run = ctx.vendor(VENDOR).request_log()
    n_after_run = len(log_after_run)
    connector_talked = bool(log_after_run)

    worker_reqs = _list_requests(log_after_run, "workers")
    assignment_reqs = _list_requests(log_after_run, "assignments")

    restarts = sum(1 for offset, _ in worker_reqs if offset == 0)
    ctx.check_l1(
        "roster_crawl_restarted_from_offset_zero",
        restarts >= 2,
        f"the connector asked for the roster's first page {restarts} time(s); the "
        "roster re-sorts while it is paged, so one forward pass cannot be shown to "
        "be complete",
    )

    covered: set[int] = set()
    for offset, limit in assignment_reqs:
        covered.update(range(offset, offset + limit))
    want_rows = int(key["assignment_rows"])
    ctx.check_l1(
        "assignment_feed_read_past_the_page_ceiling",
        bool(assignment_reqs) and set(range(want_rows)) <= covered,
        f"the connector's {len(assignment_reqs)} assignment request(s) span "
        f"offsets {sorted(covered)[:3]}..{sorted(covered)[-3:] if covered else []}, "
        f"which does not cover the feed's {want_rows} rows (the vendor clamps a "
        "page at 50, so one request cannot)",
    )

    # -- the roster, as the vendor holds it ------------------------------------
    live_roster_ids = _crawl_roster_ids(ctx)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())

    board_ids = {str(r.get("worker_id") or "") for r in rows}
    normalized_board = {
        str(row.get("worker_id") or ""):
        {column: str(row.get(column) or "") for column in BOARD_COLUMNS}
        for row in rows
    }
    expected_board = {
        str(row["worker_id"]):
        {column: str(row.get(column) or "") for column in BOARD_COLUMNS}
        for row in key["board_rows"]
    }
    ctx.check_l1(
        "board_row_set_matches_the_live_roster",
        bool(rows) and board_doc is not None
        and board_doc[0] == list(BOARD_COLUMNS)
        and len(rows) == len(board_ids)
        and board_ids == live_roster_ids
        and normalized_board == expected_board,
        (_set_detail("board rows", board_ids, live_roster_ids)
         + f"; header={board_doc[0] if board_doc else None!r}; "
         + ("all rows and fields exact" if normalized_board == expected_board
            else "one or more row fields differ from vendor truth")
         + ("" if len(rows) == len(board_ids) else "; duplicate worker_id rows")),
    )

    # -- the composite, class by class ----------------------------------------
    got_classes = _class_sets(rows)
    for name, want_ids in sorted(key["availability_sets"].items()):
        want = set(want_ids)
        got = got_classes.get(name, set())
        ctx.check_l1(
            f"board_class_{name}_set_exact",
            bool(rows) and got == want,
            _set_detail(f"{name}", got, want) if rows
            else "the connector produced no board rows",
        )

    # Two checks that cannot both be satisfied by a connector that patched one
    # number to look right: the totals must agree with the CSV the connector
    # itself wrote, AND with what the vendor actually holds.
    totals = doc.get("totals") if isinstance(doc.get("totals"), dict) else {}
    self_consistent = bool(rows) and all(
        int(totals.get(name, -1)) == len(got_classes.get(name, set()))
        for name in ("offerable", "committed", "not_available", "off_roster")
    ) and int(totals.get("roster_rows", -1)) == len(rows)
    ctx.check_l1(
        "window_totals_agree_with_the_board_rows",
        self_consistent,
        f"windows.json totals {totals} do not describe the "
        f"{len(rows)} row(s) in availability_board.csv",
    )
    ctx.check_l1(
        "window_totals_match_vendor_truth",
        bool(rows) and all(
            int(totals.get(name, -1)) == int(value)
            for name, value in key["totals"].items()
        ) and set(totals) == TOTAL_COLUMNS,
        f"windows.json totals {totals}, expected {key['totals']}",
    )

    # -- per worker, for every roster row whose line the assignment join decides
    for wid in key["audited_cohort"]:
        want = key["board_by_worker"][wid]
        got = by_worker.get(wid)
        if got is None:
            ok, detail = False, f"{wid}: no board row"
        else:
            wrong = [
                f"{c}={got.get(c)!r} (want {want[c]!r})"
                for c in BOARD_COLUMNS
                if str(got.get(c) or "") != str(want[c] or "")
            ]
            ok = not wrong
            detail = f"{wid}: " + (", ".join(wrong) or "board line exact")
        ctx.check_l1(f"board_{wid}_line_exact", ok, detail)

    # -- the picking lists ------------------------------------------------------
    generated = doc.get("generated_windows")
    generated = generated if isinstance(generated, list) else []
    got_order = [str(w.get("gig_id")) for w in generated if isinstance(w, dict)]
    ctx.check_l1(
        "window_list_and_order_exact",
        set(doc) == {"generated_windows", "totals"}
        and got_order == list(key["window_ids_in_order"]),
        f"windows {got_order}, expected {key['window_ids_in_order']} "
        "(gigs still taking crew, best-paying first)",
    )

    got_windows = {str(w.get("gig_id")): w for w in generated if isinstance(w, dict)}
    for want in key["windows"]:
        gid = want["gig_id"]
        entry = got_windows.get(gid)
        want_ids = set(want["eligible_worker_ids"])
        if entry is None:
            ok, detail = False, f"{gid}: no picking list"
        else:
            raw_ids = entry.get("eligible_worker_ids")
            raw_ids = raw_ids if isinstance(raw_ids, list) else []
            got_list = [str(w) for w in raw_ids]
            got_ids = set(got_list)
            count_ok = entry.get("eligible_count") == len(got_list)
            fields_ok = (
                set(entry) == WINDOW_COLUMNS
                and
                entry.get("gig_status") == want["gig_status"]
                and entry.get("pay_rate_cents") == want["pay_rate_cents"]
            )
            ok = got_list == want["eligible_worker_ids"] and count_ok and fields_ok
            detail = _set_detail(f"{gid} eligible", got_ids, want_ids)
            if not count_ok:
                detail += f"; eligible_count={entry.get('eligible_count')!r} does not " \
                          f"count the {len(got_list)} list entry/entries"
            if got_list != want["eligible_worker_ids"]:
                detail += "; eligible_worker_ids order, duplicates, or values differ"
            if not fields_ok:
                detail += (f"; gig_status/pay={entry.get('gig_status')!r}/"
                           f"{entry.get('pay_rate_cents')!r}, expected "
                           f"{want['gig_status']!r}/{want['pay_rate_cents']!r}")
        ctx.check_l1(f"window_{gid}_eligible_set_exact", ok, detail)

        # Per worker whose place on THIS list is decided by a row naming THIS
        # gig, in both directions: somebody already put forward for it is off
        # the list whatever became of that joining, and somebody whose only row
        # for it was withdrawn is on the list. Gated on the connector having
        # produced a list for this window at all -- "absent" is not a property
        # an empty list gets to bank.
        produced = bool(entry) and bool(entry.get("eligible_worker_ids"))
        listed = ({str(w) for w in (entry.get("eligible_worker_ids") or [])}
                  if entry else set())
        for wid, should_be_listed in sorted(want.get("place_audit", {}).items()):
            place_ok = produced and ((wid in listed) == bool(should_be_listed))
            ctx.check_l1(
                f"window_{gid}_place_for_{wid}",
                place_ok,
                (f"{wid} is {'missing from' if should_be_listed else 'on'} {gid}'s "
                 f"picking list; expected {'a place on it' if should_be_listed else 'no place on it'}")
                if produced else f"{gid}: the connector produced no picking list",
            )

    # -- the same board again ---------------------------------------------------
    code, _out, err = ctx.app.run()
    again_board_doc = _read_board(ctx)
    again_board = again_board_doc[1] if again_board_doc is not None else None
    again_windows = _read_windows(ctx)
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(again_board, list) and bool(again_board)
        and isinstance(again_windows, dict),
        f"exit={code} board={type(again_board).__name__} stderr={err[:400]}",
    )
    n_after_rerun = len(ctx.vendor(VENDOR).request_log())

    # The board is rebuilt from scratch every morning, so a second rebuild of an
    # unchanged world must land on the same answer. It cannot, for a crawl that
    # stops after one forward pass: by the second run the roster's re-sort has
    # settled, so the rows the first run lost come back and the board moves.
    # Compared against the FIRST run's rows rather than the key, so this is a
    # stability property in its own right and not a second copy of the checks
    # above — and it fails when either run produced nothing.
    stable = bool(rows) and isinstance(again_board, list) and bool(again_board) and (
        [tuple(str(r.get(c) or "") for c in BOARD_COLUMNS)
         for r in sorted(rows, key=lambda r: str(r.get("worker_id")))]
        == [tuple(str(r.get(c) or "") for c in BOARD_COLUMNS)
            for r in sorted(again_board, key=lambda r: str(r.get("worker_id")))]
    )
    ctx.check_l1(
        "rerun_board_identical_to_the_first_build",
        stable,
        f"first build wrote {len(rows)} row(s), second wrote "
        f"{len(again_board or [])}; the world did not change between them",
    )
    ctx.check_l1(
        "rerun_windows_identical_to_the_first_build",
        bool(generated) and isinstance(again_windows, dict)
        and again_windows.get("generated_windows") == generated,
        "the second build's picking lists differ from the first's over an "
        "unchanged world",
    )

    again_ids = _crawl_roster_ids(ctx)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())
    ctx.check_l1(
        "rerun_board_still_covers_the_live_roster",
        bool(again_board) and {str(r.get("worker_id") or "")
                               for r in (again_board or [])} == again_ids,
        _set_detail("second build's rows",
                    {str(r.get("worker_id") or "") for r in (again_board or [])},
                    again_ids),
    )

    # -- conduct, once per vendor lifetime, over the connector's traffic only --
    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_run, n_after_probe_1),
            *range(n_after_rerun, n_after_probe_2),
        ],
        app_runs=2,
    )
