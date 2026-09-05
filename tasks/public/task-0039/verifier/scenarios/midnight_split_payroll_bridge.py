"""task-0039 — midnight_split_timesheet_bridge.

One nightly payroll run over the timekeeping partner's punch export, then the
same export again.

This is a `build`: `bridge.py` ships as four `NotImplementedError`s. Transport,
config, CLI and the artifacts' shapes are given; deciding which payroll DAY each
punch's minutes belong to is the job.

THE DIVERGENCE. Rosterly is a zero-lie vendor (`LIES.md`: `docs.lies: []`) and
its documentation could not be plainer -- `docs/index.md` §Timestamps opens
"READ THIS FIRST", says shift stamps are local wall-clock plus an IANA zone name
that **varies per record**, and points at the record's own `timezone` field.
What is wrong is Harbor Point's own note,
`docs/harborpoint-payroll-runbook.md`, attributed and dated in its third line
("Payroll Systems. This is our own note, not Rosterly's documentation. Written
in February"):

> **Everything on this tenant is on the New York clock.** [...] Marta raised a
> ticket with Rosterly support asking what that suffix was for. Their answer was
> that it is a display hint for their own scheduling UI [...] So: chop the
> suffix off, read the wall clock as Eastern, and take the payroll date straight
> off it.

It is not one clock. Measured on the wire at CHECKPOINT=45, the tenant's 182
shifts are spread across EIGHT zones -- America/Los_Angeles, America/New_York,
America/Sao_Paulo, Asia/Kolkata, Asia/Tokyo, Australia/Sydney, Europe/Berlin,
Pacific/Chatham -- and the export puts three punches through each of them.
`tools/rework/gen_answer_key_0098.py` computes both answers for every punch and
refuses to ship a row where they agree, so the divergence is measured: **21 of
the 24 payable punches land on payroll days the runbook's single-clock answer
never reaches**, and 12 of those also flip the arrival classification. The three
America/New_York punches (PCH-0104/0105/0106) are the controls where the
runbook happens to be right.

A punch clock records absolute instants, so the export is in UTC; which day a
venue paid for is a property of the VENUE. Nothing in the punch file can tell
you that, and the runbook tells you the wrong thing. Only the wire says so.

THREE MORE DEVICES, none of them a divergence, all of them things this has to
get right to reach 1.000:

* **Corrections.** The partner re-sends a punch under the same `punch_ref` when
  a supervisor fixes it on the tablet; the LAST row wins. Three refs are
  re-sent (PCH-0102/0108/0116) with a short first row that would place the punch
  on one payroll day instead of two. Summing the rows, or keeping the first,
  breaks both the day split and the minute totals.
* **Cancelled and unknown shifts.** Rosterly soft-deletes: shift_0012/0013/0014
  come back from `/api/shifts` carrying `is_deleted: true` rather than being
  absent (documented, `docs/index.md` §Entities), and two punches name shifts
  the tenant has never held. All five are exceptions -- off the payroll report,
  on the exception list, no note written back.
* **Idempotent writeback.** A split punch gets one note per punch, keyed
  `hp-<punch_ref>`; the export is re-run and the tenant must still hold exactly
  21 payroll notes, not 42.

`naive.patch` is the runbook-faithful implementation -- a competent engineer who
read Harbor Point's own note and believed it. It runs clean, places every punch,
writes every artifact and posts notes; it is simply wrong about which day
21 of 24 punches were worked on, and about which punches straddle a day at all.

HISTORICAL MEASUREMENT (rig, 2026-08-10, 91 checks):

    gold                 91/91  1.000
    starter               0/91  0.000   (bridge.py is four NotImplementedErrors)
    stub                  0/91  0.000
    naive (runbook)      28/91  0.308
    defensive            28/91  0.308   naive + carrying the record's own zone
                                        name through to the report. The one
                                        guard a cautious engineer adds buys
                                        NOTHING: the day splits are still drawn
                                        on the Eastern clock, so the task does
                                        not rest on that one field.
    second guess         20/91  0.220   reads the venue zone, reports it, uses
                                        it for arrival -- but draws the payroll
                                        day at UTC midnight because the punch
                                        file is UTC.
    alt-correct A        91/91  1.000   crawls at limit=25, places the punches
                                        in reverse order.
    alt-correct B        90/91  0.989   resolves each shift with GET
                                        /api/shifts/{id} instead of crawling.
                                        The one miss is `builtin_l2`'s
                                        traffic-conditional
                                        `no_unnecessary_full_resync:shift`,
                                        which is never EMITTED when the
                                        connector never lists the collection,
                                        and an unemitted name scores zero. Not
                                        a check of this task's.

    floor 0.000 · headroom 1.000 · discriminating 91 · vacuous 0

The current manifest adds whole-artifact and write-request checks found during
independent source review; the historical scores above are retained rather
than retroactively claiming those checks were probed. Evidence: every artifact
is compared field by field against an answer key
measured from a live vendor; the tenant's notes are crawled by this verifier
over the vendor's published port, never taken from the connector's account of
them; `builtin_l2` fires once, immediately after the last connector run and
BEFORE this verifier issues a single request of its own, so no probe traffic
can be graded as the submission's conduct. Every check is emitted
unconditionally, and the one check that credits absence first proves the
connector wrote something.
"""

