"""task-0034 — drift_5xx_dup_writeback_compose.

One Sunday-night crew top-up against a CrewCall tenant whose roster does not
serve one of its three windows on the first few asks, then the same file again.

This is a `harden`: the shipped connector WORKS. It sweeps the roster with the
convergence recipe `docs/pagination.md` prescribes (dedupe by `id`, re-crawl
from `offset=0` until a pass finds nothing new), reconciles the agency's file
against it by address, signs up the crew members CrewCall does not hold, writes
both artifacts and exits 0. Against a tenant that answers every request it is
correct. It is not safe, and the two things that make it unsafe are both
written down as settled fact in `docs/ironvale-topup-runbook.md` — the Staffing
Desk's own note, disclaimed in its third line as "our own note, not CrewCall's
documentation".

CrewCall is an honest vendor (`LIES.md`: `docs.lies: []`) and its shipped `docs/`
are byte-identical to the vendor's. Every divergence below is between the
TENANT'S BELIEFS and the wire.

FOUR independent things have to go right.

1. **DIVERGENCE — "a window that 500s is a window that is empty for us right
   now."** The runbook records that Marcus asked CrewCall in February, that the
   500s come off a read tier under load, and that a faulted window "has never
   hidden anyone from us". The shipped client implements exactly that: a 5xx on
   a roster window becomes `{"data": []}`, which is a short page, which ends the
   pass. The vendor boots with `FAULT_5XX_ON_PAGE=2:3`, so the third window
   (offset 100 at the sweep's page size of 50) refuses its first three hits —
   and a faulted request does NOT advance the roster's re-sort, so retrying it
   replays the same window, exactly as `docs/pagination.md` says of a 429.
   MEASURED against the live vendor by `tools/rework/gen_answer_key_0129.py`:
   the swallowing sweep converges after three passes holding 100 of the tenant's
   125 rows and is permanently blind to the other 25 (`blind_window_ids`).
   TWELVE of the 34 placed crew members live in that blind spot, so twelve
   humans who are already on the tenant get signed up a second time —
   permanently, because `POST /v1/workers` has no idempotency key, no
   de-duplication, and CrewCall has no delete endpoint and no merge tool.

2. **DIVERGENCE — "CrewCall takes a worker out of the listing when the agency
   ends someone's engagement."** It does not: `docs/entities.md` says a deleted
   record stays in list responses carrying `is_deleted: true`, and nine of the
   125 rows do. The shipped index is built over every row the sweep saw, so the
   eight returning crew members in `tombstone_only_people` resolve to the
   tombstone of the worker CrewCall let go. They are never signed up, they get
   no badge, and the report says they were fine.

3. **COMPETENCE — the roster re-sorts while it is paged.** Honest, documented,
   and the runbook agrees. `single_pass_misses_by_limit` is measured live: a
   single forward pass never sees `wkr_0084` at limit 50, nor
   `wkr_0076`/`wkr_0084`/`wkr_0100` at limit 10 or 25. wkr_0084 (Emmy Jansen) is
   one of the placed crew members, which is what prices this device: an
   otherwise-gold submission whose sweep stops after one forward pass measures
   155/165 = 0.939 (`variants/one-forward-pass.patch`), losing Emmy Jansen at
   the report, the tenant and the re-run layers plus the crawl-shape and
   create-count checks.

4. **COMPETENCE — the echoed `limit` is the clamped one.** `docs/pagination.md`
   says a `limit` above 50 is clamped to 50 and that the envelope echoes the
   limit it actually used. A sweep that compares the rows it got against the
   size it ASKED for terminates on its first page. Measured: asking for 100 and
   comparing against 100 converges holding 53 of 125 rows, blind to 18 of the 34
   placed crew members. This is what `naive.patch` does, and it is why "ask for
   bigger pages so the churn has fewer boundaries to bite on" is a fix that
   makes the incident worse rather than better.

The 34 placed crew members are drawn so that the two wrong submissions fail in
OPPOSITE directions, which is the point:

| class | n | rows | what it is | starter | naive |
|---|---|---|---|---|---|
| M-visible | 5 | 5 | on the roster, inside the first clamped window (plus wkr_0084) | right | right |
| M-deep | 6 | 12 | on the roster, past the first clamped window | **right** | **wrong** |
| B | 12 | 24 | on the roster, inside the window the vendor refuses | **wrong** | **wrong** |
| T | 8 | 24 | address held only by a worker CrewCall let go | **wrong** | **right** |
| A | 3 | 3 | on nobody | right | right |

Neither wrong submission can reach the other's half without doing both jobs:
reading the window the vendor refused, and refusing to treat a tombstone as a
person. They differ on 75 of the 165 checks — 27 the starter passes and the
naive fails, 48 the naive passes and the starter fails.

MEASURED (rig probe, this scenario, 165 checks; every number is a probe reading,
not an estimate):

| submission | what it is | score |
|---|---|---|
| gold (`solution.patch`) | retries a refused window, reads the echoed limit, indexes live rows only | **165/165 = 1.000** |
| starter | the shipped connector | 60/165 = 0.364 |
| harness stub | empty submission | 0/165 = 0.000 |
| `naive.patch` | the wrong fix: bigger pages, and stop indexing removed rows | 81/165 = 0.491 |
| `variants/defensive.patch` | starter + the one obvious guard: retry the refused window | 113/165 = 0.685 |
| `variants/second-guess.patch` | starter + the other obvious guard: stop indexing removed rows | 108/165 = 0.655 |
| `variants/one-forward-pass.patch` | gold, but the sweep stops after one pass | 155/165 = 0.939 |
| `variants/alt-correct.patch` | legitimate and structurally different: a page generator, convergence by snapshot equality, a fixed-pause retry, page size 25 | **165/165 = 1.000** |

The two repair variants are the multi-device proof: each fixes exactly one of
the runbook's two false claims and leaves the other, and neither reaches 0.69,
so no single line of the shipped connector recovers the task.

Evidence: both artifacts are compared field by field against an answer key
measured from a live vendor; the tenant's own state is crawled by this verifier
over the vendor's published port, never taken from the connector's account of
it; the create count is read off the vendor's request log, counting ACCEPTED
creates only and sliced to the connector's own traffic. Every check is emitted
unconditionally, and every check that credits absence or stability first proves
the connector acted. `builtin_l2` fires once, after the last connector run, with
this verifier's own request indices excluded.
"""

