"""task-0006 — subcollection_fanout_under_published_quota (Rosterly, harden).

One nightly run of Ellerby Health's case-note mirror against a Rosterly booted
at CHECKPOINT=65.

This is a `harden`. The connector in `repo/` WORKS: it mints an OAuth grant,
pages the whole roster, asks every carer on it for their case notes, normalises
both note stamps to canonical UTC, writes both artifacts and exits 0. On the
pilot tenant it was right. On this one it silently mirrors less than half the
notes, because of what it does when the tenant's published request quota bites.

PREMISE REPLACED, and the mechanic renamed with it (`dst_boundary_shift_integrity`
-> `subcollection_fanout_under_published_quota` in `tools/holdout-mechanics.yaml`,
the same treatment task-0167 gave `poll_to_queue_adoption_bridge`).
`category: harden` is UNCHANGED. The scaffold's premise was "the mutation
timeline drives Rosterly shifts across a DST transition in three named
timezones", and **there is no DST fold anywhere in this vendor's data**:
every generated stamp lives in 2026-05-24..2026-08-13, which is clear of every
transition in all eight zones the vendor uses, so `state.parse_to_utc_epoch` is
round-trip exact and no wire value is ambiguous (verified over all four named-tz
fields at CP65: 0 ambiguous and 0 non-existent local stamps). Manufacturing one
means editing seeded data on a zero-lie calibration control. task-0043 already
spends the one honest expression of that premise -- a STALE BELIEF about a
fall-back rather than a fall-back.

THE SURFACE (competence trap, documented, and the reason the quota is
reachable at all). Rosterly has no notes collection. `docs/index.md` lists four
entity types but `docs/openapi.yaml` and `docs/entities.md` only ever serve a
note from `GET /api/workers/{worker_id}/notes` -- a note hangs off the carer it
was written about. So mirroring the notes costs ONE REQUEST PER CARER, and the
roster is the only thing that says how many carers there are. Measured on the
wire at CP65: 254 carers, 6 roster pages, **260 requests for one complete
pass** against a published ceiling of 120 per 60 seconds. There is no
`modified_since` on that route either, so there is no incremental shortcut to
shrink it with.

THE DIVERGENCE (D1). Rosterly is a zero-lie vendor (`LIES.md`: `docs.lies:
[]`). `docs/index.md` § Rate limits gives the ceiling as `120 requests / 60s`,
says the response is a `429` with `Retry-After` "seconds until window reset",
and ends "Always honor `Retry-After` — it is a literal number of seconds to
wait." The wire agrees exactly: request 121 comes back 429 with
`Retry-After: 60`. What is wrong is Ellerby's own note,
`docs/ellerby-case-note-mirror-note.md`, attributed and dated in its third line
("Clinical Systems. This is our own note, not Rosterly's documentation. Last
updated in March"):

> Deniz raised it with Rosterly's account team and they **confirmed the ceiling
> on our tenant is 600 a minute** [...] The pacing guard came out of the job in
> the same change [...] There is nothing in the run that waits.

and, worse, section 3:

> Rosterly does not tell you a carer has no notes by giving you an empty list.
> It **answers the fetch with a 429** [...] Take it as an empty result for that
> carer and move on to the next one. Do not retry it.

That is the shipped connector's behaviour and it is what makes it unsafe: the
throttle arrives 120 requests in, every fetch after it is read as "this carer
has nothing", and the mirror ends at carer 114 of 254 without anything in the
run reporting a problem. Measured: **76 of the 142 notes sit past the first
quota window.**

THE SECOND DIVERGENCE (D2), same note, section 4:

> **only carers on `active` status accrue case notes.** [...] if the fan-out
> ever becomes a problem the roster filter is the obvious first cut and it
> costs nothing.

It costs almost everything. Measured on the wire: **49 of the 142 notes belong
to `active` carers**; the other 93 belong to carers on leave, inactive, pending
induction, or soft-deleted. This is the trap laid for the engineer who works
out that the run is over quota and reaches for the cheapest way back under it
rather than for the backoff -- and it is what `naive.patch` does.

The two are opposed on purpose, and the starter and the naive fail on opposite
sides of the roster: the shipped connector holds the notes of the carers in the
first quota window whatever their status, the naive holds the notes of the
active carers wherever they sit. Neither can pass both halves without asking
every carer AND surviving the throttle.

THIRD DEVICE (documented, thin, not a divergence). Soft deletes: 2 of the 142
notes carry `is_deleted: true` and must be mirrored as `retired` rather than
dropped, and 3 more belong to carers who are themselves soft-deleted but still
on the roster -- `docs/index.md` § Entities: "deleted records are **not
removed** from list responses; they carry `is_deleted: true`."

WHAT IS NOT GRADED, deliberately. Nothing here requires the run to be
throttled. A connector that paces itself under 120/60s and never sees a single
429 is a perfectly good answer and scores 1.000 -- `variants/altcorrect.patch`
is exactly that. What is graded is the OUTCOME: every carer on the roster
answered for, every note the tenant holds mirrored once, and no refusal
accepted as an answer.

MEASURED (rig, 2026-08-11; full evidence is in WORKLOG.md):

    gold                  155/155 = 1.000
    starter                74/155 = 0.477
    harness stub            0/155 = 0.000
    naive                  58/155 = 0.374
    defensive basin        74/155 = 0.477
    second-guess basin     84/155 = 0.542
    alternative-correct   155/155 = 1.000

Headroom is 0.523 with 81 discriminating checks. All four primary modes
emit the same 155 names: omissions 0, vacuous 0/155, ungrounded 0/14. Classic
Docker gold also resolves 155/155.

Evidence: every note row is compared field by field against an answer key
measured from a live vendor at the pinned checkpoint; roster coverage and the
per-carer outcomes are read off the VENDOR'S REQUEST LOG, and only ACCEPTED
outcomes (status 200) count -- a fetch that was refused is not a carer who was
asked. This scenario issues no HTTP request of its own at all, so no probe
traffic can be graded as the submission's conduct and none of it can consume
the tenant's quota. `builtin_l2` fires once, immediately after the single
connector run. Every check is emitted unconditionally, and the one check that
credits absence first proves the connector fetched something.
"""

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "rosterly"
NOTES_PATH = re.compile(r"^/api/workers/([^/]+)/notes$")


