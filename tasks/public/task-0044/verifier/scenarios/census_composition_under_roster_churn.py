"""task-0044 — slow_count_divergence_churn_dupes.

One night's roster census against a CrewCall tenant whose night shift clocks in
while the census is running, then the same census again once the roster has
settled.

This is a `fix`: the shipped connector WORKS. It walks the roster from
`offset=0` to the short page that ends it, steps its offset by the page size the
envelope echoes back, carries crew CrewCall has taken off the books as `removed`
rather than dropping them, writes both artifacts and exits 0. Its headline
totals are right — all three of them, every night. Its composition is not.

The belief it is built on is written down in
`docs/fenmarsh-census-runbook.md`, Workforce Analytics' own note, disclaimed in
its second line as "our own note, not CrewCall's documentation":

> Dev pulled two sweeps back to back one afternoon in April and diffed them: the
> same hundred-and-twenty-odd carers came back both times, in a different order.
> The **order** moves; the **content** does not. The same row does not come back
> twice inside one sweep, and no row hides from one either.

CrewCall is an honest vendor (`LIES.md`: `docs.lies: []`), its shipped `docs/`
are byte-identical to the vendor's, and `docs/pagination.md` says the opposite
of the runbook in as many words — a record CAN appear on two consecutive pages,
a record CAN shift behind the cursor and be missed, dedupe by `id` and re-crawl
until a pass finds nothing new. The runbook's April experiment is not a lie, it
is a stale measurement: it was run in the afternoon, when the tenant's churn had
long since settled, and the census runs at 02:00 when it has not.

FOUR independent things have to go right.

1. **DIVERGENCE — "the same row does not come back twice inside one sweep."**
   MEASURED live by `tools/rework/gen_answer_key_0188.py` against the tenant's
   own churn schedule: a single forward walk at the census's page size hands
   back TWELVE ids twice (`duplicated_by_one_sweep`). Counting rows instead of
   people counts those twelve carers twice.

2. **DIVERGENCE — "no row hides from one sweep either."** The same walk never
   sees TWELVE other ids at all (`hidden_from_one_sweep`) — they clock in behind
   the cursor and move to the front of a page already read. Only a re-crawl to a
   clean pass finds them.

3. **COMPETENCE — why nobody caught it in a year.** A forward offset walk covers
   exactly as many POSITIONS as the roster has rows, so it is served exactly 125
   rows whatever the churn does. Twelve duplicates and twelve hidden carers
   cancel to the digit: `roster_rows`, `active_headcount` and
   `removed_headcount` all come out EXACTLY right (the churn schedule is chosen
   so one soft-deleted row is duplicated and one is hidden, so even the
   active/removed split survives). The Friday cross-check against payroll has
   matched every week and always will. What is wrong is which carers those
   totals are made of — and three of the ten role buckets
   (`roles_a_row_count_still_gets_right`) are right for the same cancelling
   reason, so even the breakdowns are only partly wrong.

4. **COMPETENCE — a removed carer is a row, not an absence.** Nine of the 125
   records carry `is_deleted: true` and stay in the listing. They belong on the
   census as `removed`, out of the active headcount and out of the per-status
   breakdown. The shipped connector already does this and the runbook agrees; a
   rewrite that drops them, or counts them as staff, is wrong in a way that is
   invisible in `roster_rows`.

`naive.patch` is the wrong FIX, not a second do-nothing: an engineer who reads
`docs/pagination.md` after seeing Priya twice in the export, dedupes by `id`
exactly as it says — and stops there, because the runbook is emphatic that a
second sweep is call volume for nothing and because deduping is visibly what the
duplicate symptom asked for. It repairs the twelve duplicates and leaves the
twelve hidden carers, so the census now under-counts by twelve and the headline
that had been right for a year stops being right.

That is the matched pair. The two wrong submissions differ in BOTH directions
and neither can pass the other's half without doing both jobs:

| what | starter | naive |
|---|---|---|
| the three headline totals | **right** | **wrong** |
| the twelve duplicated carers, once each | **wrong** | **right** |
| the twelve hidden carers, present at all | wrong | wrong |
| `cleaner` / `line_cook` role lines (a duplicate and a hidden row cancel) | **right** | **wrong** |
| `available` / `inactive` status lines (same cancellation) | **right** | **wrong** |

MEASURED (rig probe, this scenario, 64 checks; every number is a probe reading,
not an estimate):

| submission | what it is | score |
|---|---|---|
| gold (`solution.patch`) | key by id, re-walk until a pass finds nothing new | **64/64 = 1.000** |
| starter | the shipped census: one walk, count the rows | 27/64 = 0.422 |
| harness stub | empty submission | 0/64 = 0.000 |
| `naive.patch` | the wrong fix: key by id, still one walk | 36/64 = 0.562 |
| `variants/defensive.patch` | starter + "ask for the biggest window" (page size 50) | 55/64 = 0.859 |
| `variants/second-guess.patch` | naive + the same page-size move | 56/64 = 0.875 |
| `variants/alt-correct.patch` | legitimate and structurally different: a walk generator, convergence by snapshot equality, page size 25, census lines built from an id map | **64/64 = 1.000** |

Starter and naive differ on 23 checks in BOTH directions. SEVEN the starter
passes and the naive fails: all three headline totals, the `cleaner` and
`line_cook` role splits and the `available` and `inactive` status headcounts —
every one of them a bucket where a duplicated carer and a hidden carer cancel
inside the row count. SIXTEEN the naive passes and the starter fails: the twelve
duplicated carers' census lines, `census_counts_no_carer_twice`, and the
`dishwasher` / `host` / `warehouse` role splits, which hold a duplicated carer
and no hidden one.

**A measured limitation, recorded rather than hidden.** The two page-size
variants above score 0.859 and 0.875, which is above the 0.75 the naive is held
to. That is a property of CrewCall's drift engine, not of this task's grading: a
shifted worker jumps to the FRONT of the roster, so a forward walk can only lose
one worker per page BOUNDARY it has already crossed. At the vendor's maximum
page size a 125-row roster has two boundaries, so a single walk at limit 50
loses exactly ONE carer and duplicates one — measured, not argued, and the same
number `single_pass_misses_by_limit['50']` records on task-0071, task-0031 and
task-0168. Nothing in the drift schedule can widen it: hiding the target of
shift `s` at page size `L` requires that worker to sit past position `L*(s+1)`,
so `L=50` caps the loss at two whatever the schedule. Making a two-carer error
cost a quarter of the check mass would be padding the check list, which is what
AUTHORING-CHECKLIST forbids. The device is at its widest at the page size the
shipped census actually asks for, and both page-size variants still fail every
requirement-shaped check about how the roster was read.

Evidence: both artifacts are compared field by field against an answer key
measured from a live vendor; the roster is crawled by this verifier over the
vendor's published port with the documented convergence recipe, never taken from
the connector's account of it; the sweep-shape checks read the vendor's own
request log sliced to the connector's traffic. Every check is emitted
unconditionally, so a submission that produced no artifact scores zero rather
than shrinking the denominator, and every check that could be satisfied by
silence first proves the connector produced lines. `builtin_l2` fires once,
after the last connector run, with this verifier's own request indices excluded.
"""