import csv
from collections import Counter
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "rosterly"
NOTE_BODY_PREFIX = "Payroll split | punch "
REPORT_COLUMNS = [
    "punch_ref", "shift_id", "worker_id", "venue_timezone", "payroll_date", "minutes",
]


def _slug(value: str) -> str:
    return value.lower().replace("-", "_")


def _read_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_report(ctx) -> list[dict] | None:
    path = Path(ctx.output_dir) / "import_report.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != REPORT_COLUMNS:
                return None
            return list(reader)
    except (OSError, ValueError):
        return None


def _token(ctx) -> str:
    base = ctx.vendor(VENDOR).base_url
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": ctx.secrets.get("RY_CLIENT_ID", ""),
        "client_secret": ctx.secrets.get("RY_CLIENT_SECRET", ""),
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/oauth/token", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def _get(ctx, token: str, path: str) -> dict:
    req = urllib.request.Request(
        ctx.vendor(VENDOR).base_url + path,
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _worker_notes(ctx, token: str, worker_id: str) -> list[dict]:
    """Every note the tenant holds for one worker, read over the vendor's port."""
    rows: list[dict] = []
    offset = 0
    while True:
        envelope = _get(ctx, token, f"/api/workers/{worker_id}/notes?offset={offset}&limit=50")
        page = envelope.get("data") or []
        rows.extend(page)
        offset += 50
        if not page or offset >= int(envelope.get("total") or 0):
            return rows


def _days_of(entry: dict) -> list[tuple[str, int]]:
    out = []
    for day in entry.get("days") or []:
        if not isinstance(day, dict):
            return [("<malformed>", -1)]
        try:
            out.append((str(day.get("payroll_date")), int(day.get("minutes"))))
        except (TypeError, ValueError):
            out.append((str(day.get("payroll_date")), -1))
    return out


def _expected_days(want: dict) -> list[tuple[str, int]]:
    return [(d["payroll_date"], int(d["minutes"])) for d in want["days"]]


def _covers_whole_collection(log_slice: list[dict], list_path: str, total: int) -> bool:
    """Did the connector's own traffic read every shift the tenant holds?

    Satisfied either by walking the collection (pages whose offset/limit windows
    cover [0, total)) or by fetching each shift by id -- the caller checks the
    by-id route separately. Requirement-shaped on purpose: inaction cannot
    satisfy it.
    """
    covered: set[int] = set()
    for entry in log_slice:
        if str(entry.get("path") or "") != list_path:
            continue
        if int(entry.get("status") or 0) != 200:
            continue
        query = entry.get("query") or {}
        try:
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", 50))
        except (TypeError, ValueError):
            continue
        covered.update(range(offset, min(offset + limit, total)))
    return len(covered) >= total


def _same_typed_tree(actual, expected) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _same_typed_tree(actual[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_typed_tree(a, e) for a, e in zip(actual, expected)
        )
    return actual == expected


def _result_contract_exact(body: object, key: dict) -> bool:
    if not isinstance(body, dict) or set(body) != {
        "punch_count", "bridged_count", "unbridgeable_count", "split_line_count",
        "midnight_split_count", "total_minutes", "notes_posted", "punches",
        "unbridgeable",
    }:
        return False
    scalar_expected = {
        name: key[name]
        for name in (
            "punch_count", "bridged_count", "unbridgeable_count", "split_line_count",
            "midnight_split_count", "total_minutes",
        )
    }
    scalar_expected["notes_posted"] = len(key["notes"])
    if any(not _same_typed_tree(body.get(name), value)
           for name, value in scalar_expected.items()):
        return False
    got_punches = body.get("punches")
    got_skipped = body.get("unbridgeable")
    if not isinstance(got_punches, list) or not isinstance(got_skipped, list):
        return False
    by_ref = {p.get("punch_ref"): p for p in got_punches if isinstance(p, dict)}
    skip_by_ref = {p.get("punch_ref"): p for p in got_skipped if isinstance(p, dict)}
    if len(by_ref) != len(got_punches) or len(got_punches) != len(key["punches"]):
        return False
    if len(skip_by_ref) != len(got_skipped) or len(got_skipped) != len(key["unbridgeable"]):
        return False
    expected_punches = {
        p["punch_ref"]: {
            name: p[name]
            for name in (
                "punch_ref", "shift_id", "worker_id", "venue_timezone", "arrival",
                "minutes", "days",
            )
        }
        for p in key["punches"]
    }
    expected_skipped = {p["punch_ref"]: p for p in key["unbridgeable"]}
    return (
        set(by_ref) == set(expected_punches)
        and set(skip_by_ref) == set(expected_skipped)
        and all(_same_typed_tree(by_ref[ref], want)
                for ref, want in expected_punches.items())
        and all(_same_typed_tree(skip_by_ref[ref], want)
                for ref, want in expected_skipped.items())
    )


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    punches = key["punches"]
    skipped = key["unbridgeable"]
    notes_expected = key["notes"]
    seeded = key["seeded_note_counts"]

    ctx.vendor(VENDOR).recreate(checkpoint=key["checkpoint"])

    # -- tonight's payroll run -------------------------------------------------
    code, _out, err = ctx.app.run()
    result = _read_json(ctx, "result.json")
    report = _read_report(ctx)
    writeback = _read_json(ctx, "writeback_log.json")
    ctx.check_l1(
        "payroll_bridge_run_completed",
        code == 0 and isinstance(result, dict) and isinstance(report, list)
        and bool(report) and isinstance(writeback, dict),
        f"exit={code} result={type(result).__name__} "
        f"import_report={type(report).__name__}({len(report or [])}) "
        f"writeback_log={type(writeback).__name__} stderr={err[:400]}",
    )

    body = result if isinstance(result, dict) else {}
    rows = report if isinstance(report, list) else []
    wb = writeback if isinstance(writeback, dict) else {}

    ctx.check_l1(
        "payroll_headline_counts_exact",
        body.get("punch_count") == key["punch_count"]
        and body.get("bridged_count") == key["bridged_count"]
        and body.get("unbridgeable_count") == key["unbridgeable_count"]
        and body.get("split_line_count") == key["split_line_count"]
        and body.get("midnight_split_count") == key["midnight_split_count"]
        and body.get("total_minutes") == key["total_minutes"],
        f"reported punches={body.get('punch_count')} bridged={body.get('bridged_count')} "
        f"exceptions={body.get('unbridgeable_count')} lines={body.get('split_line_count')} "
        f"splits={body.get('midnight_split_count')} minutes={body.get('total_minutes')}; "
        f"expected {key['punch_count']}/{key['bridged_count']}/"
        f"{key['unbridgeable_count']}/{key['split_line_count']}/"
        f"{key['midnight_split_count']}/{key['total_minutes']}",
    )
    ctx.check_l1(
        "result_json_contract_exact",
        _result_contract_exact(body, key),
        "result.json must contain exactly one correctly typed documented entry per "
        f"punch; got keys={sorted(body) if isinstance(body, dict) else None}",
    )

    actual_report = Counter(
        tuple(str(row.get(col) or "") for col in REPORT_COLUMNS) for row in rows
    )
    expected_report = Counter(
        tuple(str(row[col]) for col in REPORT_COLUMNS) for row in key["report_rows"]
    )
    ctx.check_l1(
        "import_report_contract_exact",
        actual_report == expected_report,
        f"import_report.csv has {len(rows)} rows ({len(actual_report)} distinct), "
        f"expected {len(key['report_rows'])} ({len(expected_report)} distinct); "
        f"unexpected={list((actual_report - expected_report).elements())[:2]} "
        f"missing={list((expected_report - actual_report).elements())[:2]}",
    )

    reported = {str(p.get("punch_ref")): p
                for p in (body.get("punches") or []) if isinstance(p, dict)}
    exceptions = {str(p.get("punch_ref")): p
                  for p in (body.get("unbridgeable") or []) if isinstance(p, dict)}

    rows_by_ref: dict[str, list[dict]] = {}
    for row in rows:
        rows_by_ref.setdefault(str(row.get("punch_ref") or ""), []).append(row)

    # -- per punch, at the result layer ---------------------------------------
    for want in punches:
        ref = want["punch_ref"]
        got = reported.get(ref)
        if got is None:
            ok, detail = False, (
                f"{ref}: absent from result.json's payable punches"
                + (f" (reported as an exception: {exceptions[ref]!r})" if ref in exceptions
                   else ""))
        else:
            problems = []
            if str(got.get("venue_timezone")) != want["venue_timezone"]:
                problems.append(
                    f"venue_timezone={got.get('venue_timezone')!r} "
                    f"(the venue runs on {want['venue_timezone']})")
            if str(got.get("worker_id")) != want["worker_id"]:
                problems.append(f"worker_id={got.get('worker_id')!r} "
                                f"(want {want['worker_id']})")
            if str(got.get("shift_id")) != want["shift_id"]:
                problems.append(f"shift_id={got.get('shift_id')!r}")
            if _days_of(got) != _expected_days(want):
                problems.append(
                    f"days={_days_of(got)} (want {_expected_days(want)})")
            if str(got.get("arrival")) != want["arrival"]:
                problems.append(f"arrival={got.get('arrival')!r} (want {want['arrival']!r})")
            try:
                minutes_ok = int(got.get("minutes")) == want["minutes"]
            except (TypeError, ValueError):
                minutes_ok = False
            if not minutes_ok:
                problems.append(f"minutes={got.get('minutes')!r} (want {want['minutes']})")
            ok = not problems
            detail = f"{ref}: " + ("; ".join(problems) or "placed correctly")
        ctx.check_l1(f"punch_{_slug(ref)}_payroll_split_exact", ok, detail)

    for want in skipped:
        ref = want["punch_ref"]
        got = exceptions.get(ref)
        if got is None:
            ok, detail = False, (
                f"{ref}: not on the exception list"
                + (" — it was placed on the payroll report instead"
                   if ref in reported else ""))
        else:
            problems = []
            if str(got.get("shift_id")) != want["shift_id"]:
                problems.append(f"shift_id={got.get('shift_id')!r}")
            if str(got.get("reason")) != want["reason"]:
                problems.append(f"reason={got.get('reason')!r} (want {want['reason']!r})")
            if ref in reported:
                problems.append("also listed as a payable punch")
            ok = not problems
            detail = f"{ref}: " + ("; ".join(problems) or "held back correctly")
        ctx.check_l1(f"punch_{_slug(ref)}_payroll_split_exact", ok, detail)

    # -- per punch, at the payroll-report layer -------------------------------
    for want in punches:
        ref = want["punch_ref"]
        got_rows = rows_by_ref.get(ref, [])
        actual = []
        for row in got_rows:
            try:
                minutes = int(row.get("minutes"))
            except (TypeError, ValueError):
                minutes = -1
            actual.append((str(row.get("payroll_date")), minutes))
        problems = []
        if actual != _expected_days(want):
            problems.append(f"lines={actual} (want {_expected_days(want)})")
        for row in got_rows:
            if str(row.get("venue_timezone")) != want["venue_timezone"]:
                problems.append(f"venue_timezone={row.get('venue_timezone')!r}")
                break
        for row in got_rows:
            if str(row.get("worker_id")) != want["worker_id"]:
                problems.append(f"worker_id={row.get('worker_id')!r}")
                break
        ctx.check_l1(
            f"report_{_slug(ref)}_lines_exact",
            not problems,
            f"{ref}: " + ("; ".join(problems) or "reported correctly"),
        )

    # Keeping an unpayable punch off the report is free for a run that wrote no
    # report at all, so each of these first needs a witness that the connector
    # produced payroll lines for somebody.
    for want in skipped:
        ref = want["punch_ref"]
        got_rows = rows_by_ref.get(ref, [])
        ctx.check_l1(
            f"report_{_slug(ref)}_lines_exact",
            bool(rows) and not got_rows,
            f"{ref}: {len(got_rows)} payroll line(s) for a punch that is not payable "
            f"({want['reason']})" if got_rows else
            (f"{ref}: kept off the payroll report" if rows else
             "the run wrote no payroll lines at all — nothing to judge"),
        )

    # -- the same export again -------------------------------------------------
    n_before_rerun = len(ctx.vendor(VENDOR).request_log())
    code2, _out2, err2 = ctx.app.run()
    result2 = _read_json(ctx, "result.json")
    report2 = _read_report(ctx)
    writeback2 = _read_json(ctx, "writeback_log.json")
    ctx.check_l1(
        "payroll_bridge_rerun_completed",
        code2 == 0 and isinstance(result2, dict) and isinstance(report2, list)
        and isinstance(writeback2, dict),
        f"exit={code2} result={type(result2).__name__} stderr={err2[:400]}",
    )
    ctx.check_l1(
        "payroll_rerun_reproduces_the_same_placement",
        isinstance(result, dict) and isinstance(result2, dict) and result == result2
        and report == report2 and writeback == writeback2,
        "the second run over the same export must reproduce all three artifacts: "
        f"result_equal={result == result2} report_equal={report == report2} "
        f"writeback_equal={writeback == writeback2}",
    )
    connector_log = ctx.vendor(VENDOR).request_log()
    rerun_talked = len(connector_log) > n_before_rerun

    expected_writes = {n["idempotency_key"]: n for n in notes_expected}
    writes = [e for e in connector_log
              if e.get("method") == "POST" and "/notes" in str(e.get("path") or "")]
    write_problems = []
    seen_keys = set()
    for entry in writes:
        idem = str(entry.get("idempotency_key") or "")
        want = expected_writes.get(idem)
        if want is None:
            write_problems.append(f"unexpected idempotency key {idem!r}")
            continue
        seen_keys.add(idem)
        expected_path = key["note_write_path_template"].format(worker_id=want["worker_id"])
        expected_body = {"body": want["body"], "author": key["note_author"]}
        if (entry.get("path") != expected_path or entry.get("body") != expected_body
                or int(entry.get("status") or 0) != 201):
            write_problems.append(
                f"{idem}: path/body/status={entry.get('path')!r}/"
                f"{entry.get('body')!r}/{entry.get('status')!r}")
    ctx.check_l1(
        "note_write_requests_use_exact_semantics_and_idempotency_keys",
        seen_keys == set(expected_writes) and not write_problems,
        f"saw {len(writes)} note POST(s), {len(seen_keys)} expected key(s); "
        f"missing={sorted(set(expected_writes) - seen_keys)[:3]} "
        f"problems={write_problems[:3]}",
    )

    # -- conduct, once per vendor lifetime, over the connector's traffic ONLY --
    # Nothing this verifier does has touched the vendor yet, so no exclusion
    # list is needed and none can go stale.
    await builtin_l2(ctx, app_runs=2)

    # -- what the tenant actually holds ---------------------------------------
    token = _token(ctx)
    expected_by_worker: dict[str, list[str]] = {}
    for note in notes_expected:
        expected_by_worker.setdefault(note["worker_id"], []).append(note["body"])

    held_payroll_ids: set[str] = set()
    posted_anything = False
    for worker_id in key["note_workers"]:
        try:
            held = _worker_notes(ctx, token, worker_id)
        except urllib.error.HTTPError as exc:
            held = []
            fetch_error = f" (reading the worker's notes failed: {exc.code})"
        else:
            fetch_error = ""
        payroll = [n for n in held
                   if str(n.get("body") or "").startswith(NOTE_BODY_PREFIX)]
        held_payroll_ids.update(str(n.get("id")) for n in payroll)
        if payroll:
            posted_anything = True
        want_bodies = sorted(expected_by_worker.get(worker_id, []))
        got_bodies = sorted(str(n.get("body") or "") for n in payroll)
        total_ok = len(held) == seeded.get(worker_id, 0) + len(want_bodies)
        ctx.check_l1(
            f"venue_notes_{worker_id}_exact",
            got_bodies == want_bodies and total_ok,
            f"{worker_id} holds {len(payroll)} payroll note(s) and {len(held)} note(s) "
            f"in total (expected {len(want_bodies)} and "
            f"{seeded.get(worker_id, 0) + len(want_bodies)}); bodies={got_bodies[:2]} "
            f"want={want_bodies[:2]}{fetch_error}",
        )

    cancelled_refs = {u["punch_ref"] for u in skipped}
    stray: list[str] = []
    for worker_id in key["cancelled_shift_workers"]:
        try:
            held = _worker_notes(ctx, token, worker_id)
        except urllib.error.HTTPError:
            held = []
        for note in held:
            if not str(note.get("body") or "").startswith(NOTE_BODY_PREFIX):
                continue
            text = str(note.get("body") or "")
            if any(ref in text for ref in cancelled_refs):
                stray.append(f"{worker_id}:{note.get('id')}")
    ctx.check_l1(
        "no_payroll_note_written_back_for_a_cancelled_shift",
        posted_anything and not stray,
        f"{len(stray)} note(s) were written back against a cancelled shift: {stray[:3]}"
        if stray else ("the run posted no payroll notes at all — nothing to judge"
                       if not posted_anything else
                       "no cancelled shift's crew member was written to"),
    )

    logged_ids = {str(n.get("note_id")) for n in (wb.get("notes") or [])
                  if isinstance(n, dict)}
    wb_rows = wb.get("notes") if isinstance(wb.get("notes"), list) else []
    wb_by_ref = {str(n.get("punch_ref")): n for n in wb_rows if isinstance(n, dict)}
    writeback_exact = len(wb_by_ref) == len(wb_rows) == len(notes_expected)
    for want in notes_expected:
        got = wb_by_ref.get(want["punch_ref"])
        expected_fields = {
            "punch_ref": want["punch_ref"],
            "shift_id": want["shift_id"],
            "worker_id": want["worker_id"],
            "idempotency_key": want["idempotency_key"],
        }
        if (not isinstance(got, dict) or set(got) != set(expected_fields) | {"note_id"}
                or any(got.get(k) != v for k, v in expected_fields.items())
                or str(got.get("note_id") or "") not in held_payroll_ids):
            writeback_exact = False
    ctx.check_l1(
        "writeback_log_names_the_notes_the_tenant_holds",
        bool(held_payroll_ids) and logged_ids == held_payroll_ids and writeback_exact,
        f"writeback_log.json names {len(logged_ids)} note id(s), the tenant holds "
        f"{len(held_payroll_ids)}; only-in-log={sorted(logged_ids - held_payroll_ids)[:3]} "
        f"only-on-vendor={sorted(held_payroll_ids - logged_ids)[:3]}",
    )

    # -- did the connector read the roster it placed these punches against? ---
    log = connector_log[:n_before_rerun]
    by_id_ok = set()
    for entry in log:
        path = str(entry.get("path") or "")
        if path.startswith(key["list_path"] + "/") and int(entry.get("status") or 0) == 200:
            by_id_ok.add(path.rsplit("/", 1)[-1])
    referenced = {p["shift_id"] for p in punches}
    walked = _covers_whole_collection(log, key["list_path"], key["shift_total"])
    ctx.check_l1(
        "shift_roster_read_from_the_vendor_for_every_payable_punch",
        rerun_talked and (walked or referenced <= by_id_ok),
        f"the connector fetched {len(by_id_ok)} shift(s) by id and "
        f"{'did' if walked else 'did not'} walk the whole collection; "
        f"{len(referenced - by_id_ok)} referenced shift(s) unaccounted for"
        + ("" if rerun_talked else " (the second run made no requests at all)"),
    )