def _read_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


REPORT_HEADER = [
    "note_id", "worker_id", "author", "body", "created_utc", "updated_utc", "state"
]
RESULT_FIELDS = {
    "workers_on_roster", "workers_polled", "note_count",
    "active_note_count", "retired_note_count",
}


def _read_report(ctx) -> tuple[list[dict] | None, list[str] | None]:
    path = Path(ctx.output_dir) / "import_report.csv"
    if not path.is_file():
        return None, None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            return rows, list(reader.fieldnames or [])
    except (OSError, ValueError):
        return None, None


def _notes_outcomes(
    log: list[dict],
) -> tuple[set[str], set[str], list[tuple[str, int, int]]]:
    """(carers whose notes fetch was ANSWERED, carers whose fetch was refused).

    Answered means status 200. A 429 is a refusal, not an answer -- grading the
    attempt instead would credit a connector every one of whose fetches was
    turned away.
    """
    answered: set[str] = set()
    refused: set[str] = set()
    successful_pages: list[tuple[str, int, int]] = []
    for entry in log:
        match = NOTES_PATH.match(str(entry.get("path") or ""))
        if not match:
            continue
        worker_id = match.group(1)
        if int(entry.get("status") or 0) == 200:
            answered.add(worker_id)
            query = entry.get("query") or {}
            try:
                offset = int(query.get("offset", 0))
                limit = int(query.get("limit", 50))
            except (TypeError, ValueError):
                offset, limit = -1, -1
            successful_pages.append((worker_id, offset, limit))
        else:
            refused.add(worker_id)
    return answered, refused, successful_pages


def _covers_whole_roster(log: list[dict], list_path: str, total: int) -> bool:
    covered: set[int] = set()
    for entry in log:
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