import csv
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "crewcall"
CRAWL_LIMIT = 50
LINE_COLUMNS = ("worker_id", "role", "status", "standing")


def _read_census(ctx) -> list[dict] | None:
    path = Path(ctx.output_dir) / "roster_census.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            return rows if reader.fieldnames == list(LINE_COLUMNS) else None
    except (OSError, ValueError):
        return None


def _read_summary(ctx) -> dict | None:
    path = Path(ctx.output_dir) / "census_summary.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _crawl(ctx) -> list[dict]:
    """The roster as the VENDOR holds it, read over the verifier's own path.

    Dedupes by id and re-crawls from offset 0 until a whole pass turns up
    nothing new, so the verifier is not itself fooled by the churn it is grading
    the connector on. The tenant's churn schedule is finite, so this terminates.
    """
    base = ctx.vendor(VENDOR).base_url
    api_key = ctx.secrets.get("CC_API_KEY", "")
    known: dict[str, dict] = {}
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
                    known[record["id"]] = record
                    discovered += 1
            used = int(envelope.get("limit") or CRAWL_LIMIT)
            if len(rows) < used:
                break
            offset += used
        if discovered == 0:
            return list(known.values())


def _bucket(entries, field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get(field) is not None:
            out[str(entry[field])] = entry
    return out


def _list_requests(log, list_path: str) -> list[tuple[int, int, int]]:
    out = []
    for entry in log:
        if str(entry.get("path") or "") != list_path:
            continue
        if str(entry.get("method") or "GET").upper() != "GET":
            continue
        query = entry.get("query") or {}
        try:
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", 10))
        except (TypeError, ValueError):
            continue
        out.append((offset, max(1, min(limit, 50)), int(entry.get("status") or 0)))
    return out


def _signature(rows) -> list[tuple]:
    return sorted(
        tuple(str(r.get(c) or "") for c in LINE_COLUMNS)
        for r in (rows if isinstance(rows, list) else [])
    )


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    list_path = key["list_path"]

    ctx.vendor(VENDOR).recreate(checkpoint=key["checkpoints"]["census"],
                                env=key["vendor_env"])

    # -- 02:00, night one -----------------------------------------------------
    code, _out, err = ctx.app.run()
    census = _read_census(ctx)
    summary = _read_summary(ctx)
    ctx.check_l1(
        "census_run_completed",
        code == 0 and isinstance(census, list) and bool(census)
        and isinstance(summary, dict),
        f"exit={code} roster_census={type(census).__name__}({len(census or [])}) "
        f"census_summary={type(summary).__name__} stderr={err[:400]}",
    )

    # Emitted unconditionally from here on.
    lines = census if isinstance(census, list) else []
    body = summary if isinstance(summary, dict) else {}
    counted = Counter(str(r.get("worker_id") or "") for r in lines)

    # -- the three headline totals, against what the tenant actually holds -----
    ctx.check_l1(
        "census_roster_row_total_matches_the_tenant",
        bool(lines) and body.get("roster_rows") == key["roster_rows"],
        f"the census reports {body.get('roster_rows')} roster row(s); CrewCall "
        f"holds {key['roster_rows']}",
    )
    ctx.check_l1(
        "census_active_headcount_matches_the_tenant",
        bool(lines) and body.get("active_headcount") == key["active_headcount"],
        f"the census reports {body.get('active_headcount')} active carer(s); "
        f"CrewCall holds {key['active_headcount']}",
    )
    ctx.check_l1(
        "census_removed_headcount_matches_the_tenant",
        bool(lines) and body.get("removed_headcount") == key["removed_headcount"],
        f"the census reports {body.get('removed_headcount')} removed carer(s); "
        f"CrewCall holds {key['removed_headcount']}",
    )

    # The twin of the three above: the summary has to describe the census file
    # it was written beside. A submission cannot satisfy both by patching a
    # number, because the file and the totals are graded against each other AND
    # against the vendor.
    ctx.check_l1(
        "census_totals_describe_the_lines_it_wrote",
        bool(lines)
        and body.get("roster_rows") == len(lines)
        and body.get("active_headcount") == sum(
            1 for r in lines if str(r.get("standing")) == "active")
        and body.get("removed_headcount") == sum(
            1 for r in lines if str(r.get("standing")) == "removed"),
        f"summary says {body.get('roster_rows')}/{body.get('active_headcount')}/"
        f"{body.get('removed_headcount')}, but roster_census.csv holds "
        f"{len(lines)} line(s), "
        f"{sum(1 for r in lines if str(r.get('standing')) == 'active')} active and "
        f"{sum(1 for r in lines if str(r.get('standing')) == 'removed')} removed",
    )

    twice = sorted(w for w, c in counted.items() if c > 1)
    ctx.check_l1(
        "census_counts_no_carer_twice",
        bool(lines) and not twice,
        (f"{len(twice)} worker id(s) hold more than one census line: {twice[:6]}")
        if twice else ("the census produced no lines at all"
                       if not lines else "every census line is a distinct carer"),
    )

    # -- the roster, as the vendor holds it ------------------------------------
    log_after_run = ctx.vendor(VENDOR).request_log()
    n_after_run = len(log_after_run)
    roster_reqs = _list_requests(log_after_run, list_path)

    roster = _crawl(ctx)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())
    truth_ids = {str(r["id"]) for r in roster}

    ctx.check_l1(
        "census_artifact_exact",
        lines == key["lines"],
        f"census has {len(lines)} line(s); expected {len(key['lines'])} exact, "
        "worker-id-sorted lines with the documented four columns",
    )

    expected_summary = {
        "roster_rows": key["roster_rows"],
        "active_headcount": key["active_headcount"],
        "removed_headcount": key["removed_headcount"],
        "by_role": key["by_role"],
        "by_status": key["by_status"],
        "pages_read": len(roster_reqs),
    }
    ctx.check_l1(
        "census_summary_artifact_exact",
        body == expected_summary,
        f"summary differs from exact contract; pages_read={body.get('pages_read')!r}, "
        f"actual connector roster GETs={len(roster_reqs)}",
    )

    ctx.check_l1(
        "census_covers_every_carer_the_tenant_holds",
        bool(lines) and set(counted) == truth_ids,
        f"the census names {len(counted)} carer(s); CrewCall holds {len(truth_ids)}. "
        f"Missing {sorted(truth_ids - set(counted))[:6]}; "
        f"invented {sorted(set(counted) - truth_ids)[:6]}",
    )

    # -- the breakdowns the capacity model actually reads ----------------------
    got_roles = _bucket(body.get("by_role"), "role")
    for want in key["by_role"]:
        role = want["role"]
        got = got_roles.get(role)
        if got is None:
            ok, detail = False, f"{role}: no entry in by_role"
        else:
            ok = (int(got.get("active", -1)) == want["active"]
                  and int(got.get("removed", -1)) == want["removed"])
            detail = (f"{role}: active={got.get('active')} removed={got.get('removed')}; "
                      f"expected {want['active']}/{want['removed']}")
        ctx.check_l1(f"census_role_{role}_split_exact", ok, detail)

    got_statuses = _bucket(body.get("by_status"), "status")
    for want in key["by_status"]:
        status = want["status"]
        got = got_statuses.get(status)
        if got is None:
            ok, detail = False, f"{status}: no entry in by_status"
        else:
            ok = int(got.get("headcount", -1)) == want["headcount"]
            detail = (f"{status}: headcount={got.get('headcount')}; "
                      f"expected {want['headcount']}")
        ctx.check_l1(f"census_status_{status}_headcount_exact", ok, detail)

    # -- per carer, over the cohort the churn decides plus a control sample ----
    want_lines = {row["worker_id"]: row for row in key["lines"]}
    by_worker: dict[str, list[dict]] = {}
    for row in lines:
        by_worker.setdefault(str(row.get("worker_id") or ""), []).append(row)

    for wid in key["audited_cohort"]:
        want = want_lines[wid]
        held = by_worker.get(wid, [])
        if len(held) != 1:
            ok = False
            detail = (f"{wid}: {len(held)} census line(s), expected exactly 1"
                      + (" — this carer never reached the census" if not held
                         else " — this carer was counted more than once"))
        else:
            wrong = [f"{c}={held[0].get(c)!r} (want {want[c]!r})"
                     for c in LINE_COLUMNS if str(held[0].get(c) or "") != str(want[c])]
            ok = not wrong
            detail = f"{wid}: " + (", ".join(wrong) or "census line exact")
        ctx.check_l1(f"census_line_{wid}_exact", ok, detail)

    # -- how the roster was read ----------------------------------------------
    restarts = sum(1 for offset, _limit, status in roster_reqs
                   if offset == 0 and status == 200)
    ctx.check_l1(
        "roster_sweep_reran_from_the_first_window",
        restarts >= 2,
        f"the connector asked for the roster's first window {restarts} time(s); the "
        "roster re-sorts while it is paged, so one forward walk cannot be shown to "
        "be complete",
    )
    covered: set[int] = set()
    for offset, limit, status in roster_reqs:
        if status == 200:
            covered.update(range(offset, offset + limit))
    ctx.check_l1(
        "roster_sweep_spanned_every_position_the_tenant_holds",
        bool(roster_reqs) and set(range(key["roster_rows"])) <= covered,
        f"the sweep's served windows cover {len(covered)} roster position(s); the "
        f"tenant holds {key['roster_rows']} rows",
    )

    # -- the same census again, over a roster that has now settled ------------
    code, _out, err = ctx.app.run()
    again = _read_census(ctx)
    again_summary = _read_summary(ctx)
    n_after_rerun = len(ctx.vendor(VENDOR).request_log())
    rerun_reqs = _list_requests(
        ctx.vendor(VENDOR).request_log()[n_after_probe_1:n_after_rerun], list_path,
    )
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(again, list) and bool(again)
        and isinstance(again_summary, dict),
        f"exit={code} roster_census={type(again).__name__}({len(again or [])}) "
        f"stderr={err[:400]}",
    )
    # Compared against the FIRST night's file rather than the key, so this is a
    # stability property in its own right: the tenant did not change between the
    # two runs, so the census must not either. It cannot hold for a walk that
    # stops after one pass — by the second run the roster's re-sort has settled,
    # so the carers the first walk lost come back and the census moves.
    ctx.check_l1(
        "rerun_census_identical_to_the_first_night",
        bool(lines) and bool(again) and _signature(lines) == _signature(again),
        f"the first census wrote {len(lines)} line(s) and the second wrote "
        f"{len(again or [])} over an unchanged tenant"
        + ("" if lines and again else "; one of the two runs produced nothing"),
    )
    ctx.check_l1(
        "rerun_headline_totals_still_match_the_tenant",
        isinstance(again_summary, dict) and bool(again)
        and again_summary.get("roster_rows") == key["roster_rows"]
        and again_summary.get("active_headcount") == key["active_headcount"]
        and again_summary.get("removed_headcount") == key["removed_headcount"],
        f"the second census reports {(again_summary or {}).get('roster_rows')}/"
        f"{(again_summary or {}).get('active_headcount')}/"
        f"{(again_summary or {}).get('removed_headcount')}; CrewCall holds "
        f"{key['roster_rows']}/{key['active_headcount']}/{key['removed_headcount']}",
    )
    ctx.check_l1(
        "rerun_artifacts_exact",
        again == key["lines"]
        and again_summary == {**expected_summary, "pages_read": len(rerun_reqs)},
        f"second-run census/summary differs from exact contract; "
        f"connector roster GETs={len(rerun_reqs)}",
    )

    settled = _crawl(ctx)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())
    ctx.check_l1(
        "rerun_census_still_covers_every_carer",
        bool(again) and {str(r.get("worker_id") or "") for r in (again or [])}
        == {str(r["id"]) for r in settled},
        "the second census does not name the same carers CrewCall holds",
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