import csv
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "crewcall"
CRAWL_LIMIT = 50
PLACEMENT_COLUMNS = ["placement_ref", "shift_date", "crew_email", "worker_id", "outcome"]
SUMMARY_COLUMNS = {
    "row_count", "person_count", "matched_count", "created_count",
    "roster_rows_seen", "people",
}
PERSON_COLUMNS = {
    "person_key", "crew_email", "crew_name", "placement_refs", "outcome", "worker_id",
}


def _slug(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(value).lower()).strip("_")


def _read_placements(ctx) -> list[dict] | None:
    path = Path(ctx.output_dir) / "placement_report.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != PLACEMENT_COLUMNS:
                return None
            return list(reader)
    except (OSError, ValueError):
        return None


def _read_summary(ctx) -> dict | None:
    path = Path(ctx.output_dir) / "topup_summary.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _http(ctx, method: str, path: str, body: dict | None = None) -> dict:
    """One request over the verifier's own path, retried past the task's own
    page fault so this verifier is not itself blinded by the thing it grades."""
    base = ctx.vendor(VENDOR).base_url
    api_key = ctx.secrets.get("CC_API_KEY", "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {api_key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last = None
    for _ in range(8):
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500:
                raise
    raise last  # noqa: RSE102 — propagate the last upstream error


def _crawl(ctx) -> list[dict]:
    """The roster as the VENDOR holds it.

    Dedupes by id and re-crawls from offset 0 until a whole pass turns up
    nothing new, so the verifier is not fooled by the churn it is grading the
    connector on. The churn schedule is finite, so this terminates.
    """
    known: dict[str, dict] = {}
    while True:
        discovered = 0
        offset = 0
        while True:
            envelope = _http(ctx, "GET", f"/v1/workers?offset={offset}&limit={CRAWL_LIMIT}")
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


def _live(records):
    return [r for r in records if not r.get("is_deleted")]


def _address(record: dict) -> str:
    return str(record.get("email") or "").strip().lower()


def _accepted_creates(log, write_path: str) -> int:
    return sum(
        1 for e in log
        if str(e.get("path") or "") == write_path
        and str(e.get("method") or "").upper() == "POST"
        and 200 <= int(e.get("status") or 0) < 300
    )


def _list_requests(log, list_path: str) -> list[tuple[int, int, int]]:
    """(offset, limit, status) for every GET of the roster in this slice."""
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
        out.append((offset, limit, int(entry.get("status") or 0)))
    return out


def _one_live_record(live_records, ids_before, want, *, connector_talked):
    """Exactly one live worker holds this crew member's address, and it is the
    right one.

    Both halves of the task land here and they point in opposite directions: a
    connector blind to part of the roster leaves a crew member the tenant
    already employed held TWICE, while a connector that accepted a tombstone as
    a person leaves a returning crew member held ZERO times. The count is the
    evidence.
    """
    if not connector_talked:
        return False, "the connector never contacted the vendor"
    if not live_records:
        return False, "no live records read back from the vendor — nothing to judge"
    address = want["person_key"]
    who = f"{address} ({want['crew_name']})"
    held = sorted(str(r.get("id")) for r in live_records if _address(r) == address)
    if len(held) != 1:
        expectation = (
            f"the record the tenant already held ({want['existing_worker_id']})"
            if want["outcome"] == "matched" else "one freshly signed-up record"
        )
        return False, (
            f"{who}: the tenant holds {len(held)} live record(s) {held[:4]}, "
            f"expected exactly 1 — {expectation}"
        )
    got = held[0]
    if want["outcome"] == "matched":
        return (got == str(want["existing_worker_id"]),
                f"{who}: held as {got}, expected {want['existing_worker_id']}")
    before = {str(i) for i in ids_before}
    return (got not in before,
            f"{who}: the single live record {got} is one the tenant already had — "
            "this crew member had to be signed up")


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    people = key["people"]
    created_people = key["created_people"]
    ids_before = key["roster_ids_before"]
    write_path = key["write_path"]
    list_path = key["list_path"]
    faulted_offset = int(key["fault_window"]["offset"])

    ctx.vendor(VENDOR).recreate(checkpoint=key["checkpoints"]["topup"],
                                env=key["vendor_env"])

    # -- Sunday night ----------------------------------------------------------
    code, _out, err = ctx.app.run()
    placements = _read_placements(ctx)
    summary = _read_summary(ctx)
    ctx.check_l1(
        "topup_run_completed",
        code == 0 and isinstance(placements, list) and bool(placements)
        and isinstance(summary, dict),
        f"exit={code} placement_report={type(placements).__name__}"
        f"({len(placements or [])}) topup_summary={type(summary).__name__} "
        f"stderr={err[:400]}",
    )

    rows = placements if isinstance(placements, list) else []
    body = summary if isinstance(summary, dict) else {}
    reported = body.get("people") or []
    by_key = {str(p.get("person_key")): p for p in reported if isinstance(p, dict)}
    by_ref = {str(r.get("placement_ref") or ""): r for r in rows}
    expected_refs = [str(r["placement_ref"]) for r in key["rows"]]
    actual_refs = [str(r.get("placement_ref") or "") for r in rows]

    ctx.check_l1(
        "topup_headline_counts_exact",
        body.get("row_count") == key["row_count"]
        and body.get("person_count") == key["person_count"]
        and body.get("matched_count") == key["expected_matched_count"]
        and body.get("created_count") == key["expected_created_count"]
        and set(body) == SUMMARY_COLUMNS
        and isinstance(reported, list)
        and len(reported) == key["person_count"]
        and len(by_key) == key["person_count"]
        and all(isinstance(p, dict) and set(p) == PERSON_COLUMNS for p in reported)
        and actual_refs == expected_refs
        and len(by_ref) == key["row_count"],
        f"summary says rows={body.get('row_count')} people={body.get('person_count')} "
        f"matched={body.get('matched_count')} created={body.get('created_count')}; "
        f"expected {key['row_count']}/{key['person_count']}/"
        f"{key['expected_matched_count']}/{key['expected_created_count']}",
    )
    ctx.check_l1(
        "topup_roster_rows_seen_matches_the_tenant",
        body.get("roster_rows_seen") == key["roster_rows_before"],
        f"the run reports it ended up holding {body.get('roster_rows_seen')} roster "
        f"row(s); the tenant has {key['roster_rows_before']}",
    )

    # -- the request log, before this verifier adds any traffic to it ----------
    log_after_run = ctx.vendor(VENDOR).request_log()
    n_after_run = len(log_after_run)
    connector_talked = bool(log_after_run)
    roster_reqs = _list_requests(log_after_run, list_path)

    served_offsets = {offset for offset, _limit, status in roster_reqs if status == 200}
    refused_offsets = {offset for offset, _limit, status in roster_reqs if status >= 500}
    ctx.check_l1(
        "refused_roster_windows_were_read_not_skipped",
        bool(refused_offsets) and refused_offsets <= served_offsets,
        (f"the vendor refused the roster window(s) at offset(s) "
         f"{sorted(refused_offsets)} and the sweep never came back for "
         f"{sorted(refused_offsets - served_offsets)}")
        if refused_offsets else
        (f"no roster window was ever refused in the connector's traffic — it asked "
         f"for offsets {sorted(served_offsets)[:8]}, and a sweep that covers this "
         f"tenant has to reach offset {faulted_offset}"),
    )
    restarts = sum(1 for offset, _limit, status in roster_reqs
                   if offset == 0 and status == 200)
    ctx.check_l1(
        "roster_sweep_reran_from_the_first_window",
        restarts >= 2,
        f"the connector asked for the roster's first window {restarts} time(s); the "
        "roster re-sorts while it is paged, so one forward pass cannot be shown to "
        "be complete",
    )
    covered = set()
    for offset, limit, status in roster_reqs:
        if status == 200:
            covered.update(range(offset, offset + max(1, min(limit, CRAWL_LIMIT))))
    ctx.check_l1(
        "roster_sweep_spanned_every_position_the_tenant_holds",
        bool(roster_reqs) and set(range(key["roster_rows_before"])) <= covered,
        f"the sweep's served windows cover {len(covered)} roster position(s); the "
        f"tenant holds {key['roster_rows_before']} rows and the vendor clamps a page "
        "at 50, so no single request can",
    )

    accepted = _accepted_creates(log_after_run, write_path)
    ctx.check_l1(
        "accepted_signups_match_the_crew_crewcall_lacks",
        accepted == key["expected_created_count"],
        f"the vendor accepted {accepted} create(s); expected "
        f"{key['expected_created_count']} — one per crew member CrewCall does not "
        "hold, and no more",
    )

    # -- per crew member, at the summary layer --------------------------------
    for want in people:
        address = want["person_key"]
        got = by_key.get(address)
        who = f"{address} ({want['crew_name']})"
        if got is None:
            ok, detail = False, f"{who}: absent from topup_summary.json"
        else:
            problems = []
            if str(got.get("outcome")) != want["outcome"]:
                problems.append(f"outcome={got.get('outcome')!r} (want {want['outcome']!r})")
            if sorted(str(r) for r in (got.get("placement_refs") or [])) != sorted(
                want["placement_refs"]
            ):
                problems.append(
                    f"placement_refs={sorted(str(r) for r in (got.get('placement_refs') or []))} "
                    f"(want {sorted(want['placement_refs'])})"
                )
            if want["outcome"] == "matched" and str(got.get("worker_id")) != str(
                want["existing_worker_id"]
            ):
                problems.append(
                    f"worker_id={got.get('worker_id')!r}, but this crew member is "
                    f"{want['existing_worker_id']} on CrewCall"
                )
            if want["outcome"] == "created" and str(got.get("worker_id")) in {
                str(i) for i in ids_before
            }:
                problems.append(
                    f"worker_id={got.get('worker_id')!r} is a worker the tenant already "
                    "held — this crew member was not on the roster"
                )
            ok = not problems
            detail = f"{who}: " + ("; ".join(problems) or "resolved correctly")
        ctx.check_l1(f"placed_{_slug(address)}_identity_exact", ok, detail)

    # -- per placement row, at the report layer -------------------------------
    for row_spec in key["rows"]:
        ref = row_spec["placement_ref"]
        want = next(p for p in people if p["person_key"] == row_spec["person_key"])
        got = by_ref.get(ref)
        reported_person = by_key.get(want["person_key"]) or {}
        if got is None:
            ok, detail = False, f"{ref}: no row in placement_report.csv"
        else:
            expected_worker = (
                str(want["existing_worker_id"]) if want["outcome"] == "matched"
                else str(reported_person.get("worker_id") or "")
            )
            problems = []
            if str(got.get("outcome")) != want["outcome"]:
                problems.append(f"outcome={got.get('outcome')!r} (want {want['outcome']!r})")
            if str(got.get("crew_email") or "").strip().lower() != want["person_key"]:
                problems.append(f"crew_email={got.get('crew_email')!r}")
            if want["outcome"] == "matched":
                if str(got.get("worker_id")) != expected_worker:
                    problems.append(
                        f"worker_id={got.get('worker_id')!r}, but this shift belongs to "
                        f"{expected_worker}"
                    )
            else:
                if not str(got.get("worker_id") or ""):
                    problems.append("worker_id is blank on a row that had to be signed up")
                elif str(got.get("worker_id")) in {str(i) for i in ids_before}:
                    problems.append(
                        f"worker_id={got.get('worker_id')!r} is a worker the tenant "
                        "already held"
                    )
                elif str(got.get("worker_id")) != expected_worker:
                    problems.append(
                        f"worker_id={got.get('worker_id')!r} does not match the summary's "
                        f"{expected_worker!r} for this crew member"
                    )
            ok = not problems
            detail = f"{ref}: " + ("; ".join(problems) or "carries the right worker")
        ctx.check_l1(f"placement_{_slug(ref)}_carries_the_right_worker", ok, detail)

    # -- the tenant's own state ------------------------------------------------
    roster = _crawl(ctx)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())
    live = _live(roster)

    ctx.check_l1(
        "tenant_live_worker_count_exact",
        connector_talked and len(live) == key["expected_live_workers_after"],
        f"the tenant holds {len(live)} live worker(s); expected "
        f"{key['expected_live_workers_after']}"
        + ("" if connector_talked else " (the connector never contacted the vendor)"),
    )

    for want in people:
        ctx.check_l1(
            f"tenant_{_slug(want['person_key'])}_single_live_record",
            *_one_live_record(live, ids_before, want, connector_talked=connector_talked),
        )

    live_by_address: dict[str, list[dict]] = {}
    for record in live:
        live_by_address.setdefault(_address(record), []).append(record)

    for want in created_people:
        address = want["person_key"]
        held = live_by_address.get(address, [])
        if not connector_talked:
            ok, detail = False, "the connector never contacted the vendor"
        elif len(held) != 1:
            ok, detail = False, (
                f"{address}: the tenant holds {len(held)} live record(s) on this "
                "address, expected exactly the one the run signed up"
            )
        else:
            record = held[0]
            problems = []
            if _address(record) != address:
                problems.append(f"email={record.get('email')!r}")
            if str(record.get("id")) in {str(i) for i in ids_before}:
                problems.append(f"id={record.get('id')!r} predates the run")
            if record.get("is_deleted"):
                problems.append("the signed-up record is flagged deleted")
            ok = not problems
            detail = f"{address}: " + ("; ".join(problems) or "signed up correctly")
        ctx.check_l1(f"signup_{_slug(address)}_upstream_record", ok, detail)

    # Nobody the tenant already employed came out of this run held twice. Gated
    # on the run having added at least one row, so a connector that wrote
    # nothing does not get to bank it.
    # Six addresses on this tenant were already held by two live workers before
    # the run -- that is the marketplace's own history, not this run's doing, so
    # only addresses that BECAME shared count against it.
    added = [r for r in live if str(r.get("id")) not in {str(i) for i in ids_before}]
    shared_before = set(key["shared_addresses_before"])
    duplicated = sorted(
        a for a, held in live_by_address.items()
        if len(held) > 1 and a not in shared_before
    )
    ctx.check_l1(
        "no_crew_member_held_twice_after_the_topup",
        bool(added) and not duplicated,
        (f"{len(duplicated)} address(es) the tenant held once now have two live "
         f"workers on them: {duplicated[:4]}") if duplicated else
        ("the run added no rows at all — nothing to judge" if not added
         else f"all {len(added)} added row(s) sit on distinct addresses"),
    )

    # -- the same file again ---------------------------------------------------
    code, _out, err = ctx.app.run()
    rerun_summary = _read_summary(ctx)
    rerun_placements = _read_placements(ctx)
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(rerun_summary, dict)
        and set(rerun_summary) == SUMMARY_COLUMNS
        and isinstance(rerun_placements, list)
        and [str(r.get("placement_ref") or "") for r in rerun_placements]
        == expected_refs,
        f"exit={code} topup_summary={type(rerun_summary).__name__} stderr={err[:400]}",
    )
    n_after_rerun = len(ctx.vendor(VENDOR).request_log())

    ctx.check_l1(
        "rerun_signed_nobody_up_again",
        isinstance(rerun_summary, dict)
        and rerun_summary.get("created_count") == 0
        and rerun_summary.get("matched_count") == key["person_count"],
        f"the second run reported created={(rerun_summary or {}).get('created_count')} "
        f"matched={(rerun_summary or {}).get('matched_count')}; expected "
        f"0/{key['person_count']}",
    )

    rerun_by_ref = {str(r.get("placement_ref") or ""): r
                    for r in (rerun_placements or [])}
    moved = [
        ref for ref in by_ref
        if str((rerun_by_ref.get(ref) or {}).get("worker_id") or "")
        != str(by_ref[ref].get("worker_id") or "")
    ]
    ctx.check_l1(
        "rerun_worker_ids_unchanged",
        bool(rows) and bool(rerun_placements) and not moved,
        (f"{len(moved)} placement(s) resolved to a different worker on the second run "
         f"over an unchanged file: {moved[:6]}") if (rows and rerun_placements)
        else "one of the two runs produced no placement rows",
    )

    again = _crawl(ctx)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())
    ctx.check_l1(
        "rerun_tenant_live_worker_count_unchanged",
        connector_talked and len(_live(again)) == key["expected_live_workers_after"],
        f"after the second run the tenant holds {len(_live(again))} live worker(s); "
        f"expected {key['expected_live_workers_after']}",
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