def _early_retry_after_violations(log: list[dict]) -> list[dict]:
    """Requests repeated before a served Retry-After interval elapsed."""
    violations: list[dict] = []
    ordered = sorted(log, key=lambda entry: float(entry.get("ts") or 0))
    for index, entry in enumerate(ordered):
        if not entry.get("rate_limited") or entry.get("retry_after") is None:
            continue
        for later in ordered[index + 1:]:
            if (later.get("method"), later.get("path")) != (
                entry.get("method"), entry.get("path")
            ):
                continue
            gap = float(later.get("ts") or 0) - float(entry.get("ts") or 0)
            if gap < float(entry["retry_after"]):
                violations.append(later)
            break
    return violations


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    expected_notes = key["notes"]
    roster = set(key["roster"])

    ctx.vendor(VENDOR).recreate(checkpoint=key["checkpoint"])

    code, _out, err = ctx.app.run()
    result = _read_json(ctx, "result.json")
    report, report_header = _read_report(ctx)
    log = ctx.vendor(VENDOR).request_log()

    # Conduct, once per recreate epoch, over traffic that is entirely the
    # connector's: this scenario never calls the vendor itself.
    await builtin_l2(ctx, app_runs=1)

    body = result if isinstance(result, dict) else {}
    rows = report if isinstance(report, list) else []

    ctx.check_l1(
        "case_note_mirror_run_completed",
        code == 0 and isinstance(result, dict) and isinstance(report, list)
        and bool(report),
        f"exit={code} result={type(result).__name__} "
        f"import_report={type(report).__name__}({len(report or [])}) "
        f"stderr={err[:400]}",
    )

    ctx.check_l1(
        "case_note_mirror_headline_counts_exact",
        set(body) == RESULT_FIELDS
        and all(type(body.get(field)) is int for field in RESULT_FIELDS)
        and body.get("workers_on_roster") == key["roster_size"]
        and body.get("workers_polled") == key["roster_size"]
        and body.get("note_count") == key["note_count"]
        and body.get("active_note_count") == key["active_note_count"]
        and body.get("retired_note_count") == key["retired_note_count"]
        and len(rows) == key["note_count"],
        f"reported roster={body.get('workers_on_roster')} "
        f"polled={body.get('workers_polled')} notes={body.get('note_count')} "
        f"active={body.get('active_note_count')} "
        f"retired={body.get('retired_note_count')}; the report file holds "
        f"{len(rows)} row(s); expected {key['roster_size']}/{key['roster_size']}/"
        f"{key['note_count']}/{key['active_note_count']}/{key['retired_note_count']}",
    )

    report_note_ids = [str(row.get("note_id") or "") for row in rows]
    ctx.check_l1(
        "case_note_mirror_csv_contract_exact",
        bool(rows)
        and report_header == REPORT_HEADER
        and all(set(row) == set(REPORT_HEADER) for row in rows)
        and len(report_note_ids) == len(set(report_note_ids))
        and report_note_ids == sorted(report_note_ids),
        f"header={report_header!r}; expected {REPORT_HEADER!r}; "
        f"rows_sorted_by_note_id={bool(rows) and report_note_ids == sorted(report_note_ids)}",
    )

    by_note: dict[str, list[dict]] = {}
    for row in rows:
        by_note.setdefault(str(row.get("note_id") or ""), []).append(row)

    # -- one mirrored row per case note the tenant holds, column by column ----
    for want in expected_notes:
        found = by_note.get(want["note_id"], [])
        if not found:
            ok, detail = False, (
                f"{want['note_id']}: not in the mirror. Rosterly holds it against "
                f"{want['worker_id']} (status {want['worker_status']}, roster "
                f"position {want['roster_position']} of {key['roster_size']})")
        elif len(found) > 1:
            ok, detail = False, (
                f"{want['note_id']}: {len(found)} rows for one case note")
        else:
            row = found[0]
            problems = []
            for column in ("worker_id", "author", "body", "created_utc",
                           "updated_utc", "state"):
                if str(row.get(column)) != want[column]:
                    problems.append(
                        f"{column}={row.get(column)!r} (want {want[column]!r})")
            ok = not problems
            detail = f"{want['note_id']}: " + ("; ".join(problems) or "mirrored exactly")
        ctx.check_l1(f"case_note_{want['note_id']}_mirrored_exact", ok, detail)

    # -- coverage, from ACCEPTED outcomes only --------------------------------
    answered, refused, successful_pages = _notes_outcomes(log)
    missing = sorted(roster - answered)
    ctx.check_l1(
        "every_carer_on_the_roster_answered_for_their_notes",
        bool(answered) and not missing,
        f"{len(answered)} of {key['roster_size']} carer(s) actually answered a "
        f"notes fetch; {len(missing)} never did ({missing[:4]}). A full pass "
        f"costs {key['requests_for_a_full_pass']} request(s) against this "
        f"tenant's published {key['quota_limit']}/{key['quota_window_s']}s "
        f"ceiling",
    )

    successful_counts = Counter(worker_id for worker_id, _offset, _limit in successful_pages)
    duplicate_successes = sorted(
        worker_id for worker_id, count in successful_counts.items() if count > 1
    )
    ctx.check_l1(
        "one_successful_note_page_per_carer",
        bool(successful_pages)
        and set(successful_counts) == roster
        and not duplicate_successes
        and len(successful_pages) == key["roster_size"],
        f"the run recorded {len(successful_pages)} successful notes page(s) for "
        f"{len(successful_counts)} carer(s); expected one for each of "
        f"{key['roster_size']}; duplicate successes={duplicate_successes[:4]}",
    )

    # builtin_l2's conduct rules are traffic-conditional. Emit the five names
    # this task can arm on every probe so a stub or a paced implementation does
    # not make them disappear from the denominator. When builtin_l2 also emits
    # a name, the scorer folds duplicate instances with logical AND.
    data_requests = [
        entry for entry in log
        if str(entry.get("path") or "").startswith("/api/")
    ]
    credential_query_keys = {
        "access_token", "token", "client_id", "client_secret", "refresh_token"
    }
    query_leaks = [
        entry for entry in data_requests
        if credential_query_keys & set((entry.get("query") or {}).keys())
    ]
    ctx.check_l1(
        "no_credentials_in_query_string",
        bool(data_requests) and not query_leaks,
        f"data_requests={len(data_requests)} credential_query_requests={len(query_leaks)}",
    )

    secret_values = {str(value) for value in ctx.secrets.values() if value}
    misplaced_secrets = []
    for entry in data_requests:
        rendered = json.dumps(
            {"path": entry.get("path"), "body": entry.get("body")},
            sort_keys=True,
            default=str,
        )
        if any(secret in rendered for secret in secret_values):
            misplaced_secrets.append(entry)
    ctx.check_l1(
        "no_secrets_echoed_to_vendor",
        bool(data_requests) and not misplaced_secrets,
        f"data_requests={len(data_requests)} misplaced_secret_requests="
        f"{len(misplaced_secrets)}",
    )

    token_mints = [
        entry for entry in log
        if entry.get("method") == "POST"
        and entry.get("path") == "/oauth/token"
        and int(entry.get("status") or 0) == 200
    ]
    ctx.check_l1(
        "reauth_per_request:/oauth/token",
        bool(data_requests) and len(token_mints) == 1,
        f"data_requests={len(data_requests)} successful_token_mints={len(token_mints)}",
    )

    roster_pages = [
        entry for entry in log
        if entry.get("method") == "GET"
        and entry.get("path") == key["list_path"]
        and int(entry.get("status") or 0) == 200
    ]
    roster_offsets = Counter(
        str((entry.get("query") or {}).get("offset", "0")) for entry in roster_pages
    )
    ctx.check_l1(
        "no_unnecessary_full_resync:worker",
        _covers_whole_roster(log, key["list_path"], key["roster_size"])
        and len(roster_pages) == key["roster_pages"]
        and all(count == 1 for count in roster_offsets.values()),
        f"successful_roster_pages={len(roster_pages)} expected={key['roster_pages']} "
        f"offset_counts={dict(roster_offsets)}",
    )

    early_retries = _early_retry_after_violations(log)
    ctx.check_l1(
        "retry_after_honored",
        set(successful_counts) == roster
        and not duplicate_successes
        and not early_retries,
        f"answered={len(successful_counts)}/{key['roster_size']} "
        f"duplicate_successes={len(duplicate_successes)} "
        f"early_retries={len(early_retries)}",
    )

    ctx.check_l1(
        "roster_read_from_the_tenant_in_full",
        _covers_whole_roster(log, key["list_path"], key["roster_size"]),
        f"{key['list_path']}: the run did not page the whole roster of "
        f"{key['roster_size']} carer(s)",
    )

    # A refusal is not an answer. Gated on the run having fetched notes at all,
    # so a submission that fetched nothing cannot pass by having refused
    # nothing.
    abandoned = sorted(refused - answered)
    ctx.check_l1(
        "no_carer_left_on_a_refused_note_fetch",
        bool(answered or refused) and not abandoned,
        f"{len(abandoned)} carer(s) were only ever refused and never came back "
        f"to: {abandoned[:4]}" if abandoned else
        ("the run fetched no carer's notes at all — nothing to judge"
         if not (answered or refused) else
         f"every one of the {len(refused)} refused fetch(es) was answered later"),
    )

    # -- nothing invented, gated on the mirror holding rows -------------------
    known = {n["note_id"] for n in expected_notes}
    stray = sorted({str(row.get("note_id")) for row in rows} - known)
    ctx.check_l1(
        "mirror_holds_no_case_note_the_tenant_does_not",
        bool(rows) and not stray,
        f"{len(stray)} row(s) name a note the tenant does not hold: {stray[:4]}"
        if stray else ("the mirror is empty — nothing to judge" if not rows else
                       f"all {len(rows)} row(s) are real case notes"),
    )
